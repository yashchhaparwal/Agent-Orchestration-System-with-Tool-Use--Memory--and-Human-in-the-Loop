"""Phase 4 done-when, against the real Postgres checkpointer: a task pauses on a destructive tool
call, the decision is recorded (as the API does), and a *fresh* saver + graph — a restarted worker —
resumes it with the decision and finishes. Needs the compose stack.

    uv run pytest -q -m integration tests/integration/test_hitl_postgres_resume.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from packages.orchestrator.graph.serde import checkpoint_serde
from packages.orchestrator.worker import checkpointer_conninfo
from packages.shared.config import get_settings
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from tests.unit.test_graph_flow import Scenario, interrupted

pytestmark = [pytest.mark.integration]


@pytest.mark.parametrize("kind", [DecisionKind.APPROVE, DecisionKind.REJECT])
async def test_pause_restart_decide_resume(tmp_path: Path, kind: DecisionKind) -> None:
    conninfo = checkpointer_conninfo(get_settings().database_url)
    sc = Scenario(tmp_path / kind.value, email_subtasks=("C",))

    async with AsyncPostgresSaver.from_conn_string(conninfo, serde=checkpoint_serde()) as saver:
        await saver.setup()
        final, task_id = await sc.run(checkpointer=saver)
        assert interrupted(final)["kind"] == "tool_call"
    assert sc.registry.invoked == []

    async with AsyncPostgresSaver.from_conn_string(conninfo, serde=checkpoint_serde()) as saver2:
        final = await sc.decide(
            task_id,
            ApprovalDecision(decision=kind, reason="drafts only", decided_by="tester"),
            checkpointer=saver2,
        )
    assert final["status"] == "done", final.get("error")
    assert [c.name for c in sc.registry.invoked] == (
        ["actions_send_email"] if kind == DecisionKind.APPROVE else []
    )
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]
