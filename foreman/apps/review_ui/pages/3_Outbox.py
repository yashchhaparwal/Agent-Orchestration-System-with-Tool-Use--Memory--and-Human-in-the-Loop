"""Everything the agents proposed to send. Nothing here has been sent by Foreman."""

from __future__ import annotations

import json

import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import badge, inject_css
from packages.shared.config import get_settings

st.set_page_config(page_title="Foreman — outbox", page_icon="📮", layout="wide")
inject_css()


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


st.title("Outbox")
st.caption(
    "Queued by the actions server after human approval. Sending is a person's job — Foreman has no send path."
)
rows = client().outbox(limit=200)
if not rows:
    st.info("Empty.")
    st.stop()
for r in rows:
    with st.expander(
        f"#{r['id']} · {r['kind']} · {r['created_at'][:19]} · task {(r['task_id'] or '—')[:8]}"
    ):
        st.markdown(badge(r["status"], "hitl"), unsafe_allow_html=True)
        st.code(json.dumps(r["payload"], indent=2), language="json")
