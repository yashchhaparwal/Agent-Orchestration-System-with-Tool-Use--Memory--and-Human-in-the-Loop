"""Tier-3 memory records (Architecture.md §7.3, Rules.md §3): what the extractor produces, what
the store keeps, and what the planner is shown."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryOutcome(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    DECISION = "decision"  # a human decision worth remembering (e.g. "drafts only, never send")


class MemoryRecord(BaseModel):
    """One lesson worth keeping: short, self-contained, and actionable next time."""

    text: str = Field(min_length=12, max_length=600)
    task_type: str = Field(default="general", max_length=48)
    outcome: MemoryOutcome = MemoryOutcome.SUCCESS
    importance: float = Field(default=3.0, ge=1.0, le=5.0)
    tools_used: list[str] = Field(default_factory=list)


class MemoryExtraction(BaseModel):
    """The extractor's structured output."""

    records: list[MemoryRecord] = Field(default_factory=list, max_length=5)


class StoredMemory(BaseModel):
    """A record as the browser / API sees it."""

    id: str
    user_id: str
    text: str
    task_type: str
    outcome: str
    importance: float
    effective_importance: float
    created_at: str
    last_accessed: str
    access_count: int
    tools_used: list[str]
    source_task_id: str = ""


class RecalledMemory(BaseModel):
    """What recall hands to the planner (and the approval context)."""

    id: str
    text: str
    score: float
    task_type: str
    outcome: str
    importance: float
    created_at: str
