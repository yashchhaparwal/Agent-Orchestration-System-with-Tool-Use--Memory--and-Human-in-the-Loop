"""Escalation policy, approval lifecycle, and timeouts — and that nothing can auto-approve."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.hitl.timeouts import expire_due
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import ApprovalStore, TaskStore
from packages.shared.types.approval import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalTrigger,
    DecisionKind,
)
from packages.shared.types.task import TaskOptions


def test_repo_policy_loads_and_never_approves_on_timeout() -> None:
    policy = EscalationPolicy.load(Path("config/escalation.yaml"))
    assert policy.level_for(ApprovalTrigger.SENSITIVE_TOOL_CALL) == ApprovalLevel.L2
    assert policy.level_for(ApprovalTrigger.LOW_PLAN_CONFIDENCE) == ApprovalLevel.L3
    assert policy.level_for(ApprovalTrigger.REPEATED_FAILURE) == ApprovalLevel.L4
    assert policy.deadline(ApprovalLevel.L1) is None
    now = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert policy.deadline(ApprovalLevel.L2, now=now) == now + dt.timedelta(hours=24)
    for level in ApprovalLevel:
        decision = policy.timeout_decision(level)
        assert decision.decision == DecisionKind.REJECT and decision.decided_by == "timeout"


def test_policy_rejects_approve_on_timeout_in_config() -> None:
    with pytest.raises(ValueError):
        EscalationPolicy.model_validate(
            {"triggers": {"sensitive_tool_call": "L2"}, "on_timeout": {"L2": "approve"}}
        )


@pytest.fixture
def stores(tmp_path: Path) -> tuple[ApprovalStore, TaskStore]:
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    create_all(engine)
    sessions = make_session_factory(engine)
    return ApprovalStore(sessions), TaskStore(sessions)


def request(
    task_id: str, key: str = "k1", level: ApprovalLevel = ApprovalLevel.L2
) -> ApprovalRequest:
    return ApprovalRequest(
        key=f"{task_id}:{key}",
        kind=ApprovalKind.TOOL_CALL,
        level=level,
        trigger=ApprovalTrigger.SENSITIVE_TOOL_CALL,
        task_id=task_id,
        subtask_id="C",
        agent="writing",
        proposed_action={"tool": "actions_send_email", "arguments": {"to": "x@y.z"}},
        context={"request": "r"},
    )


def test_get_or_create_is_idempotent_and_decisions_are_single_shot(stores) -> None:  # type: ignore[no-untyped-def]
    approvals, tasks = stores
    task = tasks.create_task(user_id="u", request="r", options=TaskOptions())
    row, created = approvals.get_or_create(request(task.id), expires_at=None)
    again, created_again = approvals.get_or_create(request(task.id), expires_at=None)
    assert created and not created_again and row.id == again.id
    assert approvals.list(status="pending")[0]["proposed_action"]["tool"] == "actions_send_email"

    decided = approvals.record_decision(
        row.id, ApprovalDecision(decision=DecisionKind.APPROVE, decided_by="me")
    )
    assert decided is not None and decided.status == ApprovalStatus.APPROVED.value
    assert approvals.record_decision(row.id, ApprovalDecision(decision=DecisionKind.REJECT)) is None
    assert approvals.decision_of(row.id) == ApprovalDecision(
        decision=DecisionKind.APPROVE, payload={}, reason="", decided_by="me"
    )
    assert approvals.list(status="pending") == []


def test_expire_due_applies_the_timeout_decision(stores) -> None:  # type: ignore[no-untyped-def]
    approvals, tasks = stores
    task = tasks.create_task(user_id="u", request="r", options=TaskOptions())
    now = dt.datetime.now(dt.UTC)
    overdue, _ = approvals.get_or_create(
        request(task.id, "overdue"), expires_at=now - dt.timedelta(minutes=1)
    )
    fresh, _ = approvals.get_or_create(
        request(task.id, "fresh"), expires_at=now + dt.timedelta(hours=1)
    )
    never, _ = approvals.get_or_create(request(task.id, "never", ApprovalLevel.L1), expires_at=None)

    resumed = expire_due(approvals, EscalationPolicy.default(), now=now)
    assert resumed == [(task.id, overdue.id)]
    row = approvals.get(overdue.id)
    assert (
        row is not None
        and row.status == ApprovalStatus.EXPIRED.value
        and row.decided_by == "timeout"
    )
    assert row.decision == DecisionKind.REJECT.value
    assert (
        approvals.get(fresh.id).status == "pending" and approvals.get(never.id).status == "pending"
    )  # type: ignore[union-attr]
    assert expire_due(approvals, EscalationPolicy.default(), now=now) == []  # idempotent
