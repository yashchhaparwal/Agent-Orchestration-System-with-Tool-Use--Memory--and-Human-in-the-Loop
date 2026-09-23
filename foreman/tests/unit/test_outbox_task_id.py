"""Every outbox row names the task that proposed it: the registry injects the real task id into
tools that declare one, and the actions server records it (found by the Phase 7 demo)."""

from __future__ import annotations

from pathlib import Path

from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import OutboxRepository
from packages.shared.config import Settings
from packages.shared.types.tools import RiskClass, ToolSpec
from packages.tools.mcp_servers.actions.server import build_server
from packages.tools.registry.registry import with_task_id
from tests.unit.test_actions_server import _call


def spec(properties: dict[str, object]) -> ToolSpec:
    return ToolSpec(
        name="actions_send_email",
        server="actions",
        mcp_name="send_email",
        description="",
        input_schema={"type": "object", "properties": properties},
        risk=RiskClass.DESTRUCTIVE,
        agents=["writing"],
    )


def test_registry_injects_the_real_task_id_only_where_declared() -> None:
    declared = spec({"to": {"type": "string"}, "task_id": {"type": "string"}})
    args = {"to": "a@b.co", "task_id": "made-up-by-the-model"}
    assert with_task_id(declared, args, "task-7") == {"to": "a@b.co", "task_id": "task-7"}
    assert with_task_id(declared, {"to": "a@b.co"}, None) == {"to": "a@b.co"}
    undeclared = spec({"to": {"type": "string"}})
    assert with_task_id(undeclared, {"to": "a@b.co"}, "task-7") == {"to": "a@b.co"}


async def test_actions_server_records_the_task_on_the_outbox_row(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'o.db'}")
    create_all(engine)
    outbox = OutboxRepository(make_session_factory(engine))
    server = build_server(Settings(_env_file=None), outbox=outbox)  # type: ignore[call-arg]
    await _call(
        server, "send_email", to="lender@example.test", subject="s", body="b", task_id="task-7"
    )
    await _call(server, "call_api", method="GET", url="https://example.test/x")
    rows = {r["kind"]: r for r in outbox.list()}
    assert rows["email"]["task_id"] == "task-7"
    assert rows["api_call"]["task_id"] is None  # no task context → still queued, just unattributed
