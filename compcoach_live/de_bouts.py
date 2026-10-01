"""Manual same-club DE pairings and atomic, reversible paired results.

Pairings supplement imported strip lists; they never infer a bracket or score.
All changes share the database's normal write transaction and athlete revision
checks, so a result cannot leave only half of an AFM bout updated.
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

try:
    from compcoach_live.storage import (
        DE_RESULT_FIELDS, CompCoachError, ConcurrentUpdateError,
        _dump, _load, assigned_coaches, de_result_now, utc_now,
    )
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from storage import (
        DE_RESULT_FIELDS, CompCoachError, ConcurrentUpdateError,
        _dump, _load, assigned_coaches, de_result_now, utc_now,
    )


def _actor(actor: str) -> str:
    clean = str(actor or "").strip()
    if not clean:
        raise CompCoachError("Select who you are before changing an AFM bout.")
    return clean


def _bout(conn: Any, event_id: str, bout_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM de_bouts WHERE event_id = ? AND id = ?", (event_id, bout_id)
    ).fetchone()
    if row is None:
        raise CompCoachError("AFM bout not found.")
    return dict(row)


def _participants(conn: Any, event_id: str, bout: Mapping[str, Any]) -> tuple[dict, dict]:
    rows = []
    for field in ("athlete_a_id", "athlete_b_id"):
        row = conn.execute(
            "SELECT * FROM athletes WHERE event_id = ? AND id = ?",
            (event_id, bout[field]),
        ).fetchone()
        if row is None:
            raise CompCoachError("One of these athletes is no longer in this event.")
        rows.append(dict(row))
    return rows[0], rows[1]


def _eligible(athlete: Mapping[str, Any]) -> bool:
    return (
        athlete.get("phase") == "de"
        and athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
    )


def _check_versions(bout: Mapping[str, Any], expected_version: int | None) -> None:
    if expected_version is not None and int(bout["version"]) != int(expected_version):
        raise ConcurrentUpdateError("This AFM bout changed on another phone. Review it again.")


def _check_athlete_versions(athletes: tuple[dict, dict], expected: Mapping[str, int | None] | None) -> None:
    for athlete in athletes:
        revision = (expected or {}).get(athlete["id"])
        if revision is not None and int(athlete["version"]) != int(revision):
            raise ConcurrentUpdateError(
                f"{athlete['name']} changed on another phone. Review both athletes before saving."
            )


def pending_bout_for_athlete(conn: Any, event_id: str, athlete_id: str) -> dict | None:
    """Lookup used by individual Won/Lost buttons inside their existing transaction."""
    row = conn.execute(
        """SELECT * FROM de_bouts WHERE event_id = ? AND status = 'pending'
           AND (athlete_a_id = ? OR athlete_b_id = ?) ORDER BY created_at, id LIMIT 1""",
        (event_id, athlete_id, athlete_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _assert_free_pair(conn: Any, event_id: str, athletes: tuple[dict, dict], *, except_id: str = "") -> None:
    for athlete in athletes:
        existing = pending_bout_for_athlete(conn, event_id, athlete["id"])
        if existing is not None and existing["id"] != except_id:
            raise ConcurrentUpdateError(
                f"{athlete['name']} already has a pending AFM bout. Resolve or remove it first."
            )


def _ready_participants(db: Any, conn: Any, event_id: str, participants: tuple[dict, dict], actor: str) -> tuple[dict, dict]:
    """A newly checked pairing starts the next bout, invalidating old cards."""
    ready = []
    for athlete in participants:
        if athlete.get("de_awaiting_next"):
            athlete = db._update_athlete(
                conn, event_id=event_id, athlete_id=athlete["id"],
                changes={"de_awaiting_next": 0}, action="de_ready", actor=actor,
                expected_version=athlete["version"], require_active=True,
            )
        ready.append(athlete)
    meet = db._meet_for_event(conn, event_id)
    db._clear_available_coaches(
        conn, meet["id"], [coach for athlete in ready for coach in assigned_coaches(athlete)], actor,
    )
    return ready[0], ready[1]


def _log(db: Any, conn: Any, event_id: str, action: str, actor: str, previous: dict | None, new: dict) -> None:
    db._log_action(
        conn, event_id=event_id, athlete_id=None, action=action, actor=actor,
        previous=previous, new=new, version_after=new["version"],
    )


def list_de_bouts(db: Any, event_id: str, *, include_cancelled: bool = False) -> list[dict]:
    """Return pairings with current athletes, including resolved bouts for Undo."""
    with db._connection() as conn:
        rows = conn.execute(
            "SELECT * FROM de_bouts WHERE event_id = ? ORDER BY created_at DESC, id DESC",
            (event_id,),
        ).fetchall()
        visible = [dict(row) for row in rows if include_cancelled or row["status"] != "cancelled"]
        if not visible:
            return []
        athlete_by_id = {
            row["id"]: db._athlete_dict(row)
            for row in conn.execute("SELECT * FROM athletes WHERE event_id = ?", (event_id,)).fetchall()
        }
        result = []
        for bout in visible:
            try:
                bout["athlete_a"] = athlete_by_id[bout["athlete_a_id"]]
                bout["athlete_b"] = athlete_by_id[bout["athlete_b_id"]]
            except KeyError as exc:
                raise CompCoachError("One of these athletes is no longer in this event.") from exc
            result.append(bout)
    return result


def create_de_bout(
    db: Any, event_id: str, athlete_a_id: str, athlete_b_id: str, *, actor: str,
    round_label: str = "", expected_a_version: int | None = None,
    expected_b_version: int | None = None,
) -> dict:
    actor = _actor(actor)
    if athlete_a_id == athlete_b_id:
        raise CompCoachError("Select two different AFM athletes.")
    clean_round = " ".join(str(round_label or "").split())
    if len(clean_round) > 40:
        raise CompCoachError("Keep the round label within 40 characters.")
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db._assert_open(conn, event_id)
        ids = {"athlete_a_id": athlete_a_id, "athlete_b_id": athlete_b_id}
        participants = _participants(conn, event_id, ids)
        _check_athlete_versions(participants, {
            athlete_a_id: expected_a_version, athlete_b_id: expected_b_version,
        })
        if not all(_eligible(athlete) for athlete in participants):
            raise ConcurrentUpdateError("Both athletes must be active in direct elimination.")
        _assert_free_pair(conn, event_id, participants)
        participants = _ready_participants(db, conn, event_id, participants, actor)
        now, bout_id = utc_now(), uuid4().hex
        conn.execute(
            """INSERT INTO de_bouts (
                   id,event_id,athlete_a_id,athlete_b_id,round_label,status,
                   athlete_a_version,athlete_b_version,created_at,created_by,updated_at
               ) VALUES (?,?,?,?,?,'pending',?,?,?,?,?)""",
            (bout_id,event_id,athlete_a_id,athlete_b_id,clean_round,
             participants[0]["version"],participants[1]["version"],now,actor,now),
        )
        new = _bout(conn, event_id, bout_id)
        _log(db, conn, event_id, "de_bout_create", actor, None, new)
        conn.commit()
    return new


def _result_changes(current: Mapping[str, Any], *, won: bool, actor: str, now: str) -> dict:
    return {
        "active_state": "active" if won else "eliminated",
        "de_awaiting_next": int(won),
        "de_wins": int(current.get("de_wins") or 0) + int(won),
        "last_de_result": "won" if won else "lost",
        "last_de_result_at": now, "last_de_result_by": actor,
        "call_status": "waiting", "live_location": "", "reported_at": None,
        "reported_by": "", "covered_by": "", "covered_at": None,
        "takeover_coach": "", "takeover_at": None, "takeover_by": "",
        "help_requested_by": "", "help_requested_at": None, "help_location": "",
        "help_acknowledged_by": "", "help_acknowledged_at": None,
    }


def resolve_de_bout_in_transaction(
    db: Any, conn: Any, event_id: str, bout_id: str, *, winner_id: str, actor: str,
    expected_version: int | None = None,
    expected_athlete_versions: Mapping[str, int | None] | None = None,
) -> dict:
    """Resolve within a caller-owned transaction; never commit here."""
    actor = _actor(actor)
    db._assert_open(conn, event_id)
    previous = _bout(conn, event_id, bout_id)
    _check_versions(previous, expected_version)
    if previous["status"] != "pending":
        raise ConcurrentUpdateError("This AFM bout is no longer pending. The result was not saved twice.")
    participants = _participants(conn, event_id, previous)
    _check_athlete_versions(participants, expected_athlete_versions)
    if winner_id not in {athlete["id"] for athlete in participants}:
        raise CompCoachError("The winner must be one of these two athletes.")
    if not all(_eligible(athlete) for athlete in participants):
        raise ConcurrentUpdateError("One athlete is no longer active in DE. Review or remove this pairing.")
    now = de_result_now()
    updated = []
    for athlete in participants:
        updated.append(db._update_athlete(
            conn, event_id=event_id, athlete_id=athlete["id"],
            changes=_result_changes(athlete, won=athlete["id"] == winner_id, actor=actor, now=now),
            action="de_bout_result", actor=actor,
            expected_version=athlete["version"], require_active=True,
        ))
    meet = db._meet_for_event(conn, event_id)
    db._mark_released_coaches_available(
        conn, meet["id"], [coach for athlete in participants for coach in (athlete.get("covered_by"), athlete.get("takeover_coach"))], actor,
    )
    conn.execute(
        """UPDATE de_bouts SET status='resolved',winner_id=?,
               previous_a_json=?,previous_b_json=?,resolved_a_version=?,resolved_b_version=?,
               resolved_at=?,resolved_by=?,updated_at=?,version=version+1 WHERE id=?""",
        (winner_id,_dump(participants[0]),_dump(participants[1]),
         updated[0]["version"],updated[1]["version"],now,actor,now,bout_id),
    )
    new = _bout(conn, event_id, bout_id)
    _log(db, conn, event_id, "de_bout_resolve", actor, previous, new)
    return new


def resolve_de_bout(
    db: Any, event_id: str, bout_id: str, *, winner_id: str, actor: str,
    expected_version: int | None = None, expected_a_version: int | None = None,
    expected_b_version: int | None = None,
) -> dict:
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _bout(conn, event_id, bout_id)
        new = resolve_de_bout_in_transaction(
            db,conn,event_id,bout_id,winner_id=winner_id,actor=actor,
            expected_version=expected_version,
            expected_athlete_versions={current["athlete_a_id"]:expected_a_version,
                                       current["athlete_b_id"]:expected_b_version},
        )
        conn.commit()
    return new


def undo_de_bout_result(db: Any, event_id: str, bout_id: str, *, actor: str, expected_version: int | None = None) -> dict:
    actor = _actor(actor)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db._assert_open(conn, event_id)
        previous = _bout(conn, event_id, bout_id)
        _check_versions(previous, expected_version)
        if previous["status"] != "resolved":
            raise CompCoachError("Only a resolved AFM bout can have both results undone.")
        participants = _participants(conn, event_id, previous)
        _assert_free_pair(conn, event_id, participants, except_id=bout_id)
        updated = []
        source_actions = []
        for athlete, snapshot_field, revision_field in zip(
            participants, ("previous_a_json", "previous_b_json"),
            ("resolved_a_version", "resolved_b_version"),
        ):
            snapshot = _load(previous[snapshot_field], None)
            if not snapshot:
                raise CompCoachError("The saved athlete state is missing; Undo cannot safely restore this bout.")
            if athlete["phase"] != "de":
                raise ConcurrentUpdateError("One athlete has moved out of DE. This result cannot be restored here.")
            source = conn.execute(
                """SELECT * FROM actions WHERE event_id = ? AND athlete_id = ?
                   AND action = 'de_bout_result' AND version_after = ?
                   AND undone_at IS NULL ORDER BY id DESC LIMIT 1""",
                (event_id, athlete["id"], previous[revision_field]),
            ).fetchone()
            if source is None:
                raise ConcurrentUpdateError("The saved paired result changed. Refresh the corrections list.")
            saved_result = _load(source["new_json"], {})
            if any(athlete.get(field, 0 if field in {"de_wins", "de_byes"} else "") != saved_result.get(field, 0 if field in {"de_wins", "de_byes"} else "") for field in DE_RESULT_FIELDS):
                raise ConcurrentUpdateError("A newer DE result exists. Correct the latest result first.")
            later = conn.execute(
                """SELECT * FROM actions WHERE event_id = ? AND athlete_id = ?
                   AND id > ? AND undone_at IS NULL ORDER BY id""",
                (event_id, athlete["id"], source["id"]),
            ).fetchall()
            for action in later:
                if action["action"] in {"de_bout_undo", "de_result_correction", "undo"}:
                    continue
                old, new = _load(action["previous_json"], {}), _load(action["new_json"], {})
                if any(old.get(field) != new.get(field) for field in DE_RESULT_FIELDS):
                    raise ConcurrentUpdateError("A newer DE result exists. Correct the latest result first.")
            changes = {field: snapshot.get(field, athlete.get(field)) for field in DE_RESULT_FIELDS}
            # Preserve a newer call or explicit next-round activation. Otherwise
            # put both athletes back in the round whose result was corrected.
            changes["de_awaiting_next"] = (
                int(athlete.get("de_awaiting_next") or 0)
                if int(athlete.get("de_awaiting_next") or 0) != int(saved_result.get("de_awaiting_next") or 0)
                else int(snapshot.get("de_awaiting_next") or 0)
            )
            updated.append(db._update_athlete(
                conn,event_id=event_id,athlete_id=athlete["id"],
                changes=changes,
                action="de_bout_undo",actor=actor,expected_version=athlete["version"],
            ))
            source_actions.append(int(source["id"]))
        for action_id in source_actions:
            conn.execute("UPDATE actions SET undone_at = ?, undone_by = ? WHERE id = ?", (utc_now(), actor, action_id))
        conn.execute(
            """UPDATE de_bouts SET status='pending',winner_id='',
               athlete_a_version=?,athlete_b_version=?,updated_at=?,version=version+1 WHERE id=?""",
            (updated[0]["version"],updated[1]["version"],utc_now(),bout_id),
        )
        # Reactivated athletes need their current coaches. New assignments and
        # current field information are retained instead of resurrecting old calls.
        restored_coaches = [
            name for athlete in updated
            for name in (
                *assigned_coaches(athlete), athlete.get("covered_by"),
                athlete.get("help_requested_by"), athlete.get("help_acknowledged_by"),
            )
        ]
        meet = db._meet_for_event(conn, event_id)
        db._clear_available_coaches(conn, meet["id"], restored_coaches, actor)
        new = _bout(conn,event_id,bout_id)
        _log(db,conn,event_id,"de_bout_undo_result",actor,previous,new)
        conn.commit()
    return new


def _change_pairing_status(db: Any,event_id: str,bout_id: str,*,actor: str,expected_version: int | None,restore: bool) -> dict:
    actor = _actor(actor)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db._assert_open(conn,event_id)
        previous = _bout(conn,event_id,bout_id)
        _check_versions(previous,expected_version)
        required = "cancelled" if restore else "pending"
        if previous["status"] != required:
            raise ConcurrentUpdateError("This pairing changed. Refresh before trying again.")
        if restore:
            participants = _participants(conn,event_id,previous)
            if not all(_eligible(athlete) for athlete in participants):
                raise ConcurrentUpdateError("Both athletes must still be active in DE to restore this pairing.")
            _assert_free_pair(conn,event_id,participants)
            participants = _ready_participants(db, conn, event_id, participants, actor)
            conn.execute(
                "UPDATE de_bouts SET athlete_a_version = ?, athlete_b_version = ? WHERE id = ?",
                (participants[0]["version"], participants[1]["version"], bout_id),
            )
        conn.execute(
            "UPDATE de_bouts SET status=?,updated_at=?,version=version+1 WHERE id=?",
            ("pending" if restore else "cancelled",utc_now(),bout_id),
        )
        new = _bout(conn,event_id,bout_id)
        _log(db,conn,event_id,"de_bout_restore_pair" if restore else "de_bout_cancel",actor,previous,new)
        conn.commit()
    return new


def cancel_de_bout(db: Any,event_id: str,bout_id: str,*,actor: str,expected_version: int | None = None) -> dict:
    """Remove only the pairing; athlete assignments, calls and results are retained."""
    return _change_pairing_status(db,event_id,bout_id,actor=actor,expected_version=expected_version,restore=False)


def restore_de_bout_pairing(db: Any,event_id: str,bout_id: str,*,actor: str,expected_version: int | None = None) -> dict:
    return _change_pairing_status(db,event_id,bout_id,actor=actor,expected_version=expected_version,restore=True)
