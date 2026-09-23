from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.roles import ChainEntry, RoleConfig
from packages.orchestrator.loop.agent_loop import SUBMIT_TOOL, LoopDeps, run_agent_loop
from packages.orchestrator.loop.budgets import Budget
from packages.shared.types.llm import LLMResponse, Usage
from packages.shared.types.subtask import Specialist, Subtask, SubtaskStatus
from packages.shared.types.tools import ToolCall, ToolResult
from packages.tools.registry.registry import ToolRegistry
from tests.unit.test_registry_and_gate import LISTINGS, POLICY

# The gate test caps db_query at 2/min to prove fail-closed; the loop tests need calls to pass.
LOOSE_POLICY = copy.deepcopy(POLICY)
LOOSE_POLICY["tools"]["db_query"]["rate_per_min"] = 1000

AGENT = AgentSpec(name="research", role="specialist", system_prompt="You are a test agent.")
SUBTASK = Subtask(id="s1", description="find the loans", specialist=Specialist.RESEARCH)


def tool_call(cid: str, name: str, **args: Any) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args)


def submit(cid: str = "sub", **overrides: Any) -> ToolCall:
    args = {
        "status": "completed",
        "output": "three loans",
        "sources": ["loans"],
        "self_confidence": 0.9,
        "notes": "",
    }
    args.update(overrides)
    return ToolCall(id=cid, name=SUBMIT_TOOL, arguments=args)


class ScriptedLLM:
    """Stands in for a provider; records every message list it was called with."""

    provider_id = "fake"

    def __init__(self, turns: list[list[ToolCall] | str]) -> None:
        self.turns = list(turns)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(
        self, messages: list[dict[str, Any]], *, model: str, **kwargs: Any
    ) -> LLMResponse:
        self.seen.append(json.loads(json.dumps(messages)))
        turn = self.turns.pop(0) if self.turns else "…"
        if isinstance(turn, str):
            return LLMResponse(
                content=turn,
                provider="fake",
                model=model,
                usage=Usage(input_tokens=5, output_tokens=5),
            )
        return LLMResponse(
            content=None,
            tool_calls=turn,
            provider="fake",
            model=model,
            usage=Usage(input_tokens=5, output_tokens=5),
        )


class FakePool:
    def __init__(self, client: ScriptedLLM) -> None:
        self.client = client

    def get(self, provider_id: str) -> ScriptedLLM:
        return self.client


class RecordingRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__({}, {})
        real = ToolRegistry.from_listing(LISTINGS, LOOSE_POLICY, {"db": "x", "files": "y"})
        self._specs = real._specs
        self.invoked: list[ToolCall] = []

    async def invoke(self, call: ToolCall, **_: Any) -> ToolResult:
        self.invoked.append(call)
        return ToolResult(tool_call_id=call.id, name=call.name, content=f"result of {call.name}")


def make_deps(llm: ScriptedLLM, registry: RecordingRegistry, **budget: Any) -> LoopDeps:
    chain = ChainedLLM(
        "specialist", RoleConfig(chain=[ChainEntry(provider="fake", model="m")]), FakePool(llm)
    )  # type: ignore[arg-type]
    return LoopDeps(llm=chain, registry=registry, gate=Gate(registry), budget=Budget(**budget))


async def test_parallel_tool_calls_are_all_answered_before_next_llm_call(spans) -> None:  # type: ignore[no-untyped-def]
    llm = ScriptedLLM(
        [
            [
                tool_call("a", "db_query", sql="select 1"),
                tool_call("b", "files_read_file", path="claims/x.md"),
            ],
            [submit()],
        ]
    )
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry))

    assert result.status == SubtaskStatus.COMPLETED
    assert result.output == "three loans"
    assert result.tools_used == ["db_query", "files_read_file"]
    assert result.iterations == 2
    assert [c.name for c in registry.invoked] == ["db_query", "files_read_file"]
    # Second model call saw: system, user, assistant(tool_calls), tool, tool — both results, in order.
    second = llm.seen[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool", "tool"]
    assert {m["tool_call_id"] for m in second if m["role"] == "tool"} == {"a", "b"}
    assert len(result.cost_entries) == 2
    names = [s.name for s in spans.get_finished_spans()]
    assert names.count("llm.call") == 2 and "tool.db.db_query" in names and "gate.decide" in names


async def test_blocked_and_approval_calls_never_reach_registry() -> None:
    llm = ScriptedLLM(
        [
            [
                tool_call("a", "files_write_file", path="out.md", content="x"),
                tool_call("b", "nope", x=1),
            ],
            [submit()],
        ]
    )
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry))
    assert registry.invoked == []
    tool_msgs = [m for m in llm.seen[1] if m["role"] == "tool"]
    assert all(m["content"].startswith("ERROR:") for m in tool_msgs)
    assert result.status == SubtaskStatus.COMPLETED
    assert result.tools_used == []


async def test_stops_at_max_iterations_with_failed_result() -> None:
    llm = ScriptedLLM([[tool_call(f"c{i}", "db_query", sql="select 1")] for i in range(10)])
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry, max_iterations=3))
    assert result.status == SubtaskStatus.FAILED
    assert "max iterations" in (result.error or "")
    assert result.iterations == 3 and len(registry.invoked) == 3


async def test_invalid_submit_is_rejected_then_accepted() -> None:
    llm = ScriptedLLM([[submit("s1", self_confidence=7)], [submit("s2")]])
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry))
    assert result.status == SubtaskStatus.COMPLETED and result.iterations == 2
    rejection = [m for m in llm.seen[1] if m["role"] == "tool"][0]
    assert "submit_result rejected" in rejection["content"]


async def test_plain_text_is_nudged_then_fails() -> None:
    llm = ScriptedLLM(["I think the answer is 3.", "Still text.", "And again."])
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry))
    assert result.status == SubtaskStatus.FAILED
    assert "without calling submit_result" in (result.error or "")


async def test_token_budget_stops_the_loop() -> None:
    llm = ScriptedLLM(
        [
            [tool_call("a", "db_query", sql="select 1")],
            [tool_call("b", "db_query", sql="select 2")],
            [submit()],
        ]
    )
    registry = RecordingRegistry()
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, registry, max_total_tokens=12))
    assert result.status == SubtaskStatus.FAILED
    assert "token budget" in (result.error or "")


@pytest.mark.parametrize("bad_status", ["done", "ok"])
async def test_submit_status_must_be_valid_enum(bad_status: str) -> None:
    llm = ScriptedLLM([[submit("s1", status=bad_status)], [submit("s2")]])
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, RecordingRegistry()))
    assert result.status == SubtaskStatus.COMPLETED and result.iterations == 2
