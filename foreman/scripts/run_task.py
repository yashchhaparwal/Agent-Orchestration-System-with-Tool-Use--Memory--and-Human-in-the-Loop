"""Run one task through the full graph in-process (no Celery), against the live stack and models.

    uv run scripts/run_task.py "Summarise the lender documents for claim CLM-4471 and draft the complaint letter"

Requires: compose stack up, seed generated, database + files MCP servers running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog

from packages.orchestrator.runtime import make_store
from packages.orchestrator.worker import execute_task
from packages.shared.asyncio_compat import use_selector_event_loop_on_windows
from packages.shared.config import get_settings
from packages.shared.types.task import TaskOptions


async def main(request: str, user_id: str) -> int:
    settings = get_settings()
    store = make_store(settings)
    row = store.create_task(user_id=user_id, request=request, options=TaskOptions())
    print(f"task_id: {row.id}", file=sys.stderr)
    outcome = await execute_task(row.id, settings=settings)
    view = store.task_view(row.id) or {}
    print(json.dumps(view, indent=2, default=str))
    print(
        f"\nstatus: {outcome['status']} | llm calls: {view.get('llm_calls')} | tokens: {view.get('tokens')} | "
        f"cost: {view.get('cost_usd')} | error: {outcome.get('error')}",
        file=sys.stderr,
    )
    return 0 if outcome["status"] == "done" else 1


if __name__ == "__main__":
    structlog.configure(processors=[structlog.processors.KeyValueRenderer(key_order=["event"])])
    ap = argparse.ArgumentParser()
    ap.add_argument("request")
    ap.add_argument("--user", default="cli")
    args = ap.parse_args()
    use_selector_event_loop_on_windows()
    sys.exit(asyncio.run(main(args.request, args.user)))
