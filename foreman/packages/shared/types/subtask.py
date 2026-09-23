from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import ToolEvent


class Specialist(StrEnum):
    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITING = "writing"
    CODE_EXEC = "code_exec"


class Complexity(StrEnum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class SubtaskStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Subtask(BaseModel):
    """One unit of delegated work, as produced by the supervisor's plan.

    ``needs`` is what the planner says this step requires from its predecessors (free text);
    ``inputs`` is the runtime payload the dispatcher fills (predecessor outputs, reviewer feedback)
    and is deliberately absent from the planner-facing JSON schema.
    """

    id: str = Field(description="Short id, e.g. A, B, C")
    description: str = Field(description="What to do, concretely, in one paragraph")
    specialist: Specialist
    depends_on: list[str] = Field(
        default_factory=list, description="Ids of subtasks that must finish first"
    )
    needs: list[str] = Field(
        default_factory=list, description="What this step needs from predecessors"
    )
    expected_output: str = Field(default="", description="What a good result looks like")
    complexity: Complexity = Complexity.MODERATE
    inputs: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict)


class SubmittedResult(BaseModel):
    """What the specialist model fills in when it calls ``submit_result`` to finish."""

    status: SubtaskStatus
    output: str = Field(description="The deliverable for this subtask, in full.")
    sources: list[str] = Field(
        default_factory=list,
        description="Where the facts came from: file paths, table names, URLs. Empty if none.",
    )
    self_confidence: float = Field(ge=0.0, le=1.0, description="0 = guessing, 1 = certain.")
    notes: str = Field(
        default="", description="Caveats, gaps, or anything the reviewer should know."
    )


class SubtaskResult(SubmittedResult):
    """``SubmittedResult`` plus what the loop knows: tools used, iterations, cost, errors."""

    subtask_id: str
    attempt: int = 1
    tools_used: list[str] = Field(default_factory=list)
    iterations: int = 0
    cost_entries: list[CostEntry] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    fallback_used: bool = False
    human_authored: bool = False
    denied_tools: dict[str, str] = Field(
        default_factory=dict,
        description="tool -> the human's reason; a rejection stands for every retry of this subtask",
    )
    error: str | None = None

    @property
    def total_cost_usd(self) -> float | None:
        known = [c.cost_usd for c in self.cost_entries if c.cost_usd is not None]
        return sum(known) if known else None

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.cost_entries)
