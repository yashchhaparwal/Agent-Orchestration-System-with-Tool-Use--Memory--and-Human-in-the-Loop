from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.services.queue import InMemoryQueue
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import TaskStore
from packages.shared.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, InMemoryQueue, TaskStore]:
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    create_all(engine)
    store = TaskStore(make_session_factory(engine))
    queue = InMemoryQueue()
    settings = Settings(_env_file=None, api_key="secret")  # type: ignore[call-arg]
    app = create_app(settings, store=store, queue=queue)
    return TestClient(app, raise_server_exceptions=False), queue, store


def test_health_is_public(client: tuple[TestClient, InMemoryQueue, TaskStore]) -> None:
    c, _, _ = client
    assert c.get("/health").json() == {"status": "ok"}


def test_requires_api_key(client: tuple[TestClient, InMemoryQueue, TaskStore]) -> None:
    c, _, _ = client
    r = c.post("/v1/tasks", json={"request": "x"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_create_then_get_task(client: tuple[TestClient, InMemoryQueue, TaskStore]) -> None:
    c, queue, store = client
    headers = {"X-API-Key": "secret"}
    r = c.post(
        "/v1/tasks",
        json={"request": "summarise claim CLM-4471", "user_id": "u_42"},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    assert r.json()["status"] == "queued"
    assert queue.items == [task_id]

    view = c.get(f"/v1/tasks/{task_id}", headers=headers).json()
    assert view["status"] == "queued" and view["request"].startswith("summarise")
    assert view["subtasks"] == [] and view["final_output"] is None

    row = store.get_task(task_id)
    assert row is not None and row.user_id == "u_42"


def test_validation_and_not_found_are_problem_details(
    client: tuple[TestClient, InMemoryQueue, TaskStore],
) -> None:
    c, _, _ = client
    headers = {"X-API-Key": "secret"}
    r = c.post("/v1/tasks", json={"request": ""}, headers=headers)
    assert r.status_code == 422 and r.json()["title"] == "Validation failed"
    r = c.get("/v1/tasks/nope", headers=headers)
    assert r.status_code == 404 and "not found" in r.json()["detail"]
