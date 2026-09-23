"""Traces, checkpoints and replays for one task (Architecture.md §9, PRD F11).

The trace tree comes from Jaeger (every span of every trace tagged with the task id, merged and
nested by parent). When Jaeger is unreachable the ledgers in Postgres give a flat timeline instead,
so the page always shows something true.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from apps.api.services.queue import TaskQueue
from packages.orchestrator.memory.persistent import TaskStore
from packages.orchestrator.tracing.replay import diff_views, parse_override
from packages.shared.config import Settings
from packages.shared.types.task import TaskOptions

log = structlog.get_logger(__name__)

SKIP_TAGS = {"otel.scope.name", "span.kind", "internal.span.format"}
KINDS = (
    ("task", "task"),
    ("node.", "node"),
    ("llm.", "llm"),
    ("gate.", "gate"),
    ("tool.", "tool"),
    ("memory.", "memory"),
    ("hitl.", "hitl"),
    ("agent.", "agent"),
    ("MCP ", "mcp"),
)


def classify(name: str) -> str:
    for prefix, kind in KINDS:
        if name.startswith(prefix):
            return kind
    return "other"


def build_tree(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten Jaeger traces into one depth-first list ordered by start time."""
    spans: dict[str, dict[str, Any]] = {}
    for trace in traces:
        for s in trace.get("spans", []):
            parent = next(
                (r["spanID"] for r in s.get("references", []) if r.get("refType") == "CHILD_OF"),
                None,
            )
            tags = {t["key"]: t["value"] for t in s.get("tags", []) if t["key"] not in SKIP_TAGS}
            spans[s["spanID"]] = {
                "id": s["spanID"],
                "parent": parent
                if parent in {x["spanID"] for x in trace.get("spans", [])}
                else None,
                "name": s["operationName"],
                "kind": classify(s["operationName"]),
                "start_us": int(s["startTime"]),
                "duration_us": int(s.get("duration", 0)),
                "attrs": tags,
                "error": bool(tags.get("error")),
            }
    if not spans:
        return []
    t0 = min(s["start_us"] for s in spans.values())
    children: dict[str | None, list[dict[str, Any]]] = {}
    for s in spans.values():
        children.setdefault(s["parent"], []).append(s)
    for group in children.values():
        group.sort(key=lambda x: x["start_us"])
    out: list[dict[str, Any]] = []

    def walk(parent: str | None, depth: int) -> None:
        for s in children.get(parent, []):
            out.append({**s, "depth": depth, "offset_us": s["start_us"] - t0})
            walk(s["id"], depth + 1)

    walk(None, 0)
    return out


Fetcher = Callable[[str], list[dict[str, Any]]]


class TraceService:
    def __init__(
        self,
        store: TaskStore,
        settings: Settings,
        *,
        fetch: Fetcher | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._store = store
        self._settings = settings
        self._fetch = fetch or self._fetch_jaeger
        self._timeout = timeout_s

    def _fetch_jaeger(self, task_id: str) -> list[dict[str, Any]]:
        r = httpx.get(
            f"{self._settings.jaeger_query_url.rstrip('/')}/api/traces",
            params={"service": "foreman", "tags": json.dumps({"task_id": task_id}), "limit": 20},
            timeout=self._timeout,
        )
        r.raise_for_status()
        return list(r.json().get("data") or [])

    def trace(self, task_id: str) -> dict[str, Any] | None:
        if self._store.get_task(task_id) is None:
            return None
        try:
            traces = self._fetch(task_id)
            spans = build_tree(traces)
            if spans:
                total = max(s["offset_us"] + s["duration_us"] for s in spans)
                return {
                    "task_id": task_id,
                    "source": "jaeger",
                    "traces": len(traces),
                    "span_count": len(spans),
                    "duration_us": total,
                    "spans": spans,
                }
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("trace.jaeger_unavailable", task_id=task_id, error=str(e)[:160])
        timeline = self._store.timeline(task_id)
        return {
            "task_id": task_id,
            "source": "ledger",
            "traces": 0,
            "span_count": len(timeline),
            "duration_us": 0,
            "spans": timeline,
        }

    # ---- checkpoints (read-only; light graph over the Postgres saver) ----

    def checkpoints(self, task_id: str) -> list[dict[str, Any]] | None:
        if self._store.get_task(task_id) is None:
            return None
        from packages.shared.asyncio_compat import use_selector_event_loop_on_windows

        use_selector_event_loop_on_windows()  # must precede asyncio.run: psycopg async on Windows
        return asyncio.run(self._checkpoints(task_id))

    async def _checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from packages.orchestrator.graph.build_graph import build_graph
        from packages.orchestrator.graph.serde import checkpoint_serde
        from packages.orchestrator.tracing.replay import list_checkpoints, make_light_deps
        from packages.orchestrator.worker import checkpointer_conninfo

        async with AsyncPostgresSaver.from_conn_string(
            checkpointer_conninfo(self._settings.database_url), serde=checkpoint_serde()
        ) as saver:
            graph = build_graph(make_light_deps(self._settings), checkpointer=saver)
            return await list_checkpoints(graph, task_id)


class ReplayService:
    def __init__(self, store: TaskStore, queue: TaskQueue) -> None:
        self._store = store
        self._queue = queue

    def request(
        self, task_id: str, checkpoint_id: str, overrides: dict[str, Any] | list[str]
    ) -> dict[str, Any] | None:
        source = self._store.get_task(task_id)
        if source is None:
            return None
        parsed = (
            dict(parse_override(o) for o in overrides)
            if isinstance(overrides, list)
            else dict(overrides)
        )
        options = TaskOptions.model_validate(source.options or {}).model_copy(
            update={"replay_of": task_id, "replay_checkpoint": checkpoint_id}
        )
        fork = self._store.create_task(
            user_id=source.user_id, request=source.request, options=options
        )
        self._queue.enqueue_replay(fork.id, task_id, checkpoint_id, parsed)
        return {"task_id": fork.id, "replay_of": task_id, "checkpoint_id": checkpoint_id}

    def diff(self, task_id: str) -> dict[str, Any] | None:
        fork = self._store.task_view(task_id)
        if fork is None:
            return None
        source_id = (fork.get("options") or {}).get("replay_of")
        if not source_id:
            return {"error": "this task is not a replay", "task_id": task_id}
        source = self._store.task_view(source_id) or {}
        return diff_views(source, fork)
