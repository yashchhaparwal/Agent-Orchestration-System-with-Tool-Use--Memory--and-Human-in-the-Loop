"""Actions MCP server: the destructive tools. Every call is written to the ``outbox`` table for a
human to act on. Foreman never sends an email, creates a real event, or calls an external API —
by construction, not by configuration (PRD.md §4)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from packages.orchestrator.memory.db import make_engine, make_session_factory
from packages.orchestrator.memory.persistent import OutboxRepository
from packages.shared.config import Settings, get_settings
from packages.tools.mcp_servers.common import make_server, run

DEFAULT_PORT = 7005
INSTRUCTIONS = (
    "External actions. Nothing is executed here: each call is queued in the outbox for a human. "
    "Every tool needs human approval before it can even be queued."
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_BODY = 50_000


def _iso(value: str, field: str) -> str:
    try:
        return dt.datetime.fromisoformat(value).isoformat()
    except ValueError as e:
        raise ToolError(f"{field} must be an ISO-8601 datetime, got {value!r}") from e


def build_server(settings: Settings, outbox: OutboxRepository | None = None) -> MCPServer:
    server = make_server("foreman-actions", instructions=INSTRUCTIONS)
    repo = outbox or OutboxRepository(make_session_factory(make_engine(settings.database_url)))

    def _queue(kind: str, payload: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        outbox_id = repo.add(kind, payload, task_id=task_id or None)
        return {
            "outbox_id": outbox_id,
            "kind": kind,
            "status": "queued_for_human",
            "note": "Queued in the outbox for a person to review and send. Nothing has been sent.",
        }

    @server.tool()
    def send_email(to: str, subject: str, body: str, task_id: str = "") -> dict[str, Any]:
        """Queue an email for a human to review and send. Nothing is sent by this tool.

        Call this only when the subtask explicitly asks for an email to be sent; otherwise return
        the text as a draft in your result. Requires human approval.
        """
        if not _EMAIL.match(to.strip()):
            raise ToolError(f"invalid recipient address: {to!r}")
        if not subject.strip():
            raise ToolError("subject is empty")
        if len(body) > MAX_BODY:
            raise ToolError(f"body exceeds {MAX_BODY} characters")
        return _queue(
            "email", {"to": to.strip(), "subject": subject.strip(), "body": body}, task_id
        )

    @server.tool()
    def create_calendar_event(
        title: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Queue a calendar event for a human to create. Nothing is created by this tool.

        ``start`` and ``end`` are ISO-8601 datetimes. Requires human approval.
        """
        if not title.strip():
            raise ToolError("title is empty")
        start_iso, end_iso = _iso(start, "start"), _iso(end, "end")
        if end_iso <= start_iso:
            raise ToolError("end must be after start")
        bad = [a for a in (attendees or []) if not _EMAIL.match(a.strip())]
        if bad:
            raise ToolError(f"invalid attendee address(es): {bad}")
        return _queue(
            "calendar_event",
            {
                "title": title.strip(),
                "start": start_iso,
                "end": end_iso,
                "attendees": attendees or [],
            },
            task_id,
        )

    @server.tool()
    def call_api(
        method: str, url: str, payload: dict[str, Any] | None = None, task_id: str = ""
    ) -> dict[str, Any]:
        """Queue an outbound API call for a human to review. Nothing is called by this tool.

        Requires human approval.
        """
        method = method.strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ToolError(f"unsupported method {method!r}")
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ToolError("url must be an absolute http(s) URL")
        return _queue(
            "api_call", {"method": method, "url": url.strip(), "payload": payload or {}}, task_id
        )

    return server


def main() -> None:
    settings = get_settings()
    run(build_server(settings), settings, default_port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
