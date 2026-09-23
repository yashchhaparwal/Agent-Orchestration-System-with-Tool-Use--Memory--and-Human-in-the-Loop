"""The operator console renders every page headlessly against a fake API client and posts
decisions with the operator name. Uses Streamlit's AppTest, so no browser and no server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import apps.review_ui.api_client as api_client
import packages.shared.config as config
from packages.shared.config import Settings

ROOT = Path(__file__).resolve().parents[2]

APPROVAL: dict[str, Any] = {
    "id": 7,
    "task_id": "t-1234567890",
    "subtask_id": "D",
    "attempt": 1,
    "kind": "tool_call",
    "level": "L2",
    "trigger": "sensitive_tool_call",
    "agent": "writing",
    "proposed_action": {
        "tool": "actions_send_email",
        "risk": "destructive",
        "gate_reason": "destructive tools always need a human",
        "arguments": {"to": "lender@example.test", "subject": "Complaint", "body": "Dear lender"},
    },
    "reasoning": "the request asked to send it",
    "context": {
        "request": "send the letter",
        "plan": [
            {"id": "A", "specialist": "research", "description": "loans", "status": "accepted"},
            {"id": "D", "specialist": "writing", "description": "send", "status": "pending"},
        ],
        "completed_subtasks": [
            {
                "id": "A",
                "attempt": 1,
                "status": "accepted",
                "score": 5,
                "output_preview": "3 loans",
                "sources": ["db"],
            }
        ],
    },
    "status": "pending",
    "decision": None,
    "decision_payload": None,
    "decided_by": None,
    "reason": None,
    "created_at": "2026-08-28T10:00:00+00:00",
    "decided_at": None,
    "expires_at": "2026-08-29T10:00:00+00:00",
}
TASK: dict[str, Any] = {
    "task_id": "t-1234567890",
    "user_id": "u_42",
    "request": "send the letter",
    "status": "awaiting_approval",
    "error": None,
    "plan": {
        "confidence": 0.9,
        "sensitive_actions": ["send email"],
        "subtasks": [
            {"id": "A", "specialist": "research", "depends_on": [], "description": "loans"}
        ],
    },
    "subtasks": [
        {
            "id": "A",
            "specialist": "research",
            "attempt": 1,
            "status": "accepted",
            "result": {
                "output": "3 loans",
                "sources": ["db"],
                "tools_used": ["db_query"],
                "human_authored": False,
            },
            "verdict": {
                "accept": True,
                "score": 5,
                "issues": [],
                "feedback": "",
                "reviewer_model": "m",
            },
        }
    ],
    "approvals": [
        {
            "id": 7,
            "kind": "tool_call",
            "level": "L2",
            "status": "pending",
            "subtask_id": "D",
            "created_at": "2026-08-28T10:00:00+00:00",
        }
    ],
    "pending_approval_id": 7,
    "final_output": None,
    "llm_calls": 4,
    "tokens": 1234,
    "tool_calls": 2,
    "tool_calls_not_executed": 0,
    "cost_usd": None,
}
MEMORIES = [
    {
        "id": "m1",
        "user_id": "u_42",
        "text": "This user rejects sending emails: drafts only.",
        "task_type": "complaint_letter",
        "outcome": "decision",
        "importance": 5,
        "effective_importance": 4.2,
        "created_at": "2026-08-01T00:00:00+00:00",
        "last_accessed": "2026-08-20T00:00:00+00:00",
        "access_count": 3,
        "tools_used": ["actions_send_email"],
        "source_task_id": "t-1234567890",
    }
]
STATS: dict[str, Any] = {
    "window_days": 7,
    "tasks": {
        "total": 3,
        "by_status": [{"status": "done", "count": 3}],
        "per_day": [{"day": "2026-08-28", "count": 3}],
        "success_rate": 1.0,
        "mean_llm_calls": 12.0,
        "mean_tokens": 900.0,
        "mean_cost_usd": 0.0,
        "latency_p50_s": 80.0,
        "latency_p95_s": 120.0,
    },
    "approvals": {
        "total": 1,
        "by_level": [{"level": "L2", "count": 1}],
        "by_trigger": [{"trigger": "sensitive_tool_call", "count": 1}],
        "by_status": [{"status": "approved", "count": 1}],
        "escalation_rate": 0.33,
        "approval_rate": 1.0,
    },
    "tools": {"total": 5, "not_executed": 0, "by_tool": [{"tool": "db_query", "count": 5}]},
    "safety": {"unapproved_destructive_actions": 0},
    "last_eval": {
        "run_id": "20260828-1",
        "label": "smoke",
        "finished_at": "2026-08-28T12:00:00",
        "k": 1,
        "task_count": 2,
        "run_count": 2,
        "metrics": {
            "success_rate": 1.0,
            "pass_k": 1.0,
            "tool_precision": 1.0,
            "tool_recall": 1.0,
            "escalation_precision": 1.0,
            "escalation_recall": 1.0,
            "injection_resistance": 1.0,
            "judge_mean": 4.5,
            "unapproved_destructive_actions": 0,
        },
        "diff_verdict": None,
    },
}
TRACE: dict[str, Any] = {
    "task_id": "t-1234567890",
    "source": "jaeger",
    "traces": 1,
    "span_count": 3,
    "duration_us": 3_000_000,
    "spans": [
        {
            "id": "a",
            "parent": None,
            "name": "task",
            "kind": "task",
            "start_us": 0,
            "offset_us": 0,
            "duration_us": 3_000_000,
            "attrs": {"task_id": "t-1234567890"},
            "error": False,
            "depth": 0,
        },
        {
            "id": "b",
            "parent": "a",
            "name": "llm.call",
            "kind": "llm",
            "start_us": 0,
            "offset_us": 10,
            "duration_us": 900_000,
            "attrs": {"model": "m", "provider": "p"},
            "error": False,
            "depth": 1,
        },
        {
            "id": "c",
            "parent": "a",
            "name": "MCP send tools/list",
            "kind": "mcp",
            "start_us": 0,
            "offset_us": 20,
            "duration_us": 1_000,
            "attrs": {},
            "error": False,
            "depth": 1,
        },
    ],
}
OUTBOX = [
    {
        "id": 1,
        "kind": "email",
        "status": "queued_for_human",
        "task_id": "t-1234567890",
        "created_at": "2026-08-28T10:00:00+00:00",
        "payload": {"to": "lender@example.test"},
    }
]


class FakeClient:
    decisions: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def health(self) -> bool:
        return True

    def approvals(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        return [APPROVAL] if status in (None, "pending") else []

    def approval(self, approval_id: int) -> dict[str, Any]:
        assert approval_id == 7
        return APPROVAL

    def decide(
        self,
        approval_id: int,
        decision: str,
        *,
        payload: Any = None,
        reason: str = "",
        decided_by: str = "operator",
    ) -> dict[str, Any]:
        self.decisions.append(
            {
                "id": approval_id,
                "decision": decision,
                "payload": payload,
                "reason": reason,
                "by": decided_by,
            }
        )
        return {**APPROVAL, "status": "approved"}

    def tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "task_id": "t-1234567890",
                "user_id": "u_42",
                "status": "awaiting_approval",
                "created_at": "2026-08-28T10:00:00+00:00",
                "request": "send the letter",
                "error": None,
            }
        ]

    def task(self, task_id: str) -> dict[str, Any]:
        assert task_id == "t-1234567890"
        return TASK

    def create_task(
        self, request: str, user_id: str, *, require_human_review: bool = False
    ) -> dict[str, Any]:
        return {"task_id": "t-new", "status": "queued"}

    def outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        return OUTBOX

    def stats(self, days: int = 7) -> dict[str, Any]:
        return STATS

    def trace(self, task_id: str) -> dict[str, Any]:
        return TRACE

    def checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        return [
            {
                "checkpoint_id": "cp-1234567890",
                "step": 2,
                "produced_by": ["plan"],
                "next": ["dispatch"],
                "status": "running",
                "subtasks_done": [],
                "created_at": "",
            }
        ]

    def replay(self, task_id: str, checkpoint_id: str, overrides: list[str]) -> dict[str, Any]:
        self.decisions.append(
            {"replay": task_id, "checkpoint": checkpoint_id, "overrides": overrides}
        )
        return {"task_id": "t-fork", "replay_of": task_id, "checkpoint_id": checkpoint_id}

    def replay_diff(self, task_id: str) -> dict[str, Any]:
        return {
            "status": ["done", "done"],
            "llm_calls": [8, 4],
            "tool_calls": [3, 1],
            "approvals": [0, 0],
            "deliverable_changed": True,
            "deliverable_title": ["a", "b"],
            "subtasks": {"A": {"change": "same"}, "B": {"change": "removed", "source": "accepted"}},
            "tools_by_name": {"db_query": [2, 1]},
        }

    def memory_users(self) -> list[dict[str, Any]]:
        return [{"user_id": "u_42", "count": 1}]

    def memories(self, user_id: str) -> list[dict[str, Any]]:
        return list(MEMORIES) if user_id == "u_42" else []

    def delete_memories(self, user_id: str) -> dict[str, Any]:
        self.decisions.append({"deleted_for": user_id})
        return {"user_id": user_id, "deleted": 1}


@pytest.fixture(autouse=True)
def fake_api(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeClient.decisions = []
    monkeypatch.setattr(api_client, "ForemanClient", FakeClient)
    monkeypatch.setattr(config, "get_settings", lambda: Settings(_env_file=None, api_key="x"))  # type: ignore[call-arg]


def run(path: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / path), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_home_shows_queue_metrics() -> None:
    at = run("apps/review_ui/app.py")
    assert [m.value for m in at.metric] == ["1", "1", "0"]


def test_approvals_page_renders_and_posts_the_decision() -> None:
    at = run("apps/review_ui/pages/1_Approvals.py")
    assert at.selectbox(key="selected").value == 7
    text = " ".join(m.value for m in at.markdown)
    assert "actions_send_email" in text or any("actions_send_email" in c.value for c in at.code)
    assert "send the letter" in text
    assert "3 loans" in " ".join(m.value for m in at.expander[0].markdown)
    at.sidebar.text_input(key="operator").input("sayed")
    at.text_area(key="reason-7").input("looks right").run()
    approve = next(b for b in at.button if "Approve" in b.label)
    approve.click().run()
    assert not at.exception
    assert FakeClient.decisions == [
        {"id": 7, "decision": "approve", "payload": None, "reason": "looks right", "by": "sayed"}
    ]


def test_tasks_page_renders_a_task() -> None:
    at = run("apps/review_ui/pages/2_Tasks.py")
    at.text_input(key="task-id-input").input("t-1234567890").run()
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "awaiting" in text and "3 loans" in text
    assert any("approval #7" in w.value for w in at.warning)


def test_outbox_page_lists_queued_mail() -> None:
    at = run("apps/review_ui/pages/3_Outbox.py")
    assert any("lender@example.test" in c.value for c in at.code)


def test_memory_page_lists_and_deletes() -> None:
    at = run("apps/review_ui/pages/4_Memory.py")
    assert any("complaint_letter" in e.label for e in at.expander)
    assert any("drafts only" in m.value for m in at.markdown)
    at.checkbox(key="memory-confirm").check().run()
    delete = next(b for b in at.button if "Delete all" in b.label)
    delete.click().run()
    assert not at.exception
    assert {"deleted_for": "u_42"} in FakeClient.decisions


def test_stats_page_renders_tiles_and_last_eval() -> None:
    at = run("apps/review_ui/pages/5_Stats.py")
    labels = {m.label: m.value for m in at.metric}
    assert labels["Unapproved destructive actions"] == "0" and labels["Task success"] == "100%"
    assert any("the gate held" in m.value for m in at.markdown)


def test_trace_page_renders_tree_and_requests_a_replay() -> None:
    at = run("apps/review_ui/pages/6_Trace.py")
    at.text_input(key="trace-task").input("t-1234567890").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("source: jaeger" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "List checkpoints").click().run()
    picker = next(sb for sb in at.selectbox if sb.label == "Checkpoint")
    picker.select(picker.options[0]).run()
    next(b for b in at.button if "Replay into a new task" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(d.get("replay") == "t-1234567890" for d in FakeClient.decisions)
