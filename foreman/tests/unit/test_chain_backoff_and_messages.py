"""Regressions from the first live run: cross-provider fallback must not leak provider-specific
assistant fields, and a chain whose entries are all rate-limited must back off and retry."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import BadRequestError

from packages.orchestrator.llm.chains import BACKOFF_SECONDS, ChainedLLM
from packages.orchestrator.llm.openai_compat import OpenAICompatProvider
from packages.orchestrator.loop.messages import assistant_message
from packages.shared.errors import NonRetryableError, RetryableError
from packages.shared.types.llm import LLMResponse, Usage
from packages.shared.types.tools import ToolCall
from tests.unit.test_chains_and_roles import FakePool, ScriptedClient, _role


def test_assistant_message_keeps_only_portable_fields() -> None:
    resp = LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="c1", name="db_query", arguments={"sql": "select 1"})],
        provider="groq",
        model="openai/gpt-oss-120b",
        usage=Usage(),
        raw_assistant_message={
            "role": "assistant",
            "reasoning": "we should list the directory first",  # Groq adds this; Mistral 422s on it
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "db_query", "arguments": "{}"},
                }
            ],
        },
    )
    msg = assistant_message(resp)
    assert set(msg) == {"role", "content", "tool_calls"}
    assert msg["content"] == "" and msg["tool_calls"][0]["function"]["name"] == "db_query"
    plain = assistant_message(LLMResponse(content="hi", provider="x", model="m"))
    assert plain == {"role": "assistant", "content": "hi"}


async def test_chain_backs_off_when_every_entry_is_rate_limited() -> None:
    a = ScriptedClient("a", [RetryableError("429"), RetryableError("429"), "ok"])
    b = ScriptedClient("b", [RetryableError("429"), RetryableError("429")])
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)

    llm = ChainedLLM(
        "specialist", _role(("a", "m1"), ("b", "m2")), FakePool({"a": a, "b": b}), sleep=fake_sleep
    )  # type: ignore[arg-type]
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "ok" and resp.provider == "a"
    assert delays == list(BACKOFF_SECONDS[:2])  # two failed rounds, then success on round three
    assert a.calls == 3 and b.calls == 2


async def test_chain_gives_up_after_the_last_backoff_round() -> None:
    a = ScriptedClient("a", [RetryableError("429")] * 10)
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)

    llm = ChainedLLM("cheap", _role(("a", "m1")), FakePool({"a": a}), sleep=fake_sleep)  # type: ignore[arg-type]
    with pytest.raises(RetryableError, match="chain exhausted"):
        await llm.chat([{"role": "user", "content": "hi"}])
    assert delays == list(BACKOFF_SECONDS)
    assert a.calls == len(BACKOFF_SECONDS) + 1


async def test_provider_drops_temperature_when_the_route_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gpt-6-astra (like OpenAI's o-series routes) 400s on `temperature`; the provider must
    retry once without it instead of failing the chain entry."""

    class _Message:
        content = "ok"
        tool_calls = None

        def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
            return {"role": "assistant", "content": "ok"}

    class _Choice:
        message = _Message()
        finish_reason = "stop"

    class _Response:
        choices = [_Choice()]
        usage = None

    provider = OpenAICompatProvider("explabs", "http://test/v1", "key")
    seen: list[dict[str, Any]] = []

    async def fake_create(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if "temperature" in kwargs:
            raise BadRequestError(
                "The parameter 'temperature' is not supported by this model route.",
                response=httpx.Response(400, request=httpx.Request("POST", "http://test/v1")),
                body=None,
            )
        return _Response()

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    resp = await provider.chat([{"role": "user", "content": "hi"}], model="gpt-6-astra")
    assert resp.content == "ok"
    assert len(seen) == 2
    assert "temperature" in seen[0] and "temperature" not in seen[1]


async def test_chain_does_not_back_off_on_non_retryable_failures() -> None:
    a = ScriptedClient("a", [NonRetryableError("model not found")])
    slept = False

    async def fake_sleep(s: float) -> None:
        nonlocal slept
        slept = True

    llm = ChainedLLM("cheap", _role(("a", "m1")), FakePool({"a": a}), sleep=fake_sleep)  # type: ignore[arg-type]
    with pytest.raises(RetryableError, match="chain exhausted"):
        await llm.chat([{"role": "user", "content": "hi"}])
    assert slept is False and a.calls == 1
