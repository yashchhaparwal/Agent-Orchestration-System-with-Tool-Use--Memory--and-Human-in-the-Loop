"""Shared bootstrap for the MCP servers (mcp 2.x): an MCPServer bound over streamable HTTP.

Transport settings (host, port, stateless mode) are passed to ``run()`` — in the 2.x SDK they no
longer belong to the constructor.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from packages.shared.config import Settings


def make_server(name: str, *, instructions: str | None = None) -> MCPServer:
    return MCPServer(name, instructions=instructions)


def run(server: MCPServer, settings: Settings, *, default_port: int) -> None:
    port = settings.mcp_port or default_port
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=port,
        stateless_http=True,
    )
