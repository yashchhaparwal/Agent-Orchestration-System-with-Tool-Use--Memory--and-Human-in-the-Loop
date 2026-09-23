"""Sandbox MCP server: ``run_python`` in a hardened throwaway container."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from packages.shared.config import Settings, get_settings
from packages.tools.mcp_servers.common import make_server, run
from packages.tools.mcp_servers.sandbox.runner import SandboxLimits, run_in_container

DEFAULT_PORT = 7003
MAX_CODE_CHARS = 60_000
INSTRUCTIONS = (
    "Runs Python 3.12 in an isolated container: no network, read-only filesystem except /work, "
    "512 MB, 1 CPU, hard timeout. Nothing persists between calls."
)


def _docker_client() -> Any:
    import docker  # imported lazily: the SDK is only needed in this server

    return docker.from_env()


def build_server(settings: Settings, client: Any | None = None) -> MCPServer:
    server = make_server("foreman-sandbox", instructions=INSTRUCTIONS)
    limits = SandboxLimits(
        image=settings.sandbox_image,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
        max_timeout_s=settings.sandbox_max_timeout_s,
    )
    state: dict[str, Any] = {"client": client}

    def get_client() -> Any:
        if state["client"] is None:
            try:
                state["client"] = _docker_client()
            except Exception as e:  # noqa: BLE001 — surfaced to the model as a tool error
                raise ToolError(
                    f"sandbox unavailable: cannot reach Docker ({type(e).__name__})"
                ) from e
        return state["client"]

    @server.tool()
    def run_python(code: str, timeout_s: int = 30) -> dict[str, Any]:
        """Run a Python 3.12 script in a fresh, isolated container and return its output.

        Call this for calculations, data transformations, or checking a piece of logic. The
        container has NO network access, a read-only filesystem except /work, 512 MB of memory,
        one CPU, and is killed after ``timeout_s`` seconds (max 60). Print what you need to see —
        only stdout/stderr come back. Nothing you write survives the call.
        """
        if not code or not code.strip():
            raise ToolError("code is empty")
        if len(code) > MAX_CODE_CHARS:
            raise ToolError(f"code exceeds {MAX_CODE_CHARS} characters")
        try:
            result = run_in_container(get_client(), code, timeout_s=timeout_s, limits=limits)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "No such image" in msg or "not found" in msg.lower():
                raise ToolError(
                    f"sandbox image '{limits.image}' is not built; run `make sandbox-image`"
                ) from e
            raise ToolError(f"sandbox failed: {type(e).__name__}: {msg[:300]}") from e
        return {
            "limits": {"memory": limits.memory, "cpus": limits.cpus, "network": "none"},
            **asdict(result),
        }

    return server


def main() -> None:
    settings = get_settings()
    run(build_server(settings), settings, default_port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
