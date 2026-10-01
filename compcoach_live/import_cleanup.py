"""Reversibly hide page debris imported by older CompCoach versions.

The import parser now rejects recognisable website text.  Existing database
rows still need the same check: replacing application files does not re-import
or delete tournament data.  Cleanup uses the regular attendance operation so
it retains identity, assignments, results and an audit trail.
"""

from __future__ import annotations

from typing import Any

try:
    from compcoach_live.parsers import import_name_noise_reason
    from compcoach_live.storage import ConcurrentUpdateError, EventLockedError
except ModuleNotFoundError:
    from parsers import import_name_noise_reason
    from storage import ConcurrentUpdateError, EventLockedError


def quarantine_import_noise(
    db: Any, event_id: str, *, actor: str = "Import cleanup"
) -> int:
    """Withdraw positively recognised page text, preserving historical rows.

    Only active attendance rows are changed.  A strip/sector code is never
    sufficient evidence: an actual athlete assigned to sector ``VE`` remains
    valid.  Locked or concurrently changed rows are left untouched, and
    unexpected database errors propagate to the caller.

    The return value is the number of rows quarantined.  Repeating this
    operation is harmless and does not create duplicate attendance actions.
    """

    event = db.get_event(event_id)
    if not event or event.get("status") != "open":
        return 0

    quarantined = 0
    for athlete in db.list_athletes(event_id):
        if (
            athlete.get("participation_status", "active") != "active"
            or not import_name_noise_reason(athlete.get("name", ""))
        ):
            continue
        try:
            db.set_athlete_participation(
                event_id,
                athlete["id"],
                "withdrawn",
                actor,
                expected_version=int(athlete["version"]),
            )
        except EventLockedError:
            # A coordinator may archive the meet between the read and write.
            break
        except ConcurrentUpdateError:
            # Do not overwrite a coach's intervening edit from another phone.
            continue
        quarantined += 1
    return quarantined


__all__ = ["quarantine_import_noise"]
