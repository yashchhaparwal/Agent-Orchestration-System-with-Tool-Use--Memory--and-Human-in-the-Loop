"""Replay v1 on the fake graph: list checkpoints, fork after `plan` with an overridden plan into a
new task, run it, and diff the two trajectories."""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.serde import checkpoint_serde
from packages.orchestrator.tracing.replay import (
    apply_overrides,
    diff_views,
    list_checkpoints,
    parse_override,
    render_diff,
    replay,
)
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.task import TaskOptions
from tests.unit.test_graph_flow import PLAN_ABC, Scenario


def test_override_parsing_and_application() -> None:
    assert parse_override("options.require_human_review=true") == (
        "options.require_human_review",
        True,
    )
    assert parse_override("request=hello world") == ("request", "hello world")
    values = {"options": TaskOptions(), "plan": None, "request": "old"}
    out = apply_overrides(
        values, {"options.require_human_review": True, "plan": PLAN_ABC, "request": "new"}
    )
    assert out["options"].require_human_review is True and out["request"] == "new"
    assert isinstance(out["plan"], ExecutionPlan) and out["plan_confidence"] == 0.9
    assert values["request"] == "old"  # the input is not mutated


async def test_fork_after_plan_with_a_smaller_plan(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    saver = MemorySaver(serde=checkpoint_serde())
    final, source_id = await sc.run(checkpointer=saver)
    assert final["status"] == "done"
    graph = build_graph(sc.deps, checkpointer=saver)

    cps = await list_checkpoints(graph, source_id)
    assert cps[0]["source"] == "input" and cps[0]["step"] == -1
    after_plan = next(cp for cp in cps if cp["produced_by"] == ["plan"])
    assert after_plan["next"] and after_plan["subtasks_done"] == []
    assert any(cp["subtasks_done"] == ["A", "B", "C"] for cp in cps)

    smaller = {**PLAN_ABC, "subtasks": [PLAN_ABC["subtasks"][0]], "confidence": 0.95}
    calls_before = len(sc.calls)
    result = await replay(
        graph,
        store=sc.store,
        approvals=sc.approvals,
        source_task_id=source_id,
        checkpoint_id=after_plan["checkpoint_id"],
        overrides={"plan": smaller},
    )
    fork_id = result["new_task_id"]
    assert fork_id != source_id and result["as_node"] == "plan"
    assert result["final"]["status"] == "done"
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C", "A"]  # only A re-ran on the fork
    assert (
        sum(1 for c in sc.calls[calls_before:] if c["schema"] == "ExecutionPlan") == 0
    )  # no re-planning

    source_view, fork_view = sc.store.task_view(source_id), sc.store.task_view(fork_id)
    assert fork_view is not None and source_view is not None
    assert (
        fork_view["options"]["replay_of"] == source_id and fork_view["plan"]["confidence"] == 0.95
    )
    assert [s["id"] for s in fork_view["subtasks"]] == ["A"] and fork_view["status"] == "done"
    assert sc.store.task_view(source_id)["status"] == "done"  # type: ignore[index] — untouched

    diff = diff_views(source_view, fork_view)
    assert (
        diff["subtasks"]["A"]["change"] == "same" and diff["subtasks"]["B"]["change"] == "removed"
    )
    assert diff["status"] == ["done", "done"] and diff["llm_calls"][1] < diff["llm_calls"][0]
    md = render_diff(diff)
    assert "| B | removed |" in md and fork_id in md
    json.dumps(diff)  # serialisable for the API
