"""Tier 1 semantics on the in-memory implementation: keyed per task, TTL, errors, clear."""

from __future__ import annotations

from packages.orchestrator.memory.working import InMemoryWorkingMemory
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.subtask import SubtaskResult, SubtaskStatus
from tests.unit.test_graph_flow import PLAN_ABC


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def result(sid: str, output: str) -> SubtaskResult:
    return SubtaskResult(
        subtask_id=sid,
        status=SubtaskStatus.COMPLETED,
        output=output,
        sources=[],
        self_confidence=0.9,
    )


async def test_plan_results_artifacts_and_errors_are_scoped_per_task() -> None:
    clock = Clock()
    wm = InMemoryWorkingMemory(ttl_hours=1, clock=clock)
    plan = ExecutionPlan.model_validate(PLAN_ABC)
    await wm.put_plan("t1", plan)
    await wm.put_result("t1", result("A", "loans"))
    await wm.put_result("t1", result("B", "findings"))
    await wm.put_result("t2", result("A", "other task"))
    await wm.put_artifact("t1", "letter.md", "Dear lender")
    await wm.add_error("t1", "B: tool timeout")
    await wm.add_error("t1", "B: retried")

    assert (await wm.get_plan("t1")) == plan and (await wm.get_plan("t2")) is None
    got = await wm.get_results("t1")
    assert {k: v.output for k, v in got.items()} == {"A": "loans", "B": "findings"}
    assert {k: v.output for k, v in (await wm.get_results("t2")).items()} == {"A": "other task"}
    assert (await wm.get_artifact("t1", "letter.md")) == "Dear lender"
    assert (await wm.get_artifact("t1", "missing")) is None
    assert (await wm.get_errors("t1")) == ["B: tool timeout", "B: retried"]
    assert (await wm.get_errors("t2")) == []


async def test_entries_expire_after_the_ttl() -> None:
    clock = Clock()
    wm = InMemoryWorkingMemory(ttl_hours=1, clock=clock)
    await wm.put_result("t1", result("A", "loans"))
    clock.t += 3599
    assert list(await wm.get_results("t1")) == ["A"]
    clock.t += 2
    assert (await wm.get_results("t1")) == {}


async def test_clear_removes_everything_for_one_task_only() -> None:
    wm = InMemoryWorkingMemory()
    await wm.put_result("t1", result("A", "x"))
    await wm.put_artifact("t1", "a", "b")
    await wm.add_error("t1", "e")
    await wm.put_result("t10", result("A", "keep"))  # prefix sibling must survive
    await wm.clear("t1")
    assert (await wm.get_results("t1")) == {} and (await wm.get_errors("t1")) == []
    assert (await wm.get_artifact("t1", "a")) is None
    assert list(await wm.get_results("t10")) == ["A"]
