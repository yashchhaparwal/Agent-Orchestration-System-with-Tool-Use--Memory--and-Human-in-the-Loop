from __future__ import annotations

from typing import Any

from apps.api.services.queue import TaskQueue
from packages.orchestrator.memory.persistent import TaskStore
from packages.shared.types.task import TaskOptions


class TaskService:
    def __init__(self, store: TaskStore, queue: TaskQueue) -> None:
        self._store = store
        self._queue = queue

    def create(self, *, user_id: str, request: str, options: TaskOptions) -> dict[str, Any]:
        row = self._store.create_task(user_id=user_id, request=request, options=options)
        self._queue.enqueue(row.id)
        return {"task_id": row.id, "status": row.status}

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._store.task_view(task_id)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._store.list_tasks(limit=limit)
