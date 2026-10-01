"""The persistent direct-elimination work queue.

A result rotates an athlete behind the other active athletes; it does not
require a readiness transition or wait for everybody to finish the same round.
Existing outcome timestamps provide the order across refreshes and phones.
Only the UI selects which operational athletes belong to a particular queue.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


_FIRST = datetime.min.replace(tzinfo=timezone.utc)


def _nonnegative_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def de_rounds_passed(athlete: Mapping[str, Any]) -> int:
    """Count wins and byes, without treating a bye as a fenced victory."""
    return _nonnegative_count(athlete.get("de_wins")) + _nonnegative_count(
        athlete.get("de_byes")
    )


def de_queue_sort_key(athlete: Mapping[str, Any]) -> tuple[int, datetime]:
    """Never processed first; thereafter the oldest result comes first.

    In particular, a live call, a new assignment, or the old awaiting-next
    compatibility flag must not reorder this queue. A fresh win or bye already
    stores a new timestamp; correcting a result restores its previous one.
    Ties retain the input order through Python's stable sort. This also keeps
    older, second-precision historical records deterministic.
    """
    raw = str(athlete.get("last_de_result_at") or "").strip()
    rotated = bool(raw) or de_rounds_passed(athlete) > 0 or athlete.get(
        "last_de_result"
    ) in {"won", "bye"}
    if not rotated:
        return 0, _FIRST
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        # A malformed old date must not hide an otherwise valid athlete or
        # turn an existing advancement into an unprocessed first-round row.
        stamp = _FIRST
    return 1, stamp


def ordered_de_athletes(
    athletes: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return the wheel order without filtering, mutating, or freezing rows."""
    return sorted(athletes, key=de_queue_sort_key)


def de_round_label(athlete: Mapping[str, Any]) -> str:
    """A small divider label describes progress without inventing a bracket."""
    passed = de_rounds_passed(athlete)
    if not passed:
        return "First DE bout"
    unit = "round" if passed == 1 else "rounds"
    return f"Next bout · {passed} {unit} passed"
