"""Colour-by-role badges and the page stylesheet (docs/Design.md §2)."""

from __future__ import annotations

import streamlit as st

TOKENS = {
    "supervisor": ("#4F46E5", "#EEF2FF"),
    "specialist": ("#0891B2", "#ECFEFF"),
    "reviewer": ("#D97706", "#FFFBEB"),
    "memory": ("#7C3AED", "#F5F3FF"),
    "hitl": ("#E11D48", "#FFF1F2"),
    "observe": ("#059669", "#ECFDF5"),
    "muted": ("#475569", "#F8FAFC"),
}

STATUS_TONE = {
    "queued": "muted",
    "running": "supervisor",
    "awaiting_approval": "hitl",
    "done": "observe",
    "failed": "hitl",
    "cancelled": "muted",
    "pending": "hitl",
    "approved": "observe",
    "modified": "supervisor",
    "rejected": "reviewer",
    "taken_over": "hitl",
    "expired": "muted",
    "accepted": "observe",
    "planned": "muted",
}

RISK_TONE = {"safe": "observe", "risky": "reviewer", "destructive": "hitl"}
LEVEL_LABEL = {"L1": "Notify", "L2": "Approve action", "L3": "Approve plan", "L4": "Take over"}


def badge(text: str, tone: str = "muted") -> str:
    fg, bg = TOKENS.get(tone, TOKENS["muted"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;'
        f'font-weight:600;color:{fg};background:{bg};border:1px solid {fg}33">{text}</span>'
    )


def status_badge(status: str) -> str:
    return badge(status.replace("_", " "), STATUS_TONE.get(status, "muted"))


def level_badge(level: str) -> str:
    return badge(f"{level} · {LEVEL_LABEL.get(level, level)}", "hitl")


def risk_badge(risk: str | None) -> str:
    return badge(risk or "n/a", RISK_TONE.get(risk or "", "muted"))


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; max-width: 1200px; }
        h1, h2, h3 { letter-spacing: -0.01em; }
        .card { border: 1px solid #CBD5E1; border-radius: 10px; padding: 16px; background: #F8FAFC; }
        .card-hitl { border-left: 3px solid #E11D48; background: #FFF1F2; }
        .card-memory { border-left: 3px solid #7C3AED; background: #F5F3FF; }
        code, pre { font-family: "JetBrains Mono", Consolas, monospace; font-size: 12.5px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
