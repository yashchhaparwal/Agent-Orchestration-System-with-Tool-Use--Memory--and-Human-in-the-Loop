"""Recall node (Architecture.md §7.3): the user's relevant lessons, trimmed, into state.

Memory is advice for the planner, never a hard dependency: no long-term store, or a store that
cannot answer, means an empty list and a warning — the task goes on.
"""

from __future__ import annotations

from typing import Any

import structlog

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span

log = structlog.get_logger(__name__)


def make_recall_memory_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def recall_memory(state: TaskState) -> dict[str, Any]:
        if deps.long_term is None:
            return {
                "recalled_memories": [],
                "events": [
                    event("memory", "long-term memory not configured", node="recall_memory")
                ],
            }
        cfg = deps.config
        with span("memory.recall", task_id=state["task_id"], user_id=state["user_id"]) as s:
            try:
                found = await deps.long_term.recall(
                    state["user_id"],
                    state["request"],
                    k=cfg.memory_recall_k,
                    keep=cfg.memory_recall_keep,
                    max_tokens=cfg.memory_recall_max_tokens,
                )
            except Exception as e:  # noqa: BLE001 — memory must never fail a task
                log.warning("memory.recall_failed", task_id=state["task_id"], error=str(e)[:200])
                s.set_attribute("error", str(e)[:300])
                found = []
            ids = [m.id for m in found]
            s.set_attribute("count", len(found))
            s.set_attribute("ids", ",".join(ids))
        return {
            "recalled_memories": [m.model_dump(mode="json") for m in found],
            "events": [
                event(
                    "memory",
                    f"recalled {len(found)} memor{'y' if len(found) == 1 else 'ies'}",
                    node="recall_memory",
                    ids=ids,
                )
            ],
        }

    return recall_memory
