"""Phase 7: per-agent budgets from config, and the eval gate's failure rules."""

from __future__ import annotations

from pathlib import Path

from packages.evals.runner import gate_failures
from packages.orchestrator.graph.deps import GraphConfig
from packages.orchestrator.loop.budgets import Budget, BudgetConfig
from packages.shared.types.evals import EvalReport


def test_repo_budgets_load_with_inheritance_and_per_agent_overrides() -> None:
    cfg = BudgetConfig.load(Path("config/budgets.yaml"))
    assert cfg.default.max_iterations == 15 and cfg.default.max_wall_seconds == 420
    assert cfg.for_agent("code_exec").max_iterations == 8
    assert cfg.for_agent("writing").max_cost_usd == 1.0  # inherited from default
    assert cfg.for_agent("nobody") == cfg.default
    assert cfg.task_max_cost_usd == 1.0
    assert {"research", "analysis", "writing", "code_exec"} <= set(cfg.agents)


def test_budget_config_from_partial_yaml(tmp_path: Path) -> None:
    path = tmp_path / "b.yaml"
    path.write_text(
        "default: {max_iterations: 3}\nagents:\n  writing: {max_wall_seconds: 10}\n",
        encoding="utf-8",
    )
    cfg = BudgetConfig.load(path)
    assert cfg.default == Budget(max_iterations=3)
    assert cfg.for_agent("writing") == Budget(max_iterations=3, max_wall_seconds=10.0)
    assert cfg.task_max_cost_usd is None


def test_graph_config_picks_the_agent_budget_or_the_default() -> None:
    cfg = GraphConfig(specialist_budget=Budget(max_iterations=7))
    assert cfg.budget_for("writing").max_iterations == 7
    cfg = GraphConfig(
        specialist_budget=Budget(max_iterations=7),
        budgets=BudgetConfig(
            default=Budget(max_iterations=5), agents={"writing": Budget(max_iterations=2)}
        ),
    )
    assert cfg.budget_for("writing").max_iterations == 2
    assert (
        cfg.budget_for("research").max_iterations == 7
    )  # not listed → the runtime default, not the yaml default


def report(**metrics: float) -> EvalReport:
    return EvalReport(
        run_id="r", started_at="s", finished_at="f", k=1, task_count=1, run_count=1, metrics=metrics
    )


def test_gate_failures_cover_safety_invariants_and_regressions() -> None:
    assert (
        gate_failures(
            report(
                unapproved_destructive_actions=0, injection_resistance=1.0, escalation_recall=1.0
            )
        )
        == []
    )
    assert gate_failures(report(success_rate=0.1)) == []  # success is reported, not gated
    f = gate_failures(
        report(unapproved_destructive_actions=1, injection_resistance=0.8, escalation_recall=0.5)
    )
    assert len(f) == 3 and f[0].startswith("unapproved destructive actions: 1")
    r = report(unapproved_destructive_actions=0)
    r.diff = {"verdict": "regression", "new_failures": ["lookup_x"], "regressed": []}
    assert gate_failures(r) == ["regression vs baseline: new failures lookup_x; regressed "]
    r.diff = {"verdict": "no regression"}
    assert gate_failures(r) == []
