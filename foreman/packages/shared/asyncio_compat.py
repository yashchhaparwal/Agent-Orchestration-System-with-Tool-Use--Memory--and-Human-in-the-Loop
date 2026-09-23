"""Event-loop compatibility shims."""

from __future__ import annotations

import asyncio
import sys


def use_selector_event_loop_on_windows() -> None:
    """psycopg's async connections (used by LangGraph's Postgres checkpointer) do not work on
    Windows' default Proactor event loop. Call once, before ``asyncio.run``."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
