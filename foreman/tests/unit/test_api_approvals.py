from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.services.queue import InMemoryQueue
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import ApprovalStore, OutboxRepository, TaskStore
from packages.shared.config import Settings
from packages.shared.types.approval import (
    ApprovalKind,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalTrigger,
)
from packages.shared.types.task import TaskOptions

HEADERS = {"X-API-Key": "secret"}


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    create_all(engine)
    sessions = make_session_factory(engine)
    store, approvals, outbox = (
        TaskStore(sessions),
        ApprovalStore(sessions),
        OutboxRepository(sessions),
    )
    queue = InMemoryQueue()
    app = create_app(
        Settings(_env_file=None, api_key="secret"),  # type: ignore[call-arg]
        store=store,
        approvals_store=approvals,
        outbox=outbox,
        queue=queue,
    )
    task = store.create_task(user_id="u_42", request="send the letter", options=TaskOptions())
    row, _ = approvals.get_or_create(
        ApprovalRequest(
            key=f"{task.id}:tool_call:C:1:abc",
            kind=ApprovalKind.TOOL_CALL,
            level=ApprovalLevel.L2,
            trigger=ApprovalTrigger.SENSITIVE_TOOL_CALL,
            task_id=task.id,
            subtask_id="C",
            agent="writing",
            proposed_action={"tool": "actions_send_email", "arguments": {"to": "a@b.co"}},
            context={"request": "send the letter"},
        ),
        expires_at=None,
    )
    return TestClient(app, raise_server_exceptions=False), queue, task.id, row.id


def test_list_and_get(env) -> None:  # type: ignore[no-untyped-def]
    c, _, task_id, approval_id = env
    rows = c.get("/v1/approvals", headers=HEADERS).json()
    assert [r["id"] for r in rows] == [approval_id] and rows[0]["status"] == "pending"
    one = c.get(f"/v1/approvals/{approval_id}", headers=HEADERS).json()
    assert one["task_id"] == task_id and one["proposed_action"]["tool"] == "actions_send_email"
    assert c.get("/v1/approvals/999", headers=HEADERS).status_code == 404
    assert c.get("/v1/approvals").status_code == 401


def test_decide_records_and_wakes_the_worker_once(env) -> None:  # type: ignore[no-untyped-def]
    c, queue, task_id, approval_id = env
    r = c.post(f"/v1/approvals/{approval_id}/decide", json={"decision": "reject"}, headers=HEADERS)
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    r = c.post(
        f"/v1/approvals/{approval_id}/decide",
        json={
            "decision": "modify",
            "payload": {"arguments": {"to": "z@b.co"}},
            "reason": "fix",
            "decided_by": "sam",
        },
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "modified" and r.json()["decided_by"] == "sam"
    assert queue.resumes == [(task_id, approval_id)]
    r = c.post(f"/v1/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=HEADERS)
    assert r.status_code == 409 and queue.resumes == [(task_id, approval_id)]
    assert c.get("/v1/approvals", headers=HEADERS).json() == []
    assert c.get("/v1/approvals?status=modified", headers=HEADERS).json()[0]["id"] == approval_id
    view = c.get(f"/v1/tasks/{task_id}", headers=HEADERS).json()
    assert view["approvals"][0]["status"] == "modified" and view["pending_approval_id"] is None


def test_outbox_endpoint(env) -> None:  # type: ignore[no-untyped-def]
    c, _, _, _ = env
    assert c.get("/v1/outbox", headers=HEADERS).json() == []


def test_list_tasks_newest_first(env) -> None:  # type: ignore[no-untyped-def]
    c, _, task_id, _ = env
    rows = c.get("/v1/tasks?limit=5", headers=HEADERS).json()
    assert [r["task_id"] for r in rows] == [task_id]
    assert rows[0]["status"] == "queued" and rows[0]["request"] == "send the letter"
    assert c.get("/v1/tasks").status_code == 401
