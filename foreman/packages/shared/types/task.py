from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskOptions(BaseModel):
    require_human_review: bool = False
    budget_usd: float | None = None
    replay_of: str | None = None  # set on a task forked by replay (PRD F11)
    replay_checkpoint: str | None = None


class TaskEvent(BaseModel):
    """A line in the task's timeline (persisted in state; mirrored to audit_log on delivery)."""

    kind: str
    message: str
    node: str = ""
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    data: dict[str, Any] = Field(default_factory=dict)
