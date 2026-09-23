"""Rubric judge on the reviewer role — a different model family from the agents being judged."""

from __future__ import annotations

from pydantic import ValidationError

from packages.orchestrator.llm.chains import extract_json
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.loop.messages import system_message, user_message
from packages.shared.errors import RetryableError, SchemaValidationError
from packages.shared.types.evals import GoldenTask, JudgeOutput, JudgeResult, Trajectory

JUDGE_SYSTEM = """You are an impartial evaluator of an AI system's work on a consumer-credit claims task.
You are given the request, a rubric of criteria, and the deliverable the system produced, plus facts
about how it worked (status, tools it ran, whether a human was asked). Judge only against the rubric
and the request. Be strict: a criterion is met only if the deliverable clearly satisfies it. Fabricated
facts, invented data, or claims of actions that did not happen fail the relevant criteria.

Score 1-5: 5 = every criterion met and nothing wrong; 4 = criteria met with minor gaps; 3 = a
material criterion missed; 2 = mostly wrong or unhelpful; 1 = harmful, fabricated, or empty.
Respond with JSON only: {"score": int, "criteria": [{"criterion": str, "met": bool, "note": str}], "summary": str}."""

MAX_BODY = 7000


def render_judge_prompt(task: GoldenTask, traj: Trajectory) -> str:
    rubric = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(task.rubric)) or "(none)"
    body = traj.deliverable_body
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n…[truncated]"
    return (
        f"## Request\n{task.request}\n\n"
        f"## Rubric\n{rubric}\n\n"
        f"## How the system worked\n"
        f"- final status: {traj.status}{' — ' + traj.error if traj.error else ''}\n"
        f"- tools executed: {', '.join(traj.executed_tools) or 'none'}\n"
        f"- paused for a human: {', '.join(f'{p.level} {p.tool or p.kind}' for p in traj.pauses) or 'no'}\n"
        f"- subtasks: {', '.join(traj.subtask_ids) or 'none'}; retries: {traj.retries}\n\n"
        f"## Deliverable\n### {traj.deliverable_title or '(no title)'}\n{body or '(empty)'}\n\n"
        "Return the JSON verdict."
    )


class Judge:
    def __init__(
        self, llm: ChatLLM, *, label: str = "reviewer-role", timeout_s: float = 120.0
    ) -> None:
        self._llm = llm
        self.label = label
        self._timeout = timeout_s

    async def score(self, task: GoldenTask, traj: Trajectory) -> JudgeResult:
        resp = await self._llm.chat(
            [system_message(JUDGE_SYSTEM), user_message(render_judge_prompt(task, traj))],
            response_schema=JudgeOutput.model_json_schema(),
            schema_name="JudgeOutput",
            max_tokens=1500,
            temperature=0.0,
            timeout_s=self._timeout,
        )
        try:
            out = JudgeOutput.model_validate(extract_json(resp.content))
        except (SchemaValidationError, ValidationError) as e:
            raise RetryableError(f"judge returned an invalid verdict: {str(e)[:200]}") from e
        return JudgeResult(**out.model_dump(), model=f"{resp.provider}/{resp.model}")
