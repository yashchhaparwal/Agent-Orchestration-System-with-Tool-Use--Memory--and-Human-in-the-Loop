"""The actions server can only ever write to the outbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import OutboxRepository
from packages.shared.config import Settings
from packages.tools.mcp_servers.actions.server import build_server
from tests.unit.test_mcp_servers_inprocess import ToolFailedError, _call


@pytest.fixture
def outbox(tmp_path: Path) -> OutboxRepository:
    engine = make_engine(f"sqlite:///{tmp_path / 'outbox.db'}")
    create_all(engine)
    return OutboxRepository(make_session_factory(engine))


async def test_every_action_is_queued_never_sent(
    settings: Settings, outbox: OutboxRepository
) -> None:
    server = build_server(settings, outbox=outbox)
    email = await _call(
        server, "send_email", to="lender@example.test", subject="Complaint", body="Dear Sir"
    )
    event = await _call(
        server,
        "create_calendar_event",
        title="Call",
        start="2026-09-01T10:00:00",
        end="2026-09-01T10:30:00",
        attendees=["a@b.co"],
    )
    api = await _call(
        server,
        "call_api",
        method="post",
        url="https://api.example.test/claims",
        payload={"id": 4471},
    )
    for receipt in (email, event, api):
        assert (
            receipt["status"] == "queued_for_human" and "Nothing has been sent" in receipt["note"]
        )
    rows = outbox.list()
    assert [r["kind"] for r in rows] == ["api_call", "calendar_event", "email"]
    assert rows[-1]["payload"]["to"] == "lender@example.test"
    assert rows[0]["payload"]["method"] == "POST"
    assert all(r["status"] == "queued_for_human" for r in rows)


async def test_invalid_inputs_are_rejected_before_queueing(
    settings: Settings, outbox: OutboxRepository
) -> None:
    server = build_server(settings, outbox=outbox)
    with pytest.raises(ToolFailedError, match="invalid recipient"):
        await _call(server, "send_email", to="not-an-email", subject="x", body="y")
    with pytest.raises(ToolFailedError, match="after start"):
        await _call(
            server,
            "create_calendar_event",
            title="x",
            start="2026-09-01T10:00:00",
            end="2026-09-01T09:00:00",
        )
    with pytest.raises(ToolFailedError, match="ISO-8601"):
        await _call(server, "create_calendar_event", title="x", start="tomorrow", end="later")
    with pytest.raises(ToolFailedError, match="unsupported method"):
        await _call(server, "call_api", method="TRACE", url="https://x.test/")
    with pytest.raises(ToolFailedError, match="absolute http"):
        await _call(server, "call_api", method="GET", url="ftp://x.test/")
    assert outbox.list() == []
