"""Phase 2 done-when, against the real Postgres checkpointer: a task crashes mid-run, a fresh graph
instance resumes it from the checkpoint, and the store shows it done. Needs the compose stack.

    uv run pytest -q -m integration tests/integration/test_graph_postgres_resume.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from packages.orchestrator.graph.serde import checkpoint_serde
from packages.orchestrator.worker import checkpointer_conninfo
from packages.shared.config import get_settings
from tests.unit.test_graph_flow import Scenario

pytestmark = [pytest.mark.integration]


async def test_resume_from_postgres_checkpoint(tmp_path: Path) -> None:
    settings = get_settings()
    conninfo = checkpointer_conninfo(settings.database_url)
    sc = Scenario(tmp_path, crash_reviewer_once=True)

    async with AsyncPostgresSaver.from_conn_string(conninfo, serde=checkpoint_serde()) as saver:
        await saver.setup()
        with pytest.raises(RuntimeError, match="simulated worker crash"):
            await sc.run(checkpointer=saver)
    with sc.store._sessions() as s:  # noqa: SLF001
        from packages.orchestrator.memory.persistent import TaskRow

        task_id = s.query(TaskRow).one().id
    assert [c[1] for c in sc.specialist_calls] == ["A"]

    # A brand-new saver + graph (as a restarted worker would have) picks up the thread.
    async with AsyncPostgresSaver.from_conn_string(conninfo, serde=checkpoint_serde()) as saver2:
        final, _ = await sc.run(checkpointer=saver2, resume=True, thread=task_id)
    assert final["status"] == "done", final.get("error")
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]
