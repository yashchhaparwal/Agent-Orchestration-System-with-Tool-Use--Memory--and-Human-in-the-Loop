"""Rules.md §2.1 — one path to a tool. Only the registry may hold an MCP client."""

from __future__ import annotations

from pathlib import Path

ALLOWED = {"packages/tools/registry/registry.py"}
CLIENT_MARKERS = ("from mcp.client", "from mcp import Client", "mcp.Client", "ClientSession")


def test_only_the_registry_talks_to_mcp_clients() -> None:
    offenders = []
    for py in Path("packages").rglob("*.py"):
        if "mcp_servers" in py.parts:  # servers are the other side of the wire
            continue
        text = py.read_text(encoding="utf-8")
        if any(marker in text for marker in CLIENT_MARKERS) and py.as_posix() not in ALLOWED:
            offenders.append(py.as_posix())
    assert offenders == [], f"MCP client used outside the registry: {offenders}"


def test_agents_do_not_import_the_registry_invoker() -> None:
    for py in Path("packages/orchestrator/agents").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "registry" not in text.lower(), f"{py} must not touch the registry; the loop does"
