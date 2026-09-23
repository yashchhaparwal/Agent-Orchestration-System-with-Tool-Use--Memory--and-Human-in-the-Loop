"""Tier 3 against an in-process Chroma client and a deterministic fake embedder: dedup reinforces
instead of inserting, recall is per user and trimmed to the caps, delete removes one user's records
and nothing else, consolidation expires by effective importance and age."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import uuid

import chromadb
import pytest

from packages.orchestrator.llm.embeddings import EmbeddingResult
from packages.orchestrator.memory.consolidate import consolidate
from packages.orchestrator.memory.long_term import (
    LongTermMemory,
    approx_tokens,
    collection_name,
    effective_importance,
)
from packages.shared.types.memory import MemoryOutcome, MemoryRecord

DIM = 512  # sparse enough that unrelated texts do not collide into similarity


class FakeEmbedder:
    """Bag-of-words hashed into a fixed space: identical texts → identical vectors."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            v = [0.0] * DIM
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0  # noqa: S324
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return EmbeddingResult(vectors=vectors, provider="fake", model=model or "fake-embed")


class Clock:
    def __init__(self, start: dt.datetime) -> None:
        self.now = start

    def __call__(self) -> dt.datetime:
        return self.now


T0 = dt.datetime(2026, 8, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def memory() -> tuple[LongTermMemory, FakeEmbedder, Clock]:
    clock = Clock(T0)
    embedder = FakeEmbedder()
    client = chromadb.EphemeralClient()
    return (
        LongTermMemory(
            client, embedder, "fake-embed", collection=f"t{uuid.uuid4().hex[:8]}", clock=clock
        ),  # EphemeralClient is a process-wide singleton: one collection per test
        embedder,
        clock,
    )


def rec(text: str, **kw: object) -> MemoryRecord:
    return MemoryRecord(text=text, **kw)  # type: ignore[arg-type]


async def test_write_then_list(memory: tuple[LongTermMemory, FakeEmbedder, Clock]) -> None:
    lt, embedder, _ = memory
    assert collection_name("memories", "fake-embed") == "memories__fake-embed"
    assert lt.collection.endswith("__fake-embed")
    report = await lt.write(
        "u1",
        [
            rec(
                "The loans table has no income column; use rollover flags.",
                task_type="claim_review",
                importance=4,
                tools_used=["db_query"],
            )
        ],
        source_task_id="t1",
    )
    assert len(report.inserted) == 1 and report.reinforced == []
    rows = lt.list_user("u1")
    assert len(rows) == 1
    row = rows[0]
    assert row.text.startswith("The loans table") and row.task_type == "claim_review"
    assert row.importance == 4 and row.effective_importance == 4  # just written: no decay
    assert row.tools_used == ["db_query"] and row.source_task_id == "t1"
    assert row.access_count == 0 and row.created_at == "2026-08-01T12:00:00+00:00"
    assert embedder.calls == [["The loans table has no income column; use rollover flags."]]


async def test_duplicate_reinforces_instead_of_inserting(
    memory: tuple[LongTermMemory, FakeEmbedder, Clock],
) -> None:
    lt, _, clock = memory
    text = "This user rejects sending emails: drafts only."
    first = await lt.write("u1", [rec(text, outcome=MemoryOutcome.DECISION, importance=4.0)])
    clock.now = T0 + dt.timedelta(days=1)
    second = await lt.write("u1", [rec(text, outcome=MemoryOutcome.DECISION, importance=4.0)])
    assert second.inserted == [] and second.reinforced == first.inserted
    assert lt.count() == 1
    row = lt.list_user("u1")[0]
    assert row.importance == 4.5 and row.access_count == 1
    assert (
        row.last_accessed == "2026-08-02T12:00:00+00:00"
        and row.created_at == "2026-08-01T12:00:00+00:00"
    )
    # the same lesson for another user is a separate record
    other = await lt.write("u2", [rec(text, outcome=MemoryOutcome.DECISION)])
    assert len(other.inserted) == 1 and lt.count() == 2


async def test_recall_is_per_user_and_touches_records(
    memory: tuple[LongTermMemory, FakeEmbedder, Clock],
) -> None:
    lt, _, clock = memory
    await lt.write(
        "u1",
        [
            rec("Complaint letters need the claim reference in the subject line."),
            rec("Sandbox python is slow for large tables; query with SQL instead."),
        ],
    )
    await lt.write("u2", [rec("Complaint letters need the claim reference in the subject line.")])
    clock.now = T0 + dt.timedelta(hours=3)
    found = await lt.recall(
        "u1", "draft the complaint letter with the claim reference", k=5, keep=3, max_tokens=600
    )
    assert 1 <= len(found) <= 2
    assert found[0].text.startswith("Complaint letters") and 0 < found[0].score <= 1.0
    assert all(r.user_id == "u1" for r in lt.list_user("u1"))
    touched = {r.id: r for r in lt.list_user("u1")}[found[0].id]
    assert touched.access_count == 1 and touched.last_accessed == "2026-08-01T15:00:00+00:00"
    assert await lt.recall("nobody", "anything") == []


async def test_recall_respects_keep_and_token_caps(
    memory: tuple[LongTermMemory, FakeEmbedder, Clock],
) -> None:
    lt, _, _ = memory
    texts = [
        " ".join(f"topic{i}-term{j:02d}-claim" for j in range(30)) for i in range(5)
    ]  # ~570 chars ≈ 143 tokens each (under the 600-char record cap), no shared words
    assert all(130 <= approx_tokens(t) <= 160 for t in texts)
    await lt.write("u1", [rec(t) for t in texts])
    assert lt.count() == 5  # distinct enough not to dedup
    by_tokens = await lt.recall("u1", "claim review", k=5, keep=3, max_tokens=300)
    assert len(by_tokens) == 2  # 143 + 143 fit, a third would exceed 300
    by_keep = await lt.recall("u1", "claim review", k=5, keep=1, max_tokens=10_000)
    assert len(by_keep) == 1
    generous = await lt.recall("u1", "claim review", k=5, keep=3, max_tokens=10_000)
    assert len(generous) == 3


async def test_delete_user_removes_only_that_user(
    memory: tuple[LongTermMemory, FakeEmbedder, Clock],
) -> None:
    lt, _, _ = memory
    await lt.write("u1", [rec("first lesson for user one"), rec("second lesson for user one")])
    await lt.write("u2", [rec("a lesson for user two")])
    assert lt.users() == [{"user_id": "u1", "count": 2}, {"user_id": "u2", "count": 1}]
    assert lt.delete_user("u1") == 2
    assert lt.list_user("u1") == [] and lt.count() == 1
    assert lt.list_user("u2")[0].text == "a lesson for user two"
    assert lt.delete_user("u1") == 0


async def test_consolidation_expires_by_effective_importance_and_age(
    memory: tuple[LongTermMemory, FakeEmbedder, Clock],
) -> None:
    lt, _, clock = memory
    await lt.write("u1", [rec("fresh and important lesson", importance=4)])
    clock.now = T0 - dt.timedelta(days=200)
    await lt.write("u1", [rec("ancient lesson from long ago", importance=5)])
    clock.now = T0 - dt.timedelta(days=45)
    await lt.write("u1", [rec("minor observation nobody used", importance=1.2)])
    clock.now = T0 + dt.timedelta(days=2)
    assert lt.count() == 3

    report = consolidate(lt, now=clock.now, half_life_days=30, floor=1.0, max_age_days=180)
    assert report.scanned == 3 and report.expired == 2
    left = lt.list_user("u1")
    assert [r.text for r in left] == ["fresh and important lesson"]
    assert consolidate(lt, now=clock.now).expired == 0  # idempotent


def test_effective_importance_is_a_pure_decay() -> None:
    now = T0
    assert effective_importance(4.0, now, now=now, half_life_days=30) == 4.0
    assert effective_importance(4.0, now - dt.timedelta(days=30), now=now, half_life_days=30) == 2.0
    assert effective_importance(4.0, now - dt.timedelta(days=60), now=now, half_life_days=30) == 1.0
    assert (
        effective_importance(4.0, now + dt.timedelta(days=5), now=now, half_life_days=30) == 4.0
    )  # clock skew: never grows
