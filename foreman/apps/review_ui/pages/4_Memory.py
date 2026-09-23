"""Memory browser (docs/Design.md §3.5): what Foreman has learned about a user, and the delete."""

from __future__ import annotations

import httpx
import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import badge, inject_css
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — memory", page_icon="🧠", layout="wide")
inject_css()


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


def pick_user() -> None:
    chosen = st.session_state.get("memory-user-pick")
    if chosen:
        st.session_state["memory-user"] = chosen.split(" ", 1)[0]


c = client()
st.title("Memory")
st.caption(
    "Lessons extracted after each task, recalled into the planner for the same user. "
    "Importance fades when a lesson is not used; faded or old lessons expire on the nightly pass."
)
st.session_state.setdefault("memory-user", "u_42")
try:
    known = c.memory_users()
except httpx.HTTPStatusError:
    known = []
st.selectbox(
    "Users with memories",
    [f"{u['user_id']} · {u['count']} lesson{'s' if u['count'] != 1 else ''}" for u in known],
    index=None,
    placeholder="pick a user, or type an id below",
    key="memory-user-pick",
    on_change=pick_user,
)
user_id = st.text_input("User id", key="memory-user").strip()
if not user_id:
    st.stop()
try:
    rows = c.memories(user_id)
except httpx.HTTPStatusError as e:
    if e.response.status_code == 503:
        st.error("Long-term memory is not available (is ChromaDB running, is Ollama up?).")
    else:
        st.error(f"{e.response.status_code}: {e.response.text[:300]}")
    st.stop()

if not rows:
    st.info(f"No memories for `{user_id}` yet — finish a task for this user and come back.")
    st.stop()

st.dataframe(
    [
        {
            "importance": round(r["effective_importance"], 2),
            "stored": r["importance"],
            "type": r["task_type"],
            "outcome": r["outcome"],
            "created": r["created_at"][:16],
            "last used": r["last_accessed"][:16],
            "uses": r["access_count"],
            "text": r["text"][:90],
        }
        for r in rows
    ],
    width="stretch",
    hide_index=True,
)
for r in rows:
    with st.expander(
        f"{r['task_type']} · {r['outcome']} · importance {r['effective_importance']:.2f}"
    ):
        st.markdown(badge(r["outcome"], "memory"), unsafe_allow_html=True)
        st.write(r["text"])
        st.caption(
            f"id {r['id']} · from task {r['source_task_id'][:8] or '—'} · tools: "
            + (", ".join(r["tools_used"]) or "—")
        )

st.divider()
confirm = st.checkbox(f"Yes, delete every memory for {user_id}", key="memory-confirm")
if st.button("Delete all for this user", type="primary", disabled=not confirm, width="stretch"):
    out = c.delete_memories(user_id)
    st.success(f"Deleted {out['deleted']} record(s) for {user_id}.")
    st.rerun()
