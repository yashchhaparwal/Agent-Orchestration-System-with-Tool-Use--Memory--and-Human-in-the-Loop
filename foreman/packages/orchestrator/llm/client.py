"""The interfaces every model call goes through (Architecture.md §10).

``LLMClient`` is one provider endpoint. ``ChatLLM`` is what nodes and the agent loop consume: a
role-bound chain that already knows its fallback entries and can be re-bound to a cost sink.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from packages.shared.types.cost import CostEntry
from packages.shared.types.llm import LLMMessage, LLMResponse


class LLMClient(Protocol):
    provider_id: str

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "Response",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        effort: str | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse: ...

    async def embed(
        self, texts: list[str], *, model: str, timeout_s: float | None = None
    ) -> list[list[float]]: ...


class ChatLLM(Protocol):
    role: str

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "Response",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        timeout_s: float = 60.0,
    ) -> LLMResponse: ...

    def with_cost_sink(self, on_cost: Callable[[CostEntry], None] | None) -> ChatLLM: ...
