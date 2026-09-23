"""Markdown + JSON eval reports, the baseline, and the ``latest.json`` pointer the stats API reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.evals.diff import render_diff_markdown
from packages.evals.metrics import TARGETS, meets_target
from packages.shared.types.evals import EvalReport

BASELINE = "baseline.json"
LATEST = "latest.json"


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}" if value < 10 else f"{value:.1f}"
    return str(value)


def _mark(metric: str, value: Any) -> str:
    ok = meets_target(metric, value if isinstance(value, int | float) else None)
    return "" if ok is None else (" ✅" if ok else " ❌")


def render_markdown(report: EvalReport) -> str:
    m = report.metrics
    lines = [
        f"# Eval report `{report.run_id}`" + (f" — {report.label}" if report.label else ""),
        "",
        f"- started {report.started_at} · finished {report.finished_at} · k = {report.k} · "
        f"{report.task_count} tasks · {report.run_count} runs",
        f"- reviewer: `{report.reviewer or 'config default'}` · judge: `{report.judge or 'reviewer role'}`",
        "",
        "## Headline (PRD.md §7 targets)",
        "",
        "| metric | value | target |",
        "|---|---|---|",
    ]
    for metric, (op, target) in TARGETS.items():
        lines.append(
            f"| {metric} | {_fmt(m.get(metric))}{_mark(metric, m.get(metric))} | {op} {target} |"
        )
    lines += [
        "",
        "## Cost, latency, effort",
        "",
        f"- mean steps {_fmt(m.get('mean_steps'))} · mean LLM calls {_fmt(m.get('mean_llm_calls'))} · "
        f"mean tokens {_fmt(m.get('mean_tokens'))} · mean retries {_fmt(m.get('mean_retries'))}",
        f"- latency p50 {_fmt(m.get('latency_p50_s'))} s · p95 {_fmt(m.get('latency_p95_s'))} s · "
        f"mean cost ${_fmt(m.get('mean_cost_usd'))} (free tiers bill $0)",
        f"- judge mean {_fmt(m.get('judge_mean'))} / 5 · fallback rate {_fmt(m.get('fallback_rate'))} · "
        f"provider mix {json.dumps(m.get('provider_mix', {}))}",
        f"- unnecessary-call rate {_fmt(m.get('unnecessary_call_rate'))}",
        "",
        "## Per category",
        "",
        "| category | runs | success | pass^k | tool P / R | esc P / R | judge | p50 s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cat, c in report.per_category.items():
        lines.append(
            f"| {cat} | {c.get('runs')} | {_fmt(c.get('success_rate'))} | {_fmt(c.get('pass_k'))} | "
            f"{_fmt(c.get('tool_precision'))} / {_fmt(c.get('tool_recall'))} | "
            f"{_fmt(c.get('escalation_precision'))} / {_fmt(c.get('escalation_recall'))} | "
            f"{_fmt(c.get('judge_mean'))} | {_fmt(c.get('latency_p50_s'))} |"
        )
    lines += [
        "",
        "## Per difficulty",
        "",
        "| difficulty | runs | success | pass^k | judge |",
        "|---|---|---|---|---|",
    ]
    for d, c in report.per_difficulty.items():
        lines.append(
            f"| {d} | {c.get('runs')} | {_fmt(c.get('success_rate'))} | "
            f"{_fmt(c.get('pass_k'))} | {_fmt(c.get('judge_mean'))} |"
        )
    lines += [
        "",
        "## Per task",
        "",
        "| task | category | runs | ok | judge | LLM calls | s | failure reasons |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for gid, t in report.per_task.items():
        lines.append(
            f"| {gid} | {t['category']} | {t['runs']} | {t['successes']} | {_fmt(t.get('judge_mean'))} | "
            f"{_fmt(t.get('mean_llm_calls'))} | {_fmt(t.get('mean_elapsed_s'))} | "
            f"{'; '.join(t.get('failure_reasons') or [])[:160]} |"
        )
    if report.diff:
        lines += ["", render_diff_markdown(report.diff)]
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = reports_dir / f"{report.run_id}.md"
    js = reports_dir / f"{report.run_id}.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (reports_dir / LATEST).write_text(
        json.dumps(summary_for_stats(report), indent=2), encoding="utf-8"
    )
    return md, js


def summary_for_stats(report: EvalReport) -> dict[str, Any]:
    keep = (
        "success_rate",
        "pass_k",
        "tool_precision",
        "tool_recall",
        "escalation_precision",
        "escalation_recall",
        "unapproved_destructive_actions",
        "injection_resistance",
        "judge_mean",
        "mean_llm_calls",
        "latency_p50_s",
        "latency_p95_s",
        "mean_cost_usd",
    )
    return {
        "run_id": report.run_id,
        "label": report.label,
        "finished_at": report.finished_at,
        "k": report.k,
        "task_count": report.task_count,
        "run_count": report.run_count,
        "metrics": {k: report.metrics.get(k) for k in keep},
        "diff_verdict": (report.diff or {}).get("verdict"),
        "baseline_id": report.baseline_id,
    }


def save_baseline(report: EvalReport, reports_dir: Path) -> Path:
    path = reports_dir / BASELINE
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_baseline(reports_dir: Path) -> dict[str, Any] | None:
    path = reports_dir / BASELINE
    if not path.exists():
        return None
    return dict(json.loads(path.read_text(encoding="utf-8")))


def load_latest(reports_dir: Path) -> dict[str, Any] | None:
    path = reports_dir / LATEST
    if not path.exists():
        return None
    return dict(json.loads(path.read_text(encoding="utf-8")))
