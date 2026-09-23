from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.services.queue import InMemoryQueue
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import ApprovalStore, OutboxRepository, TaskStore
from packages.shared.config import Settings
from packages.shared.types.memory import StoredMemory

HEADERS = {"X-API-Key": "secret"}


class FakeStore:
    def __init__(self) -> None:
        self.rows = {
            "u_42": [
                StoredMemory(
                    id="m1",
                    user_id="u_42",
                    text="drafts only",
                    task_type="complaint_letter",
                    outcome="decision",
                    importance=5,
                    effective_importance=4.2,
                    created_at="2026-08-01T00:00:00+00:00",
                    last_accessed="2026-08-20T00:00:00+00:00",
                    access_count=3,
                    tools_used=["actions_send_email"],
                )
            ]
        }

    def list_user(self, user_id: str) -> list[StoredMemory]:
        return list(self.rows.get(user_id, []))

    def delete_user(self, user_id: str) -> int:
        return len(self.rows.pop(user_id, []))

    def users(self) -> list[dict[str, Any]]:
        return [{"user_id": u, "count": len(rows)} for u, rows in self.rows.items()]


def make_client(tmp_path: Path, **kw: object) -> TestClient:
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    create_all(engine)
    sessions = make_session_factory(engine)
    app = create_app(
        Settings(_env_file=None, api_key="secret"),  # type: ignore[call-arg]
        store=TaskStore(sessions),
        approvals_store=ApprovalStore(sessions),
        outbox=OutboxRepository(sessions),
        queue=InMemoryQueue(),
        **kw,  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_list_and_delete(tmp_path: Path) -> None:
    store = FakeStore()
    c = make_client(tmp_path, long_term=store)
    assert c.get("/v1/memory/users", headers=HEADERS).json() == [{"user_id": "u_42", "count": 1}]
    rows = c.get("/v1/memory/users/u_42", headers=HEADERS).json()
    assert [r["id"] for r in rows] == ["m1"] and rows[0]["effective_importance"] == 4.2
    assert c.get("/v1/memory/users/u_42").status_code == 401
    assert c.get("/v1/memory/users/nobody", headers=HEADERS).json() == []
    r = c.delete("/v1/memory/users/u_42", headers=HEADERS)
    assert r.status_code == 200 and r.json() == {"user_id": "u_42", "deleted": 1}
    assert c.get("/v1/memory/users/u_42", headers=HEADERS).json() == []
    assert c.delete("/v1/memory/users/u_42", headers=HEADERS).json()["deleted"] == 0


def test_unavailable_store_is_a_503_not_a_crash(tmp_path: Path) -> None:
    c = make_client(tmp_path, memory_enabled=False)
    r = c.get("/v1/memory/users/u_42", headers=HEADERS)
    assert r.status_code == 503 and "not available" in r.json()["detail"]
    assert c.get("/health").status_code == 200
