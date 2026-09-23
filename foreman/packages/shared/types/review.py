from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewJudgement(BaseModel):
    """What the reviewer model returns for one subtask result."""

    accept: bool = Field(description="True only if the output fulfils the subtask completely")
    score: int = Field(ge=1, le=5, description="1 = unusable, 5 = excellent")
    issues: list[str] = Field(
        default_factory=list, description="Specific problems found; empty if none"
    )
    feedback: str = Field(default="", description="Actionable guidance for a retry, if rejected")


class ReviewVerdict(ReviewJudgement):
    subtask_id: str
    attempt: int
    reviewer_model: str = ""
