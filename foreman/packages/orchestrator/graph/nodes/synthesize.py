"""Supervisor synthesis node: accepted results → one typed Deliverable."""

from __future__ import annotations

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
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.subtask import SubtaskResult

log = structlog.get_logger(__name__)


def render_synthesis(state: TaskState) -> str:
    plan = ExecutionPlan.model_validate(state["plan"])
    results = {
        k: SubtaskResult.model_validate(v) for k, v in (state.get("subtask_results") or {}).items()
    }
    blocks = [f"## Request\n{state['request'].strip()}"]
    for sid in plan.order():
        sub, res = plan.by_id()[sid], results.get(sid)
        if res is None:
            continue
        blocks.append(
            f"## Result {sid} — {sub.specialist.value}: {sub.description.strip()}\n"
            f"status: {res.status.value} · confidence: {res.self_confidence:.2f}\n"
            f"sources: {json.dumps(res.sources)}\n\n{res.output.strip()}"
            + (f"\n\nnotes: {res.notes.strip()}" if res.notes.strip() else "")
        )
    blocks.append("Assemble the final deliverable as JSON.")
    return "\n\n".join(blocks)


MIN_BODY_CHARS = 200
SUBSTANTIAL_INPUT_CHARS = 400


def degenerate(deliverable: Deliverable, state: TaskState) -> bool:
    """A placeholder body ("final", "see above") when the accepted results carried real content."""
    inputs = sum(
        len(str((v.get("output") if isinstance(v, dict) else getattr(v, "output", "")) or ""))
        for v in (state.get("subtask_results") or {}).values()
    )
    return inputs >= SUBSTANTIAL_INPUT_CHARS and len(deliverable.body.strip()) < MIN_BODY_CHARS


def make_synthesize_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def synthesize(state: TaskState) -> dict[str, Any]:
        costs: list[CostEntry] = []
        llm = deps.llm_for(deps.supervisor.role).with_cost_sink(costs.append)
        messages = [system_message(deps.synthesize_prompt), user_message(render_synthesis(state))]
        with span("node.synthesize", task_id=state["task_id"]) as s:
            try:
                deliverable: Deliverable | None = None
                for attempt in range(2):
                    resp = await llm.chat(
                        messages,
                        response_schema=Deliverable.model_json_schema(),
                        schema_name="Deliverable",
                        max_tokens=6000,
                        timeout_s=deps.config.llm_timeout_s,
                    )
                    candidate = Deliverable.model_validate(extract_json(resp.content))
                    if not degenerate(candidate, state):
                        deliverable = candidate
                        break
                    log.warning(
                        "synthesize.degenerate",
                        task_id=state["task_id"],
                        attempt=attempt + 1,
                        body_chars=len(candidate.body.strip()),
                    )
                    messages = [
                        *messages,
                        {"role": "assistant", "content": resp.content or ""},
                        user_message(
                            "That deliverable has no body. Write the COMPLETE deliverable in the "
                            "`body` field: every result above, in full, in Markdown, citing sources."
                        ),
                    ]
                if deliverable is None:
                    raise SchemaValidationError(
                        "the deliverable body was empty twice — refusing to deliver a placeholder"
                    )
            except (RetryableError, SchemaValidationError, ValidationError) as e:
                s.set_attribute("error", str(e)[:500])
                log.error("synthesize.failed", task_id=state["task_id"], error=str(e)[:300])
                return {
                    "status": "failed",
                    "error": f"synthesis failed: {str(e)[:400]}",
                    "cost_ledger": costs,
                    "events": [event("failed", "synthesis failed", node="synthesize")],
                }
            s.set_attribute("confidence", deliverable.confidence)
            return {
                "final_output": deliverable,
                "cost_ledger": costs,
                "events": [
                    event(
                        "synthesized",
                        f"deliverable: {deliverable.title}",
                        node="synthesize",
                        confidence=deliverable.confidence,
                    )
                ],
            }

    return synthesize
