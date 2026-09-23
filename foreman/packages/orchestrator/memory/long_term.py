"""Tier 3 — long-term memory in ChromaDB (Architecture.md §7.3).

One collection per embedding model (``memories__<model>``): a different model is a different vector
space and must never share an index. Documents are lesson texts; metadata carries user, task type,
outcome, importance, timestamps, access count, tools. Writes dedup at ``dedup_threshold`` cosine
similarity by reinforcing the existing record (importance +0.5, access +1) instead of inserting.
Recall filters by user, takes the top ``k`` and keeps at most ``keep`` records / ``max_tokens``.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from packages.orchestrator.llm.embeddings import Embedder
from packages.shared.types.memory import MemoryRecord, RecalledMemory, StoredMemory

log = structlog.get_logger(__name__)

ISO = "%Y-%m-%dT%H:%M:%S+00:00"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime(ISO)


def _parse(value: str | None, fallback: dt.datetime) -> dt.datetime:
    if not value:
        return fallback
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return fallback


def approx_tokens(text: str) -> int:
    """Cheap, provider-agnostic estimate used for the recall budget."""
    return max(1, math.ceil(len(text) / 4))


def collection_name(base: str, model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"{base}__{slug}"[:63]


def effective_importance(
    importance: float, last_accessed: dt.datetime, *, now: dt.datetime, half_life_days: float
) -> float:
    """Importance decays with time since the record was last useful; never rewritten in place."""
    idle_days = max(0.0, (now - last_accessed).total_seconds() / 86400)
    return round(float(importance * 0.5 ** (idle_days / half_life_days)), 3)


@dataclass
class WriteReport:
    inserted: list[str] = field(default_factory=list)
    reinforced: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return self.inserted + self.reinforced


class LongTermMemory:
    def __init__(
        self,
        client: Any,
        embedder: Embedder,
        model: str,
        *,
        collection: str = "memories",
        dedup_threshold: float = 0.92,
        half_life_days: float = 30.0,
        clock: Callable[[], dt.datetime] = _now,
    ) -> None:
        self.model = model
        self.collection = collection_name(collection, model)
        self._embedder = embedder
        self._threshold = dedup_threshold
        self._half_life = half_life_days
        self._clock = clock
        self._col = client.get_or_create_collection(
            name=self.collection, metadata={"hnsw:space": "cosine", "embedding_model": model}
        )

    # ---- embeddings ----

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        return (await self._embedder.embed(texts, model=self.model)).vectors

    # ---- writes ----

    async def write(
        self, user_id: str, records: list[MemoryRecord], *, source_task_id: str = ""
    ) -> WriteReport:
        report = WriteReport()
        if not records:
            return report
        vectors = await self._embed([r.text for r in records])
        now = _iso(self._clock())
        for record, vector in zip(records, vectors, strict=True):
            twin = self._nearest(user_id, vector)
            if twin is not None and twin[1] >= self._threshold:
                mid, _, meta = twin
                self._col.update(
                    ids=[mid],
                    metadatas=[
                        {
                            **meta,
                            "importance": min(5.0, float(meta.get("importance", 3.0)) + 0.5),
                            "access_count": int(meta.get("access_count", 0)) + 1,
                            "last_accessed": now,
                        }
                    ],
                )
                report.reinforced.append(mid)
                continue
            mid = uuid.uuid4().hex
            self._col.add(
                ids=[mid],
                embeddings=[vector],
                documents=[record.text],
                metadatas=[
                    {
                        "user_id": user_id,
                        "task_type": record.task_type,
                        "outcome": record.outcome.value,
                        "importance": float(record.importance),
                        "created_at": now,
                        "last_accessed": now,
                        "access_count": 0,
                        "tools_used": ",".join(record.tools_used),
                        "source_task_id": source_task_id,
                    }
                ],
            )
            report.inserted.append(mid)
        log.info(
            "memory.write",
            user_id=user_id,
            inserted=len(report.inserted),
            reinforced=len(report.reinforced),
        )
        return report

    def _nearest(
        self, user_id: str, vector: list[float]
    ) -> tuple[str, float, dict[str, Any]] | None:
        if self._col.count() == 0:
            return None
        res = self._col.query(
            query_embeddings=[vector],
            n_results=1,
            where={"user_id": user_id},
            include=["metadatas", "distances"],
        )
        ids = res.get("ids") or [[]]
        if not ids[0]:
            return None
        distance = float(res["distances"][0][0])
        return ids[0][0], 1.0 - distance, dict(res["metadatas"][0][0])

    # ---- recall ----

    async def recall(
        self,
        user_id: str,
        query: str,
        *,
        k: int = 5,
        keep: int = 3,
        max_tokens: int = 600,
        task_type: str | None = None,
    ) -> list[RecalledMemory]:
        if self._col.count() == 0:
            return []
        vector = (await self._embed([query]))[0]
        where: dict[str, Any] = (
            {"user_id": user_id}
            if task_type is None
            else {"$and": [{"user_id": user_id}, {"task_type": task_type}]}
        )
        res = self._col.query(
            query_embeddings=[vector],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        if not ids:
            return []
        chosen: list[RecalledMemory] = []
        budget = max_tokens
        for mid, doc, meta, distance in zip(
            ids, res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
        ):
            cost = approx_tokens(doc)
            if len(chosen) >= keep or cost > budget:
                continue
            budget -= cost
            chosen.append(
                RecalledMemory(
                    id=mid,
                    text=doc,
                    score=round(1.0 - float(distance), 4),
                    task_type=str(meta.get("task_type", "general")),
                    outcome=str(meta.get("outcome", "success")),
                    importance=float(meta.get("importance", 3.0)),
                    created_at=str(meta.get("created_at", "")),
                )
            )
        if chosen:
            self._touch([m.id for m in chosen], res["metadatas"][0], ids)
        return chosen

    def _touch(self, chosen: list[str], metadatas: list[dict[str, Any]], ids: list[str]) -> None:
        now = _iso(self._clock())
        by_id = dict(zip(ids, metadatas, strict=True))
        self._col.update(
            ids=chosen,
            metadatas=[
                {
                    **by_id[mid],
                    "last_accessed": now,
                    "access_count": int(by_id[mid].get("access_count", 0)) + 1,
                }
                for mid in chosen
            ],
        )

    # ---- browsing, deletion, maintenance (Chroma only; no embeddings) ----

    def list_user(self, user_id: str) -> list[StoredMemory]:
        res = self._col.get(where={"user_id": user_id}, include=["documents", "metadatas"])
        now = self._clock()
        rows = [
            self._stored(mid, doc, meta, now)
            for mid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"], strict=True)
        ]
        rows.sort(key=lambda r: (-r.effective_importance, r.created_at), reverse=False)
        return rows

    def delete_user(self, user_id: str) -> int:
        res = self._col.get(where={"user_id": user_id}, include=[])
        ids = list(res["ids"])
        if ids:
            self._col.delete(ids=ids)
        log.info("memory.delete_user", user_id=user_id, deleted=len(ids))
        return len(ids)

    def count(self) -> int:
        return int(self._col.count())

    def users(self) -> list[dict[str, Any]]:
        """Who has lessons, and how many — so an operator never has to guess a user id."""
        counts: dict[str, int] = {}
        for _, meta in self.all_metadata():
            uid = str(meta.get("user_id", ""))
            counts[uid] = counts.get(uid, 0) + 1
        return [
            {"user_id": uid, "count": n}
            for uid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def all_metadata(self) -> list[tuple[str, dict[str, Any]]]:
        res = self._col.get(include=["metadatas"])
        return list(zip(res["ids"], [dict(m) for m in res["metadatas"]], strict=True))

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self._col.delete(ids=ids)

    def _stored(self, mid: str, doc: str, meta: dict[str, Any], now: dt.datetime) -> StoredMemory:
        created = str(meta.get("created_at", ""))
        accessed = str(meta.get("last_accessed", created))
        importance = float(meta.get("importance", 3.0))
        tools = str(meta.get("tools_used", ""))
        return StoredMemory(
            id=mid,
            user_id=str(meta.get("user_id", "")),
            text=doc,
            task_type=str(meta.get("task_type", "general")),
            outcome=str(meta.get("outcome", "success")),
            importance=importance,
            effective_importance=effective_importance(
                importance, _parse(accessed, now), now=now, half_life_days=self._half_life
            ),
            created_at=created,
            last_accessed=accessed,
            access_count=int(meta.get("access_count", 0)),
            tools_used=[t for t in tools.split(",") if t],
            source_task_id=str(meta.get("source_task_id", "")),
        )
