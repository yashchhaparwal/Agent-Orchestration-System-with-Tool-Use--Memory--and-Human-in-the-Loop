from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from packages.shared.types.cost import CostEntry
from packages.shared.types.tools import ToolCall

# Messages travel in the OpenAI wire format (role/content/tool_calls/tool_call_id). Keeping them as
# dicts avoids re-modelling the provider schema; the loop builds them through `loop/messages.py`.
LLMMessage = dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = ""
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    cost: CostEntry | None = None
    raw_assistant_message: dict[str, Any] = Field(
        default_factory=dict,
        description="The assistant message as returned, for appending to history verbatim.",
    )
