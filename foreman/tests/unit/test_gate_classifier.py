"""Phase 3 gate: the classifier path for risky tools, and that it is never consulted otherwise."""

from __future__ import annotations

from typing import Any

import pytest

from packages.orchestrator.gate.classifier import (
    ClassifierVerdict,
    LLMRiskClassifier,
    StaticClassifier,
)
from packages.orchestrator.gate.decide import Gate
from packages.shared.errors import RetryableError
from packages.shared.types.gate import GateAction
from packages.shared.types.llm import LLMResponse, Usage
from packages.shared.types.tools import ToolCall
from packages.tools.registry.ratelimit import RateLimiter
from packages.tools.registry.registry import ToolRegistry
from tests.unit.test_registry_and_gate import LISTINGS, POLICY


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry.from_listing(LISTINGS, POLICY, {"db": "http://db", "files": "http://files"})


def write_call() -> ToolCall:
    return ToolCall(
        id="w1", name="files_write_file", arguments={"path": "out/a.md", "content": "x"}
    )


async def test_risky_tool_cleared_by_classifier_is_allowed(registry: ToolRegistry) -> None:
    clf = StaticClassifier("allow", "confined write under out/")
    gate = Gate(registry, RateLimiter(), classifier=clf)
    d = await gate.decide_async("writing", write_call(), context="B: write the summary")
    assert d.action == GateAction.ALLOW and d.classified and "confined" in d.reason
    assert len(clf.calls) == 1


async def test_risky_tool_not_cleared_needs_approval(registry: ToolRegistry) -> None:
    gate = Gate(registry, RateLimiter(), classifier=StaticClassifier("approve", "overwrites"))
    d = await gate.decide_async("writing", write_call())
    assert d.action == GateAction.APPROVE and d.classified


async def test_risky_tool_without_classifier_needs_approval(registry: ToolRegistry) -> None:
    d = await Gate(registry, RateLimiter()).decide_async("writing", write_call())
    assert d.action == GateAction.APPROVE and not d.classified


async def test_classifier_is_never_consulted_for_safe_destructive_or_blocked(
    registry: ToolRegistry,
) -> None:
    clf = StaticClassifier("allow")
    gate = Gate(registry, RateLimiter(), classifier=clf)
    safe = await gate.decide_async(
        "research", ToolCall(id="s", name="db_query", arguments={"sql": "select 1"})
    )
    assert safe.action == GateAction.ALLOW and not safe.classified
    wrong_agent = await gate.decide_async("research", write_call())
    assert wrong_agent.action == GateAction.BLOCK
    bad_args = await gate.decide_async(
        "writing", ToolCall(id="b", name="files_write_file", arguments={"path": 1})
    )
    assert bad_args.action == GateAction.BLOCK
    unknown = await gate.decide_async(
        "writing", ToolCall(id="u", name="actions_send_email", arguments={})
    )
    assert (
        unknown.action == GateAction.BLOCK
    )  # not registered: the actions server is not in LISTINGS
    assert clf.calls == []


async def test_destructive_tool_ignores_classifier() -> None:
    listings = {
        "actions": [
            (
                "send_email",
                "queue an email",
                {"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]},
            )
        ]
    }
    policy = {
        "servers": {"actions": {"url_env": "MCP_ACTIONS_URL"}},
        "tools": {
            "actions_send_email": {
                "server": "actions",
                "mcp_name": "send_email",
                "risk": "destructive",
                "agents": ["writing"],
            }
        },
    }
    registry = ToolRegistry.from_listing(listings, policy, {"actions": "http://a"})
    clf = StaticClassifier("allow", "would clear it")
    d = await Gate(registry, RateLimiter(), classifier=clf).decide_async(
        "writing", ToolCall(id="e", name="actions_send_email", arguments={"to": "a@b.co"})
    )
    assert d.action == GateAction.APPROVE and not d.classified and clf.calls == []


class ScriptedChat:
    """ChatLLM double for the LLM classifier."""

    role = "cheap"

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.prompts: list[str] = []

    def with_cost_sink(self, on_cost: Any) -> ScriptedChat:
        return self

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.prompts.append(messages[-1]["content"])
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return LLMResponse(content=self.outcome, provider="fake", model="m", usage=Usage())


def spec_and_call() -> tuple[Any, ToolCall]:
    registry = ToolRegistry.from_listing(LISTINGS, POLICY, {"db": "x", "files": "y"})
    return registry.get("files_write_file"), write_call()


async def test_llm_classifier_parses_allow() -> None:
    spec, call = spec_and_call()
    llm = ScriptedChat('{"decision": "allow", "reason": "writes a new file under out/"}')
    v = await LLMRiskClassifier(llm).classify(
        agent="writing", call=call, spec=spec, context="B: write"
    )
    assert v == ClassifierVerdict(decision="allow", reason="writes a new file under out/")
    assert "files_write_file" in llm.prompts[0] and "out/a.md" in llm.prompts[0]


@pytest.mark.parametrize(
    "outcome",
    [
        RetryableError("all providers down"),
        "not json",
        '{"decision": "maybe"}',
        RuntimeError("boom"),
    ],
)
async def test_llm_classifier_fails_closed(outcome: Any) -> None:
    spec, call = spec_and_call()
    v = await LLMRiskClassifier(ScriptedChat(outcome)).classify(
        agent="writing", call=call, spec=spec, context=""
    )
    assert v.decision == "approve"
