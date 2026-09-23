from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.services.queue import InMemoryQueue
from apps.api.services.trace_service import build_tree, classify
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import ApprovalStore, OutboxRepository, TaskStore
from packages.shared.config import Settings
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import GateAction, ToolEvent
from packages.shared.types.subtask import SubtaskResult, SubtaskStatus
from packages.shared.types.task import TaskOptions, TaskStatus
from packages.shared.types.tools import RiskClass

HEADERS = {"X-API-Key": "secret"}

JAEGER = [
    {
        "traceID": "t1",
        "spans": [
            {
                "traceID": "t1",
                "spanID": "root",
                "operationName": "task",
                "references": [],
                "startTime": 1_000,
                "duration": 5_000,
                "tags": [
                    {"key": "task_id", "value": "X"},
                    {"key": "otel.scope.name", "value": "foreman"},
                ],
            },
            {
                "traceID": "t1",
                "spanID": "n1",
                "operationName": "node.plan",
                "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                "startTime": 1_100,
                "duration": 2_000,
                "tags": [],
            },
            {
                "traceID": "t1",
                "spanID": "l1",
                "operationName": "llm.call",
                "references": [{"refType": "CHILD_OF", "spanID": "n1"}],
                "startTime": 1_200,
                "duration": 1_500,
                "tags": [{"key": "model", "value": "m"}, {"key": "error", "value": "429"}],
            },
            {
                "traceID": "t1",
                "spanID": "m1",
                "operationName": "MCP send tools/list",
                "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                "startTime": 3_500,
                "duration": 10,
                "tags": [],
            },
        ],
    }
]


def test_build_tree_orders_depth_first_and_classifies() -> None:
    tree = build_tree(JAEGER)
    assert [(s["name"], s["depth"]) for s in tree] == [
        ("task", 0),
        ("node.plan", 1),
        ("llm.call", 2),
        ("MCP send tools/list", 1),
    ]
    assert (
        tree[2]["error"]
        and tree[2]["attrs"] == {"model": "m", "error": "429"}
        and tree[2]["offset_us"] == 200
    )
    assert "otel.scope.name" not in tree[0]["attrs"]
    assert (
        classify("gate.decide") == "gate"
        and classify("memory.recall") == "memory"
        and classify("agent.research.iteration") == "agent"
    )
    assert build_tree([]) == []


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    create_all(engine)
    sessions = make_session_factory(engine)
    store = TaskStore(sessions)
    queue = InMemoryQueue()
    fetch_mode = {"mode": "ok"}

    def fetch(task_id: str) -> list[dict[str, Any]]:
        if fetch_mode["mode"] == "down":
            raise ValueError("jaeger down")
        return JAEGER

    app = create_app(
        Settings(_env_file=None, api_key="secret", evals_reports_dir=tmp_path / "reports"),  # type: ignore[call-arg]
        store=store,
        approvals_store=ApprovalStore(sessions),
        outbox=OutboxRepository(sessions),
        queue=queue,
        memory_enabled=False,
        trace_fetch=fetch,
    )
    done = store.create_task(user_id="u", request="r", options=TaskOptions())
    store.record_progress(
        done.id,
        results={
            "A": SubtaskResult(
                subtask_id="A",
                status=SubtaskStatus.COMPLETED,
                output="x",
                sources=[],
                self_confidence=0.9,
            )
        },
        verdicts={},
        cost_entries=[
            CostEntry(
                provider="mistral",
                model="m",
                role="specialist",
                input_tokens=10,
                output_tokens=5,
                latency_ms=300,
            )
        ],
        tool_events=[
            ToolEvent(
                subtask_id="A",
                agent="research",
                tool="db_query",
                args_hash="h",
                risk=RiskClass.SAFE,
                decision=GateAction.ALLOW,
                ok=True,
                latency_ms=20,
            ),
            ToolEvent(
                subtask_id="A",
                agent="writing",
                tool="actions_send_email",
                args_hash="h",
                risk=RiskClass.DESTRUCTIVE,
                decision=GateAction.ALLOW,
                ok=True,
            ),  # must never happen
        ],
    )
    store.set_status(done.id, TaskStatus.DONE)
    failed = store.create_task(user_id="u", request="r2", options=TaskOptions())
    store.set_status(failed.id, TaskStatus.FAILED, error="x")
    return TestClient(app, raise_server_exceptions=False), store, queue, done.id, fetch_mode


def test_stats_endpoint(env) -> None:  # type: ignore[no-untyped-def]
    c, _, _, _, _ = env
    assert c.get("/v1/stats").status_code == 401
    s = c.get("/v1/stats?days=7", headers=HEADERS).json()
    assert s["tasks"]["total"] == 2 and s["tasks"]["success_rate"] == 0.5
    assert s["tasks"]["by_status"] == [
        {"status": "done", "count": 1},
        {"status": "failed", "count": 1},
    ]
    assert s["tasks"]["mean_llm_calls"] == 1.0 and s["tasks"]["mean_tokens"] == 15.0
    assert s["tools"]["total"] == 2 and s["tools"]["by_tool"][0]["count"] == 1
    assert s["safety"]["unapproved_destructive_actions"] == 1  # the planted violation is counted
    assert s["approvals"]["total"] == 0 and s["approvals"]["escalation_rate"] == 0.0
    assert s["last_eval"] is None and s["tasks"]["latency_p50_s"] is not None


def test_trace_from_jaeger_then_ledger_fallback(env) -> None:  # type: ignore[no-untyped-def]
    c, _, _, task_id, fetch_mode = env
    tr = c.get(f"/v1/tasks/{task_id}/trace", headers=HEADERS).json()
    assert tr["source"] == "jaeger" and tr["span_count"] == 4 and tr["spans"][0]["name"] == "task"
    fetch_mode["mode"] = "down"
    tr = c.get(f"/v1/tasks/{task_id}/trace", headers=HEADERS).json()
    assert tr["source"] == "ledger" and tr["span_count"] >= 3
    assert {s["name"] for s in tr["spans"] if s["kind"] in ("llm", "tool")} == {
        "llm.call specialist",
        "tool.db_query",
        "tool.actions_send_email",
    }
    assert c.get("/v1/tasks/nope/trace", headers=HEADERS).status_code == 404


def test_replay_request_and_diff(env) -> None:  # type: ignore[no-untyped-def]
    c, store, queue, task_id, _ = env
    r = c.post(
        f"/v1/tasks/{task_id}/replay",
        json={
            "checkpoint_id": "1f1a2dd4-1009-6163",
            "overrides": {"options.require_human_review": True},
        },
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    fork_id = r.json()["task_id"]
    assert queue.replays == [
        (fork_id, task_id, "1f1a2dd4-1009-6163", {"options.require_human_review": True})
    ]
    fork = store.task_view(fork_id)
    assert (
        fork is not None and fork["options"]["replay_of"] == task_id and fork["status"] == "queued"
    )
    d = c.get(f"/v1/tasks/{fork_id}/diff", headers=HEADERS).json()
    assert (
        d["source_task_id"] == task_id
        and d["fork_task_id"] == fork_id
        and d["subtasks"]["A"]["change"] == "removed"
    )
    assert (
        c.get(f"/v1/tasks/{task_id}/diff", headers=HEADERS).json()["error"]
        == "this task is not a replay"
    )
    assert (
        c.post(
            "/v1/tasks/nope/replay", json={"checkpoint_id": "1f1a2dd4-1009-6163"}, headers=HEADERS
        ).status_code
        == 404
    )
