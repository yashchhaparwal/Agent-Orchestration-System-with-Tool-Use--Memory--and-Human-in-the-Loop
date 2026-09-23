from __future__ import annotations

import json
from typing import Any

import pytest

from packages.evals.judge import Judge, render_judge_prompt
from packages.shared.errors import RetryableError
from packages.shared.types.evals import GoldenTask, PauseRecord, Trajectory
from packages.shared.types.llm import LLMResponse, Usage


class FakeLLM:
    role = "reviewer"

    def __init__(self, content: str) -> None:
        self.content = content
        self.seen: list[list[dict[str, Any]]] = []

    def with_cost_sink(self, on_cost: Any) -> FakeLLM:
        return self

    async def chat(self, messages: list[dict[str, Any]], **kw: Any) -> LLMResponse:
        self.seen.append(messages)
        assert kw["schema_name"] == "JudgeOutput"
        return LLMResponse(
            content=self.content,
            provider="groq",
            model="qwen",
            usage=Usage(input_tokens=1, output_tokens=1),
        )


TASK = GoldenTask(
    id="t",
    category="lookup",
    title="t",
    request="list the loans on CLM-4471",
    rubric=["Lists three loans", "No invented fields"],
)
TRAJ = Trajectory(
    task_id="x",
    golden_id="t",
    run_index=0,
    user_id="u",
    status="done",
    elapsed_s=5.0,
    llm_calls=3,
    deliverable_title="Loans",
    deliverable_body="Three loans: 464, 465, 466.",
    pauses=[
        PauseRecord(kind="tool_call", level="L2", tool="actions_send_email", decision="reject")
    ],
)


def test_prompt_carries_request_rubric_and_facts() -> None:
    p = render_judge_prompt(TASK, TRAJ)
    assert (
        "list the loans on CLM-4471" in p
        and "1. Lists three loans" in p
        and "2. No invented fields" in p
    )
    assert "Three loans: 464" in p and "L2 actions_send_email" in p and "final status: done" in p


async def test_judge_parses_verdict_and_labels_the_model() -> None:
    llm = FakeLLM(
        json.dumps(
            {
                "score": 4,
                "criteria": [{"criterion": "Lists three loans", "met": True, "note": ""}],
                "summary": "fine",
            }
        )
    )
    verdict = await Judge(llm).score(TASK, TRAJ)  # type: ignore[arg-type]
    assert verdict.score == 4 and verdict.criteria[0].met and verdict.model == "groq/qwen"
    assert llm.seen[0][0]["role"] == "system" and "impartial evaluator" in llm.seen[0][0]["content"]


async def test_invalid_verdict_is_retryable() -> None:
    with pytest.raises(RetryableError):
        await Judge(FakeLLM("not json")).score(TASK, TRAJ)  # type: ignore[arg-type]
    with pytest.raises(RetryableError):
        await Judge(FakeLLM(json.dumps({"score": 9}))).score(TASK, TRAJ)  # type: ignore[arg-type]
