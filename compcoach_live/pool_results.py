"""Pool completion is separate from knowing the win/loss totals."""

from collections.abc import Mapping
from typing import Any


def pool_results_are_unknown(athlete: Mapping[str, Any]) -> bool:
    """An explicit completion override must never imply a 0/0 score."""
    return athlete.get("pool_results_unknown", 0) in (1, True, "1")


def pool_has_results(athlete: Mapping[str, Any]) -> bool:
    return (
        not pool_results_are_unknown(athlete)
        and athlete.get("pool_wins") is not None
        and athlete.get("pool_losses") is not None
    )


def pool_is_complete(athlete: Mapping[str, Any]) -> bool:
    return pool_results_are_unknown(athlete) or pool_has_results(athlete)
