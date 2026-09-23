"""Replay v1 (PRD.md F11, Architecture.md §9): fork a task from one of its checkpoints into a *new*
task, optionally with overridden state, run it to the end, and diff the two trajectories.

    uv run python -m packages.orchestrator.tracing.replay <task_id> --list
    uv run python -m packages.orchestrator.tracing.replay <task_id> --from <cp> --set plan='{...}'
    uv run python -m packages.orchestrator.tracing.replay <task_id> --from <cp> \
        --set options.require_human_review=true

The source task is never modified: the fork lives on a new thread (= new task id), whose task row
records ``options.replay_of`` / ``options.replay_checkpoint``. Interrupts during a replay are
answered with ``--decision`` (approve by default) and recorded like any other approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import structlog
from langgraph.types import Command

from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.memory.persistent import ApprovalStore, TaskStore
from packages.shared.types.approval import ApprovalDecision, DecisionKind
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.task import TaskOptions, TaskStatus

log = structlog.get_logger(__name__)


def interrupt_payload(final: dict[str, Any]) -> dict[str, Any] | None:
    """The approval the graph is waiting on, if it stopped at an interrupt."""
    interrupts = final.get("__interrupt__") or []
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", interrupts[0])
    return dict(value) if isinstance(value, dict) else {"value": value}


# ---------------------------------------------------------------- checkpoints


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {"thread_id": thread_id}
    if checkpoint_id:
        cfg["checkpoint_id"] = checkpoint_id
    return {"configurable": cfg}


async def list_checkpoints(graph: Any, task_id: str) -> list[dict[str, Any]]:
    """Oldest first: id, step, the nodes that produced it, the nodes that run next, status."""
    snapshots = [s async for s in graph.aget_state_history(_config(task_id))]
    snapshots.reverse()
    out: list[dict[str, Any]] = []
    previous_next: tuple[str, ...] = ()
    for snap in snapshots:
        cfg = snap.config.get("configurable", {})
        out.append(
            {
                "checkpoint_id": cfg.get("checkpoint_id"),
                "step": snap.metadata.get("step") if snap.metadata else None,
                "source": snap.metadata.get("source") if snap.metadata else None,
                "produced_by": list(previous_next),
                "next": list(snap.next),
                "status": snap.values.get("status") if snap.values else None,
                "subtasks_done": sorted((snap.values or {}).get("subtask_results", {}).keys()),
                "created_at": snap.created_at,
            }
        )
        previous_next = tuple(snap.next)
    return out


# ---------------------------------------------------------------- overrides


def parse_override(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise ValueError(f"override must look like key=value, got {raw!r}")
    key, value = raw.split("=", 1)
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    return key.strip(), parsed


def apply_overrides(values: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for key, value in overrides.items():
        if key == "plan":
            out["plan"] = ExecutionPlan.model_validate(value)
            out["plan_confidence"] = out["plan"].confidence
        elif key.startswith("options."):
            current = out.get("options")
            options = (
                current
                if isinstance(current, TaskOptions)
                else TaskOptions.model_validate(current or {})
            )
            out["options"] = options.model_copy(update={key.split(".", 1)[1]: value})
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------- the replay itself


async def replay(
    graph: Any,
    *,
    store: TaskStore,
    approvals: ApprovalStore,
    source_task_id: str,
    checkpoint_id: str,
    overrides: dict[str, Any] | None = None,
    new_task_id: str | None = None,
    decision: DecisionKind = DecisionKind.APPROVE,
    max_pauses: int = 8,
) -> dict[str, Any]:
    """Fork ``source_task_id`` at ``checkpoint_id`` into a new task; run it; return the final state."""
    history = [s async for s in graph.aget_state_history(_config(source_task_id))]
    by_id = {(s.config or {}).get("configurable", {}).get("checkpoint_id"): s for s in history}
    if checkpoint_id not in by_id:
        raise ValueError(f"checkpoint {checkpoint_id} not found on task {source_task_id}")
    snapshot = by_id[checkpoint_id]
    parent_id = (snapshot.parent_config or {}).get("configurable", {}).get("checkpoint_id")
    parent = by_id.get(parent_id) if parent_id else None
    as_node = parent.next[0] if parent and parent.next else "__start__"

    values = apply_overrides(dict(snapshot.values), overrides or {})
    options = values.get("options")
    options_model = (
        options if isinstance(options, TaskOptions) else TaskOptions.model_validate(options or {})
    )
    options_model = options_model.model_copy(
        update={"replay_of": source_task_id, "replay_checkpoint": checkpoint_id}
    )
    if new_task_id is None:
        row = store.create_task(
            user_id=str(values.get("user_id", "replay")),
            request=str(values.get("request", "")),
            options=options_model,
        )
        new_task_id = row.id
    values["task_id"] = new_task_id
    if values.get("plan") is not None:  # the plan node will not re-run on the fork
        plan = values["plan"]
        store.set_plan(
            new_task_id,
            plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.model_validate(plan),
        )
    values["options"] = options_model
    values.pop("final_output", None)
    values["status"] = "running"
    values["error"] = None

    new_config = _config(new_task_id)
    await graph.aupdate_state(new_config, values, as_node=as_node)
    store.set_status(new_task_id, TaskStatus.RUNNING)
    log.info(
        "replay.start",
        source=source_task_id,
        checkpoint=checkpoint_id,
        new_task_id=new_task_id,
        as_node=as_node,
        overrides=sorted((overrides or {}).keys()),
    )

    payload: Any = None
    final: dict[str, Any] = {}
    for _ in range(max_pauses + 1):
        final = await graph.ainvoke(payload, new_config)
        waiting = interrupt_payload(final)
        if waiting is None:
            break
        answer = ApprovalDecision(
            decision=decision, reason="replay auto-decision", decided_by="replay"
        )
        if waiting.get("approval_id") is not None:
            approvals.record_decision(int(waiting["approval_id"]), answer)
        payload = Command(resume=answer.model_dump(mode="json"))
    else:
        store.set_status(new_task_id, TaskStatus.FAILED, error="replay: still paused")
    return {"new_task_id": new_task_id, "as_node": as_node, "final": final}


# ---------------------------------------------------------------- diff


def _tool_counts(view: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in view.get("tool_ledger") or []:
        counts[t["tool"]] = counts.get(t["tool"], 0) + 1
    return counts


def diff_views(source: dict[str, Any], fork: dict[str, Any]) -> dict[str, Any]:
    """Compare two task views (source vs replayed fork)."""
    src_sub = {s["id"]: s for s in source.get("subtasks", [])}
    frk_sub = {s["id"]: s for s in fork.get("subtasks", [])}
    subtasks: dict[str, Any] = {}
    for sid in sorted(set(src_sub) | set(frk_sub)):
        a, b = src_sub.get(sid), frk_sub.get(sid)
        if a is None:
            subtasks[sid] = {"change": "added", "fork": (b or {}).get("status")}
        elif b is None:
            subtasks[sid] = {"change": "removed", "source": a["status"]}
        else:
            out_a = (a.get("result") or {}).get("output", "")
            out_b = (b.get("result") or {}).get("output", "")
            subtasks[sid] = {
                "change": "same"
                if (a["status"], a["attempt"], out_a) == (b["status"], b["attempt"], out_b)
                else "changed",
                "status": [a["status"], b["status"]],
                "attempt": [a["attempt"], b["attempt"]],
                "output_changed": out_a != out_b,
            }
    src_tools, frk_tools = _tool_counts(source), _tool_counts(fork)
    return {
        "source_task_id": source.get("task_id"),
        "fork_task_id": fork.get("task_id"),
        "status": [source.get("status"), fork.get("status")],
        "llm_calls": [source.get("llm_calls"), fork.get("llm_calls")],
        "tool_calls": [source.get("tool_calls"), fork.get("tool_calls")],
        "tools_by_name": {
            t: [src_tools.get(t, 0), frk_tools.get(t, 0)]
            for t in sorted(set(src_tools) | set(frk_tools))
            if src_tools.get(t, 0) != frk_tools.get(t, 0)
        },
        "approvals": [len(source.get("approvals") or []), len(fork.get("approvals") or [])],
        "subtasks": subtasks,
        "deliverable_title": [
            (source.get("final_output") or {}).get("title"),
            (fork.get("final_output") or {}).get("title"),
        ],
        "deliverable_changed": (source.get("final_output") or {}).get("body")
        != (fork.get("final_output") or {}).get("body"),
    }


def render_diff(diff: dict[str, Any]) -> str:
    lines = [
        f"## Replay diff: `{diff['source_task_id']}` → `{diff['fork_task_id']}`",
        "",
        f"- status {diff['status'][0]} → {diff['status'][1]} · "
        f"LLM calls {diff['llm_calls'][0]} → {diff['llm_calls'][1]} · "
        f"tool calls {diff['tool_calls'][0]} → {diff['tool_calls'][1]} · "
        f"approvals {diff['approvals'][0]} → {diff['approvals'][1]}",
        f"- deliverable changed: {diff['deliverable_changed']} "
        f"({diff['deliverable_title'][0]!r} → {diff['deliverable_title'][1]!r})",
        "",
        "| subtask | change | status | attempt | output changed |",
        "|---|---|---|---|---|",
    ]
    for sid, s in diff["subtasks"].items():
        if s["change"] in ("added", "removed"):
            lines.append(f"| {sid} | {s['change']} | {s.get('fork') or s.get('source')} | — | — |")
        else:
            lines.append(
                f"| {sid} | {s['change']} | {s['status'][0]} → {s['status'][1]} | "
                f"{s['attempt'][0]} → {s['attempt'][1]} | {s['output_changed']} |"
            )
    if diff["tools_by_name"]:
        lines += ["", "| tool | source | fork |", "|---|---|---|"]
        for t, (a, b) in diff["tools_by_name"].items():
            lines.append(f"| {t} | {a} | {b} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- light graph for read-only use


def make_light_deps(settings: Any) -> GraphDeps:
    """A graph with the real node/edge structure but no MCP discovery — enough to read state
    history (the API lists checkpoints with this; the worker replays with the full runtime)."""
    from packages.orchestrator.agents.catalog import build_specialists
    from packages.orchestrator.agents.reviewer.agent import build_reviewer_agent
    from packages.orchestrator.agents.supervisor.agent import (
        build_supervisor_agent,
        synthesize_prompt,
    )
    from packages.orchestrator.gate.decide import Gate
    from packages.orchestrator.runtime import make_approvals, make_llm_factory, make_store
    from packages.tools.registry.registry import ToolRegistry

    registry = ToolRegistry.from_listing({}, {"servers": {}, "tools": {}}, {})
    return GraphDeps(
        llm_for=make_llm_factory(settings),
        registry=registry,
        gate=Gate(registry),
        store=make_store(settings),
        approvals=make_approvals(settings),
        specialists=build_specialists(),
        supervisor=build_supervisor_agent(),
        synthesize_prompt=synthesize_prompt(),
        reviewer=build_reviewer_agent(),
    )


# ---------------------------------------------------------------- CLI


async def _main(args: argparse.Namespace) -> int:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from packages.orchestrator.graph.serde import checkpoint_serde
    from packages.orchestrator.runtime import build_runtime, make_approvals, make_store
    from packages.orchestrator.tracing.otel import configure_tracing
    from packages.orchestrator.worker import checkpointer_conninfo
    from packages.shared.config import get_settings

    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    settings = get_settings()
    store, approvals = make_store(settings), make_approvals(settings)
    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_conninfo(settings.database_url), serde=checkpoint_serde()
    ) as saver:
        if args.list:
            graph = build_graph(make_light_deps(settings), checkpointer=saver)
            for cp in await list_checkpoints(graph, args.task_id):
                print(
                    f"{cp['checkpoint_id']}  step {cp['step']:>3}  "
                    f"after {','.join(cp['produced_by']) or '-':<28} next {','.join(cp['next']) or 'END'}"
                    f"  done={','.join(cp['subtasks_done']) or '-'}"
                )
            return 0
        if not args.from_checkpoint:
            print("--from <checkpoint_id> is required (use --list to see them)", file=sys.stderr)
            return 2
        provider = configure_tracing(settings)
        deps = await build_runtime(settings, store=store, approvals=approvals)
        graph = build_graph(deps, checkpointer=saver)
        overrides = dict(parse_override(o) for o in args.set or [])
        result = await replay(
            graph,
            store=store,
            approvals=approvals,
            source_task_id=args.task_id,
            checkpoint_id=args.from_checkpoint,
            overrides=overrides,
            decision=DecisionKind(args.decision),
        )
        provider.force_flush()
    source_view = store.task_view(args.task_id) or {}
    fork_view = store.task_view(result["new_task_id"]) or {}
    diff = diff_views(source_view, fork_view)
    print(render_diff(diff))
    print(f"new task: {result['new_task_id']} (forked after {result['as_node']})", file=sys.stderr)
    return 0 if fork_view.get("status") == "done" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Replay a task from a checkpoint into a new task")
    ap.add_argument("task_id")
    ap.add_argument("--list", action="store_true", help="list the task's checkpoints")
    ap.add_argument("--from", dest="from_checkpoint", help="checkpoint id to fork from")
    ap.add_argument("--set", action="append", help="override key=value (JSON values allowed)")
    ap.add_argument("--decision", default="approve", choices=["approve", "reject"])
    return ap.parse_args(argv)


if __name__ == "__main__":
    from packages.shared.asyncio_compat import use_selector_event_loop_on_windows

    structlog.configure(processors=[structlog.processors.KeyValueRenderer(key_order=["event"])])
    use_selector_event_loop_on_windows()
    sys.exit(asyncio.run(_main(parse_args())))
