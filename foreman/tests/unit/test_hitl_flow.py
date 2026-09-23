"""Phase 4 decision matrix through the whole graph (fakes + MemorySaver):

L2 (a destructive tool call)   approve · modify · reject · take over · worker restart while paused
L3 (plan approval)             approve · modify · reject · take over
L4 (escalation)                approve=retry · modify=human result · reject=cancel · take over
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from packages.orchestrator.graph.serde import checkpoint_serde
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from packages.shared.types.task import TaskOptions
from tests.unit.test_graph_flow import PLAN_ABC, Scenario, interrupted

EMAIL = ("C",)


def d(kind: DecisionKind, **payload: Any) -> ApprovalDecision:
    reason = payload.pop("reason", "because")
    return ApprovalDecision(decision=kind, payload=payload, reason=reason, decided_by="tester")


# ---------------------------------------------------------------- L2: tool call


async def test_l2_pause_has_everything_a_reviewer_needs(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    final, task_id = await sc.run(checkpointer=saver)
    value = interrupted(final)
    assert value["kind"] == "tool_call" and value["level"] == "L2" and value["subtask_id"] == "C"
    assert value["proposed_action"]["tool"] == "actions_send_email"
    assert value["proposed_action"]["arguments"]["to"] == "lender@example.test"
    assert value["proposed_action"]["risk"] == "destructive"
    ctx = value["context"]
    assert ctx["request"] == "summarise and draft"
    assert {p["id"]: p["status"] for p in ctx["plan"]} == {
        "A": "accepted",
        "B": "accepted",
        "C": "pending",
    }
    assert [c["id"] for c in ctx["completed_subtasks"]] == ["A", "B"]
    pending = sc.pending()
    assert (
        pending is not None and pending["id"] == value["approval_id"] and pending["level"] == "L2"
    )
    assert sc.registry.invoked == []  # nothing executed while a human decides
    view = sc.store.task_view(task_id)  # progress is visible while paused, not only at the end
    assert view is not None and {s["id"]: s["status"] for s in view["subtasks"]} == {
        "A": "accepted",
        "B": "accepted",
        "C": "planned",
    }
    assert view["llm_calls"] == 1 + 2 + 2  # plan + A, B + their reviews; C is mid-loop
    # re-running the paused graph (a worker restart with no decision) is idempotent: same approval, still paused
    again, _ = await sc.run(checkpointer=saver, resume=True, thread=task_id)
    assert interrupted(again)["approval_id"] == value["approval_id"]
    assert len(sc.approvals.list(status=None)) == 1 and sc.notified == [pending["id"]]


async def test_l2_approve_executes_the_call_and_finishes(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(task_id, d(DecisionKind.APPROVE), checkpointer=saver)

    assert final["status"] == "done", final.get("error")
    assert [c.name for c in sc.registry.invoked] == ["actions_send_email"]
    result = final["subtask_results"]["C"]
    assert "queued_for_human" in result.output and result.attempt == 1
    assert [c[1] for c in sc.specialist_calls] == [
        "A",
        "B",
        "C",
    ]  # C's loop was resumed, not restarted
    events = [e for e in final["tool_events"] if e.tool == "actions_send_email"]
    assert len(events) == 1 and events[0].decision.value == "approve" and events[0].ok is True
    assert "approve by tester" in events[0].reason
    assert not any(final["pending_approvals"].values()) and not any(
        final["approval_decisions"].values()
    )
    view = sc.store.task_view(task_id)
    assert view is not None and view["status"] == "done"
    assert view["approvals"][0]["status"] == "approved" and view["pending_approval_id"] is None
    assert view["tool_calls"] == 1 and view["tool_calls_not_executed"] == 0


async def test_l2_modify_runs_with_the_edited_arguments(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    edited = {
        "to": "complaints@lender.example.test",
        "subject": "Complaint C",
        "body": "Dear lender",
    }
    final = await sc.decide(task_id, d(DecisionKind.MODIFY, arguments=edited), checkpointer=saver)
    assert final["status"] == "done"
    assert sc.registry.invoked[0].arguments == edited


async def test_l2_modify_with_bad_arguments_does_not_execute(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id, d(DecisionKind.MODIFY, arguments={"to": 42}), checkpointer=saver
    )
    assert final["status"] == "done" and sc.registry.invoked == []
    tool_msg = [
        m
        for c in sc.calls
        if c["role"] == "specialist"
        for m in c["messages"]
        if m.get("role") == "tool" and m.get("name") == "actions_send_email"
    ]
    assert tool_msg and "do not match the tool schema" in tool_msg[-1]["content"]


async def test_l2_reject_tells_the_agent_why_and_it_carries_on(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id, d(DecisionKind.REJECT, reason="drafts only, never send"), checkpointer=saver
    )
    assert final["status"] == "done"
    assert sc.registry.invoked == []
    assert "rejected by the human reviewer: drafts only" in final["subtask_results"]["C"].output
    view = sc.store.task_view(task_id)
    assert view is not None and view["approvals"][0]["status"] == "rejected"
    assert view["tool_calls"] == 1 and view["tool_calls_not_executed"] == 1


async def test_l2_take_over_replaces_the_subtask_output_and_skips_review(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    reviews_before = sum(1 for c in sc.calls if c["role"] == "reviewer")
    final = await sc.decide(
        task_id, d(DecisionKind.TAKE_OVER, output="Letter written by a person"), checkpointer=saver
    )
    assert final["status"] == "done"
    result = final["subtask_results"]["C"]
    assert result.human_authored and result.output == "Letter written by a person"
    verdict = final["review_verdicts"]["C"]
    assert verdict.accept and verdict.reviewer_model == "human"
    assert (
        sum(1 for c in sc.calls if c["role"] == "reviewer") == reviews_before
    )  # no model review of a human result
    assert "Letter written by a person" in final["final_output"].body


async def test_l2_survives_a_worker_restart_while_paused(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    calls_before = len(sc.calls)
    # A "new worker": fresh graph instance over the same checkpointer, decision recorded meanwhile.
    final = await sc.decide(task_id, d(DecisionKind.APPROVE), checkpointer=saver)
    assert final["status"] == "done"
    new_specialist_calls = [c for c in sc.calls[calls_before:] if c["role"] == "specialist"]
    assert (
        len(new_specialist_calls) == 1
    )  # exactly one more model call: C's next turn — nothing replayed
    assert "## Subtask C" in new_specialist_calls[0]["messages"][1]["content"]


# ---------------------------------------------------------------- L3: plan approval


async def test_l3_approve_then_the_plan_runs(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.3})
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(task_id, d(DecisionKind.APPROVE), checkpointer=saver)
    assert final["status"] == "done" and [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]


async def test_l3_modify_replaces_the_plan(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.3})
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    smaller = {**PLAN_ABC, "subtasks": [PLAN_ABC["subtasks"][0]], "confidence": 0.95}
    final = await sc.decide(task_id, d(DecisionKind.MODIFY, plan=smaller), checkpointer=saver)
    assert final["status"] == "done" and [c[1] for c in sc.specialist_calls] == ["A"]
    view = sc.store.task_view(task_id)
    assert (
        view is not None
        and view["plan"]["confidence"] == 0.95
        and [s["id"] for s in view["subtasks"]] == ["A"]
    )


async def test_l3_invalid_modification_cancels_rather_than_guessing(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.3})
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id, d(DecisionKind.MODIFY, plan={"subtasks": []}), checkpointer=saver
    )
    assert final["status"] == "cancelled" and "invalid" in final["error"]
    assert sc.specialist_calls == []


async def test_l3_reject_cancels(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.3})
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id, d(DecisionKind.REJECT, reason="not worth doing"), checkpointer=saver
    )
    assert final["status"] == "cancelled" and "not worth doing" in final["error"]
    view = sc.store.task_view(task_id)
    assert view is not None and view["status"] == "cancelled" and view["final_output"] is None


async def test_l3_take_over_delivers_the_human_text_without_running_agents(tmp_path: Path) -> None:
    sc = (
        Scenario(tmp_path, options_plan_confidence=None)
        if False
        else Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.3})
    )
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id,
        d(DecisionKind.TAKE_OVER, title="Done by hand", body="The whole answer."),
        checkpointer=saver,
    )
    assert final["status"] == "done" and sc.specialist_calls == []
    assert final["final_output"].human_authored and final["final_output"].title == "Done by hand"
    view = sc.store.task_view(task_id)
    assert (
        view is not None
        and view["status"] == "done"
        and view["final_output"]["human_authored"] is True
    )


async def test_l3_via_require_human_review_option(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver, options=TaskOptions(require_human_review=True))
    final = await sc.decide(task_id, d(DecisionKind.APPROVE), checkpointer=saver)
    assert final["status"] == "done"


# ---------------------------------------------------------------- L4: escalation


def always_reject(sid: str, attempt: int) -> dict[str, Any]:
    return {"accept": False, "score": 1, "issues": ["wrong"], "feedback": "redo"}


async def test_l4_approve_grants_another_round(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, reviewer_script=always_reject)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    assert (
        interrupted((await sc.run(checkpointer=saver, resume=True, thread=task_id))[0])["level"]
        == "L4"
    )
    sc.reviewer_script = lambda sid, attempt: {
        "accept": True,
        "score": 4,
        "issues": [],
        "feedback": "",
    }
    final = await sc.decide(task_id, d(DecisionKind.APPROVE), checkpointer=saver)
    assert final["status"] == "done"
    assert [c[2] for c in sc.specialist_calls if c[1] == "A"] == [
        1,
        2,
        3,
        4,
    ]  # one more attempt after the human said retry


async def test_l4_modify_accepts_a_human_written_subtask(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, reviewer_script=always_reject)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    sc.reviewer_script = lambda sid, attempt: {
        "accept": True,
        "score": 5,
        "issues": [],
        "feedback": "",
    }
    final = await sc.decide(
        task_id,
        d(DecisionKind.MODIFY, subtask_id="A", output="A, written by a person"),
        checkpointer=saver,
    )
    assert final["status"] == "done"
    a = final["subtask_results"]["A"]
    assert a.human_authored and a.output == "A, written by a person" and a.attempt == 4
    assert final["review_verdicts"]["A"].reviewer_model == "human"
    assert final["subtask_results"]["B"].output == "out B (with A)"


async def test_l4_take_over_and_reject(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, reviewer_script=always_reject)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id,
        d(DecisionKind.TAKE_OVER, title="Manual", body="Human deliverable"),
        checkpointer=saver,
    )
    assert final["status"] == "done" and final["final_output"].human_authored

    sc2 = Scenario(tmp_path / "second", reviewer_script=always_reject)
    saver2 = MemorySaver(serde=checkpoint_serde())
    _, task_id2 = await sc2.run(checkpointer=saver2)
    final2 = await sc2.decide(
        task_id2, d(DecisionKind.REJECT, reason="give up"), checkpointer=saver2
    )
    assert final2["status"] == "cancelled" and "give up" in final2["error"]
    assert sc2.store.task_view(task_id2)["status"] == "cancelled"  # type: ignore[index]


async def test_timeout_decision_on_l2_denies_the_call(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, email_subtasks=EMAIL)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    timeout = sc.deps.policy.timeout_decision(
        sc.deps.policy.level_for(sc.deps.policy.triggers.__iter__().__next__())
    )
    assert timeout.decision == DecisionKind.REJECT and timeout.decided_by == "timeout"
    final = await sc.decide(task_id, timeout, checkpointer=saver)
    assert final["status"] == "done" and sc.registry.invoked == []
    assert "rejected by the human reviewer" in json.dumps(final["subtask_results"]["C"].output)


async def test_l2_rejection_survives_a_review_retry(tmp_path: Path) -> None:
    """The reviewer rejects the 'not sent' result once; the retried loop must not ask again."""

    def reviewer(sid: str, attempt: int) -> dict[str, Any]:
        if sid == "C" and attempt == 1:
            return {
                "accept": False,
                "score": 2,
                "issues": ["letter not sent"],
                "feedback": "send it",
            }
        return {"accept": True, "score": 5, "issues": [], "feedback": ""}

    sc = Scenario(tmp_path, email_subtasks=EMAIL, reviewer_script=reviewer)
    saver = MemorySaver(serde=checkpoint_serde())
    _, task_id = await sc.run(checkpointer=saver)
    final = await sc.decide(
        task_id, d(DecisionKind.REJECT, reason="drafts only"), checkpointer=saver
    )
    assert final["status"] == "done", final.get("error")
    assert len(sc.approvals.list(status=None)) == 1  # the retry did not ask again
    result = final["subtask_results"]["C"]
    assert result.attempt == 2 and result.denied_tools == {"actions_send_email": "drafts only"}
    assert sc.registry.invoked == []
    kinds = [
        (e.decision.value, e.reason[:22])
        for e in final["tool_events"]
        if e.tool == "actions_send_email"
    ]
    assert kinds == [("approve", "rejected by tester: dr"), ("block", "a human already reject")]
    second_review = [
        c
        for c in sc.calls
        if c["role"] == "reviewer" and '"attempt": 2' in c["messages"][1]["content"]
    ]
    assert second_review and "## Human decisions" in second_review[-1]["messages"][1]["content"]
