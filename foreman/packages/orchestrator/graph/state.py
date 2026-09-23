"""The shared graph state (Architecture.md §4.1) and the payload a specialist branch receives.

Reducers: dict channels merge by key (a retried subtask's newer result/verdict overwrites the old
one; a ``None`` value clears an entry); list channels append. Values are Pydantic models so the
checkpointer round-trips them.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel

from packages.orchestrator.loop.agent_loop import PausedLoop
from packages.shared.types.approval import ApprovalDecision
from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult
from packages.shared.types.task import TaskEvent, TaskOptions


def merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class PendingApproval(BaseModel):
    """A specialist paused on a tool call that needs a human (Architecture.md §8)."""

    subtask_id: str
    agent: str
    attempt: int
    paused: PausedLoop


class TaskState(TypedDict, total=False):
    task_id: str
    user_id: str
    request: str
    options: TaskOptions
    recalled_memories: list[dict[str, Any]]
    plan: ExecutionPlan | None
    plan_confidence: float
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dicts]
    review_verdicts: Annotated[dict[str, ReviewVerdict], merge_dicts]
    retry_counts: Annotated[dict[str, int], merge_dicts]
    pending_approvals: Annotated[dict[str, PendingApproval | None], merge_dicts]
    approval_decisions: Annotated[dict[str, ApprovalDecision | None], merge_dicts]
    cost_ledger: Annotated[list[CostEntry], operator.add]
    tool_events: Annotated[list[ToolEvent], operator.add]
    events: Annotated[list[TaskEvent], operator.add]
    final_output: Deliverable | None
    status: str
    error: str | None


class SpecialistInput(TypedDict, total=False):
    """What `Send()` hands to a specialist node. ``resume`` carries a paused loop's checkpoint
    plus the human decision that unblocks it."""

    task_id: str
    subtask: Subtask
    predecessor_outputs: dict[str, str]
    feedback: str | None
    attempt: int
    resume: dict[str, Any] | None
    denied: dict[str, str]


def initial_state(task_id: str, user_id: str, request: str, options: TaskOptions) -> TaskState:
    return TaskState(
        task_id=task_id,
        user_id=user_id,
        request=request,
        options=options,
        recalled_memories=[],
        plan=None,
        plan_confidence=0.0,
        subtask_results={},
        review_verdicts={},
        retry_counts={},
        pending_approvals={},
        approval_decisions={},
        cost_ledger=[],
        tool_events=[],
        events=[],
        final_output=None,
        status="running",
        error=None,
    )


def pending_approvals(state: TaskState) -> list[PendingApproval]:
    raw = state.get("pending_approvals") or {}
    out = [PendingApproval.model_validate(v) for v in raw.values() if v is not None]
    return sorted(out, key=lambda p: p.subtask_id)


def approval_decisions(state: TaskState) -> dict[str, ApprovalDecision]:
    raw = state.get("approval_decisions") or {}
    return {k: ApprovalDecision.model_validate(v) for k, v in raw.items() if v is not None}


def event(kind: str, message: str, *, node: str = "", **data: Any) -> TaskEvent:
    return TaskEvent(kind=kind, message=message, node=node, data=data)
