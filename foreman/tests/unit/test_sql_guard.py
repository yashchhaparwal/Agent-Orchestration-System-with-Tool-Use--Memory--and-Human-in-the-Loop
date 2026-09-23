from __future__ import annotations

import pytest

from packages.tools.mcp_servers.database.guard import UnsafeSQLError, check_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM loans",
        "select id, principal from loans where claim_id = 4471 order by start_date",
        "SELECT * FROM loans;",
        "WITH recent AS (SELECT * FROM loans WHERE start_date > '2024-01-01') SELECT count(*) FROM recent",
        "SELECT 1 -- ; DROP TABLE loans",
        "SELECT /* comment */ reference FROM claims",
        "SELECT c.reference, count(l.id) FROM claims c JOIN loans l ON l.claim_id = c.id GROUP BY c.reference",
    ],
)
def test_plain_selects_pass(sql: str) -> None:
    cleaned = check_select(sql)
    assert cleaned.lower().startswith(("select", "with"))
    assert ";" not in cleaned


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "-- only a comment",
        "DELETE FROM loans",
        "UPDATE claims SET status = 'closed'",
        "INSERT INTO claims VALUES (1)",
        "DROP TABLE loans",
        "SELECT 1; DROP TABLE loans",
        "SELECT * INTO backup FROM loans",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "CREATE TABLE x (a int)",
        "TRUNCATE loans",
        "GRANT ALL ON loans TO public",
        "COPY loans TO '/tmp/out.csv'",
        "SET statement_timeout = 0",
        "EXPLAIN ANALYZE SELECT * FROM loans",
        "SHOW ALL",
    ],
)
def test_everything_else_rejected(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        check_select(sql)
