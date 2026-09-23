"""Persist the outcome (tier 2) and mark the task done, cancelled, or failed."""

from __future__ import annotations

import asyncio
from typing import Any

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span
from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import ToolEvent
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult
from packages.shared.types.task import TaskStatus


def make_deliver_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def deliver(state: TaskState) -> dict[str, Any]:
        task_id = state["task_id"]
        final = state.get("final_output")
        deliverable = Deliverable.model_validate(final) if final is not None else None
        ledger = [CostEntry.model_validate(c) for c in (state.get("cost_ledger") or [])]
        tool_events = [ToolEvent.model_validate(t) for t in (state.get("tool_events") or [])]
        results = {
            k: SubtaskResult.model_validate(v)
            for k, v in (state.get("subtask_results") or {}).items()
        }
        verdicts = {
            k: ReviewVerdict.model_validate(v)
            for k, v in (state.get("review_verdicts") or {}).items()
        }
        known = [c.cost_usd for c in ledger if c.cost_usd is not None]
        cost_usd = sum(known) if known else None
        if deliverable is not None:
            status = TaskStatus.DONE
        elif state.get("status") == "cancelled":
            status = TaskStatus.CANCELLED
        else:
            status = TaskStatus.FAILED
        error = None if deliverable is not None else (state.get("error") or "no deliverable")

        with span("node.deliver", task_id=task_id, status=status.value):
            await asyncio.to_thread(
                deps.store.finish_task,
                task_id,
                status=status,
                deliverable=deliverable,
                cost_usd=cost_usd,
                error=error,
                results=results,
                verdicts=verdicts,
                cost_entries=ledger,
                tool_events=tool_events,
                events=list(state.get("events") or []),
            )
        blocked = sum(1 for t in tool_events if t.decision.value != "allow")
        return {
            "status": status.value,
            "error": error,
            "events": [
                event(
                    "delivered" if deliverable else status.value,
                    f"task {status.value}; {len(ledger)} LLM calls; {len(tool_events)} tool calls "
                    f"({blocked} not executed by the gate alone)",
                    node="deliver",
                    cost_usd=cost_usd,
                    llm_calls=len(ledger),
                    tool_calls=len(tool_events),
                    tool_calls_not_executed=blocked,
                    human_authored=bool(deliverable and deliverable.human_authored),
                )
            ],
        }

    return deliver
