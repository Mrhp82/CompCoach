"""Coach names shared by setup pickers, including names typed inline."""

from collections.abc import Iterable
from typing import Any


def canonical_coach_names(
    values: Iterable[Any], existing_names: Iterable[Any] = (),
) -> list[str]:
    """Keep the directory's spelling and one entry per normalized name."""
    canonical = {}
    for value in existing_names:
        name = " ".join(str(value or "").split())
        if name:
            canonical.setdefault(name.casefold(), name)
    names, seen = [], set()
    for value in values:
        name = " ".join(str(value or "").split())
        key = name.casefold()
        if key and key not in seen:
            names.append(canonical.get(key, name))
            seen.add(key)
    return names


def setup_coach_options(directory: list[dict], fallbacks: Iterable[str]) -> list[str]:
    """Offer active directory coaches and the usual first-install names."""
    names = [row["name"] for row in directory]
    inactive = {
        " ".join(str(row["name"]).split()).casefold()
        for row in directory if not row.get("is_active", True)
    }
    return canonical_coach_names(
        [
            *(name for name in fallbacks if " ".join(name.split()).casefold() not in inactive),
            *(row["name"] for row in directory if row.get("is_active", True)),
        ],
        names,
    )
