"""Trace explorer + replay (docs/Design.md §3.4): the span tree of a task from Jaeger (ledger
timeline when Jaeger is down), a span inspector, and "replay from checkpoint"."""

from __future__ import annotations

import json

import httpx
import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import badge, inject_css
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — trace", page_icon="🧵", layout="wide")
inject_css()

TONE = {
    "task": "muted",
    "node": "supervisor",
    "agent": "specialist",
    "llm": "supervisor",
    "gate": "hitl",
    "tool": "muted",
    "memory": "memory",
    "hitl": "hitl",
    "mcp": "muted",
    "other": "muted",
}


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


def pick_recent() -> None:
    chosen = st.session_state.get("trace-recent")
    if chosen:
        st.session_state["trace-task"] = chosen.split(" ", 1)[0]


c = client()
st.session_state.setdefault("trace-task", "")
st.title("Trace")
try:
    recent = c.tasks(limit=30)
except httpx.HTTPStatusError:
    recent = []
st.selectbox(
    "Recent tasks",
    [f"{t['task_id']} · user {t['user_id']} · {t['status']} · {t['request'][:50]}" for t in recent],
    index=None,
    placeholder="pick a task",
    key="trace-recent",
    on_change=pick_recent,
)
task_id = st.text_input("Task id", key="trace-task").strip()
if not task_id:
    st.stop()
try:
    tr = c.trace(task_id)
except httpx.HTTPStatusError as e:
    st.error(f"{e.response.status_code}: {e.response.text[:300]}")
    st.stop()

show_mcp = st.checkbox("Show MCP transport spans", value=False)
spans = [s for s in tr["spans"] if show_mcp or s.get("kind") != "mcp"]
total = tr.get("duration_us") or 1
st.caption(
    f"source: {tr['source']} · {tr['span_count']} spans in {tr['traces']} trace(s) · "
    f"{total / 1e6:.1f} s"
    + (" — Jaeger is unreachable; showing the ledger timeline" if tr["source"] == "ledger" else "")
)

# The tree: one row per span, indented, with a proportional duration bar.
rows = []
for s in spans:
    dur = s.get("duration_us", 0)
    bar = "█" * max(1, round(24 * dur / total)) if total and dur else ""
    a = s.get("attrs", {})
    hint = (
        a.get("model")
        or a.get("tool")
        or a.get("decision")
        or (f"{a.get('count')} memories" if "count" in a else "")
        or a.get("status")
        or ""
    )
    rows.append(
        {
            "span": ("    " * s.get("depth", 0)) + s["name"],
            "kind": s.get("kind"),
            "start": f"+{s.get('offset_us', 0) / 1e6:.2f}s",
            "duration": f"{dur / 1e3:.0f} ms" if dur else "",
            "bar": bar,
            "note": (str(hint) + (" ⚠" if s.get("error") else ""))[:60],
        }
    )
st.dataframe(rows, hide_index=True, width="stretch", height=min(600, 38 + 28 * len(rows)))

st.subheader("Inspect a span")
labels = [f"{i:03d} · {s['name']} · {s.get('kind')}" for i, s in enumerate(spans)]
chosen = st.selectbox("Span", labels, index=None, placeholder="choose a span to see its attributes")
if chosen:
    s = spans[int(chosen.split(" ", 1)[0])]
    st.markdown(
        badge(s.get("kind", "other"), TONE.get(s.get("kind", "other"), "muted")),
        unsafe_allow_html=True,
    )
    st.code(json.dumps(s.get("attrs", {}), indent=2), language="json")

st.divider()
st.subheader("Replay from a checkpoint")
st.caption(
    "Forks this task at a checkpoint into a *new* task (the original is untouched), optionally with "
    "overridden state, and runs it to the end. Then compare the two on the diff below."
)
if st.button("List checkpoints"):
    try:
        st.session_state["checkpoints"] = c.checkpoints(task_id)
    except httpx.HTTPStatusError as e:
        st.error(f"{e.response.status_code}: {e.response.text[:300]}")
cps = st.session_state.get("checkpoints") or []
if cps:
    options = [
        f"{cp['checkpoint_id']} · step {cp['step']} · "
        f"after {', '.join(cp['produced_by']) or 'start'} → next {', '.join(cp['next']) or 'END'}"
        for cp in cps
    ]
    picked = st.selectbox("Checkpoint", options, index=None, placeholder="pick where to fork from")
    overrides = st.text_area(
        "Overrides (one key=value per line; JSON values allowed)",
        placeholder="options.require_human_review=true   or   plan={...json...}",
        height=90,
    )
    if picked and st.button("Replay into a new task", type="primary"):
        try:
            out = c.replay(
                task_id,
                picked.split(" ", 1)[0],
                [line.strip() for line in overrides.splitlines() if line.strip()],
            )
            st.success(
                f"queued replay task {out['task_id']} (from checkpoint {out['checkpoint_id'][:8]}…)"
            )
            st.session_state["replay-task"] = out["task_id"]
        except httpx.HTTPStatusError as e:
            st.error(f"{e.response.status_code}: {e.response.text[:300]}")

st.subheader("Replay diff")
fork_id = st.text_input(
    "Replay task id", value=st.session_state.get("replay-task", ""), key="diff-task"
).strip()
if fork_id:
    try:
        d = c.replay_diff(fork_id)
        if "error" in d:
            st.info(d["error"])
        else:
            st.markdown(
                f"status **{d['status'][0]} → {d['status'][1]}** · "
                f"LLM calls {d['llm_calls'][0]} → {d['llm_calls'][1]} · "
                f"tool calls {d['tool_calls'][0]} → {d['tool_calls'][1]} · "
                f"approvals {d['approvals'][0]} → {d['approvals'][1]} · "
                f"deliverable changed: {d['deliverable_changed']}"
            )
            st.dataframe(
                [
                    {"subtask": sid, **{k: str(v) for k, v in info.items()}}
                    for sid, info in d["subtasks"].items()
                ],
                hide_index=True,
                width="stretch",
            )
            if d["tools_by_name"]:
                st.dataframe(
                    [
                        {"tool": t, "source": a, "fork": b}
                        for t, (a, b) in d["tools_by_name"].items()
                    ],
                    hide_index=True,
                    width="stretch",
                )
    except httpx.HTTPStatusError as e:
        st.error(f"{e.response.status_code}: {e.response.text[:300]}")
