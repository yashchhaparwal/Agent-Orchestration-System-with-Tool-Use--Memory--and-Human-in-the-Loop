"""The golden set itself: parses, covers every category, and only names tools that exist."""

from __future__ import annotations

from pathlib import Path

import yaml

from packages.evals.loader import load_golden_tasks, select_tasks
from packages.shared.types.evals import GoldenCategory


def policy_tools() -> set[str]:
    raw = yaml.safe_load(Path("packages/tools/registry/policy.yaml").read_text(encoding="utf-8"))
    return set(raw["tools"])


def test_golden_set_is_complete_and_consistent() -> None:
    tasks = load_golden_tasks()
    assert 30 <= len(tasks) <= 50
    per_category = {c: sum(1 for t in tasks if t.category == c) for c in GoldenCategory}
    assert all(n >= 5 for n in per_category.values()), per_category
    assert len({t.id for t in tasks}) == len(tasks)
    known = policy_tools()
    for t in tasks:
        named = set(t.expected_tools) | set(t.forbidden_tools) | set(t.extra_ok_tools)
        assert named <= known, (t.id, named - known)
        if t.category == GoldenCategory.MUST_ESCALATE:
            assert t.expect_pause and t.pause_level in ("L2", "L3", "L4"), t.id
        if t.category == GoldenCategory.INJECTION:
            assert t.injection_marker and "actions_send_email" in t.forbidden_tools, t.id
        if t.category == GoldenCategory.MUST_NOT_CALL:
            assert t.forbidden_tools and not t.expect_pause, t.id
        if t.category in (
            GoldenCategory.LOOKUP,
            GoldenCategory.MULTI_STEP,
            GoldenCategory.DEPENDENT,
        ):
            assert t.rubric, t.id
    assert {t.difficulty.value for t in tasks} == {"easy", "medium", "hard"}


def test_selection_helpers() -> None:
    tasks = load_golden_tasks()
    assert [t.id for t in select_tasks(tasks, only=["lookup_loans_4471"])] == ["lookup_loans_4471"]
    inj = select_tasks(tasks, categories=["injection"])
    assert inj and all(t.category == GoldenCategory.INJECTION for t in inj)
    sampled = select_tasks(tasks, sample_per_category=2)
    assert len(sampled) == 2 * len(GoldenCategory)
