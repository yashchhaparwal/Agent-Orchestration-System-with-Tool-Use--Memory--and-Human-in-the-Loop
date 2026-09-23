"""Foreman operator UI (docs/Design.md). Streamlit v1: approval queue, task view, outbox.

uv run streamlit run apps/review_ui/app.py --server.port 8501
"""

from __future__ import annotations

import streamlit as st

from apps.review_ui.api_client import ForemanClient
from apps.review_ui.components.badges import inject_css
from packages.shared.config import get_settings


def client() -> ForemanClient:
    if "client" not in st.session_state:
        s = get_settings()
        st.session_state["client"] = ForemanClient(s.api_base_url, s.api_key)
    return st.session_state["client"]  # type: ignore[no-any-return]


st.set_page_config(page_title="Foreman — operator", page_icon="🛠️", layout="wide")
inject_css()

st.title("Foreman — operator console")
c = client()
if not c.health():
    st.error(
        f"The API at {get_settings().api_base_url} is not answering. Start it with `make api`."
    )
    st.stop()

pending = c.approvals(status="pending", limit=500)
col1, col2, col3 = st.columns(3)
col1.metric("Pending approvals", len(pending))
col2.metric("L2 · actions", sum(1 for p in pending if p["level"] == "L2"))
col3.metric("L3/L4 · plans & escalations", sum(1 for p in pending if p["level"] in ("L3", "L4")))

st.markdown(
    """
**Approvals** — decide what the agents may not decide alone.
**Tasks** — look up any task: plan, subtasks, verdicts, deliverable, ledger.
**Outbox** — everything the agents *proposed* to send. Nothing here has been sent.
"""
)
st.page_link("pages/1_Approvals.py", label="Open the approval queue →")
