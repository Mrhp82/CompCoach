"""Describe recorded DE progress without requiring a starting tableau.

Byes advance a tableau round but never count as a fenced DE win. A tableau
label is derived only when the event has an explicit starting size; manual
AFM-pairing round labels remain independent of this calculation.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral
from typing import Any


def validate_de_start_tableau(value: Any) -> int | None:
    """Accept an optional power-of-two starting size between 2 and 4096.

    Empty form values mean unconfigured. Integer strings are accepted for
    form/JSON inputs; booleans, floats and partial numbers are rejected.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("Starting DE tableau must be a power of two from 2 to 4096.")
    if isinstance(value, str):
        value = value.strip()
        if not value.isdecimal():
            raise ValueError("Starting DE tableau must be a power of two from 2 to 4096.")
        size = int(value)
    elif isinstance(value, Integral):
        size = int(value)
    else:
        raise ValueError("Starting DE tableau must be a power of two from 2 to 4096.")
    if size < 2 or size > 4096 or size & (size - 1):
        raise ValueError("Starting DE tableau must be a power of two from 2 to 4096.")
    return size


def _recorded_count(value: Any) -> tuple[int, bool]:
    """Read legacy count values safely, while surfacing malformed records."""
    if value is None or value == "":
        return 0, False
    if isinstance(value, bool):
        return 0, True
    if isinstance(value, Integral):
        count = int(value)
    elif isinstance(value, str):
        try:
            count = int(value.strip())
        except ValueError:
            return 0, True
    else:
        return 0, True
    return max(0, count), count < 0


def describe_de_progress(
    athlete: Mapping[str, Any],
    event: Mapping[str, Any] | None = None,
    *,
    initial_tableau: Any = None,
) -> dict[str, Any]:
    """Return counts, an optional derived tableau and the current bout label.

    ``initial_tableau`` overrides the event setting when supplied. Bad legacy
    counts/settings cannot crash the operational board: normalized counts
    remain visible and ``inconsistent`` tells the caller to flag the record.
    Results beyond a configured final never produce T1 or a fictitious bout.
    """
    wins, bad_wins = _recorded_count(athlete.get("de_wins"))
    byes, bad_byes = _recorded_count(athlete.get("de_byes"))
    passed = wins + byes
    configured = initial_tableau
    if configured is None and event is not None:
        configured = event.get("de_start_tableau")
    inconsistent = bad_wins or bad_byes
    try:
        initial = validate_de_start_tableau(configured)
    except (TypeError, ValueError, OverflowError):
        initial = None
        inconsistent = True

    out = (
        athlete.get("active_state") == "eliminated"
        or athlete.get("last_de_result") == "lost"
    )
    completed = False
    current_tableau = None
    if initial is not None:
        final_round_count = initial.bit_length() - 1
        if passed >= final_round_count:
            completed = not out
            inconsistent = inconsistent or passed > final_round_count or out
        else:
            current_tableau = initial >> passed
    round_label = f"T{current_tableau}" if current_tableau is not None else ""
    next_bout = None if out or completed else wins + 1
    called = athlete.get("call_status") in {"now", "on_deck", "in_hole"}
    covered = bool(str(athlete.get("covered_by") or "").strip())
    if out:
        status_label = f"Out at {round_label}" if round_label else "Out"
    elif completed:
        status_label = "Completed"
    else:
        status_label = (
            f"DE bout {next_bout}"
            if called or covered
            else f"Waiting for DE bout {next_bout}"
        )

    parts = []
    if byes:
        parts.append(f"{byes} bye{'s' if byes != 1 else ''}")
    parts.append(f"{wins} DE win{'s' if wins != 1 else ''}")
    parts.append(status_label)
    if round_label and not out:
        parts.append(round_label)
    return {
        "bye_count": byes,
        "de_wins": wins,
        "passed_rounds": passed,
        "next_de_bout_number": next_bout,
        "current_tableau": current_tableau,
        "round_label": round_label,
        "progress_summary": " · ".join(parts),
        "status_label": status_label,
        "completed": completed,
        "out": out,
        "inconsistent": inconsistent,
        "initial_tableau": initial,
    }


de_progress = describe_de_progress
