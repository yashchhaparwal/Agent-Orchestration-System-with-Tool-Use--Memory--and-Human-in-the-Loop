"""Request/response shapes for /v1/tasks. Thin: validates and delegates to the service."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from apps.api.services.task_service import TaskService
from packages.shared.types.task import TaskOptions


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    user_id: str = Field(default="anonymous", max_length=64)
    require_human_review: bool = False
    budget_usd: float | None = Field(default=None, ge=0)


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str


def create_task(service: TaskService, body: CreateTaskRequest) -> CreateTaskResponse:
    out = service.create(
        user_id=body.user_id,
        request=body.request,
        options=TaskOptions(
            require_human_review=body.require_human_review, budget_usd=body.budget_usd
        ),
    )
    return CreateTaskResponse(**out)


def list_tasks(service: TaskService, limit: int) -> list[dict[str, Any]]:
    return service.list(limit=max(1, min(limit, 200)))


def get_task(service: TaskService, task_id: str) -> dict[str, Any]:
    view = service.get(task_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return view
