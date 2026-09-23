from __future__ import annotations

from typing import Any

import pytest

from packages.orchestrator.gate.decide import Gate
from packages.shared.types.gate import GateAction
from packages.shared.types.tools import RiskClass, ToolCall
from packages.tools.registry.ratelimit import RateLimiter
from packages.tools.registry.registry import ToolListing, ToolRegistry

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "max_rows": {"type": "integer"}},
    "required": ["sql"],
}
READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}
WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
}

POLICY: dict[str, Any] = {
    "servers": {"db": {"url_env": "MCP_DB_URL"}, "files": {"url_env": "MCP_FILES_URL"}},
    "tools": {
        "db_query": {
            "server": "db",
            "mcp_name": "query",
            "risk": "safe",
            "agents": ["research"],
            "rate_per_min": 2,
        },
        "files_read_file": {
            "server": "files",
            "mcp_name": "read_file",
            "risk": "safe",
            "agents": ["research", "writing"],
        },
        "files_write_file": {
            "server": "files",
            "mcp_name": "write_file",
            "risk": "risky",
            "agents": ["writing"],
        },
        "actions_send_email": {
            "server": "actions",
            "mcp_name": "send_email",
            "risk": "destructive",
            "agents": ["writing"],
        },
    },
}

LISTINGS: dict[str, ToolListing] = {
    "db": [
        ("query", "run a select", QUERY_SCHEMA),
        ("dangerous_exec", "not in policy", {"type": "object"}),
    ],
    "files": [("read_file", "read", READ_SCHEMA), ("write_file", "write", WRITE_SCHEMA)],
}


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry.from_listing(LISTINGS, POLICY, {"db": "http://db", "files": "http://files"})


def test_unlisted_server_tool_is_not_registered(registry: ToolRegistry) -> None:
    assert "dangerous_exec" not in registry.names()
    assert registry.get("dangerous_exec") is None


def test_policy_tool_missing_on_server_is_not_registered(registry: ToolRegistry) -> None:
    assert registry.get("actions_send_email") is None  # server "actions" not running


def test_schemas_filtered_by_agent(registry: ToolRegistry) -> None:
    research = {t["function"]["name"] for t in registry.schemas_for("research")}
    writing = {t["function"]["name"] for t in registry.schemas_for("writing")}
    assert research == {"db_query", "files_read_file"}
    assert writing == {"files_read_file", "files_write_file"}
    assert registry.schemas_for("nobody") == []


def test_openai_tool_shape(registry: ToolRegistry) -> None:
    tool = registry.get("db_query")
    assert tool is not None
    assert tool.risk == RiskClass.SAFE
    shape = tool.to_openai_tool()
    assert shape["type"] == "function"
    assert shape["function"]["name"] == "db_query"
    assert shape["function"]["parameters"] == QUERY_SCHEMA


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=args)


def test_gate_matrix(registry: ToolRegistry) -> None:
    gate = Gate(registry, RateLimiter())
    assert gate.decide("research", _call("nope", x=1)).action == GateAction.BLOCK
    assert gate.decide("writing", _call("db_query", sql="select 1")).action == GateAction.BLOCK
    assert gate.decide("research", _call("db_query", sql=123)).action == GateAction.BLOCK  # schema
    assert gate.decide("research", _call("db_query")).action == GateAction.BLOCK  # missing required
    assert gate.decide("research", _call("db_query", sql="select 1")).action == GateAction.ALLOW
    assert (
        gate.decide("writing", _call("files_write_file", path="a", content="b")).action
        == GateAction.APPROVE
    )
    parse_err = ToolCall(id="c2", name="db_query", arguments={}, parse_error="bad json")
    assert gate.decide("research", parse_err).action == GateAction.BLOCK


def test_gate_rate_limit_fails_closed(registry: ToolRegistry) -> None:
    gate = Gate(registry, RateLimiter())
    ok = _call("db_query", sql="select 1")
    assert gate.decide("research", ok).action == GateAction.ALLOW
    assert gate.decide("research", ok).action == GateAction.ALLOW
    third = gate.decide("research", ok)
    assert third.action == GateAction.BLOCK
    assert "rate limit" in third.reason


def test_gate_emits_span(registry: ToolRegistry, spans) -> None:  # type: ignore[no-untyped-def]
    Gate(registry).decide("research", _call("db_query", sql="select 1"))
    names = [s.name for s in spans.get_finished_spans()]
    assert "gate.decide" in names
    gate_span = next(s for s in spans.get_finished_spans() if s.name == "gate.decide")
    assert gate_span.attributes["decision"] == "allow"
