"""L3 — approve the plan before any work starts (low confidence, or the caller asked for review).

Decisions: approve → dispatch; modify → the human's plan replaces the supervisor's; reject → the
task is cancelled; take over → the human's text becomes the deliverable and nothing else runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.hitl.approvals import build_plan_request
from packages.orchestrator.tracing.otel import span
from packages.shared.types.approval import ApprovalDecision, ApprovalTrigger, DecisionKind
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.plan import ExecutionPlan


def human_deliverable(decision: ApprovalDecision) -> Deliverable | None:
    body = str(decision.payload.get("body", "")).strip()
    if not body:
        return None
    return Deliverable(
        title=str(decision.payload.get("title") or "Deliverable provided by a human"),
        body=body,
        sources=[str(s) for s in decision.payload.get("sources", [])],
        confidence=1.0,
        human_authored=True,
    )


def make_approve_plan_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def approve_plan(state: TaskState) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(state["plan"])
        trigger = (
            ApprovalTrigger.LOW_PLAN_CONFIDENCE
            if plan.confidence < deps.config.plan_confidence_threshold
            else ApprovalTrigger.USER_REQUESTED_REVIEW
        )
        request = build_plan_request(dict(state), plan, trigger, deps.policy)
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
            trigger=trigger.value,
        ):
            raw = interrupt({"approval_id": row.id, **request.model_dump(mode="json")})
        decision = ApprovalDecision.model_validate(raw)
        who = decision.decided_by
        base = {
            "approval_id": row.id,
            "decision": decision.decision.value,
            "decided_by": who,
            "reason": decision.reason,
        }

        if decision.decision == DecisionKind.APPROVE:
            return {
                "events": [event("decided", f"plan approved by {who}", node="approve_plan", **base)]
            }

        if decision.decision == DecisionKind.MODIFY:
            try:
                new_plan = ExecutionPlan.model_validate(decision.payload.get("plan"))
            except ValidationError as e:
                reason = f"modified plan rejected by validation: {str(e)[:300]}"
                return {
                    "status": "cancelled",
                    "error": f"plan modification by {who} was invalid: {reason}",
                    "events": [event("cancelled", reason, node="approve_plan", **base)],
                }
            await asyncio.to_thread(deps.store.set_plan, state["task_id"], new_plan)
            return {
                "plan": new_plan,
                "plan_confidence": new_plan.confidence,
                "events": [
                    event(
                        "decided",
                        f"plan modified by {who}: {len(new_plan.subtasks)} subtask(s)",
                        node="approve_plan",
                        **base,
                    )
                ],
            }

        if decision.decision == DecisionKind.TAKE_OVER:
            deliverable = human_deliverable(decision)
            if deliverable is not None:
                return {
                    "final_output": deliverable,
                    "events": [
                        event("decided", f"taken over by {who}", node="approve_plan", **base)
                    ],
                }
            decision = decision.model_copy(update={"reason": "take-over without a body"})

        reason = f"plan rejected by {who}: {decision.reason or 'no reason given'}"
        return {
            "status": "cancelled",
            "error": reason,
            "events": [event("cancelled", reason, node="approve_plan", **base)],
        }

    return approve_plan
