from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.evals.report import load_latest
from packages.orchestrator.memory.persistent import TaskStore


class StatsService:
    """Operational aggregates from Postgres plus the last eval report's headline."""

    def __init__(self, store: TaskStore, reports_dir: Path) -> None:
        self._store = store
        self._reports = reports_dir

    def stats(self, *, days: int = 7) -> dict[str, Any]:
        out = self._store.stats(days=max(1, min(days, 90)))
        out["last_eval"] = load_latest(self._reports)
        return out
