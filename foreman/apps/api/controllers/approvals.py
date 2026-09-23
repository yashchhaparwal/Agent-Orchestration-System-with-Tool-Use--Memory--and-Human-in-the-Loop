"""Request/response shapes for /v1/approvals. Thin: validates and delegates to the service."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from apps.api.services.approval_service import ApprovalConflictError, ApprovalService
from packages.shared.types.approval import ApprovalDecision, DecisionKind


class DecideRequest(BaseModel):
    decision: DecisionKind
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=4000)
    decided_by: str = Field(default="operator", max_length=64)


def list_approvals(
    service: ApprovalService, status: str | None, limit: int
) -> list[dict[str, Any]]:
    return service.list(status=status or None, limit=max(1, min(limit, 500)))


def get_approval(service: ApprovalService, approval_id: int) -> dict[str, Any]:
    view = service.get(approval_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    return view


def decide(service: ApprovalService, approval_id: int, body: DecideRequest) -> dict[str, Any]:
    if body.decision == DecisionKind.REJECT and not body.reason.strip():
        raise HTTPException(status_code=422, detail="a reason is required to reject")
    decision = ApprovalDecision(
        decision=body.decision, payload=body.payload, reason=body.reason, decided_by=body.decided_by
    )
    try:
        view = service.decide(approval_id, decision)
    except ApprovalConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if view is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    return view
