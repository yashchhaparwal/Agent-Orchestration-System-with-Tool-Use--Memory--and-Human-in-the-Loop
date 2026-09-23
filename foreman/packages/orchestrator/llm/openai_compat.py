"""One provider class for every OpenAI-compatible endpoint (TokenRouter, Mistral, Groq, Gemini, Ollama).

Normalises: tool calls → `ToolCall`, JSON-schema output (made strict: ``additionalProperties:
false`` and every property required — what OpenAI and Groq strict modes demand), usage → `Usage`,
and provider exceptions → the Foreman error taxonomy so the fallback chain can decide what to do.

A per-provider semaphore caps in-flight requests: free tiers rate-limit on bursts, and parallel
specialists would otherwise fire several calls at once.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from packages.shared.errors import NonRetryableError, RetryableError
from packages.shared.types.llm import LLMMessage, LLMResponse, Usage
from packages.shared.types.tools import ToolCall


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy shaped for strict json_schema modes.

    On every object node: ``additionalProperties: false``, every property listed in ``required``,
    and ``default`` removed (strict modes reject both optional keys and defaults). Validation of
    the model's reply still uses the *original* schema, so optional fields stay optional there.
    """
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("additionalProperties", False)
                props = node.get("properties")
                if isinstance(props, dict) and props:
                    node["required"] = list(props.keys())
            node.pop("default", None)
            for key in ("properties", "$defs", "definitions"):
                if isinstance(node.get(key), dict):
                    for child in node[key].values():
                        walk(child)
            for key in ("items", "additionalProperties"):
                if isinstance(node.get(key), dict):
                    walk(node[key])
            for key in ("anyOf", "oneOf", "allOf"):
                if isinstance(node.get(key), list):
                    for child in node[key]:
                        walk(child)

    walk(out)
    return out


def text_content(raw: Any) -> str | None:
    """``message.content`` as text. Providers return a string, None (tool-call turns), or — seen
    live from the TokenRouter/Qwen fallback — a list of content parts; only text parts count."""
    if raw is None or isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for part in raw:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(raw)


def _parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"arguments were not valid JSON: {e.msg} at position {e.pos}"
    if not isinstance(parsed, dict):
        return {}, "arguments must be a JSON object"
    return parsed, None


class OpenAICompatProvider:
    def __init__(
        self,
        provider_id: str,
        base_url: str,
        api_key: str,
        *,
        default_timeout_s: float = 60.0,
        supports_effort: bool = False,
        max_concurrency: int = 2,
    ) -> None:
        self.provider_id = provider_id
        self.supports_effort = supports_effort
        self.max_concurrency = max(1, max_concurrency)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            timeout=default_timeout_s,
            max_retries=0,  # the chain decides about retries and fallbacks, not the SDK
        )

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
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": strict_schema(response_schema),
                    "strict": True,
                },
            }
        if effort and self.supports_effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s

        async with self._semaphore:
            started = time.perf_counter()
            resp = await self._create(kwargs, model, response_schema is not None)
            latency_ms = int((time.perf_counter() - started) * 1000)

        if not resp.choices:
            raise RetryableError(f"{self.provider_id}/{model}: empty choices in response")
        choice = resp.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            args, parse_error = _parse_arguments(tc.function.arguments)
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=args, parse_error=parse_error)
            )

        usage = Usage()
        if resp.usage is not None:
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
            )

        content = text_content(message.content)
        raw = message.model_dump(exclude_none=True)
        raw.pop("function_call", None)  # legacy field; never send it back
        if "content" in raw or content is not None:
            raw["content"] = content  # the history must carry text, not provider-specific parts
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            provider=self.provider_id,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            raw_assistant_message=raw,
        )

    async def embed(
        self, texts: list[str], *, model: str, timeout_s: float | None = None
    ) -> list[list[float]]:
        label = f"{self.provider_id}/{model}"
        kwargs: dict[str, Any] = {"model": model, "input": texts}
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s
        async with self._semaphore:
            try:
                resp = await self._client.embeddings.create(**kwargs)
            except (APITimeoutError, APIConnectionError) as e:
                raise RetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except RateLimitError as e:
                raise RetryableError(f"{label}: rate limited: {e}") from e
            except (AuthenticationError, PermissionDeniedError, NotFoundError) as e:
                raise NonRetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except APIStatusError as e:
                raise _map_status_error(e, label) from e
        return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]

    async def _create(self, kwargs: dict[str, Any], model: str, structured: bool) -> Any:
        label = f"{self.provider_id}/{model}"
        # Two known 400s are degraded once each, then the request is retried as-is:
        # a route that rejects `temperature` (reasoning models: gpt-6-astra, o-series), and a
        # provider that accepts json_object but not json_schema (reply still schema-validated).
        for _ in range(3):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                reason = str(e).lower()
                if "temperature" in reason and "temperature" in kwargs:
                    kwargs.pop("temperature")
                    continue
                if (
                    structured
                    and "response_format" in reason
                    and kwargs.get("response_format", {}).get("type") == "json_schema"
                ):
                    kwargs["response_format"] = {"type": "json_object"}
                    continue
                raise NonRetryableError(f"{label}: bad request: {e}") from e
            except (APITimeoutError, APIConnectionError) as e:
                raise RetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except RateLimitError as e:
                raise RetryableError(f"{label}: rate limited: {e}") from e
            except (AuthenticationError, PermissionDeniedError, NotFoundError) as e:
                raise NonRetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except APIStatusError as e:
                raise _map_status_error(e, label) from e
        raise NonRetryableError(f"{label}: bad request persisted after degrading parameters")


def _map_status_error(e: APIStatusError, label: str = "") -> Exception:
    prefix = f"{label}: " if label else ""
    if e.status_code == 429 or e.status_code >= 500:
        return RetryableError(f"{prefix}HTTP {e.status_code}: {e}")
    return NonRetryableError(f"{prefix}HTTP {e.status_code}: {e}")
