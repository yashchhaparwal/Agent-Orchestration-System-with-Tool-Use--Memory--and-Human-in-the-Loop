"""Per-role fallback chain: primary → next entry on 429 / 5xx / timeout / misconfiguration /
schema failure twice. When *every* entry failed for a retryable reason (typically all providers
rate-limited at once), the chain backs off and tries again — up to three rounds — before giving
up. Records a `CostEntry` (with ``fallback=True`` past the first entry) and one ``llm.call`` span
per attempt (Architecture.md §9–10, Rules.md §4).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError

from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import RoleConfig
from packages.orchestrator.tracing.cost import compute_cost
from packages.orchestrator.tracing.otel import llm_span
from packages.shared.errors import NonRetryableError, RetryableError, SchemaValidationError
from packages.shared.types.cost import CostEntry
from packages.shared.types.llm import LLMMessage, LLMResponse

log = structlog.get_logger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
BACKOFF_SECONDS: tuple[float, ...] = (3.0, 8.0, 20.0)


def extract_json(text: str | None) -> Any:
    """Parse model text as JSON, tolerating ``` fences. Raises SchemaValidationError."""
    if not text or not text.strip():
        raise SchemaValidationError("empty response where JSON was required")
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise SchemaValidationError(f"response was not valid JSON: {e.msg} at {e.pos}") from e


class ChainedLLM:
    def __init__(
        self,
        role: str,
        config: RoleConfig,
        pool: ProviderPool,
        *,
        prices: dict[str, dict[str, float]] | None = None,
        on_cost: Callable[[CostEntry], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.role = role
        self.config = config
        self._pool = pool
        self._prices = prices or {}
        self._on_cost = on_cost
        self._sleep = sleep

    def with_cost_sink(self, on_cost: Callable[[CostEntry], None] | None) -> ChainedLLM:
        """Same chain, different cost callback — one per task/subtask so ledgers stay separate."""
        return ChainedLLM(
            self.role,
            self.config,
            self._pool,
            prices=self._prices,
            on_cost=on_cost,
            sleep=self._sleep,
        )

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
    ) -> LLMResponse:
        all_errors: list[str] = []
        for round_index in range(len(BACKOFF_SECONDS) + 1):
            resp, errors, retry_worthy = await self._try_chain(
                messages,
                tools=tools,
                response_schema=response_schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_s=timeout_s,
            )
            if resp is not None:
                return resp
            all_errors.extend(errors)
            if not retry_worthy or round_index == len(BACKOFF_SECONDS):
                break
            delay = BACKOFF_SECONDS[round_index]
            log.warning("chain.backoff", role=self.role, round=round_index + 1, delay_s=delay)
            await self._sleep(delay)
        raise RetryableError(f"chain exhausted for role '{self.role}': " + " | ".join(all_errors))

    async def _try_chain(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None,
        response_schema: dict[str, Any] | None,
        schema_name: str,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> tuple[LLMResponse | None, list[str], bool]:
        """One pass over the chain. Returns (response, errors, any_failure_was_retryable)."""
        errors: list[str] = []
        retry_worthy = False
        validator = Draft202012Validator(response_schema) if response_schema else None

        for index, entry in enumerate(self.config.chain):
            try:
                client = self._pool.get(entry.provider)
            except NonRetryableError as e:
                errors.append(str(e))
                log.warning(
                    "chain.skip_provider", role=self.role, provider=entry.provider, error=str(e)
                )
                continue

            for attempt in range(2):  # a second try on the same entry only for schema failures
                with llm_span(
                    role=self.role, provider=entry.provider, model=entry.model, fallback=index > 0
                ) as span:
                    try:
                        resp = await client.chat(
                            messages,
                            model=entry.model,
                            tools=tools,
                            response_schema=response_schema,
                            schema_name=schema_name,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            effort=self.config.effort,
                            timeout_s=timeout_s,
                        )
                    except RetryableError as e:
                        retry_worthy = True
                        errors.append(str(e))
                        span.set_attribute("error", str(e)[:500])
                        log.warning(
                            "chain.entry_failed",
                            role=self.role,
                            provider=entry.provider,
                            model=entry.model,
                            error=str(e)[:300],
                        )
                        break  # next entry
                    except NonRetryableError as e:
                        errors.append(str(e))
                        span.set_attribute("error", str(e)[:500])
                        log.warning(
                            "chain.entry_failed",
                            role=self.role,
                            provider=entry.provider,
                            model=entry.model,
                            error=str(e)[:300],
                        )
                        break  # next entry

                    if validator is not None:
                        try:
                            validator.validate(extract_json(resp.content))
                        except (SchemaValidationError, JsonSchemaError) as e:
                            msg = f"{entry.provider}/{entry.model}: schema failure: {str(e)[:200]}"
                            errors.append(msg)
                            span.set_attribute("schema_failure", True)
                            if attempt == 0:
                                continue  # retry the same entry once
                            break  # give up on this entry

                    cost = CostEntry(
                        provider=entry.provider,
                        model=entry.model,
                        role=self.role,
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        cost_usd=compute_cost(entry.model, resp.usage, self._prices),
                        latency_ms=resp.latency_ms,
                        fallback=index > 0,
                    )
                    resp.cost = cost
                    span.set_attribute("input_tokens", cost.input_tokens)
                    span.set_attribute("output_tokens", cost.output_tokens)
                    span.set_attribute("latency_ms", cost.latency_ms)
                    if cost.cost_usd is not None:
                        span.set_attribute("cost_usd", cost.cost_usd)
                    span.set_attribute("finish_reason", resp.finish_reason)
                    span.set_attribute("tool_calls", len(resp.tool_calls))
                    if self._on_cost is not None:
                        self._on_cost(cost)
                    return resp, errors, retry_worthy

        return None, errors, retry_worthy
