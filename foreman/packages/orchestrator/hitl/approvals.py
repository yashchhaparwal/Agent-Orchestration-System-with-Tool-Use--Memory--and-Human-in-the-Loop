"""Builds the context package a reviewer sees (diagram 05) and the request records the graph
raises at each escalation point."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.loop.agent_loop import PausedLoop, args_hash
from packages.shared.types.approval import (
    ApprovalKind,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalTrigger,
)
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult

OUTPUT_PREVIEW = 600


def build_context(state: dict[str, Any]) -> dict[str, Any]:
    """Request, plan summary, and what has been done so far — the part every level shares."""
    plan_raw = state.get("plan")
    plan = ExecutionPlan.model_validate(plan_raw) if plan_raw else None
    results = {
        k: SubtaskResult.model_validate(v) for k, v in (state.get("subtask_results") or {}).items()
    }
    verdicts = {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }
    plan_summary = []
    if plan:
        for s in plan.subtasks:
            r, v = results.get(s.id), verdicts.get(s.id)
            status = (
                "accepted"
                if v and v.accept and r and v.attempt == r.attempt
                else "rejected"
                if v and r and v.attempt == r.attempt
                else "done, awaiting review"
                if r
                else "pending"
            )
            plan_summary.append(
                {
                    "id": s.id,
                    "specialist": s.specialist.value,
                    "description": s.description,
                    "depends_on": s.depends_on,
                    "status": status,
                }
            )
    completed = [
        {
            "id": sid,
            "attempt": r.attempt,
            "status": r.status.value,
            "score": verdicts[sid].score if sid in verdicts else None,
            "output_preview": r.output[:OUTPUT_PREVIEW],
            "sources": r.sources,
        }
        for sid, r in sorted(results.items())
    ]
    return {
        "task_id": state.get("task_id"),
        "user_id": state.get("user_id"),
        "request": state.get("request"),
        "plan": plan_summary,
        "plan_confidence": state.get("plan_confidence"),
        "completed_subtasks": completed,
        "memories": list(state.get("recalled_memories") or [])[:3],
        "error": state.get("error"),
    }


def _last_assistant_text(paused: PausedLoop) -> str:
    for message in reversed(paused.checkpoint.messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])[:1000]
    return ""


def build_tool_call_request(
    state: dict[str, Any], paused: PausedLoop, policy: EscalationPolicy
) -> ApprovalRequest:
    head = paused.next_call
    trigger = ApprovalTrigger.SENSITIVE_TOOL_CALL
    key = f"{state['task_id']}:tool_call:{paused.subtask_id}:{paused.attempt}:{args_hash(head.call.arguments)}"
    return ApprovalRequest(
        key=key,
        kind=ApprovalKind.TOOL_CALL,
        level=policy.level_for(trigger),
        trigger=trigger,
        task_id=str(state["task_id"]),
        subtask_id=paused.subtask_id,
        attempt=paused.attempt,
        agent=paused.agent,
        proposed_action={
            "tool": head.call.name,
            "arguments": head.call.arguments,
            "risk": head.risk.value if head.risk else None,
            "gate_reason": head.reason,
            "other_pending": [p.call.name for p in paused.checkpoint.pending[1:]],
        },
        reasoning=_last_assistant_text(paused),
        context=build_context(state),
    )


def build_plan_request(
    state: dict[str, Any], plan: ExecutionPlan, trigger: ApprovalTrigger, policy: EscalationPolicy
) -> ApprovalRequest:
    digest = hashlib.sha256(plan.model_dump_json().encode("utf-8")).hexdigest()[:16]
    return ApprovalRequest(
        key=f"{state['task_id']}:plan:{digest}",
        kind=ApprovalKind.PLAN,
        level=policy.level_for(trigger),
        trigger=trigger,
        task_id=str(state["task_id"]),
        agent="supervisor",
        proposed_action={"plan": json.loads(plan.model_dump_json())},
        reasoning=plan.rationale,
        context=build_context(state),
    )


def build_escalation_request(
    state: dict[str, Any], reason: str, sequence: int, policy: EscalationPolicy
) -> ApprovalRequest:
    trigger = ApprovalTrigger.REPEATED_FAILURE
    rejected = [
        k
        for k, v in (state.get("review_verdicts") or {}).items()
        if not ReviewVerdict.model_validate(v).accept
    ]
    return ApprovalRequest(
        key=f"{state['task_id']}:escalation:{sequence}",
        kind=ApprovalKind.ESCALATION,
        level=policy.level_for(trigger),
        trigger=trigger,
        task_id=str(state["task_id"]),
        agent="supervisor",
        proposed_action={
            "reason": reason,
            "rejected_subtasks": sorted(rejected),
            "options": {
                "approve": "retry the rejected subtasks once more",
                "modify": "provide the output for a subtask: {subtask_id, output}",
                "take_over": "provide the final deliverable: {title, body, sources}",
                "reject": "cancel the task",
            },
        },
        reasoning=reason,
        context=build_context(state),
    )


def level_label(level: ApprovalLevel) -> str:
    return {
        ApprovalLevel.L1: "Notify",
        ApprovalLevel.L2: "Approve action",
        ApprovalLevel.L3: "Approve plan",
        ApprovalLevel.L4: "Take over",
    }[level]
