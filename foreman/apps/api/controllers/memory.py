from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from apps.api.services.memory_service import MemoryService, MemoryUnavailableError


class DeleteMemoryResponse(BaseModel):
    user_id: str
    deleted: int


def list_memories(service: MemoryService, user_id: str) -> list[dict[str, Any]]:
    try:
        return service.list_user(user_id)
    except MemoryUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def list_users(service: MemoryService) -> list[dict[str, Any]]:
    try:
        return service.users()
    except MemoryUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def delete_memories(service: MemoryService, user_id: str) -> DeleteMemoryResponse:
    try:
        return DeleteMemoryResponse(user_id=user_id, deleted=service.delete(user_id))
    except MemoryUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
