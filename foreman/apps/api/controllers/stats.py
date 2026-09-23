from __future__ import annotations

from typing import Any

from apps.api.services.stats_service import StatsService


def get_stats(service: StatsService, days: int) -> dict[str, Any]:
    return service.stats(days=days)
