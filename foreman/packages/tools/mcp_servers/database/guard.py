"""SQL guard: exactly one read-only SELECT (or WITH ... SELECT), nothing else (Architecture.md §6.2).

This is the first line of defence; the second is the SELECT-only database role. Both must fail
closed — anything the guard cannot positively classify as a plain SELECT is rejected.
"""

from __future__ import annotations

import sqlparse
from sqlparse import tokens as tok

FORBIDDEN_WORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "upsert",
        "drop",
        "alter",
        "create",
        "truncate",
        "rename",
        "reindex",
        "cluster",
        "vacuum",
        "analyze",
        "grant",
        "revoke",
        "copy",
        "execute",
        "exec",
        "call",
        "do",
        "lock",
        "listen",
        "notify",
        "into",
        "set",
        "reset",
        "show",
        "prepare",
        "deallocate",
        "discard",
        "load",
        "refresh",
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_terminate_backend",
        "lo_import",
        "lo_export",
        "dblink",
        "current_setting",
        "set_config",
    }
)

_ALLOWED_FIRST = {"select", "with"}


class UnsafeSQLError(ValueError):
    """Raised with a reason the model can act on."""


def check_select(sql: str) -> str:
    """Return the cleaned statement if it is a single plain SELECT; raise UnsafeSQLError otherwise."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("empty SQL")
    cleaned = sqlparse.format(sql, strip_comments=True).strip()
    cleaned = cleaned.rstrip(";").strip()
    if not cleaned:
        raise UnsafeSQLError("empty SQL after removing comments")
    if ";" in cleaned:
        raise UnsafeSQLError("exactly one statement is allowed (found ';')")

    statements = [s for s in sqlparse.parse(cleaned) if str(s).strip()]
    if len(statements) != 1:
        raise UnsafeSQLError("exactly one statement is allowed")
    statement = statements[0]

    first = statement.token_first(skip_cm=True)
    if first is None or first.value.lower() not in _ALLOWED_FIRST:
        raise UnsafeSQLError("only SELECT (or WITH ... SELECT) statements are allowed")

    for token in statement.flatten():
        if token.ttype in tok.Keyword or token.ttype in tok.Name or token.ttype in tok.Name.Builtin:
            word = token.value.lower().strip('"')
            if word in FORBIDDEN_WORDS:
                raise UnsafeSQLError(f"forbidden keyword or function: {word.upper()}")
    return cleaned
