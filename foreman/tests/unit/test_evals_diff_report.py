from __future__ import annotations

import json
from pathlib import Path

from packages.evals.diff import compare, render_diff_markdown
from packages.evals.report import (
    load_baseline,
    load_latest,
    render_markdown,
    save_baseline,
    write_report,
)
from packages.shared.types.evals import EvalReport


def report(run_id: str, per_task: dict[str, tuple[int, int]], **metrics: float) -> dict:  # type: ignore[type-arg]
    return {
        "run_id": run_id,
        "per_task": {gid: {"runs": r, "successes": s} for gid, (r, s) in per_task.items()},
        "metrics": metrics,
    }


def test_compare_finds_new_failures_passes_regressions_and_deltas() -> None:
    base = report(
        "b",
        {"a": (3, 3), "b": (3, 0), "c": (3, 2), "d": (3, 1), "gone": (3, 3)},
        success_rate=0.7,
        pass_k=0.4,
    )
    cur = report(
        "c",
        {"a": (3, 2), "b": (3, 3), "c": (3, 1), "d": (3, 2), "new": (3, 3)},
        success_rate=0.8,
        pass_k=0.4,
        judge_mean=4.2,
    )
    d = compare(cur, base)
    assert d["new_failures"] == ["a"] and d["new_passes"] == ["b"]
    assert d["regressed"] == ["c"] and d["improved"] == ["d"]
    assert d["only_in_current"] == ["new"] and d["only_in_baseline"] == ["gone"]
    assert d["metric_deltas"]["success_rate"] == {"baseline": 0.7, "current": 0.8, "delta": 0.1}
    assert d["metric_deltas"]["judge_mean"]["delta"] is None
    assert d["verdict"] == "regression" and d["compared_tasks"] == 4
    md = render_diff_markdown(d)
    assert "**regression**" in md and "| success_rate | 0.7 | 0.8 | 0.1 |" in md
    assert (
        compare(report("x", {"a": (1, 1)}), report("y", {"a": (1, 1)}))["verdict"]
        == "no regression"
    )


def test_report_files_baseline_and_latest(tmp_path: Path) -> None:
    rep = EvalReport(
        run_id="20260828-120000",
        label="smoke",
        started_at="s",
        finished_at="f",
        k=1,
        task_count=2,
        run_count=2,
        metrics={
            "success_rate": 1.0,
            "pass_k": 1.0,
            "unapproved_destructive_actions": 0,
            "injection_resistance": 1.0,
            "latency_p50_s": 42.0,
        },
        per_category={"lookup": {"runs": 2, "success_rate": 1.0}},
        per_difficulty={"easy": {"runs": 2, "success_rate": 1.0}},
        per_task={
            "t1": {
                "category": "lookup",
                "difficulty": "easy",
                "runs": 2,
                "successes": 2,
                "failure_reasons": [],
            }
        },
        notes=["Gemini quota exhausted; reviewer fell back to Groq"],
    )
    md = render_markdown(rep)
    assert (
        "| success_rate | 1.000 ✅ | >= 0.85 |" in md
        and "| unapproved_destructive_actions | 0 ✅ | == 0 |" in md
    )
    assert "| tool_precision | — | >= 0.9 |" in md  # unknown metrics are shown, not hidden
    assert "Gemini quota" in md
    md_path, js_path = write_report(rep, tmp_path)
    assert md_path.exists() and json.loads(js_path.read_text())["run_id"] == rep.run_id
    latest = load_latest(tmp_path)
    assert latest and latest["metrics"]["success_rate"] == 1.0 and latest["run_id"] == rep.run_id
    assert load_baseline(tmp_path) is None
    save_baseline(rep, tmp_path)
    assert load_baseline(tmp_path)["run_id"] == rep.run_id  # type: ignore[index]
