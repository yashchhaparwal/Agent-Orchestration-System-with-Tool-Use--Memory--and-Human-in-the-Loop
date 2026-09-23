"""Write node (Architecture.md §7.3): digest the finished task, extract lessons on the cheap role,
upsert them (dedup reinforces instead of duplicating). Runs after ``deliver``; nothing here can
change the task's outcome, so every failure is logged and swallowed."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.memory.extractor import build_task_digest, extract_memories
from packages.orchestrator.memory.long_term import WriteReport
from packages.orchestrator.tracing.otel import span
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import ToolEvent
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult

log = structlog.get_logger(__name__)


def make_write_memory_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def write_memory(state: TaskState) -> dict[str, Any]:
        task_id = state["task_id"]
        if deps.long_term is None:
            return {
                "events": [event("memory", "long-term memory not configured", node="write_memory")]
            }

        approvals = [
            a
            for a in await asyncio.to_thread(deps.approvals.list, status=None, limit=500)
            if a.get("task_id") == task_id
        ]
        digest = build_task_digest(state, approvals)
        costs: list[CostEntry] = []
        llm = deps.llm_for(deps.memory_extractor.role).with_cost_sink(costs.append)
        report = WriteReport()
        with span("memory.write", task_id=task_id, user_id=state["user_id"]) as s:
            try:
                records = await extract_memories(
                    llm,
                    deps.memory_extractor.system_prompt,
                    digest,
                    timeout_s=deps.config.llm_timeout_s,
                )
                s.set_attribute("extracted", len(records))
                report = await deps.long_term.write(
                    state["user_id"], records, source_task_id=task_id
                )
            except Exception as e:  # noqa: BLE001 — memory must never fail a task
                log.warning("memory.write_failed", task_id=task_id, error=str(e)[:200])
                s.set_attribute("error", str(e)[:300])
            s.set_attribute("inserted", ",".join(report.inserted))
            s.set_attribute("reinforced", ",".join(report.reinforced))

        if costs:  # the extractor call happens after deliver persisted the ledger; add it
            await asyncio.to_thread(
                deps.store.record_progress,
                task_id,
                results={
                    k: SubtaskResult.model_validate(v)
                    for k, v in (state.get("subtask_results") or {}).items()
                },
                verdicts={
                    k: ReviewVerdict.model_validate(v)
                    for k, v in (state.get("review_verdicts") or {}).items()
                },
                cost_entries=[CostEntry.model_validate(c) for c in (state.get("cost_ledger") or [])]
                + costs,
                tool_events=[ToolEvent.model_validate(t) for t in (state.get("tool_events") or [])],
            )
        return {
            "cost_ledger": costs,
            "events": [
                event(
                    "memory",
                    f"wrote {len(report.inserted)} lesson(s), reinforced {len(report.reinforced)}",
                    node="write_memory",
                    inserted=report.inserted,
                    reinforced=report.reinforced,
                )
            ],
        }

    return write_memory
