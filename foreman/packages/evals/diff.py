"""Compare an eval report against a baseline: which tasks newly fail or pass, how metrics moved."""

from __future__ import annotations

from typing import Any

WATCHED = (
    "success_rate",
    "pass_k",
    "tool_precision",
    "tool_recall",
    "unnecessary_call_rate",
    "escalation_precision",
    "escalation_recall",
    "unapproved_destructive_actions",
    "injection_resistance",
    "judge_mean",
    "mean_steps",
    "mean_llm_calls",
    "latency_p50_s",
    "latency_p95_s",
    "mean_cost_usd",
)


def _rate(entry: dict[str, Any] | None) -> float | None:
    if not entry or not entry.get("runs"):
        return None
    return float(entry["successes"]) / float(entry["runs"])


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    cur_tasks: dict[str, dict[str, Any]] = current.get("per_task", {})
    base_tasks: dict[str, dict[str, Any]] = baseline.get("per_task", {})
    new_failures, new_passes, regressed, improved = [], [], [], []
    for gid in sorted(set(cur_tasks) & set(base_tasks)):
        c, b = _rate(cur_tasks[gid]), _rate(base_tasks[gid])
        if c is None or b is None:
            continue
        if b == 1.0 and c < 1.0:
            new_failures.append(gid)
        elif b < 1.0 and c == 1.0:
            new_passes.append(gid)
        elif c < b:
            regressed.append(gid)
        elif c > b:
            improved.append(gid)
    cur_m, base_m = current.get("metrics", {}), baseline.get("metrics", {})
    deltas: dict[str, dict[str, Any]] = {}
    for m in WATCHED:
        cv, bv = cur_m.get(m), base_m.get(m)
        if cv is None and bv is None:
            continue
        deltas[m] = {
            "baseline": bv,
            "current": cv,
            "delta": round(cv - bv, 4) if cv is not None and bv is not None else None,
        }
    return {
        "baseline_id": baseline.get("run_id"),
        "compared_tasks": len(set(cur_tasks) & set(base_tasks)),
        "only_in_current": sorted(set(cur_tasks) - set(base_tasks)),
        "only_in_baseline": sorted(set(base_tasks) - set(cur_tasks)),
        "new_failures": new_failures,
        "new_passes": new_passes,
        "regressed": regressed,
        "improved": improved,
        "metric_deltas": deltas,
        "verdict": "regression" if new_failures or regressed else "no regression",
    }


def render_diff_markdown(diff: dict[str, Any]) -> str:
    lines = [
        f"### Diff vs baseline `{diff.get('baseline_id')}` — **{diff.get('verdict')}**",
        "",
        f"- compared tasks: {diff.get('compared_tasks')}; "
        f"new failures: {', '.join(diff.get('new_failures') or []) or 'none'}; "
        f"new passes: {', '.join(diff.get('new_passes') or []) or 'none'}",
        f"- regressed: {', '.join(diff.get('regressed') or []) or 'none'}; "
        f"improved: {', '.join(diff.get('improved') or []) or 'none'}",
    ]
    if diff.get("only_in_current") or diff.get("only_in_baseline"):
        lines.append(
            f"- only in current: {', '.join(diff['only_in_current']) or 'none'}; "
            f"only in baseline: {', '.join(diff['only_in_baseline']) or 'none'}"
        )
    lines += ["", "| metric | baseline | current | delta |", "|---|---|---|---|"]
    for m, d in diff.get("metric_deltas", {}).items():
        lines.append(
            f"| {m} | {d['baseline']} | {d['current']} | {d['delta'] if d['delta'] is not None else '—'} |"
        )
    return "\n".join(lines)
