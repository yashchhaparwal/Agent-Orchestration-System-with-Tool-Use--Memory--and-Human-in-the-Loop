"""Tool registry: discovers tools from the MCP servers, merges them with policy.yaml, and is the
ONLY code that invokes a tool. Agents never hold an MCP client (Rules.md §2.1).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import structlog
import yaml
from mcp import Client

from packages.shared.config import Settings
from packages.shared.types.tools import RiskClass, ToolCall, ToolResult, ToolSpec

log = structlog.get_logger(__name__)

# (name, description, input_schema) as listed by a server — the shape `discover` and tests share.
ToolListing = list[tuple[str, str, dict[str, Any]]]


def load_policy(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("servers", {})
    data.setdefault("tools", {})
    return data


def with_task_id(spec: ToolSpec, arguments: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    """Tools that declare a `task_id` input (the actions server) get the real task id, whatever
    the model put there, so every outbox row names the task that proposed it."""
    if not task_id or "task_id" not in (spec.input_schema.get("properties") or {}):
        return arguments
    return {**arguments, "task_id": task_id}


class ToolRegistry:
    def __init__(self, specs: dict[str, ToolSpec], server_urls: dict[str, str]) -> None:
        self._specs = specs
        self._server_urls = server_urls

    # ---------- construction ----------

    @classmethod
    def from_listing(
        cls,
        listings: dict[str, ToolListing],
        policy: dict[str, Any],
        server_urls: dict[str, str],
    ) -> ToolRegistry:
        """Merge server listings with policy. Unlisted tools are dropped; missing ones are warned."""
        specs: dict[str, ToolSpec] = {}
        by_server_and_mcp = {
            (p["server"], p["mcp_name"]): (name, p) for name, p in policy["tools"].items()
        }
        seen: set[tuple[str, str]] = set()
        for server, tools in listings.items():
            for mcp_name, description, input_schema in tools:
                entry = by_server_and_mcp.get((server, mcp_name))
                if entry is None:
                    log.warning("registry.unlisted_tool_ignored", server=server, tool=mcp_name)
                    continue
                name, p = entry
                seen.add((server, mcp_name))
                specs[name] = ToolSpec(
                    name=name,
                    server=server,
                    mcp_name=mcp_name,
                    description=description or "",
                    input_schema=input_schema or {"type": "object", "properties": {}},
                    risk=RiskClass(p["risk"]),
                    agents=list(p.get("agents", [])),
                    rate_per_min=int(p.get("rate_per_min", 60)),
                    timeout_s=int(p.get("timeout_s", 20)),
                )
        for key, (name, _) in by_server_and_mcp.items():
            if key not in seen:
                log.warning("registry.policy_tool_not_on_server", tool=name, server=key[0])
        return cls(specs, server_urls)

    @classmethod
    async def discover(cls, settings: Settings, policy_path: Path | None = None) -> ToolRegistry:
        policy = load_policy(policy_path or settings.tool_policy_path)
        server_urls = {
            name: settings.env_value(cfg["url_env"]) for name, cfg in policy["servers"].items()
        }
        listings: dict[str, ToolListing] = {}
        for server, url in server_urls.items():
            try:
                listings[server] = await _list_tools(url)
            except Exception as e:  # noqa: BLE001 — a down server must not take the worker down
                log.error("registry.server_unreachable", server=server, url=url, error=str(e))
                listings[server] = []
        return cls.from_listing(listings, policy, server_urls)

    # ---------- queries ----------

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def allowed_for(self, agent: str) -> list[ToolSpec]:
        return [s for s in self._specs.values() if agent in s.agents]

    def schemas_for(self, agent: str) -> list[dict[str, Any]]:
        return [s.to_openai_tool() for s in self.allowed_for(agent)]

    # ---------- invocation ----------

    async def invoke(self, call: ToolCall, *, task_id: str | None = None) -> ToolResult:
        spec = self._specs.get(call.name)
        if spec is None:
            return ToolResult(
                tool_call_id=call.id, name=call.name, content="unknown tool", is_error=True
            )
        url = self._server_urls.get(spec.server, "")
        started = time.perf_counter()
        try:
            async with asyncio.timeout(spec.timeout_s):
                content, is_error = await _call_tool(
                    url, spec.mcp_name, with_task_id(spec, call.arguments, task_id)
                )
        except TimeoutError:
            content, is_error = f"tool timed out after {spec.timeout_s}s", True
        except Exception as e:  # noqa: BLE001 — surfaced to the model as an error result
            content, is_error = f"tool call failed: {type(e).__name__}: {e}", True
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=content,
            is_error=is_error,
            latency_ms=latency_ms,
        )


def _text_of(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in getattr(result, "content", []) or []]
    return "\n".join(p for p in parts if p)


async def _list_tools(url: str) -> ToolListing:
    async with Client(url) as client:
        listed = await client.list_tools()
        return [(t.name, t.description or "", dict(t.input_schema or {})) for t in listed.tools]


async def _call_tool(url: str, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    async with Client(url) as client:
        result = await client.call_tool(name, arguments=arguments)
        return _text_of(result), bool(getattr(result, "is_error", False))
