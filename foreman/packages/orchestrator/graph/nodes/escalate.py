"""L4 — the agents could not finish: a subtask was rejected past its retries, the plan cannot make
progress, or planning itself failed. A human decides:

    approve   → retry the rejected subtasks once more (their retry counts reset)
    modify    → the human supplies one subtask's output ({subtask_id, output}); it is accepted as-is
    take_over → the human supplies the final deliverable ({title, body, sources})
    reject    → the task is cancelled
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.nodes.approve_plan import human_deliverable
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.hitl.approvals import build_escalation_request
from packages.orchestrator.tracing.otel import span
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubmittedResult, SubtaskResult, SubtaskStatus


def _reason(state: TaskState) -> str:
    if state.get("error"):
        return str(state["error"])
    verdicts = {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }
    rejected = [
        f"{k} ({v.score}/5: {'; '.join(v.issues)[:120]})"
        for k, v in verdicts.items()
        if not v.accept
    ]
    if rejected:
        return "rejected after retries: " + ", ".join(rejected)
    return "no runnable subtask and the plan is not complete"


def make_escalate_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def escalate(state: TaskState) -> dict[str, Any]:
        reason = _reason(state)
        sequence = sum(1 for e in (state.get("events") or []) if e.kind == "escalated")
        request = build_escalation_request(dict(state), reason, sequence, deps.policy)
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
        escalated = event("escalated", reason, node="escalate", level=request.level.value)
        no_plan = state.get("plan") is None

        if decision.decision == DecisionKind.APPROVE and not no_plan:
            rejected = request.proposed_action.get("rejected_subtasks", [])
            return {
                "retry_counts": {sid: 0 for sid in rejected},
                "events": [
                    escalated,
                    event("decided", f"retry granted by {who}", node="escalate", **base),
                ],
            }

        if decision.decision == DecisionKind.MODIFY and not no_plan:
            sid = str(decision.payload.get("subtask_id", "")).strip()
            output = str(decision.payload.get("output", "")).strip()
            results = state.get("subtask_results") or {}
            if sid in results and output:
                previous = SubtaskResult.model_validate(results[sid])
                attempt = previous.attempt + 1
                human_result = SubtaskResult(
                    **SubmittedResult(
                        status=SubtaskStatus.COMPLETED,
                        output=output,
                        sources=[str(s) for s in decision.payload.get("sources", [])],
                        self_confidence=1.0,
                        notes=f"output provided by {who}",
                    ).model_dump(),
                    subtask_id=sid,
                    attempt=attempt,
                    human_authored=True,
                )
                verdict = ReviewVerdict(
                    accept=True,
                    score=5,
                    subtask_id=sid,
                    attempt=attempt,
                    reviewer_model="human",
                    feedback=f"accepted: provided by {who}",
                )
                return {
                    "subtask_results": {sid: human_result},
                    "review_verdicts": {sid: verdict},
                    "retry_counts": {sid: 0},
                    "events": [
                        escalated,
                        event("decided", f"{sid} supplied by {who}", node="escalate", **base),
                    ],
                }
            decision = decision.model_copy(
                update={"reason": "modify needs a known subtask_id and a non-empty output"}
            )

        if decision.decision == DecisionKind.TAKE_OVER:
            deliverable = human_deliverable(decision)
            if deliverable is not None:
                return {
                    "final_output": deliverable,
                    "events": [
                        escalated,
                        event("decided", f"taken over by {who}", node="escalate", **base),
                    ],
                }
            decision = decision.model_copy(update={"reason": "take-over without a body"})

        cancel = f"cancelled by {who}: {decision.reason or reason}"
        return {
            "status": "cancelled",
            "error": cancel,
            "events": [escalated, event("cancelled", cancel, node="escalate", **base)],
        }

    return escalate
