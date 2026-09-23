"""The agent loop (diagram 03, Architecture.md §5.1) — pausable.

    build context → LLM → tool calls? → gate → registry → results appended → LLM … → submit_result

The model finishes by calling the ``submit_result`` tool. Every other tool call goes through
``Gate.decide_async`` and only then ``ToolRegistry.invoke``. All results of a turn are appended
before the next model call. Guards: iterations, tokens, cost, wall-clock (``Budget``).

When the gate answers ``approve`` the loop cannot continue without a human. Instead of blocking,
it returns a ``PausedLoop`` carrying a serialisable ``LoopCheckpoint`` (message history, budgets,
the pending calls, results already produced in the same turn). The graph stores it, pauses via
``interrupt()`` in a node that does no other work, and on resume calls ``run_agent_loop`` again
with the checkpoint and the human's decision — no model call is ever repeated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError
from pydantic import BaseModel, ValidationError
from pydantic import Field as PydanticField

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.llm.openai_compat import strict_schema
from packages.orchestrator.loop.budgets import Budget, BudgetTracker
from packages.orchestrator.loop.messages import (
    assistant_message,
    render_subtask,
    system_message,
    tool_messages,
    user_message,
)
from packages.orchestrator.tracing.otel import span, tool_span
from packages.shared.errors import BudgetExceededError, RetryableError
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import GateAction, ToolEvent
from packages.shared.types.llm import LLMMessage, LLMResponse
from packages.shared.types.subtask import SubmittedResult, Subtask, SubtaskResult, SubtaskStatus
from packages.shared.types.tools import RiskClass, ToolCall, ToolResult
from packages.tools.registry.registry import ToolRegistry

log = structlog.get_logger(__name__)

SUBMIT_TOOL = "submit_result"
MAX_NUDGES = 2


def submit_tool_schema() -> dict[str, Any]:
    schema = SubmittedResult.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": SUBMIT_TOOL,
            "description": (
                "Finish the subtask. Call this exactly once, when you have the deliverable. "
                "Put the full deliverable in `output`; list every source you relied on."
            ),
            "parameters": strict_schema(schema),
        },
    }


def args_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


# ---------- pause / resume models ----------


class PendingCall(BaseModel):
    call: ToolCall
    reason: str
    risk: RiskClass | None = None


class LoopCheckpoint(BaseModel):
    """Everything needed to continue a paused loop later, in another process."""

    messages: list[dict[str, Any]]
    iteration: int
    nudges: int
    tools_used: list[str]
    cost_entries: list[CostEntry]
    tool_events: list[ToolEvent]
    total_tokens: int
    total_cost_usd: float
    pending: list[PendingCall] = PydanticField(min_length=1)
    ready_results: list[ToolResult] = PydanticField(default_factory=list)
    submitted: SubmittedResult | None = None
    denied: dict[str, str] = PydanticField(
        default_factory=dict,
        description="tool -> reason; a human rejection is final for this subtask",
    )


class PausedLoop(BaseModel):
    subtask_id: str
    agent: str
    attempt: int
    checkpoint: LoopCheckpoint

    @property
    def next_call(self) -> PendingCall:
        return self.checkpoint.pending[0]


class LoopResume(BaseModel):
    checkpoint: LoopCheckpoint
    decision: ApprovalDecision


@dataclass
class LoopDeps:
    llm: ChatLLM
    registry: ToolRegistry
    gate: Gate
    budget: Budget = field(default_factory=Budget)
    llm_timeout_s: float = 90.0
    task_id: str = ""  # threaded into tools that record it (the actions server's outbox)


LoopOutcome = SubtaskResult | PausedLoop


async def run_agent_loop(
    agent: AgentSpec,
    subtask: Subtask,
    deps: LoopDeps,
    *,
    attempt: int = 1,
    resume: LoopResume | None = None,
    denied: dict[str, str] | None = None,
) -> LoopOutcome:
    run = _Run(agent, subtask, deps, attempt, denied)
    if resume is not None:
        return await run.resume(resume.checkpoint, resume.decision)
    return await run.run()


class _Run:
    def __init__(
        self,
        agent: AgentSpec,
        subtask: Subtask,
        deps: LoopDeps,
        attempt: int,
        denied: dict[str, str] | None = None,
    ) -> None:
        self.agent, self.subtask, self.deps, self.attempt = agent, subtask, deps, attempt
        self.tracker = BudgetTracker(deps.budget)
        self.cost_entries: list[CostEntry] = []
        self.tool_events: list[ToolEvent] = []
        self.tools_used: list[str] = []
        self.nudges = 0
        self.iteration = 0
        self.denied: dict[str, str] = dict(denied or {})
        self.messages: list[LLMMessage] = [
            system_message(agent.system_prompt),
            user_message(render_subtask(subtask)),
        ]
        self.tools = deps.registry.schemas_for(agent.name) + [submit_tool_schema()]
        self.context = f"{subtask.id}: {subtask.description}"
        self.llm = deps.llm.with_cost_sink(self._on_cost)

    # ---------- bookkeeping ----------

    def _on_cost(self, entry: CostEntry) -> None:
        self.cost_entries.append(entry)
        self.tracker.record(entry)

    def _restore(self, cp: LoopCheckpoint) -> None:
        self.messages = [dict(m) for m in cp.messages]
        self.iteration, self.nudges = cp.iteration, cp.nudges
        self.tools_used = list(cp.tools_used)
        self.cost_entries = list(cp.cost_entries)
        self.tool_events = list(cp.tool_events)
        self.denied = {**self.denied, **cp.denied}
        self.tracker.iterations = cp.iteration
        self.tracker.total_tokens = cp.total_tokens
        self.tracker.total_cost_usd = cp.total_cost_usd

    def _checkpoint(
        self,
        pending: list[PendingCall],
        ready: list[ToolResult],
        submitted: SubmittedResult | None,
    ) -> LoopCheckpoint:
        return LoopCheckpoint(
            messages=self.messages,
            iteration=self.iteration,
            nudges=self.nudges,
            tools_used=self.tools_used,
            cost_entries=self.cost_entries,
            tool_events=self.tool_events,
            total_tokens=self.tracker.total_tokens,
            total_cost_usd=self.tracker.total_cost_usd,
            pending=pending,
            ready_results=ready,
            submitted=submitted,
            denied=self.denied,
        )

    def _paused(
        self,
        pending: list[PendingCall],
        ready: list[ToolResult],
        submitted: SubmittedResult | None,
    ) -> PausedLoop:
        log.info(
            "agent.paused",
            agent=self.agent.name,
            subtask_id=self.subtask.id,
            pending=[p.call.name for p in pending],
        )
        return PausedLoop(
            subtask_id=self.subtask.id,
            agent=self.agent.name,
            attempt=self.attempt,
            checkpoint=self._checkpoint(pending, ready, submitted),
        )

    def _finish(
        self, submitted: SubmittedResult, *, error: str | None = None, human: bool = False
    ) -> SubtaskResult:
        return SubtaskResult(
            **submitted.model_dump(),
            subtask_id=self.subtask.id,
            attempt=self.attempt,
            tools_used=sorted(set(self.tools_used)),
            iterations=self.iteration,
            cost_entries=self.cost_entries,
            tool_events=self.tool_events,
            fallback_used=any(c.fallback for c in self.cost_entries),
            human_authored=human,
            denied_tools=dict(self.denied),
            error=error,
        )

    def _failed(self, reason: str) -> SubtaskResult:
        return self._finish(
            SubmittedResult(
                status=SubtaskStatus.FAILED, output="", self_confidence=0.0, notes=reason
            ),
            error=reason,
        )

    # ---------- resume ----------

    async def resume(self, cp: LoopCheckpoint, decision: ApprovalDecision) -> LoopOutcome:
        self._restore(cp)
        pending = list(cp.pending)
        ready = list(cp.ready_results)
        head = pending.pop(0)

        if decision.decision == DecisionKind.TAKE_OVER:
            output = str(decision.payload.get("output", "")).strip()
            if output:
                self.tool_events.append(
                    self._event(head, GateAction.APPROVE, f"taken over by {decision.decided_by}")
                )
                sources = [str(s) for s in decision.payload.get("sources", [])]
                return self._finish(
                    SubmittedResult(
                        status=SubtaskStatus.COMPLETED,
                        output=output,
                        sources=sources,
                        self_confidence=1.0,
                        notes=f"output provided by {decision.decided_by}",
                    ),
                    human=True,
                )
            decision = decision.model_copy(
                update={"decision": DecisionKind.REJECT, "reason": "take-over without an output"}
            )

        ready.append(await self._resolve(head, decision))
        if pending:  # more calls in the same turn still need a decision
            return self._paused(pending, ready, cp.submitted)
        self.messages.extend(tool_messages(ready))
        if cp.submitted is not None:
            return self._finish(cp.submitted)
        return await self.run()

    async def _resolve(self, head: PendingCall, decision: ApprovalDecision) -> ToolResult:
        call = head.call
        who = decision.decided_by
        if decision.decision == DecisionKind.REJECT:
            self.tool_events.append(
                self._event(head, GateAction.APPROVE, f"rejected by {who}: {decision.reason}")
            )
            why = decision.reason or "no reason given"
            self.denied[call.name] = why
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                is_error=True,
                content=(
                    f"rejected by the human reviewer: {why}. Do not call {call.name} again in this "
                    "subtask; finish with what you have and say the action was not taken."
                ),
            )
        if decision.decision == DecisionKind.MODIFY:
            arguments = decision.payload.get("arguments")
            spec = self.deps.registry.get(call.name)
            if not isinstance(arguments, dict) or spec is None:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    is_error=True,
                    content="modified call had no valid arguments; not executed",
                )
            try:
                Draft202012Validator(spec.input_schema).validate(arguments)
            except JsonSchemaError as e:
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    is_error=True,
                    content=f"modified arguments do not match the tool schema: {e.message}",
                )
            call = call.model_copy(update={"arguments": arguments})
        return await self._invoke(
            call,
            head.risk,
            reason=f"{decision.decision.value} by {who}: {decision.reason}".strip(": "),
        )

    # ---------- the loop ----------

    async def run(self) -> LoopOutcome:
        with span(
            f"agent.{self.agent.name}",
            subtask_id=self.subtask.id,
            agent=self.agent.name,
            attempt=self.attempt,
            resumed=self.iteration > 0,
        ):
            while True:
                try:
                    self.iteration = self.tracker.start_iteration()
                except BudgetExceededError as e:
                    return self._failed(str(e))
                outcome = await self._turn()
                if outcome is not None:
                    return outcome

    async def _turn(self) -> LoopOutcome | None:
        """One model call and its tool calls. Returns an outcome to stop, None to continue."""
        with span(
            f"agent.{self.agent.name}.iteration",
            subtask_id=self.subtask.id,
            iteration=self.iteration,
        ):
            try:
                response = await self.llm.chat(
                    self.messages, tools=self.tools, timeout_s=self.deps.llm_timeout_s
                )
            except BudgetExceededError as e:
                return self._failed(str(e))
            except RetryableError as e:
                return self._failed(f"all model providers failed: {e}")

            self.messages.append(assistant_message(response))

            if not response.tool_calls:
                self.nudges += 1
                if self.nudges > MAX_NUDGES:
                    return self._failed("model stopped without calling submit_result")
                self.messages.append(user_message("You must finish by calling `submit_result`."))
                return None

            submitted, results, other_calls = self._split_submit(response)
            executed, pending = await self._gate_and_execute(other_calls)
            results.extend(executed)
            if pending:
                return self._paused(pending, results, submitted)
            self.messages.extend(tool_messages(results))  # every result before the next call
            if submitted is not None:
                log.info(
                    "agent.submitted",
                    agent=self.agent.name,
                    subtask_id=self.subtask.id,
                    iterations=self.iteration,
                )
                return self._finish(submitted)
            return None

    def _split_submit(
        self, response: LLMResponse
    ) -> tuple[SubmittedResult | None, list[ToolResult], list[ToolCall]]:
        submitted: SubmittedResult | None = None
        results: list[ToolResult] = []
        other_calls: list[ToolCall] = []
        for call in response.tool_calls:
            if call.name != SUBMIT_TOOL:
                other_calls.append(call)
                continue
            try:
                if call.parse_error:
                    raise ValueError(call.parse_error)
                submitted = SubmittedResult.model_validate(call.arguments)
                results.append(ToolResult(tool_call_id=call.id, name=call.name, content="accepted"))
            except (ValidationError, ValueError) as e:
                results.append(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        is_error=True,
                        content=f"submit_result rejected; fix and call again: {str(e)[:600]}",
                    )
                )
        return submitted, results, other_calls

    async def _gate_and_execute(
        self, calls: list[ToolCall]
    ) -> tuple[list[ToolResult], list[PendingCall]]:
        """Gate every call; execute the allowed ones concurrently; collect the ones needing a human."""
        results: list[ToolResult] = []
        pending: list[PendingCall] = []
        to_run: list[tuple[ToolCall, RiskClass | None]] = []
        gated: list[ToolCall] = []
        for call in calls:
            if call.name in self.denied:  # a human already said no: final for this subtask
                reason = f"a human already rejected {call.name} for this subtask: {self.denied[call.name]}"
                self.tool_events.append(
                    self._event(PendingCall(call=call, reason=reason), GateAction.BLOCK, reason)
                )
                results.append(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        is_error=True,
                        content=f"denied: {reason}. Do not retry it.",
                    )
                )
            else:
                gated.append(call)
        decisions = await asyncio.gather(
            *(self.deps.gate.decide_async(self.agent.name, c, context=self.context) for c in gated)
        )
        for call, decision in zip(gated, decisions, strict=True):
            if decision.action == GateAction.BLOCK:
                self.tool_events.append(
                    self._event(
                        PendingCall(call=call, reason=decision.reason, risk=decision.risk),
                        GateAction.BLOCK,
                        decision.reason,
                    )
                )
                results.append(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        is_error=True,
                        content=f"denied: {decision.reason}",
                    )
                )
            elif decision.action == GateAction.APPROVE:
                pending.append(PendingCall(call=call, reason=decision.reason, risk=decision.risk))
            else:
                to_run.append((call, decision.risk))
        executed = await asyncio.gather(
            *(self._invoke(call, risk, reason="allowed by gate") for call, risk in to_run)
        )
        results.extend(executed)
        return results, pending

    async def _invoke(self, call: ToolCall, risk: RiskClass | None, *, reason: str) -> ToolResult:
        spec = self.deps.registry.get(call.name)
        server = spec.server if spec else "unknown"
        with tool_span(server=server, tool=call.name, agent=self.agent.name) as s:
            s.set_attribute("args_hash", args_hash(call.arguments))
            result = await self.deps.registry.invoke(call, task_id=self.deps.task_id)
            s.set_attribute("ok", not result.is_error)
            s.set_attribute("result_size", len(result.content))
            s.set_attribute("latency_ms", result.latency_ms)
        self.tools_used.append(call.name)
        gate_action = GateAction.ALLOW if reason == "allowed by gate" else GateAction.APPROVE
        self.tool_events.append(
            ToolEvent(
                subtask_id=self.subtask.id,
                agent=self.agent.name,
                tool=call.name,
                args_hash=args_hash(call.arguments),
                risk=risk,
                decision=gate_action,
                reason=reason,
                ok=not result.is_error,
                latency_ms=result.latency_ms,
                result_size=len(result.content),
            )
        )
        return result

    def _event(self, head: PendingCall, action: GateAction, reason: str) -> ToolEvent:
        return ToolEvent(
            subtask_id=self.subtask.id,
            agent=self.agent.name,
            tool=head.call.name,
            args_hash=args_hash(head.call.arguments),
            risk=head.risk,
            decision=action,
            reason=reason,
        )
