"""The ``embedding`` role: a fallback chain over providers that serve the *same* embedding model.

Different embedding models are different vector spaces, so a chain must never silently swap
models under one collection. ``LongTermMemory`` pins the model it was created with and asks the
chain for exactly that model; a fallback entry is only used when it serves the same model id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog

from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import RoleConfig
from packages.orchestrator.tracing.otel import span
from packages.shared.errors import NonRetryableError, RetryableError

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str


class Embedder(Protocol):
    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult: ...


class EmbeddingChain:
    def __init__(self, config: RoleConfig, pool: ProviderPool, *, timeout_s: float = 60.0) -> None:
        self.config = config
        self._pool = pool
        self._timeout = timeout_s

    @property
    def primary_model(self) -> str:
        return self.config.chain[0].model

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        wanted = model or self.primary_model
        entries = [e for e in self.config.chain if e.model == wanted]
        if not entries:
            raise NonRetryableError(f"no embedding chain entry serves model {wanted!r}")
        errors: list[str] = []
        for index, entry in enumerate(entries):
            try:
                client = self._pool.get(entry.provider)
            except NonRetryableError as e:
                errors.append(str(e))
                continue
            with span(
                "llm.embed",
                role="embedding",
                provider=entry.provider,
                model=entry.model,
                fallback=index > 0,
                count=len(texts),
            ) as s:
                try:
                    vectors = await client.embed(texts, model=entry.model, timeout_s=self._timeout)
                except (RetryableError, NonRetryableError) as e:
                    s.set_attribute("error", str(e)[:300])
                    errors.append(f"{entry.provider}/{entry.model}: {e}")
                    log.warning(
                        "embed.entry_failed",
                        provider=entry.provider,
                        model=entry.model,
                        error=str(e)[:200],
                    )
                    continue
            return EmbeddingResult(vectors=vectors, provider=entry.provider, model=entry.model)
        raise RetryableError("embedding chain exhausted: " + " | ".join(errors))
