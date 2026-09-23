"""Builders for the OpenAI-format message list the loop maintains.

Assistant turns are rebuilt from the standard fields only (``role``, ``content``, ``tool_calls``).
Providers decorate their responses with extra keys (``reasoning`` on Groq's gpt-oss, and others),
and re-sending those to a *different* provider after a fallback is a 422 — so they never enter
the history.
"""

from __future__ import annotations

import json
from typing import Any

from packages.shared.types.llm import LLMMessage, LLMResponse
from packages.shared.types.subtask import Subtask
from packages.shared.types.tools import ToolResult

MAX_TOOL_RESULT_CHARS = 24_000


def system_message(text: str) -> LLMMessage:
    return {"role": "system", "content": text}


def user_message(text: str) -> LLMMessage:
    return {"role": "user", "content": text}


def render_subtask(subtask: Subtask) -> str:
    parts = [
        f"## Subtask {subtask.id}",
        subtask.description.strip(),
    ]
    if subtask.needs:
        parts.append("## Needs from earlier steps\n" + "\n".join(f"- {n}" for n in subtask.needs))
    if subtask.inputs:
        parts.append(
            "## Inputs from earlier steps\n```json\n"
            + json.dumps(subtask.inputs, indent=2, default=str)
            + "\n```"
        )
    if subtask.expected_output:
        parts.append("## Expected output\n" + subtask.expected_output.strip())
    parts.append(
        "When you are done, call `submit_result` exactly once with your deliverable. "
        "Do not answer in plain text."
    )
    return "\n\n".join(parts)


def assistant_message(response: LLMResponse) -> LLMMessage:
    """The assistant turn with only portable fields, so any provider in the chain accepts it."""
    msg: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in response.tool_calls
        ]
    return msg


def tool_messages(results: list[ToolResult]) -> list[LLMMessage]:
    """One `tool` message per result; all appended together before the next model call."""
    out: list[LLMMessage] = []
    for r in results:
        content = r.content
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = (
                content[:MAX_TOOL_RESULT_CHARS]
                + f"\n…[truncated {len(r.content) - MAX_TOOL_RESULT_CHARS} chars]"
            )
        if r.is_error:
            content = f"ERROR: {content}"
        # No "name" here: the tool role carries only tool_call_id + content in the current OpenAI
        # spec (the assistant's tool_calls already binds id -> name); strict routes 400 on extras.
        out.append({"role": "tool", "tool_call_id": r.tool_call_id, "content": content})
    return out
