from __future__ import annotations

from pydantic import BaseModel


class CostEntry(BaseModel):
    """One LLM call's accounting. ``cost_usd`` is ``None`` when the model has no price on file —
    never a silent zero (Rules.md §5)."""

    provider: str
    model: str
    role: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    fallback: bool = False
