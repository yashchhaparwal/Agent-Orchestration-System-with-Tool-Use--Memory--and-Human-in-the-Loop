"""The permission gate (Architecture.md §6.3). Every tool call passes through the gate before the
registry may invoke it. Fail closed: anything not positively allowed is blocked or sent for approval.

    1. tool not in registry, or agent not in its allow-list       → block
    2. arguments unparseable / do not match the tool's schema     → block
    3. rate limit for (agent, tool) exceeded                      → block
    4. risk == safe                                               → allow
    5. risk == destructive                                        → approve (a human, always)
    6. risk == risky → classifier on (agent, tool, args, context) → allow | approve
       (no classifier, or any classifier failure                  → approve)
"""

from __future__ import annotations

import time

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError

from packages.orchestrator.gate.classifier import RiskClassifier
from packages.orchestrator.tracing.otel import gate_span
from packages.shared.types.gate import Decision, GateAction
from packages.shared.types.tools import RiskClass, ToolCall, ToolSpec
from packages.tools.registry.ratelimit import RateLimiter, RateLimiterLike
from packages.tools.registry.registry import ToolRegistry


class Gate:
    def __init__(
        self,
        registry: ToolRegistry,
        limiter: RateLimiterLike | None = None,
        *,
        classifier: RiskClassifier | None = None,
    ) -> None:
        self._registry = registry
        self._limiter = limiter or RateLimiter()
        self._classifier = classifier

    # ---------- public ----------

    def decide(self, agent: str, call: ToolCall) -> Decision:
        """Rule-based decision only (risky → approve). Synchronous callers and tests."""
        started = time.perf_counter()
        with gate_span(tool=call.name, agent=agent) as span:
            decision, _ = self._rules(agent, call)
            decision.latency_ms = int((time.perf_counter() - started) * 1000)
            self._annotate(span, decision)
            return decision

    async def decide_async(self, agent: str, call: ToolCall, *, context: str = "") -> Decision:
        """Rules, then the classifier for risky tools. What the agent loop calls."""
        started = time.perf_counter()
        with gate_span(tool=call.name, agent=agent) as span:
            decision, spec = self._rules(agent, call)
            if (
                spec is not None
                and spec.risk == RiskClass.RISKY
                and decision.action == GateAction.APPROVE
                and self._classifier is not None
            ):
                verdict = await self._classifier.classify(
                    agent=agent, call=call, spec=spec, context=context
                )
                decision = Decision(
                    action=GateAction.ALLOW if verdict.decision == "allow" else GateAction.APPROVE,
                    reason=f"classifier: {verdict.reason}",
                    risk=RiskClass.RISKY,
                    classified=True,
                )
            decision.latency_ms = int((time.perf_counter() - started) * 1000)
            self._annotate(span, decision)
            return decision

    # ---------- internals ----------

    def _rules(self, agent: str, call: ToolCall) -> tuple[Decision, ToolSpec | None]:
        spec = self._registry.get(call.name)
        if spec is None:
            return Decision(action=GateAction.BLOCK, reason=f"unknown tool '{call.name}'"), None
        if agent not in spec.agents:
            return (
                Decision(
                    action=GateAction.BLOCK,
                    reason=f"tool '{call.name}' is not allowed for agent '{agent}'",
                    risk=spec.risk,
                ),
                spec,
            )
        if call.parse_error:
            return Decision(action=GateAction.BLOCK, reason=call.parse_error, risk=spec.risk), spec
        try:
            Draft202012Validator(spec.input_schema).validate(call.arguments)
        except JsonSchemaError as e:
            return (
                Decision(
                    action=GateAction.BLOCK,
                    reason=f"arguments do not match the tool schema: {e.message}",
                    risk=spec.risk,
                ),
                spec,
            )
        if not self._limiter.allow(f"{agent}:{call.name}", spec.rate_per_min):
            return (
                Decision(
                    action=GateAction.BLOCK,
                    reason=(
                        f"rate limit of {spec.rate_per_min}/min reached for '{call.name}'; "
                        "retry later"
                    ),
                    risk=spec.risk,
                ),
                spec,
            )
        if spec.risk == RiskClass.SAFE:
            return Decision(action=GateAction.ALLOW, reason="safe tool", risk=spec.risk), spec
        if spec.risk == RiskClass.DESTRUCTIVE:
            return (
                Decision(
                    action=GateAction.APPROVE,
                    reason="destructive tool always requires human approval",
                    risk=spec.risk,
                ),
                spec,
            )
        return (
            Decision(
                action=GateAction.APPROVE,
                reason="risky tool; human approval required unless the classifier clears it",
                risk=spec.risk,
            ),
            spec,
        )

    @staticmethod
    def _annotate(span, decision: Decision) -> None:  # type: ignore[no-untyped-def]
        span.set_attribute("decision", decision.action.value)
        span.set_attribute("reason", decision.reason[:200])
        span.set_attribute("classified", decision.classified)
        if decision.risk is not None:
            span.set_attribute("risk", decision.risk.value)
