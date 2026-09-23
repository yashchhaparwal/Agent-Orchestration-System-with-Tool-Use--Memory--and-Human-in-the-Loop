"""Consolidation v1 (Architecture.md §7.3): expire what has stopped being useful.

Importance is never rewritten in place — the *effective* importance is a pure function of the
stored importance and the time since the record was last recalled or reinforced (half-life decay),
so repeated runs cannot compound. A record is expired when its effective importance falls below
``floor`` or it is older than ``max_age_days``. Clustering / merging is v2.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import structlog

from packages.orchestrator.memory.long_term import LongTermMemory, _parse, effective_importance

log = structlog.get_logger(__name__)


@dataclass
class ConsolidationReport:
    scanned: int = 0
    expired: int = 0
    fading: int = 0  # still kept, but below half of their stored importance


def consolidate(
    memory: LongTermMemory,
    *,
    now: dt.datetime | None = None,
    half_life_days: float = 30.0,
    floor: float = 1.0,
    max_age_days: int = 180,
) -> ConsolidationReport:
    moment = now or dt.datetime.now(dt.UTC)
    report = ConsolidationReport()
    doomed: list[str] = []
    for mid, meta in memory.all_metadata():
        report.scanned += 1
        created = _parse(str(meta.get("created_at") or ""), moment)
        accessed = _parse(str(meta.get("last_accessed") or ""), created)
        stored = float(meta.get("importance", 3.0))
        effective = effective_importance(
            stored, accessed, now=moment, half_life_days=half_life_days
        )
        age_days = (moment - created).total_seconds() / 86400
        if age_days > max_age_days or effective < floor:
            doomed.append(mid)
        elif effective < stored / 2:
            report.fading += 1
    memory.delete_ids(doomed)
    report.expired = len(doomed)
    log.info(
        "memory.consolidated",
        scanned=report.scanned,
        expired=report.expired,
        fading=report.fading,
    )
    return report
