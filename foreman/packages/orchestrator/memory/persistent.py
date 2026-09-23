"""Tier 2 — PostgreSQL system of record (Architecture.md §7.2): tables and the repositories that
are the ONLY code that touches them (Rules.md §1, §3)."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    delete,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from packages.orchestrator.memory.db import Base
from packages.shared.types.approval import (
    STATUS_FOR_DECISION,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
)
from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult
from packages.shared.types.task import TaskEvent, TaskOptions, TaskStatus


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _json(model: Any) -> Any:
    return json.loads(model.model_dump_json()) if model is not None else None


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    request: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True, default=TaskStatus.QUEUED.value)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SubtaskRow(Base):
    __tablename__ = "subtasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # f"{task_id}:{subtask_id}"
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[str] = mapped_column(String(16))
    specialist: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    verdict: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class LLMCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64), index=True)
    args_hash: Mapped[str] = mapped_column(String(32))
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OutboxRow(Base):
    """External actions the agents *proposed*. Nothing here is ever sent by Foreman (PRD.md §4)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued_for_human")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApprovalRow(Base):
    """A decision point raised by the graph (Architecture.md §8). ``dedupe_key`` makes creation
    idempotent across node re-execution."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(4))
    trigger: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str] = mapped_column(String(32), default="")
    proposed_action: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), index=True, default=ApprovalStatus.PENDING.value
    )
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaskStore:
    """Synchronous repository. Async callers wrap calls in ``asyncio.to_thread``."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    # ---------- tasks ----------

    def create_task(self, *, user_id: str, request: str, options: TaskOptions) -> TaskRow:
        row = TaskRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            request=request,
            options=_json(options),
            status=TaskStatus.QUEUED.value,
        )
        with self._sessions() as s:
            s.add(row)
            s.add(AuditLogRow(task_id=row.id, actor=user_id, action="task.created", payload={}))
            s.commit()
            s.refresh(row)
        return row

    def get_task(self, task_id: str) -> TaskRow | None:
        with self._sessions() as s:
            return s.get(TaskRow, task_id)

    def set_status(
        self, task_id: str, status: TaskStatus, *, error: str | None = None, actor: str = "worker"
    ) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.status = status.value
            if error is not None:
                row.error = error
            s.add(
                AuditLogRow(
                    task_id=task_id,
                    actor=actor,
                    action=f"task.{status.value}",
                    payload={"error": error},
                )
            )
            s.commit()

    def set_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.plan = _json(plan)
            keep = [sub.id for sub in plan.subtasks]
            s.execute(  # a human may have replaced the plan (L3 modify): drop subtasks that are gone
                delete(SubtaskRow)
                .where(SubtaskRow.task_id == task_id)
                .where(SubtaskRow.subtask_id.not_in(keep))
            )
            for sub in plan.subtasks:
                s.merge(
                    SubtaskRow(
                        id=f"{task_id}:{sub.id}",
                        task_id=task_id,
                        subtask_id=sub.id,
                        specialist=sub.specialist.value,
                        spec=_json(sub),
                        status="planned",
                    )
                )
            s.commit()

    def record_progress(
        self,
        task_id: str,
        *,
        results: dict[str, SubtaskResult],
        verdicts: dict[str, ReviewVerdict],
        cost_entries: list[CostEntry],
        tool_events: list[ToolEvent] | None = None,
    ) -> None:
        """Persist what the graph has done so far — called when a task pauses for a human, so the
        task view is truthful while people decide."""
        with self._sessions() as s:
            if s.get(TaskRow, task_id) is None:
                return
            self._write_progress(s, task_id, results, verdicts, cost_entries, tool_events)
            s.commit()

    @staticmethod
    def _write_progress(
        s: Session,
        task_id: str,
        results: dict[str, SubtaskResult],
        verdicts: dict[str, ReviewVerdict],
        cost_entries: list[CostEntry],
        tool_events: list[ToolEvent] | None,
    ) -> None:
        for sid, result in results.items():
            verdict = verdicts.get(sid)
            existing = s.get(SubtaskRow, f"{task_id}:{sid}")
            if existing is None:
                existing = SubtaskRow(
                    id=f"{task_id}:{sid}",
                    task_id=task_id,
                    subtask_id=sid,
                    specialist="",
                    spec={},
                    status="",
                )
                s.add(existing)
            existing.attempt = result.attempt
            existing.result = _json(result)
            existing.verdict = _json(verdict)
            accepted = verdict is not None and verdict.accept and verdict.attempt == result.attempt
            existing.status = (
                "accepted"
                if accepted
                else "rejected"
                if verdict is not None
                else result.status.value
            )
        # The ledgers mirror graph state, which accumulates across pauses: replace, never append twice.
        s.execute(delete(LLMCallRow).where(LLMCallRow.task_id == task_id))
        s.execute(delete(ToolInvocationRow).where(ToolInvocationRow.task_id == task_id))
        for c in cost_entries:
            s.add(
                LLMCallRow(
                    task_id=task_id,
                    role=c.role,
                    provider=c.provider,
                    model=c.model,
                    input_tokens=c.input_tokens,
                    output_tokens=c.output_tokens,
                    cost_usd=c.cost_usd,
                    latency_ms=c.latency_ms,
                    fallback=c.fallback,
                )
            )
        for t in tool_events or []:
            s.add(
                ToolInvocationRow(
                    task_id=task_id,
                    subtask_id=t.subtask_id,
                    agent=t.agent,
                    tool=t.tool,
                    args_hash=t.args_hash,
                    risk=t.risk.value if t.risk else None,
                    decision=t.decision.value,
                    reason=t.reason[:2000],
                    ok=t.ok,
                    latency_ms=t.latency_ms,
                    result_size=t.result_size,
                )
            )

    def finish_task(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        deliverable: Deliverable | None,
        cost_usd: float | None,
        error: str | None,
        results: dict[str, SubtaskResult],
        verdicts: dict[str, ReviewVerdict],
        cost_entries: list[CostEntry],
        events: list[TaskEvent],
        tool_events: list[ToolEvent] | None = None,
    ) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.status = status.value
            row.final_output = _json(deliverable)
            row.cost_usd = cost_usd
            row.error = error
            self._write_progress(s, task_id, results, verdicts, cost_entries, tool_events)
            for e in events:
                s.add(
                    AuditLogRow(
                        task_id=task_id,
                        actor=e.node or "graph",
                        action=e.kind,
                        payload={"message": e.message, **e.data},
                    )
                )
            s.add(
                AuditLogRow(
                    task_id=task_id,
                    actor="worker",
                    action=f"task.{status.value}",
                    payload={"error": error},
                )
            )
            s.commit()

    # ---------- reads for the API ----------

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first: enough for an operator to find a task without knowing its id."""
        with self._sessions() as s:
            stmt = select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit)
            return [
                {
                    "task_id": r.id,
                    "user_id": r.user_id,
                    "status": r.status,
                    "created_at": _iso(r.created_at),
                    "request": r.request[:120],
                    "error": r.error,
                }
                for r in s.scalars(stmt).all()
            ]

    def stats(self, *, days: int = 7) -> dict[str, Any]:
        """Operational aggregates for the window (Architecture.md 9): tasks, approvals, tools,
        latency percentiles, and the all-time count of unapproved destructive actions."""
        since = _now() - dt.timedelta(days=days)

        def in_window(value: dt.datetime | None) -> bool:
            if value is None:
                return False
            aware = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
            return aware >= since

        with self._sessions() as s:
            tasks = [t for t in s.scalars(select(TaskRow)).all() if in_window(t.created_at)]
            ids = [t.id for t in tasks]
            calls = (
                s.scalars(select(LLMCallRow).where(LLMCallRow.task_id.in_(ids))).all()
                if ids
                else []
            )
            approvals = (
                s.scalars(select(ApprovalRow).where(ApprovalRow.task_id.in_(ids))).all()
                if ids
                else []
            )
            tools = (
                s.scalars(select(ToolInvocationRow).where(ToolInvocationRow.task_id.in_(ids))).all()
                if ids
                else []
            )
            unapproved = s.scalar(
                select(func.count())
                .select_from(ToolInvocationRow)
                .where(ToolInvocationRow.risk == "destructive")
                .where(ToolInvocationRow.decision == "allow")
                .where(ToolInvocationRow.ok.is_not(None))
            )

        def counts(values: list[str]) -> dict[str, int]:
            out: dict[str, int] = {}
            for v in values:
                out[v] = out.get(v, 0) + 1
            return out

        def pct(values: list[float], p: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
            return round(ordered[idx], 1)

        done = [t for t in tasks if t.status == TaskStatus.DONE.value]
        terminal = [
            t
            for t in tasks
            if t.status
            in (TaskStatus.DONE.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value)
        ]
        latencies = [
            (t.updated_at - t.created_at).total_seconds()
            for t in done
            if t.updated_at and t.created_at
        ]
        calls_by_task: dict[str, int] = {}
        tokens_by_task: dict[str, int] = {}
        for c in calls:
            calls_by_task[c.task_id] = calls_by_task.get(c.task_id, 0) + 1
            tokens_by_task[c.task_id] = (
                tokens_by_task.get(c.task_id, 0) + c.input_tokens + c.output_tokens
            )
        decided = [a for a in approvals if a.status != ApprovalStatus.PENDING.value]
        approved = [
            a
            for a in decided
            if a.status in (ApprovalStatus.APPROVED.value, ApprovalStatus.MODIFIED.value)
        ]
        tasks_with_approvals = {a.task_id for a in approvals}
        by_day = counts(
            [(t.created_at.date().isoformat() if t.created_at else "unknown") for t in tasks]
        )
        tool_counts = counts([t.tool for t in tools])
        return {
            "window_days": days,
            "tasks": {
                "total": len(tasks),
                "by_status": [
                    {"status": k, "count": v}
                    for k, v in sorted(counts([t.status for t in tasks]).items())
                ],
                "per_day": [{"day": k, "count": v} for k, v in sorted(by_day.items())],
                "success_rate": round(len(done) / len(terminal), 4) if terminal else None,
                "mean_llm_calls": (
                    round(sum(calls_by_task.get(t.id, 0) for t in done) / len(done), 2)
                    if done
                    else None
                ),
                "mean_tokens": (
                    round(sum(tokens_by_task.get(t.id, 0) for t in done) / len(done), 1)
                    if done
                    else None
                ),
                "mean_cost_usd": (
                    round(sum(t.cost_usd or 0.0 for t in done) / len(done), 4) if done else None
                ),
                "latency_p50_s": pct(latencies, 50),
                "latency_p95_s": pct(latencies, 95),
            },
            "approvals": {
                "total": len(approvals),
                "by_level": [
                    {"level": k, "count": v}
                    for k, v in sorted(counts([a.level for a in approvals]).items())
                ],
                "by_trigger": [
                    {"trigger": k, "count": v}
                    for k, v in sorted(counts([a.trigger for a in approvals]).items())
                ],
                "by_status": [
                    {"status": k, "count": v}
                    for k, v in sorted(counts([a.status for a in approvals]).items())
                ],
                "escalation_rate": (
                    round(len(tasks_with_approvals) / len(tasks), 4) if tasks else None
                ),
                "approval_rate": round(len(approved) / len(decided), 4) if decided else None,
            },
            "tools": {
                "total": len(tools),
                "not_executed": sum(1 for t in tools if t.ok is None),
                "by_tool": [
                    {"tool": k, "count": v}
                    for k, v in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
                ],
            },
            "safety": {"unapproved_destructive_actions": int(unapproved or 0)},
        }

    def timeline(self, task_id: str) -> list[dict[str, Any]]:
        """Ledger-only timeline of one task (the trace fallback when Jaeger is unreachable)."""
        with self._sessions() as s:
            calls = s.scalars(select(LLMCallRow).where(LLMCallRow.task_id == task_id)).all()
            tools = s.scalars(
                select(ToolInvocationRow).where(ToolInvocationRow.task_id == task_id)
            ).all()
            approvals = s.scalars(select(ApprovalRow).where(ApprovalRow.task_id == task_id)).all()
            events = s.scalars(select(AuditLogRow).where(AuditLogRow.task_id == task_id)).all()
        items: list[tuple[dt.datetime, str, str, int, dict[str, Any], bool]] = []
        for c in calls:
            items.append(
                (
                    c.created_at,
                    f"llm.call {c.role}",
                    "llm",
                    c.latency_ms * 1000,
                    {
                        "provider": c.provider,
                        "model": c.model,
                        "tokens": c.input_tokens + c.output_tokens,
                        "fallback": c.fallback,
                    },
                    False,
                )
            )
        for t in tools:
            items.append(
                (
                    t.created_at,
                    f"tool.{t.tool}",
                    "tool",
                    t.latency_ms * 1000,
                    {
                        "subtask_id": t.subtask_id,
                        "decision": t.decision,
                        "risk": t.risk,
                        "ok": t.ok,
                        "reason": (t.reason or "")[:120],
                    },
                    t.ok is False,
                )
            )
        for a in approvals:
            items.append(
                (
                    a.created_at,
                    f"hitl.{a.kind} {a.level}",
                    "hitl",
                    0,
                    {
                        "approval_id": a.id,
                        "status": a.status,
                        "decision": a.decision,
                        "decided_by": a.decided_by,
                    },
                    False,
                )
            )
        for e in events:
            items.append(
                (
                    e.created_at,
                    f"event.{e.action}",
                    "other",
                    0,
                    {"actor": e.actor, **(e.payload or {})},
                    False,
                )
            )
        items.sort(key=lambda x: x[0])
        if not items:
            return []
        t0 = items[0][0]
        out = []
        for i, (at, name, kind, dur, attrs, err) in enumerate(items):
            out.append(
                {
                    "id": f"l{i}",
                    "parent": None,
                    "name": name,
                    "kind": kind,
                    "start_us": int(at.timestamp() * 1e6),
                    "offset_us": int((at - t0).total_seconds() * 1e6),
                    "duration_us": int(dur),
                    "attrs": {k: v for k, v in attrs.items() if v is not None},
                    "error": err,
                    "depth": 0,
                }
            )
        return out

    def task_view(self, task_id: str) -> dict[str, Any] | None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return None
            subs = s.scalars(select(SubtaskRow).where(SubtaskRow.task_id == task_id)).all()
            calls = s.scalars(select(LLMCallRow).where(LLMCallRow.task_id == task_id)).all()
            tools = s.scalars(
                select(ToolInvocationRow).where(ToolInvocationRow.task_id == task_id)
            ).all()
            approvals = s.scalars(
                select(ApprovalRow).where(ApprovalRow.task_id == task_id).order_by(ApprovalRow.id)
            ).all()
            return {
                "task_id": row.id,
                "user_id": row.user_id,
                "request": row.request,
                "status": row.status,
                "plan": row.plan,
                "subtasks": [
                    {
                        "id": x.subtask_id,
                        "specialist": x.specialist,
                        "status": x.status,
                        "attempt": x.attempt,
                        "result": x.result,
                        "verdict": x.verdict,
                    }
                    for x in sorted(subs, key=lambda x: x.subtask_id)
                ],
                "final_output": row.final_output,
                "cost_usd": row.cost_usd,
                "llm_calls": len(calls),
                "tokens": sum(c.input_tokens + c.output_tokens for c in calls),
                "tool_calls": len(tools),
                "tool_calls_not_executed": sum(1 for t in tools if t.ok is None),
                "options": row.options or {},
                "tool_ledger": [
                    {
                        "subtask_id": t.subtask_id,
                        "tool": t.tool,
                        "risk": t.risk,
                        "decision": t.decision,
                        "ok": t.ok,
                        "reason": (t.reason or "")[:120],
                        "at": _iso(t.created_at),
                    }
                    for t in sorted(tools, key=lambda t: t.id)
                ],
                "approvals": [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "level": a.level,
                        "status": a.status,
                        "decision": a.decision,
                    }
                    for a in approvals
                ],
                "pending_approval_id": next(
                    (a.id for a in approvals if a.status == ApprovalStatus.PENDING.value), None
                ),
                "error": row.error,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }


class ApprovalStore:
    """Lifecycle of approval requests: pending → approved | modified | rejected | taken_over | expired."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def get_or_create(
        self, request: ApprovalRequest, *, expires_at: dt.datetime | None
    ) -> tuple[ApprovalRow, bool]:
        with self._sessions() as s:
            existing = s.scalar(select(ApprovalRow).where(ApprovalRow.dedupe_key == request.key))
            if existing is not None:
                return existing, False
            row = ApprovalRow(
                dedupe_key=request.key,
                task_id=request.task_id,
                subtask_id=request.subtask_id,
                attempt=request.attempt,
                kind=request.kind.value,
                level=request.level.value,
                trigger=request.trigger.value,
                agent=request.agent,
                proposed_action=request.proposed_action,
                reasoning=request.reasoning,
                context=request.context,
                expires_at=expires_at,
            )
            s.add(row)
            try:
                s.commit()
            except IntegrityError:  # a concurrent creator won the race
                s.rollback()
                found = s.scalar(select(ApprovalRow).where(ApprovalRow.dedupe_key == request.key))
                if found is None:
                    raise
                return found, False
            s.add(
                AuditLogRow(
                    task_id=request.task_id,
                    actor="graph",
                    action="approval.requested",
                    payload={"approval_id": row.id, "level": row.level, "kind": row.kind},
                )
            )
            s.commit()
            s.refresh(row)
            return row, True

    def get(self, approval_id: int) -> ApprovalRow | None:
        with self._sessions() as s:
            return s.get(ApprovalRow, approval_id)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._sessions() as s:
            stmt = select(ApprovalRow).order_by(ApprovalRow.id.desc()).limit(limit)
            if status:
                stmt = stmt.where(ApprovalRow.status == status)
            return [self.view(r) for r in s.scalars(stmt).all()]

    @staticmethod
    def view(row: ApprovalRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "subtask_id": row.subtask_id,
            "attempt": row.attempt,
            "kind": row.kind,
            "level": row.level,
            "trigger": row.trigger,
            "agent": row.agent,
            "proposed_action": row.proposed_action,
            "reasoning": row.reasoning,
            "context": row.context,
            "status": row.status,
            "decision": row.decision,
            "decision_payload": row.decision_payload,
            "decided_by": row.decided_by,
            "reason": row.reason,
            "created_at": _iso(row.created_at),
            "decided_at": _iso(row.decided_at),
            "expires_at": _iso(row.expires_at),
        }

    def record_decision(
        self, approval_id: int, decision: ApprovalDecision, *, status: ApprovalStatus | None = None
    ) -> ApprovalRow | None:
        """Pending → decided, atomically. Returns None if the request was not pending."""
        with self._sessions() as s:
            row = s.get(ApprovalRow, approval_id)
            if row is None or row.status != ApprovalStatus.PENDING.value:
                return None
            row.status = (status or STATUS_FOR_DECISION[decision.decision]).value
            row.decision = decision.decision.value
            row.decision_payload = decision.payload
            row.decided_by = decision.decided_by
            row.reason = decision.reason
            row.decided_at = _now()
            s.add(
                AuditLogRow(
                    task_id=row.task_id,
                    actor=decision.decided_by,
                    action=f"approval.{row.status}",
                    payload={"approval_id": row.id, "reason": decision.reason},
                )
            )
            s.commit()
            s.refresh(row)
            return row

    def decision_of(self, approval_id: int) -> ApprovalDecision | None:
        row = self.get(approval_id)
        if row is None or row.decision is None:
            return None
        return ApprovalDecision(
            decision=row.decision,  # type: ignore[arg-type]
            payload=row.decision_payload or {},
            reason=row.reason or "",
            decided_by=row.decided_by or "operator",
        )

    def due(self, now: dt.datetime) -> Sequence[ApprovalRow]:
        with self._sessions() as s:
            stmt = (
                select(ApprovalRow)
                .where(ApprovalRow.status == ApprovalStatus.PENDING.value)
                .where(ApprovalRow.expires_at.is_not(None))
                .where(ApprovalRow.expires_at <= now)
            )
            return list(s.scalars(stmt).all())


class OutboxRepository:
    """Written by the actions MCP server. Read by humans (the operator UI). Never drained automatically."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def add(self, kind: str, payload: dict[str, Any], *, task_id: str | None = None) -> int:
        with self._sessions() as s:
            row = OutboxRow(kind=kind, payload=payload, task_id=task_id)
            s.add(row)
            s.commit()
            s.refresh(row)
            return int(row.id)

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._sessions() as s:
            rows = s.scalars(select(OutboxRow).order_by(OutboxRow.id.desc()).limit(limit)).all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "kind": r.kind,
                    "payload": r.payload,
                    "status": r.status,
                    "created_at": _iso(r.created_at),
                }
                for r in rows
            ]
