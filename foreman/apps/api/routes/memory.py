from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from apps.api.controllers import memory as controller
from apps.api.controllers.memory import DeleteMemoryResponse
from apps.api.services.memory_service import MemoryService

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def get_service(request: Request) -> MemoryService:
    return request.app.state.memory_service  # type: ignore[no-any-return]


Service = Annotated[MemoryService, Depends(get_service)]


@router.get("/users")
def list_users(service: Service) -> list[dict[str, Any]]:
    return controller.list_users(service)


@router.get("/users/{user_id}")
def list_memories(user_id: str, service: Service) -> list[dict[str, Any]]:
    return controller.list_memories(service, user_id)


@router.delete("/users/{user_id}", response_model=DeleteMemoryResponse)
def delete_memories(user_id: str, service: Service) -> DeleteMemoryResponse:
    return controller.delete_memories(service, user_id)
