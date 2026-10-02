"""Group the shared athlete board by its saved coach assignment.

Pools keep their main/side distinction; DE coaches form an equal group. This
helper does not filter athletes or reorder work within a group, so the caller
can retain its live-call priority and rotating bout queue across events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

try:
    from compcoach_live.storage import assigned_coaches
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from storage import assigned_coaches


def _coach_sort_key(name: str) -> tuple[Any, ...]:
    normalized = name.casefold()
    parts = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", normalized) if part
    )
    return parts, normalized


def group_team_athletes_by_coach(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return ``{label, coaches, athletes}`` groups without modifying rows.

    Main coach owns a Pool row, with side coach as its fallback. All assigned
    DE coaches own one shared group, irrespective of their stored order.
    Normalized names share the first spelling encountered in the input.
    Athlete IDs are unique in storage; if the caller repeats an ID, its first
    row is retained. Rows without an ID remain distinct, even with equal names.
    """
    canonical_names: dict[str, str] = {}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for athlete in rows:
        athlete_id = str(athlete.get("id") or "").strip()
        if athlete_id and athlete_id in seen_ids:
            continue
        if athlete_id:
            seen_ids.add(athlete_id)
        coaches = assigned_coaches(athlete)
        if athlete.get("phase") != "de":
            coaches = coaches[:1]
        normalized_names = []
        for name in coaches:
            normalized = name.casefold()
            canonical_names.setdefault(normalized, name)
            normalized_names.append(normalized)
        membership = tuple(sorted(normalized_names, key=_coach_sort_key))
        group = groups.setdefault(membership, {"label": "", "coaches": [], "athletes": []})
        group["athletes"].append(athlete)

    result = []
    for membership in sorted(groups, key=lambda names: (not bool(names), tuple(_coach_sort_key(name) for name in names))):
        group = groups[membership]
        names = [canonical_names[name] for name in membership]
        group["coaches"] = names
        group["label"] = (
            "Unassigned" if not names else f"Coach {names[0]}" if len(names) == 1
            else "Coaches " + " · ".join(names)
        )
        result.append(group)
    return result
