"""The agent loop pauses on an approve decision and resumes from its checkpoint."""

from __future__ import annotations

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.loop.agent_loop import LoopResume, PausedLoop, run_agent_loop
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from packages.shared.types.subtask import Specialist, Subtask, SubtaskStatus
from tests.unit.test_agent_loop import RecordingRegistry, ScriptedLLM, make_deps, submit, tool_call

WRITER = AgentSpec(name="writing", role="specialist", system_prompt="You are a test writer.")
TASK = Subtask(id="w1", description="write the file", specialist=Specialist.WRITING)


def write(cid: str = "w", path: str = "out/a.md") -> object:
    return tool_call(cid, "files_write_file", path=path, content="hello")


def decision(kind: DecisionKind, **payload: object) -> ApprovalDecision:
    return ApprovalDecision(decision=kind, payload=payload, reason="r", decided_by="tester")


async def test_risky_call_pauses_with_a_checkpoint() -> None:
    llm = ScriptedLLM([[write("w"), tool_call("r", "files_read_file", path="x.md")], [submit()]])
    registry = RecordingRegistry()
    outcome = await run_agent_loop(WRITER, TASK, make_deps(llm, registry))
    assert isinstance(outcome, PausedLoop)
    cp = outcome.checkpoint
    assert [p.call.name for p in cp.pending] == ["files_write_file"]
    assert [r.name for r in cp.ready_results] == ["files_read_file"]  # the safe call already ran
    assert [c.name for c in registry.invoked] == ["files_read_file"]
    assert cp.messages[-1]["role"] == "assistant" and cp.iteration == 1 and cp.total_tokens == 10
    assert len(llm.turns) == 1  # the submit turn has not been consumed: nothing ran past the pause


async def test_resume_approve_executes_and_continues_without_replaying() -> None:
    llm = ScriptedLLM([[write("w")], [submit()]])
    registry = RecordingRegistry()
    deps = make_deps(llm, registry)
    paused = await run_agent_loop(WRITER, TASK, deps)
    assert isinstance(paused, PausedLoop)
    calls_before = len(llm.seen)
    result = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(checkpoint=paused.checkpoint, decision=decision(DecisionKind.APPROVE)),
    )
    assert not isinstance(result, PausedLoop)
    assert result.status == SubtaskStatus.COMPLETED and result.iterations == 2
    assert [c.name for c in registry.invoked] == ["files_write_file"]
    assert len(llm.seen) == calls_before + 1  # exactly one more model call
    assert [m["role"] for m in llm.seen[-1]] == ["system", "user", "assistant", "tool"]
    assert [e.decision.value for e in result.tool_events] == ["approve"] and result.tool_events[
        0
    ].ok is True


async def test_resume_modify_reject_take_over() -> None:
    async def paused_run() -> tuple[ScriptedLLM, RecordingRegistry, PausedLoop, object]:
        llm = ScriptedLLM([[write("w")], [submit()]])
        registry = RecordingRegistry()
        deps = make_deps(llm, registry)
        p = await run_agent_loop(WRITER, TASK, deps)
        assert isinstance(p, PausedLoop)
        return llm, registry, p, deps

    llm, registry, p, deps = await paused_run()
    modified = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(
            checkpoint=p.checkpoint,
            decision=decision(
                DecisionKind.MODIFY, arguments={"path": "out/b.md", "content": "edited"}
            ),
        ),
    )
    assert registry.invoked[0].arguments == {"path": "out/b.md", "content": "edited"}
    assert not isinstance(modified, PausedLoop) and modified.status == SubtaskStatus.COMPLETED

    llm, registry, p, deps = await paused_run()
    rejected = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(checkpoint=p.checkpoint, decision=decision(DecisionKind.REJECT)),
    )
    assert registry.invoked == [] and not isinstance(rejected, PausedLoop)
    assert llm.seen[-1][-1]["content"].startswith("ERROR: rejected by the human reviewer")

    llm, registry, p, deps = await paused_run()
    taken = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(
            checkpoint=p.checkpoint,
            decision=decision(DecisionKind.TAKE_OVER, output="Done by hand"),
        ),
    )
    assert (
        not isinstance(taken, PausedLoop)
        and taken.human_authored
        and taken.output == "Done by hand"
    )
    assert registry.invoked == [] and len(llm.turns) == 1  # the model was never called again


async def test_two_pending_calls_are_decided_one_at_a_time() -> None:
    llm = ScriptedLLM([[write("w1", "out/a.md"), write("w2", "out/b.md")], [submit()]])
    registry = RecordingRegistry()
    deps = make_deps(llm, registry)
    first = await run_agent_loop(WRITER, TASK, deps)
    assert isinstance(first, PausedLoop) and len(first.checkpoint.pending) == 2
    second = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(checkpoint=first.checkpoint, decision=decision(DecisionKind.APPROVE)),
    )
    assert isinstance(second, PausedLoop) and [p.call.id for p in second.checkpoint.pending] == [
        "w2"
    ]
    assert [r.tool_call_id for r in second.checkpoint.ready_results] == ["w1"]
    done = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(checkpoint=second.checkpoint, decision=decision(DecisionKind.REJECT)),
    )
    assert not isinstance(done, PausedLoop) and done.status == SubtaskStatus.COMPLETED
    assert [c.id for c in registry.invoked] == ["w1"]
    tool_msgs = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [
        "w1",
        "w2",
    ]  # both results, in order, in one turn


async def test_a_rejection_is_final_for_the_subtask() -> None:
    """After a human rejects a call, the agent retrying the same tool is blocked outright — no
    second approval request, no way to wear the reviewer down."""
    llm = ScriptedLLM([[write("w1")], [write("w2", "out/c.md")], [submit()]])
    registry = RecordingRegistry()
    deps = make_deps(llm, registry)
    p = await run_agent_loop(WRITER, TASK, deps)
    assert isinstance(p, PausedLoop)
    done = await run_agent_loop(
        WRITER,
        TASK,
        deps,
        resume=LoopResume(checkpoint=p.checkpoint, decision=decision(DecisionKind.REJECT)),
    )
    assert not isinstance(done, PausedLoop) and done.status == SubtaskStatus.COMPLETED
    assert registry.invoked == []
    assert [e.decision.value for e in done.tool_events] == ["approve", "block"]
    assert "rejected by tester: r" in done.tool_events[0].reason
    assert "already rejected files_write_file" in done.tool_events[1].reason
    assert llm.seen[-1][-1]["content"].startswith("ERROR: denied: a human already rejected")
    assert "Do not call files_write_file again" in llm.seen[-2][-1]["content"]


async def test_seeded_denials_block_before_the_gate() -> None:
    """A retry of the subtask starts with the human's earlier refusals already in force."""
    llm = ScriptedLLM([[write("w")], [submit()]])
    registry = RecordingRegistry()
    done = await run_agent_loop(
        WRITER, TASK, make_deps(llm, registry), denied={"files_write_file": "no"}
    )
    assert not isinstance(done, PausedLoop) and done.status == SubtaskStatus.COMPLETED
    assert registry.invoked == [] and done.denied_tools == {"files_write_file": "no"}
    assert [e.decision.value for e in done.tool_events] == ["block"]
