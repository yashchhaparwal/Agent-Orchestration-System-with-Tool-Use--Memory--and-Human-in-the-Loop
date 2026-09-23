from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span


async def intake(state: TaskState) -> dict[str, Any]:
    with span("node.intake", task_id=state["task_id"]):
        request = (state.get("request") or "").strip()
        if not request:
            return {
                "status": "failed",
                "error": "empty request",
                "events": [event("failed", "empty request", node="intake")],
            }
        return {
            "status": "running",
            "events": [event("started", f"task accepted ({len(request)} chars)", node="intake")],
        }
