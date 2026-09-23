"""Every gated tool call becomes a ToolEvent; the store persists them; the API view counts them."""

from __future__ import annotations

from pathlib import Path

from packages.orchestrator.loop.agent_loop import run_agent_loop
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import TaskStore
from packages.shared.types.gate import GateAction, ToolEvent
from packages.shared.types.subtask import SubtaskStatus
from packages.shared.types.task import TaskOptions, TaskStatus
from packages.shared.types.tools import RiskClass
from tests.unit.test_agent_loop import (
    AGENT,
    SUBTASK,
    RecordingRegistry,
    ScriptedLLM,
    make_deps,
    submit,
    tool_call,
)


async def test_loop_records_one_event_per_gated_call() -> None:
    llm = ScriptedLLM(
        [
            [
                tool_call("a", "db_query", sql="select 1"),  # safe -> allow, executed
                tool_call("b", "nope", x=1),  # unknown -> block
                tool_call("c", "files_write_file", path="o.md", content="x"),  # research may not
            ],
            [submit()],
        ]
    )
    result = await run_agent_loop(AGENT, SUBTASK, make_deps(llm, RecordingRegistry()))
    assert result.status == SubtaskStatus.COMPLETED
    by_tool = {e.tool: e for e in result.tool_events}
    assert set(by_tool) == {"db_query", "nope", "files_write_file"}
    assert by_tool["db_query"].decision == GateAction.ALLOW and by_tool["db_query"].ok is True
    assert by_tool["db_query"].risk == RiskClass.SAFE and by_tool["db_query"].result_size > 0
    assert by_tool["nope"].decision == GateAction.BLOCK and by_tool["nope"].ok is None
    assert by_tool["files_write_file"].decision == GateAction.BLOCK  # wrong agent
    assert all(len(e.args_hash) == 24 and e.subtask_id == "s1" for e in result.tool_events)


def test_store_persists_tool_events_and_counts_them(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    create_all(engine)
    store = TaskStore(make_session_factory(engine))
    row = store.create_task(user_id="u", request="r", options=TaskOptions())
    events = [
        ToolEvent(
            subtask_id="A",
            agent="research",
            tool="db_query",
            args_hash="h1",
            risk=RiskClass.SAFE,
            decision=GateAction.ALLOW,
            ok=True,
            latency_ms=5,
            result_size=10,
        ),
        ToolEvent(
            subtask_id="A",
            agent="research",
            tool="nope",
            args_hash="h2",
            decision=GateAction.BLOCK,
            reason="unknown tool",
        ),
        ToolEvent(
            subtask_id="C",
            agent="writing",
            tool="actions_send_email",
            args_hash="h3",
            risk=RiskClass.DESTRUCTIVE,
            decision=GateAction.APPROVE,
            reason="destructive",
        ),
    ]
    store.finish_task(
        row.id,
        status=TaskStatus.DONE,
        deliverable=None,
        cost_usd=None,
        error=None,
        results={},
        verdicts={},
        cost_entries=[],
        events=[],
        tool_events=events,
    )
    view = store.task_view(row.id)
    assert view is not None and view["tool_calls"] == 3 and view["tool_calls_not_executed"] == 2
