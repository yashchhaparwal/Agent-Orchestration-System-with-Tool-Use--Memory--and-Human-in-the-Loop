from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from apps.api.controllers import approvals as controller
from apps.api.controllers.approvals import DecideRequest
from apps.api.services.approval_service import ApprovalService
from packages.orchestrator.memory.persistent import OutboxRepository

router = APIRouter(prefix="/v1", tags=["approvals"])


def get_service(request: Request) -> ApprovalService:
    return request.app.state.approval_service  # type: ignore[no-any-return]


def get_outbox(request: Request) -> OutboxRepository:
    return request.app.state.outbox  # type: ignore[no-any-return]


Service = Annotated[ApprovalService, Depends(get_service)]
Outbox = Annotated[OutboxRepository, Depends(get_outbox)]


@router.get("/approvals")
def list_approvals(
    service: Service,
    status: Annotated[str | None, Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return controller.list_approvals(service, status, limit)


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: int, service: Service) -> dict[str, Any]:
    return controller.get_approval(service, approval_id)


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: int, body: DecideRequest, service: Service) -> dict[str, Any]:
    return controller.decide(service, approval_id, body)


@router.get("/outbox")
def list_outbox(
    outbox: Outbox, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[dict[str, Any]]:
    return outbox.list(limit=limit)
