"""Approval queue + detail (docs/Design.md §3.1–3.2)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import (
    inject_css,
    level_badge,
    risk_badge,
    status_badge,
)
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — approvals", page_icon="⏸️", layout="wide")
inject_css()


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


def decide(
    approval_id: int, decision: str, *, payload: dict[str, Any] | None = None, reason: str = ""
) -> None:
    try:
        client().decide(
            approval_id,
            decision,
            payload=payload,
            reason=reason,
            decided_by=st.session_state.get("operator", "operator"),
        )
        st.success(f"Recorded: {decision}. The worker is resuming the task.")
        st.session_state.pop("selected", None)
        st.rerun()
    except httpx.HTTPStatusError as e:
        st.error(f"{e.response.status_code}: {e.response.text[:300]}")


c = client()
st.session_state.setdefault("operator", st.query_params.get("operator", "operator"))
st.sidebar.text_input("Your name (recorded on decisions)", key="operator")
if st.session_state["operator"] and st.query_params.get("operator") != st.session_state["operator"]:
    st.query_params["operator"] = st.session_state["operator"]  # survives F5
show = st.sidebar.selectbox(
    "Show", ["pending", "approved", "modified", "rejected", "taken_over", "expired", "all"], index=0
)
rows = c.approvals(status=None if show == "all" else show, limit=200)

st.title("Approvals")
if not rows:
    st.info(
        "No pending approvals. A running task appears here when it pauses for a decision; watch the Tasks page."
    )
    st.stop()

# ---- queue ----
table = [
    {
        "id": r["id"],
        "level": r["level"],
        "kind": r["kind"],
        "trigger": r["trigger"],
        "task": r["task_id"][:8],
        "subtask": r["subtask_id"] or "—",
        "action": (r["proposed_action"].get("tool") or r["kind"]),
        "status": r["status"],
        "expires": (r["expires_at"] or "")[:16],
    }
    for r in rows
]
st.dataframe(table, width="stretch", hide_index=True)
ids = [r["id"] for r in rows]
selected = st.selectbox("Open approval", ids, index=0, key="selected")
detail = c.approval(int(selected))

# ---- detail ----
left, right = st.columns([3, 2])
with left:
    st.markdown(
        f"{level_badge(detail['level'])} &nbsp; {status_badge(detail['status'])} &nbsp; "
        f"trigger: `{detail['trigger']}` · task `{detail['task_id']}` · agent `{detail['agent']}`",
        unsafe_allow_html=True,
    )
    ctx = detail.get("context") or {}
    st.subheader("Request")
    st.markdown(f'<div class="card">{ctx.get("request", "")}</div>', unsafe_allow_html=True)

    if ctx.get("plan"):
        st.subheader("Plan")
        for s in ctx["plan"]:
            marker = (
                "✓"
                if s["status"] == "accepted"
                else "→"
                if s["id"] == detail.get("subtask_id")
                else "·"
            )
            st.markdown(
                f"{marker} **{s['id']}** ({s['specialist']}) — {s['description']}  \n{status_badge(s['status'])}",
                unsafe_allow_html=True,
            )

    if ctx.get("completed_subtasks"):
        st.subheader("Done so far")
        for r in ctx["completed_subtasks"]:
            with st.expander(
                f"{r['id']} · attempt {r['attempt']} · {r['status']} · score {r['score']}"
            ):
                st.write(r["output_preview"])
                st.caption("sources: " + ", ".join(r.get("sources") or []))

    if ctx.get("memories"):
        st.subheader("Similar past experience")
        for m in ctx["memories"][:3]:
            st.markdown(
                f"<div class='card card-memory'>{m.get('text', '')}<br><small>"
                f"{m.get('task_type', '')} · {m.get('outcome', '')} · relevance {float(m.get('score', 0)):.2f}"
                "</small></div>",
                unsafe_allow_html=True,
            )

    st.subheader("Decision point")
    action = detail.get("proposed_action") or {}
    if detail["kind"] == "tool_call":
        st.markdown(
            f'<div class="card card-hitl"><b>{action.get("tool")}</b> &nbsp; {risk_badge(action.get("risk"))}'
            f"<br><small>{action.get('gate_reason', '')}</small></div>",
            unsafe_allow_html=True,
        )
        st.code(json.dumps(action.get("arguments", {}), indent=2), language="json")
    elif detail["kind"] == "plan":
        st.code(json.dumps(action.get("plan", {}), indent=2), language="json")
    else:
        st.markdown(
            f'<div class="card card-hitl">{action.get("reason", "")}</div>', unsafe_allow_html=True
        )
        st.json(action.get("options", {}))
    if detail.get("reasoning"):
        st.caption("Agent's reasoning")
        st.markdown(f"> {detail['reasoning']}")

with right:
    st.subheader("Decide")
    if detail["status"] != "pending":
        st.info(
            f"Already {detail['status']} by {detail.get('decided_by')}: {detail.get('reason') or ''}"
        )
        st.stop()
    reason = st.text_area("Reason (required to reject)", key=f"reason-{selected}")

    if st.button("✅ Approve", type="primary", width="stretch"):
        decide(int(selected), "approve", reason=reason)

    with st.expander("✏️ Modify"):
        if detail["kind"] == "tool_call":
            args = action.get("arguments", {}) or {}
            simple = all(
                isinstance(v, str | int | float | bool) or v is None for v in args.values()
            )
            if simple and args:
                st.caption("Edit the fields you want to change, then run.")
                edited_args: dict[str, Any] = {}
                for key, value in args.items():
                    label = key.replace("_", " ")
                    widget_key = f"arg-{selected}-{key}"
                    if isinstance(value, bool):
                        edited_args[key] = st.checkbox(label, value=value, key=widget_key)
                    elif isinstance(value, int | float):
                        edited_args[key] = st.number_input(label, value=value, key=widget_key)
                    elif isinstance(value, str) and (len(value) > 120 or chr(10) in value):
                        edited_args[key] = st.text_area(
                            label, value=value, height=240, key=widget_key
                        )
                    else:
                        edited_args[key] = st.text_input(
                            label, value="" if value is None else str(value), key=widget_key
                        )
                if st.button("Run with these arguments", width="stretch"):
                    decide(
                        int(selected),
                        "modify",
                        payload={"arguments": edited_args},
                        reason=reason,
                    )
            else:
                edited = st.text_area(
                    "Arguments (JSON)",
                    value=json.dumps(args, indent=2),
                    height=180,
                    key=f"args-{selected}",
                )
                if st.button("Run with these arguments", width="stretch"):
                    try:
                        decide(
                            int(selected),
                            "modify",
                            payload={"arguments": json.loads(edited)},
                            reason=reason,
                        )
                    except json.JSONDecodeError as e:
                        st.error(f"invalid JSON: {e}")
        elif detail["kind"] == "plan":
            edited = st.text_area(
                "Plan (JSON)",
                value=json.dumps(action.get("plan", {}), indent=2),
                height=300,
                key=f"plan-{selected}",
            )
            if st.button("Use this plan", width="stretch"):
                try:
                    decide(
                        int(selected), "modify", payload={"plan": json.loads(edited)}, reason=reason
                    )
                except json.JSONDecodeError as e:
                    st.error(f"invalid JSON: {e}")
        else:
            sid = st.text_input("Subtask id", key=f"sid-{selected}")
            output = st.text_area("Output for that subtask", height=200, key=f"out-{selected}")
            if st.button("Accept this output", width="stretch"):
                decide(
                    int(selected),
                    "modify",
                    payload={"subtask_id": sid, "output": output},
                    reason=reason,
                )

    if st.button("⛔ Reject", width="stretch"):
        if not reason.strip():
            st.warning("A reason is required to reject.")
        else:
            decide(int(selected), "reject", reason=reason)

    with st.expander("🧑 Take over"):
        if detail["kind"] == "tool_call":
            output = st.text_area(
                "Output for the subtask (the agent stands down)", height=200, key=f"take-{selected}"
            )
            if st.button("Submit as the subtask result", width="stretch"):
                decide(int(selected), "take_over", payload={"output": output}, reason=reason)
        else:
            title = st.text_input("Deliverable title", key=f"title-{selected}")
            body = st.text_area("Deliverable body (Markdown)", height=260, key=f"body-{selected}")
            if st.button("Submit as the final deliverable", width="stretch"):
                decide(
                    int(selected),
                    "take_over",
                    payload={"title": title, "body": body},
                    reason=reason,
                )
