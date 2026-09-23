"""Stats (docs/Design.md §3.6): operational tiles, the last eval headline, and a few bar charts.
One accent hue per chart, direct numbers on tiles, and a table view under every chart."""

from __future__ import annotations

import httpx
import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import badge, inject_css
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — stats", page_icon="📊", layout="wide")
inject_css()
ACCENT = "#4F46E5"


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def num(value: float | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.1f}{suffix}"


st.title("Stats")
days = st.sidebar.slider("Window (days)", 1, 30, 7)
try:
    s = client().stats(days=days)
except httpx.HTTPStatusError as e:
    st.error(f"{e.response.status_code}: {e.response.text[:300]}")
    st.stop()

ops = s["tasks"]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric(f"Tasks ({days} d)", ops["total"])
c2.metric("Completed", pct(ops["success_rate"]), help="done / (done + failed + cancelled)")
c3.metric("Mean LLM calls / task", num(ops["mean_llm_calls"]))
c4.metric(
    "Escalation rate", pct(s["approvals"]["escalation_rate"]), help="tasks that paused for a human"
)
c5.metric(
    "Approval rate", pct(s["approvals"]["approval_rate"]), help="approved + modified / decided"
)
unapproved = s["safety"]["unapproved_destructive_actions"]
c6.metric("Unapproved destructive actions", unapproved)
st.markdown(
    badge("0 unapproved destructive actions — the gate held", "observe")
    if unapproved == 0
    else badge(f"{unapproved} unapproved destructive action(s) — investigate", "hitl"),
    unsafe_allow_html=True,
)
st.caption(
    f"Latency p50 {num(ops['latency_p50_s'], ' s')} · p95 {num(ops['latency_p95_s'], ' s')} · "
    f"mean tokens {num(ops['mean_tokens'])} · mean cost ${ops['mean_cost_usd'] or 0:.4f} (free tiers)"
)

st.subheader("Last eval run")
ev = s.get("last_eval")
if not ev:
    st.info("No eval report yet — run `uv run python -m packages.evals.runner --k 3`.")
else:
    m = ev["metrics"]
    e1, e2, e3, e4, e5, e6 = st.columns(6)
    e1.metric("Task success", pct(m.get("success_rate")))
    e2.metric("pass^k", pct(m.get("pass_k")), help=f"k = {ev['k']}")
    e3.metric("Tool P / R", f"{pct(m.get('tool_precision'))} / {pct(m.get('tool_recall'))}")
    e4.metric(
        "Escalation P / R",
        f"{pct(m.get('escalation_precision'))} / {pct(m.get('escalation_recall'))}",
    )
    e5.metric("Injection resistance", pct(m.get("injection_resistance")))
    e6.metric("Judge mean", num(m.get("judge_mean"), " / 5"))
    st.caption(
        f"run `{ev['run_id']}` {ev.get('label') or ''} · {ev['task_count']} tasks · {ev['run_count']} runs · "
        f"finished {ev['finished_at'][:16]} · vs baseline: {ev.get('diff_verdict') or 'no baseline'}"
    )

left, right = st.columns(2)
with left:
    st.subheader("Tasks per day")
    per_day = s["tasks"]["per_day"]
    if per_day:
        st.bar_chart({"tasks": {d["day"]: d["count"] for d in per_day}}, color=ACCENT, height=220)
        with st.expander("table"):
            st.dataframe(per_day, hide_index=True, width="stretch")
    else:
        st.caption("no tasks in the window")
    st.subheader("Tool calls by tool")
    tools = s["tools"]["by_tool"]
    if tools:
        st.bar_chart(
            {"calls": {t["tool"]: t["count"] for t in tools}},
            color=ACCENT,
            height=260,
            horizontal=True,
        )
        with st.expander("table"):
            st.dataframe(tools, hide_index=True, width="stretch")
with right:
    st.subheader("Approvals by level")
    by_level = s["approvals"]["by_level"]
    if by_level:
        st.bar_chart(
            {"approvals": {r["level"]: r["count"] for r in by_level}}, color="#E11D48", height=220
        )
        with st.expander("table"):
            st.dataframe(by_level, hide_index=True, width="stretch")
    else:
        st.caption("no approvals in the window")
    st.subheader("Decisions")
    st.dataframe(s["approvals"]["by_status"], hide_index=True, width="stretch")
    st.subheader("Tasks by status")
    st.dataframe(ops["by_status"], hide_index=True, width="stretch")
