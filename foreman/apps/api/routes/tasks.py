from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from apps.api.controllers import tasks as controller
from apps.api.controllers import trace as trace_controller
from apps.api.controllers.tasks import CreateTaskRequest, CreateTaskResponse
from apps.api.controllers.trace import ReplayRequest
from apps.api.services.task_service import TaskService
from apps.api.services.trace_service import ReplayService, TraceService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def get_service(request: Request) -> TaskService:
    return request.app.state.task_service  # type: ignore[no-any-return]


Service = Annotated[TaskService, Depends(get_service)]


def get_trace_service(request: Request) -> TraceService:
    return request.app.state.trace_service  # type: ignore[no-any-return]


def get_replay_service(request: Request) -> ReplayService:
    return request.app.state.replay_service  # type: ignore[no-any-return]


Trace = Annotated[TraceService, Depends(get_trace_service)]
Replay = Annotated[ReplayService, Depends(get_replay_service)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateTaskResponse)
def create_task(body: CreateTaskRequest, service: Service) -> CreateTaskResponse:
    return controller.create_task(service, body)


@router.get("")
def list_tasks(service: Service, limit: int = 50) -> list[dict[str, Any]]:
    return controller.list_tasks(service, limit)


@router.get("/{task_id}")
def get_task(task_id: str, service: Service) -> dict[str, Any]:
    return controller.get_task(service, task_id)


@router.get("/{task_id}/trace")
def get_trace(task_id: str, service: Trace) -> dict[str, Any]:
    return trace_controller.get_trace(service, task_id)


@router.get("/{task_id}/checkpoints")
def get_checkpoints(task_id: str, service: Trace) -> list[dict[str, Any]]:
    return trace_controller.get_checkpoints(service, task_id)


@router.post("/{task_id}/replay", status_code=status.HTTP_202_ACCEPTED)
def request_replay(task_id: str, body: ReplayRequest, service: Replay) -> dict[str, Any]:
    return trace_controller.request_replay(service, task_id, body)


@router.get("/{task_id}/diff")
def get_replay_diff(task_id: str, service: Replay) -> dict[str, Any]:
    return trace_controller.get_replay_diff(service, task_id)
