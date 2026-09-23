"""Turn a finished graph run (final state + task view) into a ``Trajectory`` the metrics can read."""

from __future__ import annotations

from typing import Any

from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.evals import PauseRecord, ToolCallRecord, Trajectory
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan


def build_trajectory(
    *,
    golden_id: str,
    run_index: int,
    task_id: str,
    user_id: str,
    final: dict[str, Any],
    view: dict[str, Any] | None,
    elapsed_s: float,
    pauses: list[PauseRecord],
    outbox_rows: int,
) -> Trajectory:
    events = [ToolEvent.model_validate(t) for t in (final.get("tool_events") or [])]
    ledger = [CostEntry.model_validate(c) for c in (final.get("cost_ledger") or [])]
    plan_raw = final.get("plan")
    plan = ExecutionPlan.model_validate(plan_raw) if plan_raw else None
    deliverable_raw = final.get("final_output")
    deliverable = Deliverable.model_validate(deliverable_raw) if deliverable_raw else None
    providers: dict[str, int] = {}
    for c in ledger:
        providers[c.provider] = providers.get(c.provider, 0) + 1
    view = view or {}
    return Trajectory(
        task_id=task_id,
        golden_id=golden_id,
        run_index=run_index,
        user_id=user_id,
        status=str(final.get("status") or view.get("status") or "unknown"),
        error=final.get("error") or view.get("error"),
        elapsed_s=round(elapsed_s, 2),
        llm_calls=int(view.get("llm_calls") or len(ledger)),
        tokens=int(view.get("tokens") or sum(c.input_tokens + c.output_tokens for c in ledger)),
        cost_usd=view.get("cost_usd"),
        tool_calls=[
            ToolCallRecord(
                subtask_id=e.subtask_id,
                tool=e.tool,
                risk=e.risk.value if e.risk else None,
                decision=e.decision.value,
                executed=e.ok is not None,
                ok=e.ok,
            )
            for e in events
        ],
        pauses=pauses,
        subtask_ids=[s.id for s in plan.subtasks] if plan else [],
        specialists=[s.specialist.value for s in plan.subtasks] if plan else [],
        has_dependency=bool(plan and any(s.depends_on for s in plan.subtasks)),
        retries=sum(int(v) for v in (final.get("retry_counts") or {}).values()),
        deliverable_title=deliverable.title if deliverable else "",
        deliverable_body=deliverable.body if deliverable else "",
        recalled_memories=len(final.get("recalled_memories") or []),
        providers=providers,
        fallback_calls=sum(1 for c in ledger if c.fallback),
        outbox_rows=outbox_rows,
    )
