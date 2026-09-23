"""The four guards every agent loop must have (Rules.md §2.8): iterations, tokens, cost, wall-clock."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from packages.shared.errors import BudgetExceededError
from packages.shared.types.cost import CostEntry


@dataclass(frozen=True)
class Budget:
    max_iterations: int = 15
    max_total_tokens: int = 150_000
    max_cost_usd: float | None = 1.0
    max_wall_seconds: float = 300.0


@dataclass(frozen=True)
class BudgetConfig:
    """`config/budgets.yaml`: a default loop budget, per-agent overrides, and a whole-task cap."""

    default: Budget = field(default_factory=Budget)
    agents: dict[str, Budget] = field(default_factory=dict)
    task_max_cost_usd: float | None = None

    def for_agent(self, name: str) -> Budget:
        return self.agents.get(name, self.default)

    @classmethod
    def load(cls, path: Path) -> BudgetConfig:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        default = _budget(raw.get("default") or {}, Budget())
        agents = {
            name: _budget(spec or {}, default) for name, spec in (raw.get("agents") or {}).items()
        }
        task = raw.get("task") or {}
        return cls(default=default, agents=agents, task_max_cost_usd=task.get("max_cost_usd"))


def _budget(spec: dict[str, Any], base: Budget) -> Budget:
    """A budget from YAML keys, inheriting anything not given from ``base``."""
    return Budget(
        max_iterations=int(spec.get("max_iterations", base.max_iterations)),
        max_total_tokens=int(spec.get("max_total_tokens", base.max_total_tokens)),
        max_cost_usd=spec.get("max_cost_usd", base.max_cost_usd),
        max_wall_seconds=float(spec.get("max_wall_seconds", base.max_wall_seconds)),
    )


@dataclass
class BudgetTracker:
    budget: Budget
    iterations: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def start_iteration(self) -> int:
        self.iterations += 1
        if self.iterations > self.budget.max_iterations:
            raise BudgetExceededError(f"max iterations ({self.budget.max_iterations}) reached")
        self.check_wall()
        return self.iterations

    def record(self, entry: CostEntry) -> None:
        self.total_tokens += entry.input_tokens + entry.output_tokens
        if entry.cost_usd is not None:
            self.total_cost_usd += entry.cost_usd
        if self.total_tokens > self.budget.max_total_tokens:
            raise BudgetExceededError(
                f"token budget exceeded: {self.total_tokens} > {self.budget.max_total_tokens}"
            )
        if self.budget.max_cost_usd is not None and self.total_cost_usd > self.budget.max_cost_usd:
            raise BudgetExceededError(
                f"cost budget exceeded: ${self.total_cost_usd:.4f} > ${self.budget.max_cost_usd:.2f}"
            )

    def check_wall(self) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed > self.budget.max_wall_seconds:
            raise BudgetExceededError(f"wall-clock budget exceeded: {elapsed:.0f}s")
