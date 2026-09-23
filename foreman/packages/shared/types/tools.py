from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskClass(StrEnum):
    SAFE = "safe"
    RISKY = "risky"
    DESTRUCTIVE = "destructive"


class ToolCall(BaseModel):
    """A tool request emitted by the model. ``parse_error`` is set when the arguments were not valid JSON."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    latency_ms: int = 0


class ToolSpec(BaseModel):
    """An MCP tool merged with its policy entry. Only tools with a policy entry are ever registered."""

    name: str = Field(description="Registry name exposed to the model, e.g. db_query")
    server: str
    mcp_name: str = Field(description="The tool's name on its MCP server, e.g. query")
    description: str
    input_schema: dict[str, Any]
    risk: RiskClass
    agents: list[str]
    rate_per_min: int = 60
    timeout_s: int = 20

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
