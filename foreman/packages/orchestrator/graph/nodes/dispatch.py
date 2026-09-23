"""The dispatch node records the fan-out; the conditional edge after it emits the Send()s."""

from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.edges import ready_subtasks
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span
from packages.shared.types.plan import ExecutionPlan


async def dispatch(state: TaskState) -> dict[str, Any]:
    plan = ExecutionPlan.model_validate(state["plan"])
    ready = ready_subtasks(plan, {}, {})
    with span("node.dispatch", task_id=state["task_id"], ready=len(ready)):
        return {
            "events": [
                event(
                    "dispatch",
                    "dispatching " + ", ".join(s.id for s in ready),
                    node="dispatch",
                    subtasks=[s.id for s in ready],
                )
            ]
        }
