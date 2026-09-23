"""One node per specialist. Receives a `SpecialistInput` from Send(), runs (or resumes) the agent
loop, and merges the outcome into the shared state: a result, or a pause awaiting a human."""

from __future__ import annotations

from typing import Any

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import PendingApproval, SpecialistInput, event
from packages.orchestrator.loop.agent_loop import LoopDeps, LoopResume, PausedLoop, run_agent_loop
from packages.shared.types.subtask import Subtask


def make_specialist_node(agent: AgentSpec, deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def specialist(payload: SpecialistInput) -> dict[str, Any]:
        subtask = Subtask.model_validate(payload["subtask"])
        inputs = dict(subtask.inputs)
        if payload.get("predecessor_outputs"):
            # Tier 1 first (freshest, shared across workers); the Send payload is the fallback.
            fresh = await deps.working.get_results(payload["task_id"])
            inputs["predecessor_outputs"] = {
                sid: (fresh[sid].output if sid in fresh else text)
                for sid, text in payload["predecessor_outputs"].items()
            }
        if payload.get("feedback"):
            inputs["reviewer_feedback"] = payload["feedback"]
        enriched = subtask.model_copy(update={"inputs": inputs})
        attempt = int(payload.get("attempt", 1))
        raw_resume = payload.get("resume")
        resume = LoopResume.model_validate(raw_resume) if raw_resume else None

        loop_deps = LoopDeps(
            llm=deps.llm_for(agent.role),
            registry=deps.registry,
            gate=deps.gate,
            budget=deps.config.budget_for(agent.name),
            llm_timeout_s=deps.config.llm_timeout_s,
            task_id=payload["task_id"],
        )
        outcome = await run_agent_loop(
            agent,
            enriched,
            loop_deps,
            attempt=attempt,
            resume=resume,
            denied=payload.get("denied") or {},
        )
        node = f"specialist_{agent.name}"

        if isinstance(outcome, PausedLoop):
            head = outcome.next_call
            return {
                "pending_approvals": {
                    subtask.id: PendingApproval(
                        subtask_id=subtask.id, agent=agent.name, attempt=attempt, paused=outcome
                    )
                },
                "approval_decisions": {subtask.id: None},
                "events": [
                    event(
                        "paused",
                        f"{subtask.id} ({agent.name}) needs approval for {head.call.name}",
                        node=node,
                        subtask_id=subtask.id,
                        attempt=attempt,
                        tool=head.call.name,
                        reason=head.reason,
                    )
                ],
            }

        await deps.working.put_result(payload["task_id"], outcome)
        if outcome.error:
            await deps.working.add_error(payload["task_id"], f"{subtask.id}: {outcome.error}")
        return {
            "subtask_results": {subtask.id: outcome},
            "cost_ledger": outcome.cost_entries,
            "tool_events": outcome.tool_events,
            "pending_approvals": {subtask.id: None},
            "approval_decisions": {subtask.id: None},
            "events": [
                event(
                    "subtask_done",
                    f"{subtask.id} ({agent.name}) attempt {outcome.attempt}: {outcome.status.value}"
                    + (" (human)" if outcome.human_authored else ""),
                    node=node,
                    subtask_id=subtask.id,
                    attempt=outcome.attempt,
                    status=outcome.status.value,
                    tools_used=outcome.tools_used,
                    iterations=outcome.iterations,
                    human_authored=outcome.human_authored,
                )
            ],
        }

    return specialist
