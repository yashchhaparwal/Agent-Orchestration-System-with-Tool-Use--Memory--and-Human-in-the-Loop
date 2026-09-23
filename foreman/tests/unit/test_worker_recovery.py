"""A worker that starts after a crash re-enqueues only the tasks the crash left `running`."""

from __future__ import annotations

from pathlib import Path

from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import TaskStore
from packages.orchestrator.worker import recover_running_tasks
from packages.shared.types.task import TaskOptions, TaskStatus


def test_only_running_tasks_are_re_enqueued(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    create_all(engine)
    store = TaskStore(make_session_factory(engine))
    rows = {
        status: store.create_task(
            user_id="u", request=f"task {status.value}", options=TaskOptions()
        )
        for status in (
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.DONE,
        )
    }
    for status, row in rows.items():
        store.set_status(row.id, status)
    enqueued: list[str] = []

    recovered = recover_running_tasks(store, enqueued.append)

    assert recovered == enqueued == [rows[TaskStatus.RUNNING].id]
    assert recover_running_tasks(store, enqueued.append) == [
        rows[TaskStatus.RUNNING].id
    ]  # still running until a worker picks it up
