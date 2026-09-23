from __future__ import annotations

from pydantic import BaseModel, Field


class Deliverable(BaseModel):
    """The synthesised final output of a task."""

    title: str
    body: str = Field(description="The complete deliverable in Markdown, citing sources inline")
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    human_authored: bool = False
