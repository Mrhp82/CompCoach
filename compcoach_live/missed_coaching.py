"""Factual coach-status snapshots for an uncovered-bout report.

These helpers describe what the app had recorded at reporting time. Absence
of an availability declaration never means that a coach was free, and a
planned athlete or pod assignment never means that a coach was at a strip.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _clean_name(value: Any) -> str:
    return " ".join(str(value or "").split())


def _name_key(value: Any) -> str:
    return _clean_name(value).casefold()


def _declared_available(value: Any) -> bool:
    """Handle persisted boolean values without treating the string '0' as true."""

    return value is True or value == 1 or (
        isinstance(value, str) and value.strip().casefold() in {"1", "true"}
    )


def _athlete_snapshot(
    athlete: Mapping[str, Any], event_names: Mapping[str, str]
) -> dict[str, Any]:
    event_id = str(athlete.get("event_id") or "")
    return {
        "athlete_id": str(athlete.get("id") or ""),
        "athlete_name": str(athlete.get("name") or ""),
        "athlete_version": int(athlete.get("version") or 0),
        "event_id": event_id,
        "event_name": str(event_names.get(event_id) or ""),
        "phase": str(athlete.get("phase") or ""),
        # The DE source strip describes a pod, not an actual bout location.
        "actual_strip": str(athlete.get("live_location") or "").strip(),
        "source_pod": str(athlete.get("pod") or "").strip(),
        "source_strip": str(athlete.get("source_strip") or "").strip(),
        "call_status": str(athlete.get("call_status") or "waiting"),
        "covered_since": athlete.get("covered_at"),
        "reserved_since": athlete.get("takeover_at"),
    }


def build_coach_status_snapshot(
    coach_names: Iterable[str],
    athletes: Iterable[Mapping[str, Any]],
    availability: Iterable[Mapping[str, Any]],
    event_names: Mapping[str, str],
) -> dict[str, Any]:
    """Build a JSON-ready snapshot from rows already read by the caller.

    Only active roster coaches are included, once per normalized name. Active
    physical coverage and reserved takeovers override a stale availability
    flag. A coach with both kinds of record is classified as busy while both
    athlete lists remain visible. Counts therefore partition the roster.
    """

    canonical_names: dict[str, str] = {}
    for name in coach_names:
        clean = _clean_name(name)
        if clean:
            canonical_names.setdefault(clean.casefold(), clean)

    stored = {
        _name_key(row.get("coach_name")): row
        for row in availability
        if _name_key(row.get("coach_name")) in canonical_names
    }
    busy: dict[str, list[dict[str, Any]]] = {key: [] for key in canonical_names}
    reserved: dict[str, list[dict[str, Any]]] = {key: [] for key in canonical_names}
    for athlete in athletes:
        if (
            athlete.get("active_state", "active") != "active"
            or athlete.get("participation_status", "active") != "active"
        ):
            continue
        covering_key = _name_key(athlete.get("covered_by"))
        takeover_key = _name_key(athlete.get("takeover_coach"))
        if covering_key in busy:
            busy[covering_key].append(_athlete_snapshot(athlete, event_names))
        if takeover_key in reserved:
            reserved[takeover_key].append(_athlete_snapshot(athlete, event_names))

    coaches: list[dict[str, Any]] = []
    counts = {"busy": 0, "reserved": 0, "available": 0, "unconfirmed": 0}
    for key, coach_name in canonical_names.items():
        row = stored.get(key, {})
        sort_key = lambda athlete: (
            athlete["event_name"].casefold(),
            athlete["athlete_name"].casefold(),
            athlete["athlete_id"],
        )
        busy_with = sorted(busy[key], key=sort_key)
        reserved_for = sorted(reserved[key], key=sort_key)
        declared = _declared_available(row.get("is_available"))
        is_declared_available = declared and not busy_with and not reserved_for
        if busy_with:
            status = "busy"
        elif reserved_for:
            status = "reserved"
        elif is_declared_available:
            status = "available"
        else:
            status = "unconfirmed"
        counts[status] += 1
        coaches.append(
            {
                "coach_name": coach_name,
                "status": status,
                "busy_with": busy_with,
                "reserved_for": reserved_for,
                "is_declared_available": is_declared_available,
                "stored_is_available": declared,
                "available_since": row.get("available_since"),
                "availability_version": int(row.get("version") or 0),
                "availability_updated_at": row.get("updated_at"),
                "availability_updated_by": str(row.get("updated_by") or ""),
            }
        )

    total = len(coaches)
    labels = [
        ("busy", "covering athletes"),
        ("reserved", "reserved"),
        ("available", "declared available"),
        ("unconfirmed", "availability unconfirmed"),
    ]
    return {
        "coaches": coaches,
        "total_coaches": total,
        **{f"{status}_count": count for status, count in counts.items()},
        "all_committed": bool(total and counts["busy"] + counts["reserved"] == total),
        "summary_label": " · ".join(
            f"{counts[status]} {label}" for status, label in labels if counts[status]
        ) or "No active coaches recorded",
        "status_note": "Status at reporting time",
    }
