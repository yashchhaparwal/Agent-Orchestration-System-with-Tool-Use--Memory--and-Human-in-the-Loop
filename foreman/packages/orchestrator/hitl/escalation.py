"""Escalation policy: trigger → level, and what happens when nobody decides (config/escalation.yaml).

The policy can express *reject* and *cancel* on timeout — never approve. That is enforced at load
time, not left to configuration discipline.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from packages.shared.types.approval import (
    ApprovalDecision,
    ApprovalLevel,
    ApprovalTrigger,
    DecisionKind,
)


class EscalationPolicy(BaseModel):
    triggers: dict[ApprovalTrigger, ApprovalLevel]
    timeouts_hours: dict[ApprovalLevel, float] = Field(default_factory=dict)
    on_timeout: dict[ApprovalLevel, Literal["reject", "cancel"]] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> EscalationPolicy:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    @classmethod
    def default(cls) -> EscalationPolicy:
        return cls(
            triggers={
                ApprovalTrigger.LOW_PLAN_CONFIDENCE: ApprovalLevel.L3,
                ApprovalTrigger.SENSITIVE_TOOL_CALL: ApprovalLevel.L2,
                ApprovalTrigger.REPEATED_FAILURE: ApprovalLevel.L4,
                ApprovalTrigger.LOW_REVIEW_SCORE: ApprovalLevel.L3,
                ApprovalTrigger.USER_REQUESTED_REVIEW: ApprovalLevel.L3,
            },
            timeouts_hours={
                ApprovalLevel.L1: 0,
                ApprovalLevel.L2: 24,
                ApprovalLevel.L3: 48,
                ApprovalLevel.L4: 48,
            },
            on_timeout={
                ApprovalLevel.L2: "reject",
                ApprovalLevel.L3: "cancel",
                ApprovalLevel.L4: "cancel",
            },
        )

    def level_for(self, trigger: ApprovalTrigger) -> ApprovalLevel:
        return self.triggers.get(trigger, ApprovalLevel.L4)  # unknown trigger → strictest

    def deadline(
        self, level: ApprovalLevel, *, now: dt.datetime | None = None
    ) -> dt.datetime | None:
        hours = self.timeouts_hours.get(level, 0)
        if hours <= 0:
            return None
        base = now or dt.datetime.now(dt.UTC)
        return base + dt.timedelta(hours=hours)

    def timeout_decision(self, level: ApprovalLevel) -> ApprovalDecision:
        """The decision applied when a request expires. Always a rejection — for a tool call that
        denies the call; for a plan or an escalation the graph cancels the task."""
        action = self.on_timeout.get(level, "cancel")
        return ApprovalDecision(
            decision=DecisionKind.REJECT,
            reason=f"no decision before the {level.value} deadline; policy: {action}",
            decided_by="timeout",
        )
