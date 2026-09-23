"""L2 — a specialist paused on a tool call. This node does nothing before ``interrupt()`` except
record the request idempotently and notify, so re-execution on resume is free of side effects."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import (
    TaskState,
    approval_decisions,
    event,
    pending_approvals,
)
from packages.orchestrator.hitl.approvals import build_tool_call_request
from packages.orchestrator.tracing.otel import span
from packages.shared.types.approval import ApprovalDecision


def make_await_approval_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def await_approval(state: TaskState) -> dict[str, Any]:
        decided = approval_decisions(state)
        targets = [p for p in pending_approvals(state) if p.subtask_id not in decided]
        if not targets:
            return {}
        target = targets[0]
        request = build_tool_call_request(dict(state), target.paused, deps.policy)
        row, created = await asyncio.to_thread(
            deps.approvals.get_or_create, request, expires_at=deps.policy.deadline(request.level)
        )
        if created and deps.notifier is not None:
            await deps.notifier(request, row.id)

        with span(
            "hitl.interrupt",
            task_id=state["task_id"],
            approval_id=row.id,
            level=request.level.value,
            trigger=request.trigger.value,
            subtask_id=target.subtask_id,
        ):
            raw = interrupt({"approval_id": row.id, **request.model_dump(mode="json")})
        decision = ApprovalDecision.model_validate(raw)
        with span(
            "hitl.resume",
            task_id=state["task_id"],
            approval_id=row.id,
            decision=decision.decision.value,
            decided_by=decision.decided_by,
        ):
            return {
                "approval_decisions": {target.subtask_id: decision},
                "events": [
                    event(
                        "decided",
                        f"{target.subtask_id}: {request.proposed_action.get('tool')} → "
                        f"{decision.decision.value} by {decision.decided_by}",
                        node="await_approval",
                        approval_id=row.id,
                        subtask_id=target.subtask_id,
                        decision=decision.decision.value,
                        decided_by=decision.decided_by,
                        reason=decision.reason,
                    )
                ],
            }

    return await_approval
