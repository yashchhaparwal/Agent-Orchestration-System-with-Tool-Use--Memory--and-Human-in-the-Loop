from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from packages.orchestrator.llm.chains import ChainedLLM, extract_json
from packages.orchestrator.llm.openai_compat import strict_schema
from packages.orchestrator.llm.roles import ChainEntry, RoleConfig, load_models_config
from packages.shared.errors import NonRetryableError, RetryableError, SchemaValidationError
from packages.shared.types.llm import LLMResponse, Usage


class ScriptedClient:
    """A fake provider: each call pops the next scripted outcome (an exception or a content string)."""

    def __init__(self, provider_id: str, outcomes: list[Any]) -> None:
        self.provider_id = provider_id
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(
        self, messages: list[dict[str, Any]], *, model: str, **kwargs: Any
    ) -> LLMResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(
            content=outcome,
            provider=self.provider_id,
            model=model,
            usage=Usage(input_tokens=10, output_tokens=5),
            latency_ms=1,
        )


class FakePool:
    def __init__(self, clients: dict[str, Any]) -> None:
        self.clients = clients

    def get(self, provider_id: str) -> Any:
        if provider_id not in self.clients:
            raise NonRetryableError(f"no client for {provider_id}")
        return self.clients[provider_id]


def _role(*entries: tuple[str, str]) -> RoleConfig:
    return RoleConfig(chain=[ChainEntry(provider=p, model=m) for p, m in entries])


async def test_falls_back_on_retryable_error_and_marks_fallback() -> None:
    a = ScriptedClient("a", [RetryableError("429")])
    b = ScriptedClient("b", ["hello"])
    costs = []
    llm = ChainedLLM(
        "specialist",
        _role(("a", "m1"), ("b", "m2")),
        FakePool({"a": a, "b": b}),
        on_cost=costs.append,
    )  # type: ignore[arg-type]
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "hello" and resp.provider == "b"
    assert resp.cost is not None and resp.cost.fallback is True
    assert costs[0].fallback is True and costs[0].cost_usd is None  # no price on file → None, not 0


async def test_skips_misconfigured_provider() -> None:
    b = ScriptedClient("b", ["ok"])
    llm = ChainedLLM("cheap", _role(("missing", "m"), ("b", "m2")), FakePool({"b": b}))  # type: ignore[arg-type]
    resp = await llm.chat([{"role": "user", "content": "hi"}])
    assert resp.provider == "b"


async def test_exhausted_chain_raises_retryable() -> None:
    # Non-retryable failures on every entry: no backoff rounds, immediate exhaustion.
    # (Backoff on retryable failures is covered in test_chain_backoff_and_messages.py.)
    a = ScriptedClient("a", [NonRetryableError("boom")])
    b = ScriptedClient("b", [NonRetryableError("model not found")])
    llm = ChainedLLM("reviewer", _role(("a", "m1"), ("b", "m2")), FakePool({"a": a, "b": b}))  # type: ignore[arg-type]
    with pytest.raises(RetryableError, match="chain exhausted"):
        await llm.chat([{"role": "user", "content": "hi"}])


async def test_schema_failure_retries_same_entry_once_then_moves_on() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    a = ScriptedClient("a", ["not json", '{"n": "str"}'])  # two schema failures → next entry
    b = ScriptedClient("b", ['```json\n{"n": 3}\n```'])
    llm = ChainedLLM("supervisor", _role(("a", "m1"), ("b", "m2")), FakePool({"a": a, "b": b}))  # type: ignore[arg-type]
    resp = await llm.chat([{"role": "user", "content": "hi"}], response_schema=schema)
    assert a.calls == 2 and resp.provider == "b"
    assert extract_json(resp.content) == {"n": 3}


def test_extract_json_rejects_empty() -> None:
    with pytest.raises(SchemaValidationError):
        extract_json("")


def test_strict_schema_sets_additional_properties_everywhere() -> None:
    schema = {
        "type": "object",
        "properties": {
            "child": {"type": "object", "properties": {"x": {"type": "string"}}},
            "items": {"type": "array", "items": {"type": "object", "properties": {}}},
        },
        "$defs": {"D": {"type": "object", "properties": {}}},
    }
    out = strict_schema(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["child"]["additionalProperties"] is False
    assert out["properties"]["items"]["items"]["additionalProperties"] is False
    assert out["$defs"]["D"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # original untouched


def test_models_config_drops_paid_entries_unless_enabled(tmp_path: Path) -> None:
    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        "providers:\n  free: {base_url_env: GROQ_BASE_URL, api_key_env: GROQ_API_KEY, free_tier: true}\n"
        "  paid: {base_url_env: TOKENROUTER_BASE_URL, api_key_env: TOKENROUTER_API_KEY, paid: true}\n"
        "roles:\n"
        "  cheap:\n    chain: [{provider: paid, model: x}]\n"
        "  mixed:\n    chain: [{provider: paid, model: x}, {provider: free, model: y}]\n",
        encoding="utf-8",
    )
    # a chain that would become empty is an error, never a silently missing role
    with pytest.raises(ValueError, match="only paid providers"):
        load_models_config(cfg, enable_paid=False)
    # with the flag on, paid entries stay in order
    on = load_models_config(cfg, enable_paid=True)
    assert on.role("cheap").chain[0].provider == "paid"
    assert [e.provider for e in on.role("mixed").chain] == ["paid", "free"]


def test_models_config_mixed_chain_falls_back_to_free(tmp_path: Path) -> None:
    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        "providers:\n  free: {base_url_env: GROQ_BASE_URL, api_key_env: GROQ_API_KEY, free_tier: true}\n"
        "  paid: {base_url_env: TOKENROUTER_BASE_URL, api_key_env: TOKENROUTER_API_KEY, paid: true}\n"
        "roles:\n  mixed:\n    chain: [{provider: paid, model: x}, {provider: free, model: y}]\n",
        encoding="utf-8",
    )
    off = load_models_config(cfg, enable_paid=False)
    assert [e.provider for e in off.role("mixed").chain] == ["free"]


def test_repo_models_config_is_free_only_by_default() -> None:
    cfg = load_models_config(Path("config/models.yaml"), enable_paid=False)
    for role in ("supervisor", "specialist", "reviewer", "cheap", "embedding"):
        assert cfg.role(role).chain, role
        for entry in cfg.role(role).chain:
            assert cfg.providers[entry.provider].free_tier, f"{role} uses non-free {entry.provider}"
