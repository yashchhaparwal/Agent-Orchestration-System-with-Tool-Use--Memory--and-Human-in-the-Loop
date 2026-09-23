from __future__ import annotations

import pytest
from langgraph.types import Send

from packages.orchestrator.graph.edges import (
    NODE_APPROVE_PLAN,
    NODE_DISPATCH,
    NODE_ESCALATE,
    NODE_SYNTHESIZE,
    make_route_after_plan,
    make_route_after_review,
    needs_review,
    ready_subtasks,
    route_dispatch,
)
from packages.orchestrator.graph.state import initial_state
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import Specialist, Subtask, SubtaskResult, SubtaskStatus
from packages.shared.types.task import TaskOptions


def sub(id_: str, specialist: str = "research", deps: list[str] | None = None) -> Subtask:
    return Subtask(
        id=id_, description=f"do {id_}", specialist=Specialist(specialist), depends_on=deps or []
    )


def plan_abc() -> ExecutionPlan:
    return ExecutionPlan(
        subtasks=[sub("A"), sub("B", "analysis", ["A"]), sub("C", "writing", ["A", "B"])],
        confidence=0.9,
    )


def result(
    id_: str, attempt: int = 1, status: SubtaskStatus = SubtaskStatus.COMPLETED
) -> SubtaskResult:
    return SubtaskResult(
        subtask_id=id_, attempt=attempt, status=status, output=f"out {id_}", self_confidence=0.9
    )


def verdict(id_: str, attempt: int = 1, accept: bool = True) -> ReviewVerdict:
    return ReviewVerdict(
        subtask_id=id_, attempt=attempt, accept=accept, score=5 if accept else 2, feedback="fix it"
    )


def state_with(plan: ExecutionPlan, **updates):  # type: ignore[no-untyped-def]
    s = initial_state("t1", "u1", "do the thing", TaskOptions())
    s["plan"] = plan
    s["plan_confidence"] = plan.confidence
    s.update(updates)  # type: ignore[typeddict-item]
    return s


# ---------- ExecutionPlan validation ----------


def test_plan_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ExecutionPlan(subtasks=[sub("A"), sub("A")], confidence=0.5)


def test_plan_rejects_unknown_and_self_dependency() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ExecutionPlan(subtasks=[sub("A", deps=["Z"])], confidence=0.5)
    with pytest.raises(ValueError, match="itself"):
        ExecutionPlan(subtasks=[sub("A", deps=["A"])], confidence=0.5)


def test_plan_rejects_cycles_and_orders_topologically() -> None:
    with pytest.raises(ValueError, match="cycle"):
        ExecutionPlan(subtasks=[sub("A", deps=["B"]), sub("B", deps=["A"])], confidence=0.5)
    assert plan_abc().order() == ["A", "B", "C"]


def test_planner_schema_hides_runtime_inputs() -> None:
    schema = ExecutionPlan.model_json_schema()
    assert "inputs" not in schema["$defs"]["Subtask"]["properties"]
    assert "needs" in schema["$defs"]["Subtask"]["properties"]


# ---------- edges ----------


def test_route_after_plan_thresholds_and_review_flag() -> None:
    route = make_route_after_plan(0.6)
    assert route(state_with(plan_abc())) == NODE_DISPATCH
    low = ExecutionPlan(subtasks=[sub("A")], confidence=0.3)
    assert route(state_with(low)) == NODE_APPROVE_PLAN
    s = state_with(plan_abc())
    s["options"] = TaskOptions(require_human_review=True)
    assert route(s) == NODE_APPROVE_PLAN
    no_plan = state_with(plan_abc())
    no_plan["plan"] = None
    assert route(no_plan) == NODE_ESCALATE


def test_ready_and_needs_review() -> None:
    plan = plan_abc()
    assert [s.id for s in ready_subtasks(plan, {}, {})] == ["A"]
    results = {"A": result("A")}
    assert needs_review(results, {}) == [results["A"]]
    verdicts = {"A": verdict("A")}
    assert needs_review(results, verdicts) == []
    assert [s.id for s in ready_subtasks(plan, results, verdicts)] == ["B"]
    # a retried result outranks the old verdict
    results["A"] = result("A", attempt=2)
    assert [r.subtask_id for r in needs_review(results, verdicts)] == ["A"]
    assert ready_subtasks(plan, results, verdicts) == []


def test_dispatch_fans_out_only_ready_subtasks() -> None:
    sends = route_dispatch(state_with(plan_abc()))
    assert isinstance(sends, list) and len(sends) == 1
    assert isinstance(sends[0], Send) and sends[0].node == "specialist_research"
    assert sends[0].arg["attempt"] == 1 and sends[0].arg["feedback"] is None
    two_parallel = ExecutionPlan(subtasks=[sub("A"), sub("B", "analysis")], confidence=0.8)
    assert {s.node for s in route_dispatch(state_with(two_parallel))} == {
        "specialist_research",
        "specialist_analysis",
    }


def test_review_routing_retries_then_escalates() -> None:
    route = make_route_after_review(max_retries=2)
    plan = plan_abc()
    rejected = state_with(
        plan,
        subtask_results={"A": result("A")},
        review_verdicts={"A": verdict("A", accept=False)},
        retry_counts={"A": 1},
    )
    out = route(rejected)
    assert isinstance(out, list) and out[0].node == "specialist_research"
    assert out[0].arg["attempt"] == 2 and out[0].arg["feedback"] == "fix it"
    exhausted = state_with(
        plan,
        subtask_results={"A": result("A", attempt=3)},
        review_verdicts={"A": verdict("A", attempt=3, accept=False)},
        retry_counts={"A": 3},
    )
    assert route(exhausted) == NODE_ESCALATE


def test_review_routing_dispatches_dependents_and_synthesizes_when_done() -> None:
    route = make_route_after_review(max_retries=2)
    plan = plan_abc()
    after_a = state_with(
        plan, subtask_results={"A": result("A")}, review_verdicts={"A": verdict("A")}
    )
    out = route(after_a)
    assert isinstance(out, list) and [s.node for s in out] == ["specialist_analysis"]
    assert out[0].arg["predecessor_outputs"] == {"A": "out A"}
    done = state_with(
        plan,
        subtask_results={k: result(k) for k in "ABC"},
        review_verdicts={k: verdict(k) for k in "ABC"},
    )
    assert route(done) == NODE_SYNTHESIZE


def test_review_routing_escalates_when_stuck() -> None:
    route = make_route_after_review(max_retries=2)
    plan = ExecutionPlan(subtasks=[sub("A"), sub("B", deps=["A"])], confidence=0.9)
    # A failed and was rejected 3 times -> escalate (B can never run)
    stuck = state_with(
        plan,
        subtask_results={"A": result("A", 3, SubtaskStatus.FAILED)},
        review_verdicts={"A": verdict("A", 3, accept=False)},
        retry_counts={"A": 3},
    )
    assert route(stuck) == NODE_ESCALATE
