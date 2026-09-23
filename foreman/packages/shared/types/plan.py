from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field, model_validator

from packages.shared.types.subtask import Subtask


class ExecutionPlan(BaseModel):
    """The supervisor's plan: an ordered set of subtasks with dependencies (Architecture.md §4.2).

    Validation rejects duplicate ids, unknown or self dependencies, and cycles — a plan that
    passes here can always be dispatched to completion.
    """

    subtasks: list[Subtask] = Field(min_length=1, max_length=8)
    confidence: float = Field(
        ge=0.0, le=1.0, description="How likely this plan achieves the request"
    )
    sensitive_actions: list[str] = Field(
        default_factory=list,
        description="Irreversible actions the request implies (send email, pay, delete). Empty if none.",
    )
    rationale: str = Field(default="", description="One paragraph: why these steps in this order")

    @model_validator(mode="after")
    def _check_graph(self) -> ExecutionPlan:
        ids = [s.id for s in self.subtasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate subtask ids")
        known = set(ids)
        for s in self.subtasks:
            for dep in s.depends_on:
                if dep == s.id:
                    raise ValueError(f"subtask {s.id} depends on itself")
                if dep not in known:
                    raise ValueError(f"subtask {s.id} depends on unknown subtask {dep}")
        if len(self.order()) != len(ids):
            raise ValueError("dependency cycle in plan")
        return self

    def by_id(self) -> dict[str, Subtask]:
        return {s.id: s for s in self.subtasks}

    def order(self) -> list[str]:
        """Topological order (Kahn). Shorter than the id list means a cycle."""
        indeg = {s.id: len(s.depends_on) for s in self.subtasks}
        dependents: dict[str, list[str]] = {s.id: [] for s in self.subtasks}
        for s in self.subtasks:
            for dep in s.depends_on:
                dependents.setdefault(dep, []).append(s.id)
        queue = deque(sorted(i for i, d in indeg.items() if d == 0))
        out: list[str] = []
        while queue:
            node = queue.popleft()
            out.append(node)
            for child in dependents.get(node, []):
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)
        return out
