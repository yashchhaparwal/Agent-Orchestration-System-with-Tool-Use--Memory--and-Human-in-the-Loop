"""Load the golden set: every ``*.yaml`` under ``golden_tasks/`` holds a list of tasks (or one)."""

from __future__ import annotations

from pathlib import Path

import yaml

from packages.shared.types.evals import GoldenCategory, GoldenTask

GOLDEN_DIR = Path(__file__).parent / "golden_tasks"


def load_golden_tasks(directory: Path | None = None) -> list[GoldenTask]:
    root = directory or GOLDEN_DIR
    tasks: list[GoldenTask] = []
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            tasks.append(GoldenTask.model_validate(item))
    ids = [t.id for t in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate golden task ids: {duplicates}")
    return tasks


def select_tasks(
    tasks: list[GoldenTask],
    *,
    only: list[str] | None = None,
    categories: list[str] | None = None,
    sample_per_category: int | None = None,
) -> list[GoldenTask]:
    chosen = list(tasks)
    if only:
        wanted = set(only)
        chosen = [t for t in chosen if t.id in wanted]
    if categories:
        cats = {GoldenCategory(c) for c in categories}
        chosen = [t for t in chosen if t.category in cats]
    if sample_per_category:
        seen: dict[GoldenCategory, int] = {}
        sampled = []
        for t in chosen:  # deterministic: file order
            if seen.get(t.category, 0) < sample_per_category:
                sampled.append(t)
                seen[t.category] = seen.get(t.category, 0) + 1
        chosen = sampled
    return chosen
