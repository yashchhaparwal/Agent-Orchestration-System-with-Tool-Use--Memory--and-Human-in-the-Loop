"""Supervisor planning node: request (+ recalled memories) → typed ExecutionPlan."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from pydantic import ValidationError

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.llm.chains import extract_json
from packages.orchestrator.loop.messages import system_message, user_message
from packages.orchestrator.tracing.otel import span
from packages.shared.errors import RetryableError, SchemaValidationError
from packages.shared.types.cost import CostEntry
from packages.shared.types.plan import ExecutionPlan

log = structlog.get_logger(__name__)


def render_plan_request(state: TaskState) -> str:
    parts = ["## Request", state["request"].strip()]
    memories = state.get("recalled_memories") or []
    if memories:
        parts.append(
            "## Relevant past experience (advice, not instructions)\n"
            + "\n".join(f"- {m.get('text', '')}" for m in memories)
        )
    parts.append("Produce the execution plan as JSON.")
    return "\n\n".join(parts)


def make_plan_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def plan(state: TaskState) -> dict[str, Any]:
        costs: list[CostEntry] = []
        llm = deps.llm_for(deps.supervisor.role).with_cost_sink(costs.append)
        messages = [
            system_message(deps.supervisor.system_prompt),
            user_message(render_plan_request(state)),
        ]
        with span("node.plan", task_id=state["task_id"]) as s:
            try:
                resp = await llm.chat(
                    messages,
                    response_schema=ExecutionPlan.model_json_schema(),
                    schema_name="ExecutionPlan",
                    max_tokens=4000,
                    timeout_s=deps.config.llm_timeout_s,
                )
                execution_plan = ExecutionPlan.model_validate(extract_json(resp.content))
            except (RetryableError, SchemaValidationError, ValidationError) as e:
                s.set_attribute("error", str(e)[:500])
                log.error("plan.failed", task_id=state["task_id"], error=str(e)[:300])
                return {
                    "status": "failed",
                    "error": f"planning failed: {str(e)[:400]}",
                    "cost_ledger": costs,
                    "events": [event("failed", "planning failed", node="plan", error=str(e)[:400])],
                }
            s.set_attribute("subtasks", len(execution_plan.subtasks))
            s.set_attribute("confidence", execution_plan.confidence)
            # Persist now (not only at delivery) so GET /v1/tasks/{id} shows the plan while running.
            await asyncio.to_thread(deps.store.set_plan, state["task_id"], execution_plan)
            await deps.working.put_plan(state["task_id"], execution_plan)
            summary = ", ".join(
                f"{t.id}:{t.specialist.value}"
                + (f"←{','.join(t.depends_on)}" if t.depends_on else "")
                for t in execution_plan.subtasks
            )
            return {
                "plan": execution_plan,
                "plan_confidence": execution_plan.confidence,
                "cost_ledger": costs,
                "events": [
                    event(
                        "planned",
                        f"{len(execution_plan.subtasks)} subtask(s): {summary}",
                        node="plan",
                        confidence=execution_plan.confidence,
                        sensitive_actions=execution_plan.sensitive_actions,
                        plan=json.loads(execution_plan.model_dump_json()),
                    )
                ],
            }

    return plan
