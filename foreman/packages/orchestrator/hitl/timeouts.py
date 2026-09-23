"""Expire overdue approvals with the policy's timeout decision (never approve) and hand back the
(task, approval) pairs whose graphs must now be resumed."""

from __future__ import annotations

import datetime as dt

import structlog

from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.memory.persistent import ApprovalStore
from packages.shared.types.approval import ApprovalLevel, ApprovalStatus

log = structlog.get_logger(__name__)


def expire_due(
    store: ApprovalStore, policy: EscalationPolicy, *, now: dt.datetime | None = None
) -> list[tuple[str, int]]:
    """Returns ``[(task_id, approval_id), …]`` for every request that just expired."""
    moment = now or dt.datetime.now(dt.UTC)
    resumed: list[tuple[str, int]] = []
    for row in store.due(moment):
        decision = policy.timeout_decision(ApprovalLevel(row.level))
        updated = store.record_decision(row.id, decision, status=ApprovalStatus.EXPIRED)
        if updated is None:  # decided by a human in the meantime
            continue
        log.warning(
            "hitl.approval_expired",
            approval_id=row.id,
            task_id=row.task_id,
            level=row.level,
            decision=decision.decision.value,
        )
        resumed.append((row.task_id, row.id))
    return resumed
