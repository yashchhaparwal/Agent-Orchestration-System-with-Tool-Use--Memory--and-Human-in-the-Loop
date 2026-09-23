from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from apps.api.services.trace_service import ReplayService, TraceService


class ReplayRequest(BaseModel):
    checkpoint_id: str = Field(min_length=8)
    overrides: dict[str, Any] = Field(default_factory=dict)


def get_trace(service: TraceService, task_id: str) -> dict[str, Any]:
    out = service.trace(task_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return out


def get_checkpoints(service: TraceService, task_id: str) -> list[dict[str, Any]]:
    out = service.checkpoints(task_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return out


def request_replay(service: ReplayService, task_id: str, body: ReplayRequest) -> dict[str, Any]:
    out = service.request(task_id, body.checkpoint_id, body.overrides)
    if out is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return out


def get_replay_diff(service: ReplayService, task_id: str) -> dict[str, Any]:
    out = service.diff(task_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return out
