"""Human-in-the-loop types (Architecture.md §8, diagram 05)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ApprovalLevel(StrEnum):
    L1 = "L1"  # notify: proceed, inform
    L2 = "L2"  # approve one action
    L3 = "L3"  # approve the plan before any work
    L4 = "L4"  # take over: agents stand down


class ApprovalKind(StrEnum):
    TOOL_CALL = "tool_call"
    PLAN = "plan"
    ESCALATION = "escalation"


class ApprovalTrigger(StrEnum):
    SENSITIVE_TOOL_CALL = "sensitive_tool_call"
    LOW_PLAN_CONFIDENCE = "low_plan_confidence"
    USER_REQUESTED_REVIEW = "user_requested_review"
    REPEATED_FAILURE = "repeated_failure"
    LOW_REVIEW_SCORE = "low_review_score"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    TAKEN_OVER = "taken_over"
    EXPIRED = "expired"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    MODIFY = "modify"
    REJECT = "reject"
    TAKE_OVER = "take_over"


STATUS_FOR_DECISION: dict[DecisionKind, ApprovalStatus] = {
    DecisionKind.APPROVE: ApprovalStatus.APPROVED,
    DecisionKind.MODIFY: ApprovalStatus.MODIFIED,
    DecisionKind.REJECT: ApprovalStatus.REJECTED,
    DecisionKind.TAKE_OVER: ApprovalStatus.TAKEN_OVER,
}


class ApprovalDecision(BaseModel):
    """What a human (or the timeout policy) decided. ``payload`` carries edits:

    - tool call: ``{"arguments": {...}}`` for modify; ``{"output": "...", "sources": [...]}`` for take-over
    - plan:      ``{"plan": {...}}`` for modify; ``{"title", "body", "sources"}`` for take-over
    - escalation: ``{"subtask_id", "output"}`` for modify; ``{"title", "body", "sources"}`` for take-over
    """

    decision: DecisionKind
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    decided_by: str = "operator"


class ApprovalRequest(BaseModel):
    """Everything a reviewer needs to decide, and the idempotency key that stops a re-executed
    node from creating the same request twice."""

    key: str
    kind: ApprovalKind
    level: ApprovalLevel
    trigger: ApprovalTrigger
    task_id: str
    subtask_id: str | None = None
    attempt: int = 1
    agent: str = ""
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
