"""The whole graph with fakes: scripted LLMs per role, a recording tool registry with one
destructive tool, a SQLite store, and LangGraph's in-memory checkpointer. Covers the Phase 2
done-when (A → B → C end to end, resume after a crash), retry-with-feedback, and — since Phase 4 —
that plan approval and escalation pause the graph instead of failing it. The decision matrix lives
in test_hitl_flow.py and reuses ``Scenario``."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.deps import GraphConfig, GraphDeps
from packages.orchestrator.graph.serde import checkpoint_serde
from packages.orchestrator.graph.state import initial_state
from packages.orchestrator.hitl.escalation import EscalationPolicy
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import ApprovalStore, TaskStore
from packages.shared.types.approval import ApprovalDecision, ApprovalRequest
from packages.shared.types.cost import CostEntry
from packages.shared.types.llm import LLMResponse, Usage
from packages.shared.types.task import TaskOptions
from packages.shared.types.tools import ToolCall, ToolResult
from packages.tools.registry.registry import ToolRegistry

PLAN_ABC: dict[str, Any] = {
    "subtasks": [
        {
            "id": "A",
            "description": "find loans",
            "specialist": "research",
            "depends_on": [],
            "needs": [],
            "expected_output": "table",
            "complexity": "simple",
        },
        {
            "id": "B",
            "description": "check affordability",
            "specialist": "analysis",
            "depends_on": ["A"],
            "needs": ["loans"],
            "expected_output": "findings",
            "complexity": "moderate",
        },
        {
            "id": "C",
            "description": "draft letter",
            "specialist": "writing",
            "depends_on": ["A", "B"],
            "needs": ["findings"],
            "expected_output": "letter",
            "complexity": "moderate",
        },
    ],
    "confidence": 0.9,
    "sensitive_actions": [],
    "rationale": "facts, then analysis, then writing",
}

EMAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["to", "subject", "body"],
}
ACTIONS_LISTING = {"actions": [("send_email", "queue an email for a human", EMAIL_SCHEMA)]}
ACTIONS_POLICY: dict[str, Any] = {
    "servers": {"actions": {"url_env": "MCP_ACTIONS_URL"}},
    "tools": {
        "actions_send_email": {
            "server": "actions",
            "mcp_name": "send_email",
            "risk": "destructive",
            "agents": ["writing"],
        }
    },
}


class RecordingRegistry(ToolRegistry):
    """Registers the destructive email tool; records invocations instead of talking to a server."""

    def __init__(self) -> None:
        base = ToolRegistry.from_listing(
            ACTIONS_LISTING, ACTIONS_POLICY, {"actions": "http://actions"}
        )
        super().__init__(base._specs, {"actions": "http://actions"})
        self.invoked: list[ToolCall] = []

    async def invoke(self, call: ToolCall, **_: Any) -> ToolResult:
        self.invoked.append(call)
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=json.dumps({"outbox_id": len(self.invoked), "status": "queued_for_human"}),
        )


class FakeChain:
    """A ChatLLM for one role. `handler(messages, schema_name) -> content | ToolCall list`."""

    def __init__(
        self,
        role: str,
        handler: Callable[[list[dict[str, Any]], str], Any],
        log: list[dict[str, Any]],
    ) -> None:
        self.role = role
        self._handler = handler
        self._log = log
        self._on_cost: Callable[[CostEntry], None] | None = None

    def with_cost_sink(self, on_cost: Callable[[CostEntry], None] | None) -> FakeChain:
        clone = FakeChain(self.role, self._handler, self._log)
        clone._on_cost = on_cost
        return clone

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
        response_schema: Any = None,
        schema_name: str = "Response",
        **_: Any,
    ) -> LLMResponse:
        self._log.append(
            {"role": self.role, "schema": schema_name, "messages": json.loads(json.dumps(messages))}
        )
        out = self._handler(messages, schema_name)
        if self._on_cost:
            self._on_cost(
                CostEntry(
                    provider="fake", model="m", role=self.role, input_tokens=3, output_tokens=2
                )
            )
        if isinstance(out, list):
            return LLMResponse(
                content=None,
                tool_calls=out,
                provider="fake",
                model="m",
                usage=Usage(input_tokens=3, output_tokens=2),
            )
        return LLMResponse(
            content=out, provider="fake", model="m", usage=Usage(input_tokens=3, output_tokens=2)
        )


class Scenario:
    """Builds GraphDeps with scripted behaviour and records every model call and tool call."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        plan: dict[str, Any] | None = None,
        reviewer_script: Callable[[str, int], dict[str, Any]] | None = None,
        crash_reviewer_once: bool = False,
        email_subtasks: tuple[str, ...] = (),
        long_term: Any = None,
        empty_first_attempt: tuple[str, ...] = (),
        short_synthesis: int = 0,
        pad_outputs: int = 0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plan = plan or PLAN_ABC
        self.reviewer_script = reviewer_script or (
            lambda sid, attempt: {"accept": True, "score": 5, "issues": [], "feedback": ""}
        )
        self.crashes_left = 1 if crash_reviewer_once else 0
        self.email_subtasks = set(email_subtasks)
        self.specialist_calls: list[
            tuple[str, str, int]
        ] = []  # (agent, subtask_id, attempt) — one per loop start
        self.notified: list[int] = []
        self.empty_first_attempt = set(empty_first_attempt)
        self.short_synthesis = short_synthesis  # how many times synthesis returns a placeholder
        self.pad_outputs = pad_outputs  # extra characters per specialist output (realistic sizes)

        tmp_path.mkdir(parents=True, exist_ok=True)
        engine = make_engine(f"sqlite:///{tmp_path / 'store.db'}")
        create_all(engine)
        sessions = make_session_factory(engine)
        self.store = TaskStore(sessions)
        self.approvals = ApprovalStore(sessions)
        self.registry = RecordingRegistry()
        self.deps = GraphDeps(
            llm_for=self.llm_for,
            registry=self.registry,
            gate=Gate(self.registry),
            store=self.store,
            approvals=self.approvals,
            specialists={
                n: AgentSpec(name=n, role="specialist", system_prompt=f"you are {n}")
                for n in ("research", "analysis", "writing", "code_exec")
            },
            supervisor=AgentSpec(name="supervisor", role="supervisor", system_prompt="plan"),
            synthesize_prompt="synthesise",
            reviewer=AgentSpec(name="reviewer", role="reviewer", system_prompt="review"),
            policy=EscalationPolicy.default(),
            notifier=self._notify,
            long_term=long_term,
            config=GraphConfig(plan_confidence_threshold=0.6, max_retries=2),
        )

    async def _notify(self, request: ApprovalRequest, approval_id: int) -> None:
        self.notified.append(approval_id)

    # ---- role handlers ----

    def _supervisor(self, messages: list[dict[str, Any]], schema_name: str) -> str:
        if schema_name == "ExecutionPlan":
            return json.dumps(self.plan)
        assert schema_name == "Deliverable"
        if self.short_synthesis > 0:
            self.short_synthesis -= 1
            return json.dumps({"title": "Final", "body": "final", "sources": [], "confidence": 0.5})
        body = messages[1]["content"]  # the synthesis prompt (a retry nudge may come after it)
        return json.dumps(
            {"title": "Final", "body": body[-600:], "sources": ["loans"], "confidence": 0.8}
        )

    def _dispatch_specialist(self, messages: list[dict[str, Any]], schema_name: str) -> Any:
        agent = messages[0]["content"].removeprefix("you are ")
        user = messages[1]["content"]
        sid = user.split("## Subtask ", 1)[1].split("\n", 1)[0].strip()
        turns = sum(1 for m in messages if m.get("role") == "assistant")
        if turns == 0:
            attempt = 1 + sum(1 for a, s, _ in self.specialist_calls if a == agent and s == sid)
            self.specialist_calls.append((agent, sid, attempt))
            if sid in self.email_subtasks:
                return [
                    ToolCall(
                        id=f"email-{sid}-{attempt}",
                        name="actions_send_email",
                        arguments={
                            "to": "lender@example.test",
                            "subject": f"Complaint {sid}",
                            "body": "Dear lender",
                        },
                    )
                ]
        else:
            attempt = max(
                (att for a, s, att in self.specialist_calls if a == agent and s == sid), default=1
            )
        pred = (
            "with " + ", ".join(sorted(self._pred_keys(user)))
            if "predecessor_outputs" in user
            else "alone"
        )
        last_tool = next((m["content"] for m in reversed(messages) if m.get("role") == "tool"), "")
        output = f"out {sid} ({pred})" + (
            f" email:{last_tool[:80]}" if sid in self.email_subtasks else ""
        )
        if self.pad_outputs:
            output += " " + "detail " * (self.pad_outputs // 7)
        if sid in self.empty_first_attempt and attempt == 1:
            output = ""  # a completed result with no content (seen live: reviewer accepted it)
        return [
            ToolCall(
                id=f"sub-{sid}-{attempt}-{turns}",
                name="submit_result",
                arguments={
                    "status": "completed",
                    "output": output,
                    "sources": ["loans"],
                    "self_confidence": 0.9,
                    "notes": "",
                },
            )
        ]

    @staticmethod
    def _pred_keys(user_text: str) -> list[str]:
        block = user_text.split("```json", 1)[1].split("```", 1)[0]
        return list(json.loads(block).get("predecessor_outputs", {}).keys())

    def _reviewer(self, messages: list[dict[str, Any]], schema_name: str) -> str:
        if self.crashes_left:
            self.crashes_left -= 1
            raise RuntimeError("simulated worker crash")
        payload = json.loads(
            messages[-1]["content"]
            .split("## Specialist result\n```json\n", 1)[1]
            .split("```", 1)[0]
        )
        return json.dumps(self.reviewer_script(payload["subtask_id"], payload["attempt"]))

    def _extractor(self, messages: list[dict[str, Any]], schema_name: str) -> str:
        assert schema_name == "MemoryExtraction"
        digest = messages[-1]["content"]
        request = (
            digest.split("request: ", 1)[1].split("\n", 1)[0] if "request: " in digest else "task"
        )
        return json.dumps(
            {
                "records": [
                    {
                        "text": f"Lesson from: {request[:60]}",
                        "task_type": "claim_review",
                        "outcome": "success",
                        "importance": 3,
                        "tools_used": [],
                    }
                ]
            }
        )

    def llm_for(self, role: str) -> FakeChain:
        if role == "supervisor":
            return FakeChain(role, self._supervisor, self.calls)
        if role == "reviewer":
            return FakeChain(role, self._reviewer, self.calls)
        if role == "cheap":
            return FakeChain(role, self._extractor, self.calls)
        return FakeChain(role, self._dispatch_specialist, self.calls)

    # ---- running ----

    @staticmethod
    def config(task_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": task_id}}

    async def run(
        self,
        request: str = "summarise and draft",
        *,
        checkpointer: Any = None,
        resume: bool = False,
        thread: str | None = None,
        options: TaskOptions | None = None,
    ) -> tuple[dict[str, Any], str]:
        saver = checkpointer or MemorySaver(serde=checkpoint_serde())
        graph = build_graph(self.deps, checkpointer=saver)
        row = (
            self.store.create_task(user_id="u1", request=request, options=options or TaskOptions())
            if not thread
            else None
        )
        task_id = thread or row.id  # type: ignore[union-attr]
        state = None if resume else initial_state(task_id, "u1", request, options or TaskOptions())
        final = await graph.ainvoke(state, self.config(task_id))
        return final, task_id

    async def decide(
        self, task_id: str, decision: ApprovalDecision, *, checkpointer: Any
    ) -> dict[str, Any]:
        """What the worker does after the API recorded a decision: record + resume."""
        pending = self.pending()
        assert pending, "no pending approval to decide"
        self.approvals.record_decision(pending["id"], decision)
        graph = build_graph(self.deps, checkpointer=checkpointer)
        return await graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")), self.config(task_id)
        )

    def pending(self) -> dict[str, Any] | None:
        rows = self.approvals.list(status="pending")
        return rows[0] if rows else None


def interrupted(final: dict[str, Any]) -> dict[str, Any]:
    assert "__interrupt__" in final and final["__interrupt__"], "graph did not pause"
    value = final["__interrupt__"][0].value
    assert isinstance(value, dict)
    return value


async def test_three_step_plan_runs_to_a_deliverable(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, task_id = await sc.run()

    assert final["status"] == "done", final.get("error")
    assert final["final_output"].title == "Final"
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]
    assert final["subtask_results"]["B"].output == "out B (with A)"
    assert final["subtask_results"]["C"].output == "out C (with A, B)"
    assert all(v.accept for v in final["review_verdicts"].values())
    kinds = [e.kind for e in final["events"]]
    for kind in (
        "started",
        "planned",
        "dispatch",
        "subtask_done",
        "reviewed",
        "synthesized",
        "delivered",
    ):
        assert kind in kinds
    view = sc.store.task_view(task_id)
    assert (
        view is not None and view["status"] == "done" and view["final_output"]["title"] == "Final"
    )
    assert {s["id"]: s["status"] for s in view["subtasks"]} == {
        "A": "accepted",
        "B": "accepted",
        "C": "accepted",
    }
    assert (
        view["llm_calls"] == len(final["cost_ledger"]) == 1 + 3 + 3 + 1
    )  # plan + 3 specialists + 3 reviews + synth
    assert view["plan"]["confidence"] == 0.9 and sc.pending() is None


async def test_independent_subtasks_run_before_their_dependent(tmp_path: Path) -> None:
    plan = json.loads(json.dumps(PLAN_ABC))
    plan["subtasks"][1]["depends_on"] = []  # A and B independent; C needs both
    sc = Scenario(tmp_path, plan=plan)
    final, _ = await sc.run()
    assert final["status"] == "done"
    order = [c[1] for c in sc.specialist_calls]
    assert set(order[:2]) == {"A", "B"} and order[2] == "C"
    assert final["subtask_results"]["C"].output == "out C (with A, B)"


async def test_rejected_subtask_is_retried_with_feedback(tmp_path: Path) -> None:
    def reviewer(sid: str, attempt: int) -> dict[str, Any]:
        if sid == "A" and attempt == 1:
            return {
                "accept": False,
                "score": 2,
                "issues": ["no sources"],
                "feedback": "add the loan table as a source",
            }
        return {"accept": True, "score": 5, "issues": [], "feedback": ""}

    sc = Scenario(tmp_path, reviewer_script=reviewer)
    final, _ = await sc.run()
    assert final["status"] == "done"
    assert [c for c in sc.specialist_calls if c[1] == "A"] == [
        ("research", "A", 1),
        ("research", "A", 2),
    ]
    second_call = [
        c
        for c in sc.calls
        if c["role"] == "specialist" and "## Subtask A" in c["messages"][1]["content"]
    ][1]
    assert "reviewer_feedback" in second_call["messages"][1]["content"]
    assert "add the loan table" in second_call["messages"][1]["content"]
    assert final["retry_counts"] == {"A": 1}
    assert final["subtask_results"]["A"].attempt == 2 and final["review_verdicts"]["A"].attempt == 2


async def test_max_retries_pauses_for_a_human_instead_of_failing(tmp_path: Path) -> None:
    sc = Scenario(
        tmp_path,
        reviewer_script=lambda sid, attempt: {
            "accept": False,
            "score": 1,
            "issues": ["wrong"],
            "feedback": "redo",
        },
    )
    saver = MemorySaver(serde=checkpoint_serde())
    final, task_id = await sc.run(checkpointer=saver)
    value = interrupted(final)
    assert value["kind"] == "escalation" and value["level"] == "L4"
    assert [c[2] for c in sc.specialist_calls if c[1] == "A"] == [1, 2, 3]
    pending = sc.pending()
    assert pending is not None and pending["kind"] == "escalation" and pending["task_id"] == task_id
    assert sc.notified == [pending["id"]]
    assert sc.store.task_view(task_id)["status"] == "queued"  # type: ignore[index] — the worker sets awaiting_approval


async def test_low_confidence_plan_pauses_for_approval(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, plan={**PLAN_ABC, "confidence": 0.2})
    final, _ = await sc.run(checkpointer=MemorySaver(serde=checkpoint_serde()))
    value = interrupted(final)
    assert (
        value["kind"] == "plan"
        and value["level"] == "L3"
        and value["trigger"] == "low_plan_confidence"
    )
    assert value["proposed_action"]["plan"]["confidence"] == 0.2
    assert sc.specialist_calls == []


async def test_require_human_review_option_pauses_at_the_plan(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, _ = await sc.run(
        checkpointer=MemorySaver(serde=checkpoint_serde()),
        options=TaskOptions(require_human_review=True),
    )
    value = interrupted(final)
    assert value["kind"] == "plan" and value["trigger"] == "user_requested_review"


async def test_crash_mid_task_then_resume_from_checkpoint(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, crash_reviewer_once=True)
    saver = MemorySaver(serde=checkpoint_serde())
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        await sc.run(checkpointer=saver)
    with sc.store._sessions() as s:  # noqa: SLF001 — test-only peek
        from packages.orchestrator.memory.persistent import TaskRow

        task_id = s.query(TaskRow).one().id
    assert [c[1] for c in sc.specialist_calls] == ["A"]  # A ran once before the crash

    final, _ = await sc.run(checkpointer=saver, resume=True, thread=task_id)
    assert final["status"] == "done", final.get("error")
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]  # A was NOT re-run
    assert sc.store.task_view(task_id)["status"] == "done"  # type: ignore[index]


async def test_plan_is_persisted_as_soon_as_it_exists(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, task_id = await sc.run()
    view = sc.store.task_view(task_id)
    assert view is not None and view["plan"]["confidence"] == 0.9
    assert [s["id"] for s in view["subtasks"]] == ["A", "B", "C"]
    assert {s["specialist"] for s in view["subtasks"]} == {"research", "analysis", "writing"}
