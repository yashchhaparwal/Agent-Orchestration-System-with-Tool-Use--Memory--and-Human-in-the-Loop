"""Exercise the MCP servers through the in-process mcp 2.x Client (no HTTP, no Postgres): the files
server against a temp workspace, and the database server against SQLite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy import create_engine, text

from packages.shared.config import Settings
from packages.tools.mcp_servers.database.server import build_server as build_db
from packages.tools.mcp_servers.files.server import build_server as build_files


class ToolFailedError(RuntimeError):
    """The server returned is_error=True (or raised); message carries the server's reason."""


async def _call(server: Any, name: str, **args: Any) -> Any:
    try:
        async with Client(server) as client:
            result = await client.call_tool(name, args)
    except Exception as e:  # noqa: BLE001 — some SDK paths raise instead of returning is_error
        raise ToolFailedError(str(e)) from e
    body = "\n".join(getattr(b, "text", "") for b in result.content)
    if result.is_error:
        raise ToolFailedError(body)
    return json.loads(body) if body.lstrip().startswith("{") else body


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "claims" / "CLM-1").mkdir(parents=True)
    (ws / "claims" / "CLM-1" / "doc.md").write_text("# Doc\n" + "x" * 50, encoding="utf-8")
    return ws


async def test_files_list_read_and_confinement(settings: Settings, workspace: Path) -> None:
    server = build_files(settings, root=workspace)
    listing = await _call(server, "list_dir", path="claims/CLM-1")
    assert listing["entries"][0]["name"] == "doc.md"

    doc = await _call(server, "read_file", path="claims/CLM-1/doc.md", max_chars=10)
    assert doc["truncated"] is True and doc["content"] == "# Doc\nxxxx"

    with pytest.raises(ToolFailedError, match="escapes"):
        await _call(server, "read_file", path="../outside.txt")


async def test_files_write_refuses_overwrite(settings: Settings, workspace: Path) -> None:
    server = build_files(settings, root=workspace)
    out = await _call(server, "write_file", path="out/new.md", content="hello")
    assert out["bytes"] == 5 and (workspace / "out" / "new.md").read_text() == "hello"
    with pytest.raises(ToolFailedError, match="exists"):
        await _call(server, "write_file", path="out/new.md", content="again")


async def test_database_query_guard_and_cap(settings: Settings, tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE loans (id INTEGER PRIMARY KEY, principal NUMERIC)"))
        conn.execute(text("INSERT INTO loans VALUES (1, 100.5), (2, 200), (3, 300)"))
    server = build_db(settings, engine=engine)

    schema = await _call(server, "schema")
    assert schema["tables"][0]["name"] == "loans"

    # SQLite has no SET TRANSACTION; the server issues Postgres statements, so neutralise them.
    import packages.tools.mcp_servers.database.server as srv

    original = srv.text

    def sqlite_safe_text(s: str) -> Any:
        return original("SELECT 1") if s.startswith("SET ") else original(s)

    srv.text = sqlite_safe_text  # type: ignore[assignment]
    try:
        rows = await _call(server, "query", sql="SELECT * FROM loans ORDER BY id", max_rows=2)
        assert rows["row_count"] == 2 and rows["truncated"] is True
        assert rows["rows"][0]["principal"] == 100.5
        with pytest.raises(ToolFailedError, match="rejected"):
            await _call(server, "query", sql="DELETE FROM loans")
    finally:
        srv.text = original  # type: ignore[assignment]
