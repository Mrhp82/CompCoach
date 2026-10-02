"""One-tap missed-coaching reports and a factual, reversible review.

A missed coaching report is independent of the fencing result and of the
coverage controls. Staff snapshots describe the app at report-entry time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

try:
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from storage import CompCoachError


STAFF_TIME_NOTE = (
    "Staff record when this report was entered. This does not establish "
    "availability at the actual bout time."
)


def _notice(success: bool, message: str) -> None:
    st.session_state["de_fast_notice"] = (success, message)
    st.session_state["de_fast_refresh"] = True


def _time(value: Any, timezone_name: str = "UTC") -> str:
    if not value:
        return "time not recorded"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        try:
            parsed = parsed.astimezone(ZoneInfo(timezone_name))
        except (ZoneInfoNotFoundError, ValueError):
            parsed = parsed.astimezone(timezone.utc)
        zone = parsed.strftime("%Z") or "local time"
        return f"{parsed:%d %b %H:%M:%S} {zone}"
    except (TypeError, ValueError):
        return str(value)


def _writable(event: dict, meet: dict, actor: str) -> bool:
    return bool(
        str(actor or "").strip()
        and event.get("status") == "open"
        and meet.get("status") == "open"
        and meet.get("day_status", "active") == "active"
        and not meet.get("ended_at")
    )


def missed_bout_context(athlete: dict) -> str:
    """Default late Won/Lost reports to the fenced bout just completed."""
    if athlete.get("phase") == "de" and athlete.get("last_de_result") in {"won", "lost"}:
        if athlete.get("active_state") == "eliminated" or athlete.get("de_awaiting_next"):
            return "last_completed"
    return "current"


def _report(
    db: Any, event_id: str, athlete_id: str, actor: str, version: int,
    context: str, retry_key: str, retry_slot: str, note_key: str | None = None,
) -> None:
    note = str(st.session_state.get(note_key) or "").strip() if note_key else ""
    try:
        receipt = db.report_missed_coaching(
            event_id, athlete_id, actor, expected_version=version,
            idempotency_key=retry_key, note=note, bout_context=context,
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        unsaved_note = bool(receipt.get("was_already_recorded") and note and note != str(receipt.get("note") or "").strip())
        # A retry of this captured callback retains its UUID. A subsequent
        # render can report another genuinely missed pool bout with a new UUID.
        if st.session_state.get(retry_slot) == retry_key:
            st.session_state.pop(retry_slot, None)
        if note_key and not unsaved_note:
            st.session_state[note_key] = ""
        label = "Missed coaching already recorded for this bout." if receipt.get("was_already_recorded") else "Missed coaching recorded."
        detail = " The new note was not added; the original report has been kept." if unsaved_note else ""
        _notice(True, f"{label}{detail} The fencing result and coverage are unchanged.")


def _retry_id(slot: str) -> str:
    if not st.session_state.get(slot):
        st.session_state[slot] = uuid4().hex
    return str(st.session_state[slot])


def render_missed_coaching_control(
    db: Any, event: dict, actor: str, athlete: dict, *, key_prefix: str,
    meet_state: dict | None = None,
) -> None:
    """A single button; no result mutation, automatic release, or confirmation."""
    meet = meet_state if meet_state is not None else (db.get_meet_for_event(event["id"]) or event)
    if (
        not _writable(event, meet, actor)
        or athlete.get("participation_status", "active") != "active"
    ):
        return
    version = int(athlete.get("version") or 0)
    context = missed_bout_context(athlete)
    base = f"{key_prefix}_missed_coaching_{athlete['id']}"
    slot = f"{base}_retry_v{version}_{context}"
    st.button(
        "⚠️ Missed coaching", key=base, width="stretch",
        help="Record an uncovered bout. This does not change Won/Lost or release a coach.",
        on_click=_report,
        args=(db, str(event["id"]), str(athlete["id"]), actor, version,
              context, _retry_id(slot), slot),
    )


def _correct(db: Any, meet_id: str, incident_id: str, actor: str,
             version: int, note_key: str) -> None:
    try:
        db.correct_missed_coaching(
            meet_id, incident_id, actor, expected_version=version,
            note=str(st.session_state.get(note_key) or "").strip(),
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        _notice(True, "Report marked as incorrect. Its history has been kept.")


def _staff_snapshot(incident: dict, timezone_name: str = "UTC") -> None:
    snapshot = incident.get("coaches_snapshot") or {}
    st.caption(STAFF_TIME_NOTE)
    counts = (
        f"{int(snapshot.get('busy_count') or 0)} marked covering athletes · "
        f"{int(snapshot.get('reserved_count') or 0)} reserved · "
        f"{int(snapshot.get('available_count') or 0)} declared available · "
        f"{int(snapshot.get('unconfirmed_count') or 0)} availability unconfirmed"
    )
    st.write(counts)
    if snapshot.get("all_committed"):
        st.caption("Every active coach had coverage or a reservation recorded at report entry.")
    for coach in snapshot.get("coaches") or []:
        name = str(coach.get("coach_name") or "Coach")
        status = coach.get("status")
        if status == "busy":
            st.write(f"{name} · Marked covering athletes")
        elif status == "reserved":
            st.write(f"{name} · Reserved for coverage")
        elif status == "available":
            st.write(f"{name} · Declared available · since {_time(coach.get('available_since'), timezone_name)}")
        else:
            st.write(f"{name} · Availability unconfirmed")
        for label, rows, since_field in (
            ("With", coach.get("busy_with") or [], "covered_since"),
            ("Reserved for", coach.get("reserved_for") or [], "reserved_since"),
        ):
            for row in rows:
                st.caption(
                    f"{label} {row.get('athlete_name') or 'athlete'} · "
                    f"{row.get('event_name') or 'event'} · "
                    f"Actual strip {row.get('actual_strip') or 'not recorded'} · "
                    f"since {_time(row.get(since_field), timezone_name)}"
                )


def _incident_label(incident: dict) -> str:
    name = str(incident.get("athlete_name") or "Athlete")
    phase = "Pools" if incident.get("phase") == "pools" else "DE"
    number = incident.get("bout_number")
    bout = f" · tracked turn {number}" if phase == "DE" and number is not None else ""
    return f"{name} · {incident.get('event_name') or 'Event'} · {phase}{bout}"


def _render_incident(db: Any, meet: dict, actor: str, incident: dict,
                     *, writable: bool, key_prefix: str) -> None:
    timezone_name = str(meet.get("timezone") or "UTC")
    st.write(_incident_label(incident))
    location = str(incident.get("live_location") or "")
    if not location and incident.get("phase") == "pools":
        location = str(incident.get("source_strip") or "")
    st.caption(
        f"Actual strip {location or 'not recorded'} · "
        f"Report entered {_time(incident.get('recorded_at'), timezone_name)} · "
        f"by {incident.get('recorded_by') or 'staff'}"
    )
    if incident.get("phase") == "de" and incident.get("bout_number") is not None:
        st.caption("Tracked turns include byes; this is not the official tableau round.")
    if incident.get("bout_kind") == "last_completed":
        st.caption(
            "Reported for the most recent completed bout. "
            f"Result saved {_time(incident.get('source_result_at'), timezone_name)}; this is not the bout start time."
        )
    if incident.get("note"):
        st.write(f"Note: {incident['note']}")
    athlete_snapshot = incident.get("athlete_snapshot") or {}
    coverage = str(athlete_snapshot.get("covered_by") or "").strip()
    if coverage:
        st.caption(f"App still showed coverage by {coverage} at reporting time.")
    elif (incident.get("summary") or {}).get("target_coverage_was_recorded"):
        st.caption("App still showed coverage for this athlete at reporting time.")
    st.caption(STAFF_TIME_NOTE)
    snapshot = incident.get("coaches_snapshot") or {}
    st.write(snapshot.get("summary_label") or "No active coaches recorded")
    with st.expander(f"Staff snapshot · {incident.get('athlete_name') or 'athlete'}", expanded=False):
        _staff_snapshot(incident, timezone_name)
    if incident.get("corrected_at"):
        st.caption(
            f"Marked incorrect {_time(incident['corrected_at'], timezone_name)} · "
            f"by {incident.get('corrected_by') or 'staff'}"
        )
        if incident.get("correction_note"):
            st.write(f"Correction note: {incident['correction_note']}")
    elif writable:
        base = f"{key_prefix}_incident_{incident['id']}"
        with st.expander("Correct this report", expanded=False):
            note_key = f"{base}_correction_note"
            st.text_input("Correction note (optional)", key=note_key, max_chars=1000)
            st.button(
                "Mark report as incorrect", key=f"{base}_correct", width="stretch",
                on_click=_correct,
                args=(db, str(meet["id"]), str(incident["id"]), actor,
                      int(incident.get("version") or 0), note_key),
            )


def _manual_report(db: Any, meet: dict, actor: str, *, key_prefix: str) -> None:
    candidates: dict[str, tuple[dict, dict]] = {}
    for event in db.list_meet_events(meet["id"]):
        if not _writable(event, meet, actor):
            continue
        for athlete in db.list_athletes(event["id"]):
            if athlete.get("participation_status", "active") == "active":
                candidates[str(athlete["id"])] = (event, athlete)
    if not candidates:
        return
    def label(athlete_id: str) -> str:
        event, athlete = candidates[athlete_id]
        phase = "Pools" if athlete.get("phase") == "pools" else "DE"
        state = " · Out" if athlete.get("active_state") == "eliminated" else ""
        return f"{athlete['name']} · {event['name']} · {phase}{state}"
    st.caption("You can report after entering a result, including athletes who are Out or finished Pools.")
    selected = st.selectbox(
        "Athlete to report", list(candidates), index=None,
        placeholder="Choose an athlete for a later report", format_func=label,
        key=f"{key_prefix}_athlete",
    )
    if not selected:
        return
    event, athlete = candidates[str(selected)]
    version = int(athlete.get("version") or 0)
    context = missed_bout_context(athlete)
    if athlete.get("phase") == "de" and athlete.get("last_de_result") in {"won", "lost"}:
        # An eliminated athlete has no current fenced bout to report.
        options = ["last_completed"] if athlete.get("active_state") == "eliminated" else ["current", "last_completed"]
        context = st.radio(
            "Bout to report", options, index=options.index(context), horizontal=True,
            format_func=lambda value: "Most recent completed bout" if value == "last_completed" else "Current bout",
            key=f"{key_prefix}_context_{selected}_v{version}",
        )
    elif athlete.get("phase") == "de" and athlete.get("last_de_result") == "bye":
        st.caption("A bye is not a fenced bout. This report concerns the current bout after the bye.")
    base = f"{key_prefix}_manual_{selected}_v{version}_{context}"
    note_key = f"{base}_note"
    slot = f"{base}_retry"
    st.text_input("Notes (optional)", key=note_key, max_chars=1000)
    st.button(
        "Record missed coaching", key=f"{base}_send", width="stretch",
        on_click=_report,
        args=(db, str(event["id"]), str(athlete["id"]), actor, version,
              context, _retry_id(slot), slot, note_key),
    )


def render_missed_coaching_review(
    db: Any, meet: dict, actor: str, role: str, *, read_only: bool = False,
    include_training: bool = False, key_prefix: str = "missed_review",
) -> None:
    """Collapsed shared review, including late reports and retained corrections."""
    incidents = db.list_missed_coaching(
        meet["id"], include_corrected=True, include_training=include_training,
    )
    active = [row for row in incidents if not row.get("corrected_at")]
    corrected = [row for row in incidents if row.get("corrected_at")]
    writable = not read_only and role in {"admin", "coordinator", "coach"} and _writable(meet, meet, actor)
    if not incidents and not writable:
        return
    count = len({str(row["athlete_id"]) for row in active})
    base = f"{key_prefix}_{meet['id']}_{role}"
    with st.expander(f"⚠️ Missed coaching · {count} athletes", expanded=False):
        st.caption("These reports do not change fencing results, assignments, or coach coverage.")
        if writable:
            _manual_report(db, meet, actor, key_prefix=base)
        if not active:
            st.caption("No active missed-coaching reports.")
        for incident in active:
            _render_incident(db, meet, actor, incident, writable=writable, key_prefix=base)
        if corrected:
            with st.expander(f"Marked incorrect · {len(corrected)} reports", expanded=False):
                for incident in corrected:
                    _render_incident(db, meet, actor, incident, writable=False, key_prefix=base)
