"""Two hard rules found by the Phase 6 smoke eval: an empty completed result is rejected before
any model review, and a placeholder deliverable is retried once and then fails loudly."""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_graph_flow import Scenario


async def test_empty_completed_result_is_rejected_by_rule_not_by_a_model(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, empty_first_attempt=("A",))
    final, task_id = await sc.run()
    assert final["status"] == "done", final.get("error")
    assert [c for c in sc.specialist_calls if c[1] == "A"] == [
        ("research", "A", 1),
        ("research", "A", 2),
    ]
    reviewer_calls = [c for c in sc.calls if c["role"] == "reviewer"]
    assert len(reviewer_calls) == 3  # A attempt 2, B, C — never the empty attempt
    assert final["retry_counts"] == {"A": 1}
    verdict = final["review_verdicts"]["A"]
    assert verdict.accept and verdict.attempt == 2
    second_call = [
        c
        for c in sc.calls
        if c["role"] == "specialist" and "## Subtask A" in c["messages"][1]["content"]
    ][1]
    assert (
        "empty" in second_call["messages"][1]["content"].lower()
    )  # the feedback names the problem


async def test_degenerate_synthesis_is_retried_once(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, short_synthesis=1, pad_outputs=300)
    final, _ = await sc.run()
    assert final["status"] == "done", final.get("error")
    synth_calls = [c for c in sc.calls if c["schema"] == "Deliverable"]
    assert len(synth_calls) == 2
    assert "has no body" in synth_calls[1]["messages"][-1]["content"]
    assert len(final["final_output"].body) > 200 and final["final_output"].body != "final"


async def test_synthesis_fails_loudly_when_the_body_stays_empty(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, short_synthesis=5, pad_outputs=300)
    final, task_id = await sc.run()
    assert final["status"] == "failed" and "empty twice" in (final.get("error") or "")
    assert final.get("final_output") is None
    assert len([c for c in sc.calls if c["schema"] == "Deliverable"]) == 2
    assert sc.store.task_view(task_id)["status"] == "failed"  # type: ignore[index]
