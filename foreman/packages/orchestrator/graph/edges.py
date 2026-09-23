"""Conditional edges (Architecture.md §4.3). Plain functions over typed state — an LLM never
decides which node runs next (Rules.md §2.4).

Rejected results are re-sent to the same specialist with the reviewer's feedback until
``max_retries`` rejections; then the task escalates. Subtasks whose dependencies are all accepted
are dispatched, in parallel when independent. A specialist that paused on a tool call routes the
whole task to ``await_approval`` (nothing else runs while a human decides); once decided, the
paused specialist is resumed with its checkpoint. When nothing can run and not everything is
accepted, the task escalates rather than hanging.
"""

from __future__ import annotations

from langgraph.types import Send

from packages.orchestrator.graph.state import (
    SpecialistInput,
    TaskState,
    approval_decisions,
    pending_approvals,
)
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult

NODE_DISPATCH = "dispatch"
NODE_APPROVE_PLAN = "approve_plan"
NODE_AWAIT_APPROVAL = "await_approval"
NODE_SYNTHESIZE = "synthesize"
NODE_ESCALATE = "escalate"
NODE_DELIVER = "deliver"


def specialist_node_name(specialist: str) -> str:
    return f"specialist_{specialist}"


# ---------- helpers over typed state ----------


def _plan(state: TaskState) -> ExecutionPlan:
    plan = state.get("plan")
    if plan is None:
        raise ValueError("no plan in state")
    return ExecutionPlan.model_validate(plan)


def _results(state: TaskState) -> dict[str, SubtaskResult]:
    return {
        k: SubtaskResult.model_validate(v) for k, v in (state.get("subtask_results") or {}).items()
    }


def _verdicts(state: TaskState) -> dict[str, ReviewVerdict]:
    return {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }


def is_accepted(
    subtask_id: str, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> bool:
    r, v = results.get(subtask_id), verdicts.get(subtask_id)
    return r is not None and v is not None and v.attempt == r.attempt and v.accept


def is_pending_review(
    subtask_id: str, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> bool:
    r, v = results.get(subtask_id), verdicts.get(subtask_id)
    return r is not None and (v is None or v.attempt < r.attempt)


def needs_review(
    results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> list[SubtaskResult]:
    return [r for sid, r in results.items() if is_pending_review(sid, results, verdicts)]


def ready_subtasks(
    plan: ExecutionPlan,
    results: dict[str, SubtaskResult],
    verdicts: dict[str, ReviewVerdict],
    paused: set[str] | None = None,
) -> list[Subtask]:
    """Not yet run (no result at all), not paused, and every dependency accepted."""
    out = []
    for s in plan.subtasks:
        if s.id in results or (paused and s.id in paused):
            continue
        if all(is_accepted(d, results, verdicts) for d in s.depends_on):
            out.append(s)
    return out


def rejected_subtasks(
    plan: ExecutionPlan, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> list[tuple[Subtask, ReviewVerdict]]:
    out = []
    for s in plan.subtasks:
        r, v = results.get(s.id), verdicts.get(s.id)
        if r is not None and v is not None and v.attempt == r.attempt and not v.accept:
            out.append((s, v))
    return out


def predecessor_outputs(subtask: Subtask, results: dict[str, SubtaskResult]) -> dict[str, str]:
    return {d: results[d].output for d in subtask.depends_on if d in results}


def _denied_for(state: TaskState, subtask_id: str) -> dict[str, str]:
    """Tools a human rejected in an earlier attempt of this subtask — the refusal stands."""
    previous = _results(state).get(subtask_id)
    return dict(previous.denied_tools) if previous is not None else {}


def send_for(state: TaskState, subtask: Subtask, *, attempt: int, feedback: str | None) -> Send:
    payload: SpecialistInput = {
        "task_id": state["task_id"],
        "subtask": subtask,
        "predecessor_outputs": predecessor_outputs(subtask, _results(state)),
        "feedback": feedback,
        "attempt": attempt,
        "resume": None,
        "denied": _denied_for(state, subtask.id),
    }
    return Send(specialist_node_name(subtask.specialist.value), payload)


def _undecided_pending(state: TaskState) -> list[str]:
    decided = approval_decisions(state)
    return [p.subtask_id for p in pending_approvals(state) if p.subtask_id not in decided]


# ---------- the edges ----------


def make_route_after_plan(confidence_threshold: float):  # type: ignore[no-untyped-def]
    def route_after_plan(state: TaskState) -> str:
        if state.get("plan") is None or state.get("status") == "failed":
            return NODE_ESCALATE
        plan = _plan(state)
        if plan.confidence < confidence_threshold or state["options"].require_human_review:
            return NODE_APPROVE_PLAN
        return NODE_DISPATCH

    return route_after_plan


def route_after_approve_plan(state: TaskState) -> str:
    if state.get("status") == "cancelled" or state.get("final_output") is not None:
        return NODE_DELIVER
    return NODE_DISPATCH


def route_dispatch(state: TaskState) -> list[Send] | str:
    """After the dispatch node: fan out every ready subtask; nothing ready means a broken plan."""
    plan = _plan(state)
    results, verdicts = _results(state), _verdicts(state)
    ready = ready_subtasks(plan, results, verdicts)
    if not ready:
        return NODE_ESCALATE
    return [send_for(state, s, attempt=1, feedback=None) for s in ready]


def make_route_after_review(max_retries: int):  # type: ignore[no-untyped-def]
    def route_after_review(state: TaskState) -> list[Send] | str:
        if _undecided_pending(state):
            return NODE_AWAIT_APPROVAL
        return _continue(state, max_retries)

    return route_after_review


def route_after_await_approval(state: TaskState) -> list[Send] | str:
    """Resume the paused specialist whose approval was just decided."""
    decided = approval_decisions(state)
    sends: list[Send] = []
    for p in pending_approvals(state):
        decision = decided.get(p.subtask_id)
        if decision is None:
            continue
        payload: SpecialistInput = {
            "task_id": state["task_id"],
            "subtask": _plan(state).by_id()[p.subtask_id],
            "predecessor_outputs": {},
            "feedback": None,
            "attempt": p.attempt,
            "denied": {},
            "resume": {
                "checkpoint": p.paused.checkpoint.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        }
        sends.append(Send(specialist_node_name(p.agent), payload))
    return sends or NODE_ESCALATE


def make_route_after_escalate(max_retries: int):  # type: ignore[no-untyped-def]
    def route_after_escalate(state: TaskState) -> list[Send] | str:
        if state.get("status") == "cancelled" or state.get("final_output") is not None:
            return NODE_DELIVER
        return _continue(state, max_retries)

    return route_after_escalate


def _continue(state: TaskState, max_retries: int) -> list[Send] | str:
    plan = _plan(state)
    results, verdicts = _results(state), _verdicts(state)
    retry_counts = state.get("retry_counts") or {}
    paused = {p.subtask_id for p in pending_approvals(state)}

    sends: list[Send] = []
    for subtask, verdict in rejected_subtasks(plan, results, verdicts):
        if retry_counts.get(subtask.id, 0) > max_retries:
            return NODE_ESCALATE
        attempt = results[subtask.id].attempt + 1
        sends.append(send_for(state, subtask, attempt=attempt, feedback=verdict.feedback))

    for subtask in ready_subtasks(plan, results, verdicts, paused):
        sends.append(send_for(state, subtask, attempt=1, feedback=None))

    if sends:
        return sends
    if all(is_accepted(s.id, results, verdicts) for s in plan.subtasks):
        return NODE_SYNTHESIZE
    return NODE_ESCALATE  # nothing runnable, not finished: a dependency can never be met
