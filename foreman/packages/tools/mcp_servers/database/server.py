"""Database MCP server: ``schema`` and ``query`` over a SELECT-only connection."""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from packages.shared.config import Settings, get_settings
from packages.tools.mcp_servers.common import make_server, run
from packages.tools.mcp_servers.database.guard import UnsafeSQLError, check_select

DEFAULT_PORT = 7004
STATEMENT_TIMEOUT_MS = 15_000
INSTRUCTIONS = "Read-only access to the claims database. Only SELECT statements are accepted."


def _jsonable(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes | memoryview):
        return bytes(value).hex()
    return value


def build_server(settings: Settings, engine: Engine | None = None) -> MCPServer:
    server = make_server("foreman-database", instructions=INSTRUCTIONS)
    db = engine or create_engine(settings.database_url_readonly, pool_pre_ping=True)

    @server.tool()
    def schema() -> dict[str, Any]:
        """Describe the database: tables, columns with types, primary keys, foreign keys.

        Call this first when you do not know the table or column names. Costs nothing.
        """
        inspector = inspect(db)
        tables = []
        for name in sorted(inspector.get_table_names()):
            columns = [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": bool(c.get("nullable", True)),
                }
                for c in inspector.get_columns(name)
            ]
            pk = inspector.get_pk_constraint(name).get("constrained_columns", [])
            fks = []
            for fk in inspector.get_foreign_keys(name):
                referred = ", ".join(fk.get("referred_columns", []))
                fks.append(
                    {
                        "columns": fk.get("constrained_columns", []),
                        "references": f"{fk.get('referred_table')}({referred})",
                    }
                )
            tables.append(
                {"name": name, "columns": columns, "primary_key": pk, "foreign_keys": fks}
            )
        return {"tables": tables}

    @server.tool()
    def query(sql: str, max_rows: int = 200) -> dict[str, Any]:
        """Run ONE read-only SELECT statement and return rows as JSON.

        Call this when the subtask needs records from the database. Only SELECT (or WITH … SELECT)
        is accepted; anything else is rejected with a reason. Results are capped at ``max_rows``
        (default 200) and ``truncated`` tells you if more rows exist — add a WHERE or LIMIT then.
        """
        try:
            statement = check_select(sql)
        except UnsafeSQLError as e:
            raise ToolError(f"rejected: {e}") from e
        cap = max(1, min(int(max_rows), 1000))
        wrapped = f"SELECT * FROM ({statement}) AS q LIMIT :cap"
        with db.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            result = conn.execute(text(wrapped), {"cap": cap + 1})
            columns = list(result.keys())
            rows = [dict(zip(columns, (_jsonable(v) for v in row), strict=True)) for row in result]
        truncated = len(rows) > cap
        rows = rows[:cap]
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}

    return server


def main() -> None:
    settings = get_settings()
    run(build_server(settings), settings, default_port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
