from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from packages.shared.types.tools import RiskClass


class GateAction(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"
    BLOCK = "block"


class Decision(BaseModel):
    action: GateAction
    reason: str
    risk: RiskClass | None = None
    latency_ms: int = 0
    classified: bool = False  # true when the LLM classifier made the call (risky tools only)


class ToolEvent(BaseModel):
    """One gated tool call, as recorded in the ``tool_invocations`` ledger (Architecture.md §7.2)."""

    subtask_id: str
    agent: str
    tool: str
    args_hash: str = Field(
        description="sha256 of the canonical JSON arguments — never the raw args"
    )
    risk: RiskClass | None = None
    decision: GateAction
    reason: str = ""
    ok: bool | None = Field(default=None, description="None when the call never executed")
    latency_ms: int = 0
    result_size: int = 0
