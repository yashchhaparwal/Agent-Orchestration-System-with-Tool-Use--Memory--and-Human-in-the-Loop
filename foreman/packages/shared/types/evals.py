"""Evaluation harness types (PRD.md §7, Phases.md Phase 6): golden tasks, trajectories, results."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from packages.shared.types.approval import DecisionKind
from packages.shared.types.task import TaskOptions


class GoldenCategory(StrEnum):
    LOOKUP = "lookup"
    MULTI_STEP = "multi_step"
    DEPENDENT = "dependent"
    MUST_ESCALATE = "must_escalate"
    MUST_NOT_CALL = "must_not_call"
    UNANSWERABLE = "unanswerable"
    INJECTION = "injection"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class HitlPolicy(BaseModel):
    """What the harness answers when the graph pauses for a human."""

    decision: DecisionKind = DecisionKind.APPROVE
    reason: str = "eval harness auto-decision"
    payload: dict[str, Any] = Field(default_factory=dict)


class GoldenTask(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    category: GoldenCategory
    difficulty: Difficulty = Difficulty.MEDIUM
    title: str
    request: str = Field(min_length=10)
    options: TaskOptions = Field(default_factory=TaskOptions)
    # tools: recall counts expected tools that ran; precision counts runs' calls inside expected + extra_ok
    expected_tools: list[str] = Field(default_factory=list)
    extra_ok_tools: list[str] = Field(default_factory=lambda: ["db_schema", "files_list_dir"])
    forbidden_tools: list[str] = Field(
        default_factory=list
    )  # an *attempt* (even blocked) fails the run
    # human-in-the-loop expectations
    expect_pause: bool = False
    pause_level: str | None = None
    pause_tool: str | None = None
    hitl: HitlPolicy = Field(default_factory=HitlPolicy)
    # outcome
    expected_status: str = "done"
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    min_subtasks: int = 1
    max_subtasks: int | None = None
    require_dependency: bool = False
    rubric: list[str] = Field(default_factory=list)
    judge_threshold: int = Field(default=4, ge=1, le=5)
    injection_marker: str | None = (
        None  # reported if quoted; the failure is a side effect, not a quote
    )
    timeout_s: int = 900


class ToolCallRecord(BaseModel):
    subtask_id: str
    tool: str
    risk: str | None = None
    decision: str
    executed: bool
    ok: bool | None = None


class PauseRecord(BaseModel):
    kind: str
    level: str
    tool: str | None = None
    subtask_id: str | None = None
    decision: str


class Trajectory(BaseModel):
    task_id: str
    golden_id: str
    run_index: int
    user_id: str
    status: str
    error: str | None = None
    elapsed_s: float
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    pauses: list[PauseRecord] = Field(default_factory=list)
    subtask_ids: list[str] = Field(default_factory=list)
    specialists: list[str] = Field(default_factory=list)
    has_dependency: bool = False
    retries: int = 0
    deliverable_title: str = ""
    deliverable_body: str = ""
    recalled_memories: int = 0
    providers: dict[str, int] = Field(default_factory=dict)
    fallback_calls: int = 0
    outbox_rows: int = 0

    @property
    def executed_tools(self) -> list[str]:
        return [c.tool for c in self.tool_calls if c.executed]

    @property
    def attempted_tools(self) -> list[str]:
        return [c.tool for c in self.tool_calls]

    @property
    def steps(self) -> int:
        return self.llm_calls + len(self.executed_tools)

    @property
    def deliverable_text(self) -> str:
        return f"{self.deliverable_title}\n{self.deliverable_body}"


class Assertion(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class JudgeCriterion(BaseModel):
    criterion: str
    met: bool
    note: str = ""


class JudgeOutput(BaseModel):
    """The judge model's structured answer."""

    score: int = Field(ge=1, le=5)
    criteria: list[JudgeCriterion] = Field(default_factory=list)
    summary: str = ""


class JudgeResult(JudgeOutput):
    model: str = ""


class RunResult(BaseModel):
    golden_id: str
    category: GoldenCategory
    difficulty: Difficulty
    run_index: int
    trajectory: Trajectory
    assertions: list[Assertion] = Field(default_factory=list)
    judge: JudgeResult | None = None
    success: bool
    failure_reasons: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    run_id: str
    label: str = ""
    started_at: str
    finished_at: str
    k: int
    task_count: int
    run_count: int
    reviewer: str = ""
    judge: str = ""
    results: list[RunResult] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    per_category: dict[str, dict[str, Any]] = Field(default_factory=dict)
    per_difficulty: dict[str, dict[str, Any]] = Field(default_factory=dict)
    per_task: dict[str, dict[str, Any]] = Field(default_factory=dict)
    baseline_id: str | None = None
    diff: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)
