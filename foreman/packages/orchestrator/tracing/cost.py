"""Cost from token usage and the list-price table in config/models.yaml."""

from __future__ import annotations

from packages.shared.types.llm import Usage


def compute_cost(model: str, usage: Usage, prices: dict[str, dict[str, float]]) -> float | None:
    """USD for one call, or ``None`` if the model has no price on file (never a silent zero)."""
    price = prices.get(model)
    if not price or "input" not in price or "output" not in price:
        return None
    return round(
        usage.input_tokens * price["input"] / 1_000_000
        + usage.output_tokens * price["output"] / 1_000_000,
        6,
    )
