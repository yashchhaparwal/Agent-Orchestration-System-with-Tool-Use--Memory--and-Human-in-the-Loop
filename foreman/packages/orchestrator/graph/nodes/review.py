"""Reviewer node: scores every result that has not been reviewed at its current attempt.

Runs once per superstep after the specialists (fan-in). Uses the reviewer role chain — a different
model family from the specialists. A reviewer outage is a rejection with feedback, never an
acceptance (fail closed). Results a human supplied are accepted without a model call.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from pydantic import ValidationError

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.edges import needs_review
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.llm.chains import extract_json
from packages.orchestrator.loop.messages import system_message, user_message
from packages.orchestrator.tracing.otel import span
from packages.shared.errors import RetryableError, SchemaValidationError
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewJudgement, ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult, SubtaskStatus

log = structlog.get_logger(__name__)

MIN_OUTPUT_CHARS = 10  # empty or a few characters: rejected by rule, no model asked


def _human_decisions(result: SubtaskResult) -> str:
    """Tell the reviewer which actions a human refused, so it judges the rest of the work."""
    if not result.denied_tools:
        return ""
    lines = [
        "",
        "## Human decisions",
        "A human reviewer rejected these tool calls during this subtask; the specialist was right",
        "not to perform them. Judge the rest of the work and do not reject the result for the",
        "missing action:",
        "```json",
        json.dumps(result.denied_tools, indent=2),
        "```",
        "",
    ]
    return chr(10).join(lines)


def render_review(subtask: Subtask, result: SubtaskResult) -> str:
    spec = subtask.model_dump(exclude={"inputs"})
    res = result.model_dump(exclude={"cost_entries", "tool_events"})
    nl = chr(10)
    return (
        "## Subtask specification"
        + nl
        + "```json"
        + nl
        + json.dumps(spec, indent=2, default=str)
        + nl
        + "```"
        + nl
        + nl
        + "## Specialist result"
        + nl
        + "```json"
        + nl
        + json.dumps(res, indent=2, default=str)
        + nl
        + "```"
        + nl
        + _human_decisions(result)
        + nl
        + "Judge the result as JSON."
    )


def make_review_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def review(state: TaskState) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(state["plan"]).by_id()
        results = {
            k: SubtaskResult.model_validate(v)
            for k, v in (state.get("subtask_results") or {}).items()
        }
        verdicts = {
            k: ReviewVerdict.model_validate(v)
            for k, v in (state.get("review_verdicts") or {}).items()
        }
        pending = needs_review(results, verdicts)

        costs: list[CostEntry] = []
        llm = deps.llm_for(deps.reviewer.role).with_cost_sink(costs.append)
        new_verdicts: dict[str, ReviewVerdict] = {}
        retry_increments: dict[str, int] = {}
        events = []
        retry_counts = state.get("retry_counts") or {}

        for result in pending:
            subtask = plan[result.subtask_id]
            if result.human_authored:
                verdict = ReviewVerdict(
                    accept=True,
                    score=5,
                    subtask_id=subtask.id,
                    attempt=result.attempt,
                    reviewer_model="human",
                    feedback="accepted: provided by a human",
                )
                new_verdicts[subtask.id] = verdict
                events.append(
                    event(
                        "reviewed",
                        f"{subtask.id} attempt {result.attempt}: accepted (human)",
                        node="review",
                        subtask_id=subtask.id,
                        attempt=result.attempt,
                        accept=True,
                        score=5,
                        issues=[],
                    )
                )
                continue
            if (
                result.status == SubtaskStatus.COMPLETED
                and len(result.output.strip()) < MIN_OUTPUT_CHARS
            ):
                verdict = ReviewVerdict(
                    accept=False,
                    score=1,
                    subtask_id=subtask.id,
                    attempt=result.attempt,
                    reviewer_model="rule",
                    issues=["empty output"],
                    feedback=(
                        "The result is marked completed but its output is empty. Produce the "
                        "actual content the subtask asks for."
                    ),
                )
                new_verdicts[subtask.id] = verdict
                retry_increments[subtask.id] = retry_counts.get(subtask.id, 0) + 1
                retry_counts = {**retry_counts, **retry_increments}
                events.append(
                    event(
                        "reviewed",
                        f"{subtask.id} attempt {result.attempt}: rejected by rule (empty output)",
                        node="review",
                        subtask_id=subtask.id,
                        attempt=result.attempt,
                        accept=False,
                        score=1,
                        issues=["empty output"],
                    )
                )
                continue
            with span(
                "node.review",
                task_id=state["task_id"],
                subtask_id=subtask.id,
                attempt=result.attempt,
            ) as s:
                messages = [
                    system_message(deps.reviewer.system_prompt),
                    user_message(render_review(subtask, result)),
                ]
                model_used = ""
                try:
                    resp = await llm.chat(
                        messages,
                        response_schema=ReviewJudgement.model_json_schema(),
                        schema_name="ReviewJudgement",
                        max_tokens=1500,
                        timeout_s=deps.config.llm_timeout_s,
                    )
                    judgement = ReviewJudgement.model_validate(extract_json(resp.content))
                    model_used = f"{resp.provider}/{resp.model}"
                except (RetryableError, SchemaValidationError, ValidationError) as e:
                    log.warning("review.unavailable", subtask_id=subtask.id, error=str(e)[:300])
                    judgement = ReviewJudgement(
                        accept=False,
                        score=1,
                        issues=["reviewer unavailable"],
                        feedback=f"The reviewer could not assess this result ({str(e)[:160]}). "
                        "Re-submit with every claim clearly sourced.",
                    )
                verdict = ReviewVerdict(
                    **judgement.model_dump(),
                    subtask_id=subtask.id,
                    attempt=result.attempt,
                    reviewer_model=model_used,
                )
                new_verdicts[subtask.id] = verdict
                s.set_attribute("accept", verdict.accept)
                s.set_attribute("score", verdict.score)
                if not verdict.accept:
                    retry_increments[subtask.id] = retry_counts.get(subtask.id, 0) + 1
                    retry_counts = {**retry_counts, **retry_increments}
                events.append(
                    event(
                        "reviewed",
                        f"{subtask.id} attempt {result.attempt}: "
                        f"{'accepted' if verdict.accept else 'rejected'} ({verdict.score}/5)",
                        node="review",
                        subtask_id=subtask.id,
                        attempt=result.attempt,
                        accept=verdict.accept,
                        score=verdict.score,
                        issues=verdict.issues,
                    )
                )

        # Persist what is known so far, so the operator UI shows live progress between pauses.
        await asyncio.to_thread(
            deps.store.record_progress,
            state["task_id"],
            results=results,
            verdicts={**verdicts, **new_verdicts},
            cost_entries=[CostEntry.model_validate(c) for c in (state.get("cost_ledger") or [])]
            + costs,
            tool_events=[ToolEvent.model_validate(t) for t in (state.get("tool_events") or [])],
        )
        return {
            "review_verdicts": new_verdicts,
            "retry_counts": retry_increments,
            "cost_ledger": costs,
            "events": events,
        }

    return review
