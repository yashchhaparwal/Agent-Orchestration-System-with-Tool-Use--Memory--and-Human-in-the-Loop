from __future__ import annotations

from typing import Any

from apps.api.services.queue import TaskQueue
from packages.orchestrator.memory.persistent import ApprovalStore
from packages.shared.types.approval import ApprovalDecision


class ApprovalConflictError(Exception):
    """The request is not pending any more."""


class ApprovalService:
    def __init__(self, approvals: ApprovalStore, queue: TaskQueue) -> None:
        self._approvals = approvals
        self._queue = queue

    def list(self, *, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        return self._approvals.list(status=status, limit=limit)

    def get(self, approval_id: int) -> dict[str, Any] | None:
        row = self._approvals.get(approval_id)
        return ApprovalStore.view(row) if row else None

    def decide(self, approval_id: int, decision: ApprovalDecision) -> dict[str, Any] | None:
        """Record the decision and wake the worker. None if unknown; conflict if not pending."""
        if self._approvals.get(approval_id) is None:
            return None
        row = self._approvals.record_decision(approval_id, decision)
        if row is None:
            raise ApprovalConflictError(f"approval {approval_id} is not pending")
        self._queue.enqueue_resume(row.task_id, row.id)
        return ApprovalStore.view(row)
