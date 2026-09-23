"""Phase 3 done-when, against the five running MCP servers, Redis, Postgres, and Docker:

- every policy tool is discovered and registered;
- an unauthorised tool is blocked with a logged reason;
- a destructive tool is stopped at the gate (approval) and, even when invoked directly, only
  queues to the outbox;
- the sandbox runs code with no network and is killed on timeout.

    uv run pytest -q -m integration tests/integration/test_gate_live.py
"""

from __future__ import annotations

import json

import pytest

from packages.orchestrator.gate.classifier import StaticClassifier
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.memory.db import make_engine, make_session_factory
from packages.orchestrator.memory.persistent import OutboxRepository
from packages.orchestrator.runtime import make_rate_limiter
from packages.shared.config import get_settings
from packages.shared.types.gate import GateAction
from packages.shared.types.tools import RiskClass, ToolCall
from packages.tools.registry.registry import ToolRegistry

pytestmark = [pytest.mark.integration]

EXPECTED_TOOLS = {
    "db_schema",
    "db_query",
    "files_list_dir",
    "files_read_file",
    "files_write_file",
    "web_search",
    "web_fetch",
    "sandbox_run_python",
    "actions_send_email",
    "actions_create_calendar_event",
    "actions_call_api",
}


@pytest.fixture(scope="module")
def settings():  # type: ignore[no-untyped-def]
    return get_settings()


@pytest.fixture
async def registry(settings) -> ToolRegistry:  # type: ignore[no-untyped-def]
    return await ToolRegistry.discover(settings)


async def test_all_policy_tools_are_registered(registry: ToolRegistry) -> None:
    assert set(registry.names()) == EXPECTED_TOOLS
    assert registry.get("sandbox_run_python").risk == RiskClass.RISKY  # type: ignore[union-attr]
    assert registry.get("actions_send_email").risk == RiskClass.DESTRUCTIVE  # type: ignore[union-attr]


async def test_unauthorised_and_destructive_calls_never_execute(
    registry: ToolRegistry, settings
) -> None:  # type: ignore[no-untyped-def]
    gate = Gate(registry, make_rate_limiter(settings), classifier=StaticClassifier("approve"))
    # research is not allowed the sandbox
    d = await gate.decide_async(
        "research", ToolCall(id="1", name="sandbox_run_python", arguments={"code": "print(1)"})
    )
    assert d.action == GateAction.BLOCK and "not allowed for agent 'research'" in d.reason
    # writing may propose an email, but it is destructive -> a human, always
    d = await gate.decide_async(
        "writing",
        ToolCall(
            id="2",
            name="actions_send_email",
            arguments={"to": "lender@example.test", "subject": "s", "body": "b"},
        ),
    )
    assert d.action == GateAction.APPROVE and d.risk == RiskClass.DESTRUCTIVE and not d.classified
    # unknown tool, malformed arguments
    d = await gate.decide_async("writing", ToolCall(id="3", name="rm_rf", arguments={}))
    assert d.action == GateAction.BLOCK and "unknown tool" in d.reason
    d = await gate.decide_async(
        "analysis", ToolCall(id="4", name="sandbox_run_python", arguments={"code": 42})
    )
    assert d.action == GateAction.BLOCK and "schema" in d.reason


async def test_destructive_tool_only_queues_to_outbox(registry: ToolRegistry, settings) -> None:  # type: ignore[no-untyped-def]
    outbox = OutboxRepository(make_session_factory(make_engine(settings.database_url)))
    before = {r["id"] for r in outbox.list(limit=500)}
    # Invoked directly (as if a human had approved): the only effect is a queued row.
    result = await registry.invoke(
        ToolCall(
            id="5",
            name="actions_send_email",
            arguments={
                "to": "lender@example.test",
                "subject": "Complaint CLM-4471",
                "body": "Draft",
            },
        )
    )
    assert not result.is_error, result.content
    assert "queued_for_human" in result.content and "Nothing has been sent" in result.content
    new = [r for r in outbox.list(limit=500) if r["id"] not in before]
    assert len(new) == 1 and new[0]["kind"] == "email" and new[0]["status"] == "queued_for_human"


async def test_sandbox_has_no_network_and_is_killed_on_timeout(registry: ToolRegistry) -> None:
    gate = Gate(registry, classifier=StaticClassifier("allow", "pure computation"))
    code_net = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('NETWORK_OPEN')\n"
        "except OSError as e:\n"
        "    print('NETWORK_BLOCKED', type(e).__name__)\n"
    )
    call = ToolCall(
        id="6", name="sandbox_run_python", arguments={"code": code_net, "timeout_s": 15}
    )
    assert (
        await gate.decide_async("analysis", call, context="B: compute")
    ).action == GateAction.ALLOW
    result = await registry.invoke(call)
    assert not result.is_error, result.content
    assert "NETWORK_BLOCKED" in result.content and "NETWORK_OPEN" not in result.content

    compute = await registry.invoke(
        ToolCall(
            id="7",
            name="sandbox_run_python",
            arguments={"code": "import pandas as pd; print(pd.Series([150,180,216]).sum())"},
        )
    )
    assert not compute.is_error and "546" in compute.content

    hang = await registry.invoke(
        ToolCall(
            id="8",
            name="sandbox_run_python",
            arguments={"code": "import time; time.sleep(60)", "timeout_s": 3},
        )
    )
    assert not hang.is_error, hang.content
    hang_result = json.loads(hang.content)
    assert hang_result["timed_out"] is True and "killed after 3s" in hang_result["stderr"]
    assert hang_result["duration_ms"] < 15_000

    escape = await registry.invoke(
        ToolCall(
            id="9",
            name="sandbox_run_python",
            arguments={"code": "open('/etc/hostname','w').write('x')"},
        )
    )
    assert not escape.is_error and (
        "Read-only file system" in escape.content or "Permission denied" in escape.content
    )
