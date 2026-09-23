"""Run one specialist on one ad-hoc subtask against the live MCP servers (Phases.md, Phase 1).

    uv run scripts/run_subtask.py "List the loans for claim CLM-4471 and summarise the lender's response"
    uv run scripts/run_subtask.py --agent writing "Draft and send the complaint letter for CLM-4471"

If the gate needs a human (a destructive tool), the loop pauses and the checkpoint is printed;
the full approval flow lives in the graph + API + operator UI (Phase 4), not in this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog

from packages.orchestrator.agents.catalog import build_specialists
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.loop.agent_loop import LoopDeps, PausedLoop, run_agent_loop
from packages.orchestrator.loop.budgets import Budget
from packages.orchestrator.runtime import make_llm_factory, make_rate_limiter
from packages.orchestrator.tracing.otel import configure_tracing, span
from packages.shared.config import get_settings
from packages.shared.types.subtask import Specialist, Subtask
from packages.tools.registry.registry import ToolRegistry


async def main(agent_name: str, text: str, expected: str, max_iterations: int) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    settings = get_settings()
    provider = configure_tracing(settings)
    agent = build_specialists()[agent_name]
    llm = make_llm_factory(settings)(agent.role)

    registry = await ToolRegistry.discover(settings)
    print(f"tools registered: {registry.names()}", file=sys.stderr)
    if not registry.allowed_for(agent.name):
        print(f"no tools available to {agent.name} — are the MCP servers running?", file=sys.stderr)
        return 2

    deps = LoopDeps(
        llm=llm,
        registry=registry,
        gate=Gate(registry, make_rate_limiter(settings)),
        budget=Budget(max_iterations=max_iterations),
    )
    subtask = Subtask(
        id="adhoc-1", description=text, specialist=Specialist(agent_name), expected_output=expected
    )
    with span("task", task_id="adhoc", user_id="cli"):
        outcome = await run_agent_loop(agent, subtask, deps)
    provider.force_flush()

    if isinstance(outcome, PausedLoop):
        head = outcome.next_call
        print(
            json.dumps(
                {
                    "paused": True,
                    "tool": head.call.name,
                    "arguments": head.call.arguments,
                    "gate_reason": head.reason,
                    "iteration": outcome.checkpoint.iteration,
                    "llm_calls": len(outcome.checkpoint.cost_entries),
                },
                indent=2,
            )
        )
        print(
            "\npaused: a human must decide (use the graph + API + UI for the full flow)",
            file=sys.stderr,
        )
        return 3

    print(
        json.dumps(
            outcome.model_dump(exclude={"cost_entries", "tool_events"}), indent=2, default=str
        )
    )
    cost = outcome.total_cost_usd
    print(
        f"\n{len(outcome.cost_entries)} LLM calls | {outcome.total_tokens} tokens | "
        f"cost {'$' + format(cost, '.4f') if cost is not None else 'n/a (free tier)'} | "
        f"fallback used: {outcome.fallback_used} | tools: {outcome.tools_used} | "
        f"gated events: {len(outcome.tool_events)}",
        file=sys.stderr,
    )
    return 0 if outcome.status != "failed" else 1


if __name__ == "__main__":
    structlog.configure(processors=[structlog.processors.KeyValueRenderer(key_order=["event"])])
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="the subtask, in plain language")
    ap.add_argument(
        "--agent", default="research", choices=["research", "analysis", "writing", "code_exec"]
    )
    ap.add_argument("--expected", default="A compact, sourced summary.")
    ap.add_argument("--max-iterations", type=int, default=10)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.agent, args.text, args.expected, args.max_iterations)))
