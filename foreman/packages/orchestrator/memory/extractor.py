"""Turn a finished task into 0–3 lessons (Architecture.md §7.3, Rules.md §11).

``build_task_digest`` compresses the graph state into a short, factual account — what was asked,
what the plan was, how each subtask went, what humans decided and why, what was delivered — and
the cheap-role model returns ``MemoryExtraction``. The extractor never sees raw tool output.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from packages.orchestrator.graph.state import TaskState
from packages.orchestrator.llm.chains import extract_json
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.loop.messages import system_message, user_message
from packages.shared.errors import RetryableError, SchemaValidationError
from packages.shared.types.memory import MemoryExtraction, MemoryRecord
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult

MAX_DIGEST_CHARS = 6000


def build_task_digest(state: TaskState, approvals: list[dict[str, Any]]) -> str:
    plan = state.get("plan")
    plan_model = ExecutionPlan.model_validate(plan) if plan is not None else None
    results = {
        k: SubtaskResult.model_validate(v) for k, v in (state.get("subtask_results") or {}).items()
    }
    verdicts = {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }
    lines = [
        f"status: {state.get('status', 'unknown')}",
        f"request: {state['request'].strip()[:600]}",
    ]
    if plan_model is not None:
        lines.append("plan:")
        for s in plan_model.subtasks:
            lines.append(f"  - {s.id} ({s.specialist.value}): {s.description[:160]}")
        if plan_model.sensitive_actions:
            lines.append("  sensitive actions: " + "; ".join(plan_model.sensitive_actions))
    if results:
        lines.append("subtasks:")
        for sid, r in sorted(results.items()):
            v = verdicts.get(sid)
            verdict = (
                f"accepted {v.score}/5"
                if v and v.accept
                else f"rejected {v.score}/5"
                if v
                else "unreviewed"
            )
            issues = "; ".join(v.issues[:3]) if v and v.issues else ""
            lines.append(
                f"  - {sid}: {r.status.value}, attempt {r.attempt}, {verdict}"
                + (f", issues: {issues}" if issues else "")
                + (f", tools: {', '.join(r.tools_used)}" if r.tools_used else "")
                + (", written by a human" if r.human_authored else "")
                + (f", human refused: {', '.join(r.denied_tools)}" if r.denied_tools else "")
                + (f", notes: {r.notes[:160]}" if r.notes else "")
            )
    if not approvals:
        lines.append("human decisions: none (no approval was requested in this task)")
    else:
        lines.append("human decisions:")
        for a in approvals:
            what = a.get("proposed_action", {}).get("tool") or a.get("kind")
            lines.append(
                f"  - {a.get('level')} {what}: {a.get('status')}"
                + (f" by {a.get('decided_by')}" if a.get("decided_by") else "")
                + (f" — reason: {a.get('reason')}" if a.get("reason") else "")
            )
    final = state.get("final_output")
    if final is not None:
        title = getattr(final, "title", None) or (
            final.get("title") if isinstance(final, dict) else ""
        )
        lines.append(f"deliverable: {title}")
    if state.get("error"):
        lines.append(f"error: {str(state['error'])[:300]}")
    return "\n".join(lines)[:MAX_DIGEST_CHARS]


async def extract_memories(
    llm: ChatLLM, system_prompt: str, digest: str, *, timeout_s: float = 60.0
) -> list[MemoryRecord]:
    resp = await llm.chat(
        [
            system_message(system_prompt),
            user_message("## Task digest\n" + digest + "\n\nReturn the lessons as JSON."),
        ],
        response_schema=MemoryExtraction.model_json_schema(),
        schema_name="MemoryExtraction",
        max_tokens=1200,
        timeout_s=timeout_s,
    )
    try:
        return MemoryExtraction.model_validate(extract_json(resp.content)).records
    except (SchemaValidationError, ValidationError) as e:
        raise RetryableError(f"memory extractor returned an invalid payload: {str(e)[:200]}") from e


def records_as_json(records: list[MemoryRecord]) -> str:
    return json.dumps([r.model_dump(mode="json") for r in records])
