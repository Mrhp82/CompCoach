"""A single, restricted parent link follows a competition across its days.

Parents can publish a call and its actual strip. This capability never grants
coach access and never changes coverage, results, assignments or availability.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

try:
    from .pool_results import pool_is_complete
    from .storage import CompCoachError, ConcurrentUpdateError, EventLockedError, utc_now
except ImportError:  # ``streamlit run app.py`` from the application directory.
    from pool_results import pool_is_complete
    from storage import CompCoachError, ConcurrentUpdateError, EventLockedError, utc_now


PARENT_ACCESS_DDL = """
CREATE TABLE IF NOT EXISTS parent_access_links (
    competition_id TEXT PRIMARY KEY REFERENCES competitions(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
)
"""

PARENT_VISIBILITY_DDL = """
CREATE TABLE IF NOT EXISTS parent_visibility_settings (
    competition_id TEXT PRIMARY KEY REFERENCES competitions(id) ON DELETE CASCADE,
    show_coach_status INTEGER NOT NULL DEFAULT 0 CHECK (show_coach_status IN (0, 1)),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
)
"""

_CALLS = {"now", "on_deck", "in_hole"}
_PUBLIC_ATHLETE_FIELDS = (
    "id", "event_id", "name", "phase", "source_strip", "live_location",
    "call_status", "reported_at", "reported_by", "version",
)


def ensure_parent_schema(db: Any) -> None:
    """Add the portable table once per database handle, without touching staff."""
    if (getattr(db, "_parent_access_schema_ready", False)
            and getattr(db, "_parent_access_schema_version", 0) >= 2):
        return
    with db._connection() as conn:
        conn.execute(PARENT_ACCESS_DDL)
        conn.execute(PARENT_VISIBILITY_DDL)
        conn.commit()
    db._parent_access_schema_ready = True
    db._parent_access_schema_version = 2


def get_parent_coach_visibility(db: Any, competition_id: str) -> bool:
    """Read the Admin's competition-wide choice; existing competitions default off."""
    ensure_parent_schema(db)
    with db._connection() as conn:
        row = conn.execute(
            "SELECT show_coach_status FROM parent_visibility_settings WHERE competition_id = ?",
            (competition_id,),
        ).fetchone()
    return bool(row and row["show_coach_status"])


def set_parent_coach_visibility(
    db: Any, competition_id: str, enabled: bool, actor: str,
) -> bool:
    """Save the choice from the Admin-only Share controls, without rotating links."""
    if not isinstance(enabled, bool):
        raise TypeError("Coach visibility must be true or false.")
    clean_actor = str(actor or "").strip()
    if not clean_actor:
        raise ValueError("Actor is required.")
    ensure_parent_schema(db)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM competitions WHERE id = ?", (competition_id,)).fetchone():
            raise CompCoachError("Competition not found.")
        conn.execute(
            "INSERT INTO parent_visibility_settings "
            "(competition_id, show_coach_status, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(competition_id) DO UPDATE SET "
            "show_coach_status = excluded.show_coach_status, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (competition_id, int(enabled), utc_now(), clean_actor),
        )
        conn.commit()
    return enabled


def get_or_create_parent_access(db: Any, competition_id: str, actor: str) -> dict[str, Any]:
    """Return the competition's common capability; caller must be an admin.

    Reopening this share panel or moving to tomorrow never creates a new URL.
    The application's staff route controls who may call this internal helper.
    """
    clean_actor = str(actor or "").strip()
    if not clean_actor:
        raise ValueError("Actor is required.")
    ensure_parent_schema(db)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM competitions WHERE id = ?", (competition_id,)).fetchone():
            raise CompCoachError("Competition not found.")
        conn.execute(
            "INSERT OR IGNORE INTO parent_access_links "
            "(competition_id, token, created_at, created_by) VALUES (?, ?, ?, ?)",
            (competition_id, secrets.token_urlsafe(32), utc_now(), clean_actor),
        )
        row = conn.execute("SELECT * FROM parent_access_links WHERE competition_id = ?", (competition_id,)).fetchone()
        conn.commit()
    return dict(row)


def _authorize(conn: Any, competition_id: str, token: str) -> bool:
    if not isinstance(token, str) or not token or len(token) > 256 or not token.isascii():
        return False
    row = conn.execute("SELECT token FROM parent_access_links WHERE competition_id = ?", (competition_id,)).fetchone()
    return bool(row and hmac.compare_digest(str(row["token"]), token))


def _day(conn: Any, competition_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, competition_id, name, competition_date, timezone, status, "
        "day_status, ended_at, updated_at FROM meets "
        "WHERE competition_id = ? AND day_status = 'active' "
        "AND status = 'open' AND ended_at IS NULL LIMIT 1", (competition_id,),
    ).fetchone()
    return dict(row) if row else None


def _eligible(athlete: dict[str, Any]) -> bool:
    return (
        athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
        and not (athlete.get("phase") == "pools" and pool_is_complete(athlete))
    )


def _public_coaches(conn: Any, day_id: str) -> list[dict[str, Any]]:
    """Project the current day's persisted state onto four safe public fields.

    A planned pod or an athlete's imported strip does not locate a coach.
    Physical coverage declarations in the audit log retain the last known
    strip after a bout finishes. Parent calls cannot declare a coach's strip.
    """
    day = conn.execute("SELECT active_coaches_json FROM meets WHERE id = ?", (day_id,)).fetchone()
    coaches = [str(name).strip() for name in json.loads(day["active_coaches_json"] or "[]") if str(name).strip()]
    if not coaches:
        return []
    stored = {
        str(row["coach_name"]): dict(row)
        for row in conn.execute(
            "SELECT coach_name, is_available, updated_at FROM coach_availability WHERE meet_id = ?",
            (day_id,),
        ).fetchall()
    }
    duties = [dict(row) for row in conn.execute(
        "SELECT athletes.covered_by, athletes.covered_at, athletes.takeover_coach, athletes.takeover_at "
        "FROM athletes JOIN meet_events ON meet_events.event_id = athletes.event_id "
        "WHERE meet_events.meet_id = ? AND athletes.active_state = 'active' "
        "AND athletes.participation_status = 'active'",
        (day_id,),
    ).fetchall()]
    declarations: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT actions.new_json, actions.created_at FROM actions "
        "JOIN meet_events ON meet_events.event_id = actions.event_id "
        "WHERE meet_events.meet_id = ? AND actions.undone_at IS NULL "
        "AND actions.action IN ('coverage_start', 'claim', 'live_update') "
        "ORDER BY actions.id DESC",
        (day_id,),
    ).fetchall():
        try:
            declaration = json.loads(row["new_json"] or "{}")
        except (TypeError, ValueError):
            continue
        name = str(declaration.get("covered_by") or "")
        strip = str(declaration.get("live_location") or "").strip()
        if name in coaches and name not in declarations and strip:
            declarations[name] = (strip, str(row["created_at"]))
        if len(declarations) == len(coaches):
            break
    result = []
    for name in coaches:
        row = stored.get(name)
        covering = [duty for duty in duties if duty["covered_by"] == name]
        committed = [duty for duty in duties if duty["takeover_coach"] == name]
        if covering:
            availability = "busy"
        elif committed:
            availability = "committed"
        elif row:
            availability = "available" if row["is_available"] else "unavailable"
        else:
            availability = "not_reported"
        strip, strip_at = declarations.get(name, ("", ""))
        updates = [
            str(timestamp) for timestamp in [
                row["updated_at"] if row else None, strip_at,
                *(duty["covered_at"] for duty in covering),
                *(duty["takeover_at"] for duty in committed),
            ] if timestamp
        ]
        result.append({
            "coach_name": name, "availability": availability,
            "last_declared_strip": strip, "updated_at": max(updates) if updates else None,
        })
    return result


def get_parent_context(db: Any, competition_id: str, token: str) -> dict[str, Any]:
    """Return only the open current-day roster, without any staff access tokens."""
    ensure_parent_schema(db)
    context: dict[str, Any] = {
        "authorized": False, "competition": None, "day": None,
        "events": [], "athletes": [], "reason": "invalid_link",
        "show_coach_status": False, "coaches": [],
    }
    with db._connection() as conn:
        if not _authorize(conn, competition_id, token):
            return context
        row = conn.execute(
            "SELECT id, name, status, timezone, start_date, end_date, updated_at "
            "FROM competitions WHERE id = ?", (competition_id,),
        ).fetchone()
        if not row:
            return context
        context.update(authorized=True, competition=dict(row))
        if row["status"] != "open":
            context["reason"] = "competition_closed"
            return context
        day = _day(conn, competition_id)
        context["day"] = day
        if not day:
            context["reason"] = "no_active_day"
            return context
        visibility = conn.execute(
            "SELECT show_coach_status FROM parent_visibility_settings WHERE competition_id = ?",
            (competition_id,),
        ).fetchone()
        if visibility and visibility["show_coach_status"]:
            context["show_coach_status"] = True
            context["coaches"] = _public_coaches(conn, str(day["id"]))
        events = conn.execute(
            "SELECT events.id, events.name, events.status, meet_events.sort_order "
            "FROM events JOIN meet_events ON meet_events.event_id = events.id "
            "WHERE meet_events.meet_id = ? AND events.status = 'open' "
            "ORDER BY meet_events.sort_order", (day["id"],),
        ).fetchall()
        context["events"] = [dict(event) for event in events]
        event_names = {event["id"]: event["name"] for event in events}
        closed_pools = {
            row["event_id"] for row in conn.execute(
                "SELECT phase_states.event_id FROM phase_states "
                "JOIN meet_events ON meet_events.event_id = phase_states.event_id "
                "WHERE meet_events.meet_id = ? AND phase_states.phase = 'pools' "
                "AND phase_states.closed_at IS NOT NULL", (day["id"],),
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT athletes.* FROM athletes "
            "JOIN meet_events ON meet_events.event_id = athletes.event_id "
            "JOIN events ON events.id = athletes.event_id "
            "WHERE meet_events.meet_id = ? AND events.status = 'open' "
            "ORDER BY athletes.name COLLATE NOCASE, athletes.id", (day["id"],),
        ).fetchall()
        for raw in rows:
            athlete = dict(raw)
            if _eligible(athlete) and not (
                athlete["phase"] == "pools" and athlete["event_id"] in closed_pools
            ):
                public = {key: athlete.get(key) for key in _PUBLIC_ATHLETE_FIELDS}
                public["event_name"] = event_names[athlete["event_id"]]
                context["athletes"].append(public)
        context["reason"] = "ready" if context["athletes"] else "no_active_athletes"
    return context


def parent_context_revision(
    context: dict[str, Any], selected_athlete_id: str | None = None,
) -> str:
    """Refresh only the roster and the parent's selected athlete when supplied.

    Other families' call changes must not redraw an unrelated strip editor.
    An empty selection observes roster changes only. The optional default
    retains the complete revision for domain consumers and diagnostics.
    """
    visible = context
    if selected_athlete_id is not None:
        athletes = context.get("athletes") or []
        visible = {
            "authorized": context.get("authorized"),
            "competition": context.get("competition"),
            # Generic day updated_at also changes for private coach activity.
            # Observe its public identity/state plus the optional projection.
            "day": {key: value for key, value in context["day"].items() if key != "updated_at"}
                if context.get("day") else None,
            "events": context.get("events"),
            "reason": context.get("reason"),
            "show_coach_status": context.get("show_coach_status", False),
            "coaches": context.get("coaches", []),
            "roster": [
                {key: athlete.get(key) for key in ("id", "name", "event_id", "event_name")}
                for athlete in athletes
            ],
            "selected_athlete": next(
                (athlete for athlete in athletes if athlete["id"] == selected_athlete_id), None,
            ),
        }
    return hashlib.sha256(json.dumps(visible, sort_keys=True, default=str).encode()).hexdigest()


def report_parent_call(
    db: Any, competition_id: str, token: str, event_id: str, athlete_id: str, *,
    status: str, location: str, expected_version: int,
    expected_day_id: str, expected_phase: str,
) -> dict[str, Any]:
    """Persist a restricted parent update with day, phase and bout/version guards."""
    if status not in _CALLS:
        raise ValueError("Choose Fencing now, On deck or In the hole.")
    if not isinstance(expected_version, int) or isinstance(expected_version, bool):
        raise ValueError("Refresh this page before sending an update.")
    if expected_phase not in {"pools", "de"} or not expected_day_id:
        raise ValueError("Refresh this page before sending an update.")
    strip = " ".join(str(location or "").split()).upper()
    if len(strip) > 16 or any(ord(char) < 32 for char in strip):
        raise ValueError("Use a short strip label, for example C3.")
    ensure_parent_schema(db)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _authorize(conn, competition_id, token):
            raise CompCoachError("This parent link is not valid.")
        competition = conn.execute("SELECT status FROM competitions WHERE id = ?", (competition_id,)).fetchone()
        if not competition or competition["status"] != "open":
            raise EventLockedError("This competition has ended. The update was not saved.")
        day = _day(conn, competition_id)
        if not day or day["id"] != expected_day_id:
            raise ConcurrentUpdateError("The competition day has changed or ended. Refresh before sending an update.")
        member = conn.execute(
            "SELECT events.status FROM events JOIN meet_events ON meet_events.event_id = events.id "
            "WHERE meet_events.meet_id = ? AND events.id = ?", (day["id"], event_id),
        ).fetchone()
        if not member or member["status"] != "open":
            raise ConcurrentUpdateError("This event is no longer active. The update was not saved.")
        row = conn.execute("SELECT * FROM athletes WHERE event_id = ? AND id = ?", (event_id, athlete_id)).fetchone()
        if not row:
            raise CompCoachError("Athlete not found in this event.")
        athlete = dict(row)
        closed_phase = conn.execute(
            "SELECT closed_at FROM phase_states WHERE event_id = ? AND phase = ?",
            (event_id, expected_phase),
        ).fetchone()
        if closed_phase and closed_phase["closed_at"]:
            raise ConcurrentUpdateError("This phase has ended. The update was not saved.")
        if athlete["phase"] != expected_phase or int(athlete["version"]) != expected_version:
            raise ConcurrentUpdateError("This athlete's bout or information changed. The page has refreshed; check the new call before sending again.")
        if not _eligible(athlete):
            raise ConcurrentUpdateError("This athlete is no longer awaiting a call. The update was not saved.")
        changes = {
            "call_status": status, "live_location": strip,
            "reported_at": utc_now(), "reported_by": "Parent",
        }
        if expected_phase == "de":
            changes["de_awaiting_next"] = 0
        # Do not use report_call: its staff workflow deliberately alters coach
        # availability. Parent input can only enrich an athlete's field report.
        result = db._update_athlete(
            conn, event_id=event_id, athlete_id=athlete_id, changes=changes,
            action="parent_call", actor="Parent", expected_version=expected_version,
            require_active=True,
        )
        conn.commit()
    return result
