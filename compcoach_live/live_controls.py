"""Fast mobile calls and actual coach coverage for individual athletes.

Imported DE strips identify the calling pod.  They must never become a new
bout's live strip without a coach explicitly entering that strip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

try:
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from storage import CompCoachError


CALL_ACTIONS = (("now", "Now"), ("on_deck", "On deck"),
                ("in_hole", "In the hole"), ("waiting", "Not called"))


def _notice(success: bool, message: str) -> None:
    """Let the owning app refresh every fragment after this callback finishes."""
    st.session_state["de_fast_notice"] = (success, message)
    st.session_state["de_fast_refresh"] = True


def _strip_value(key: str) -> str:
    return str(st.session_state.get(key) or "").strip().upper()


def _save_call(db: Any, event_id: str, athlete_id: str, actor: str,
               version: int, status: str, strip_key: str) -> None:
    # The version comes from the rendered button, before any fresh board read.
    # Two phones cannot silently overwrite each other's more recent updates.
    location = "" if status == "waiting" else _strip_value(strip_key)
    try:
        db.report_call(event_id, athlete_id, status=status, location=location,
                       actor=actor, expected_version=version)
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        label = dict(CALL_ACTIONS)[status]
        detail = (f" · {location}." if location else
                  " · Actual strip to confirm." if status != "waiting" else ".")
        _notice(True, f"{label} saved{detail}")


def _mark_busy(db: Any, event_id: str, athlete_id: str, actor: str,
               version: int, strip_key: str, coach: str | None,
               coach_key: str | None = None,
               coach_versions: dict[str, int] | None = None) -> None:
    selected = str(st.session_state.get(coach_key) or "") if coach_key else str(coach or "")
    location = _strip_value(strip_key)
    if not selected.strip():
        _notice(False, "Choose the coach who is with this athlete.")
        return
    if not location:
        _notice(False, "Enter the actual bout strip first, for example C3.")
        return
    try:
        db.cover_athlete(event_id, athlete_id, selected.strip(), actor,
                         location=location, expected_version=version,
                         expected_coach_version=(coach_versions or {}).get(selected.strip()))
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        _notice(True, f"{selected.strip()} is with the athlete · {location}.")


def _release(db: Any, event_id: str, athlete_id: str, actor: str,
             version: int) -> None:
    try:
        db.release(event_id, athlete_id, actor, expected_version=version)
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        _notice(True, "Coverage released. This athlete needs a coach again.")


def _elapsed(value: str | None) -> str:
    if not value:
        return "just now"
    try:
        since = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        minutes = max(0, int((datetime.now(timezone.utc) - since).total_seconds()) // 60)
    except (TypeError, ValueError):
        return "just now"
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h {minutes % 60:02d}m ago"


def render_live_controls(db: Any, event: dict, role: str, actor: str,
                         athlete: dict, *, key_prefix: str,
                         coach_states: list[dict] | None = None,
                         meet_state: dict | None = None) -> None:
    """One-tap call updates and coverage, including not-yet-called athletes."""
    event_id = str(event["id"])
    athlete_id = str(athlete["id"])
    version = int(athlete["version"])
    meet = meet_state if meet_state is not None else (db.get_meet_for_event(event_id) or event)
    writable = (
        event.get("status") == "open" and meet.get("status") == "open"
        and meet.get("day_status", "active") == "active"
        and not meet.get("ended_at") and bool(str(actor or "").strip())
        and athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
    )
    coverage = str(athlete.get("covered_by") or "")
    if coverage:
        st.caption(f"{coverage} is with {athlete['name']} · "
                   f"{athlete.get('live_location') or 'strip not reported'} · "
                   f"since {_elapsed(athlete.get('covered_at'))}")
    if not writable:
        return

    # A newly completed bout advances the athlete's version and clears the
    # textbox.  Persistent widget state cannot reuse the last bout's strip.
    base = f"live_{key_prefix}_{athlete_id}"
    strip_key = f"{base}_strip_v{version}"
    default_strip = str(athlete.get("live_location") or "")
    if athlete.get("phase") != "de" and not default_strip:
        default_strip = str(athlete.get("source_strip") or "")
    if athlete.get("call_status", "waiting") != "waiting" and not default_strip:
        label = dict(CALL_ACTIONS).get(athlete["call_status"], "Call reported")
        st.warning(f"{label} · **Actual strip to confirm**. Add it when known.")
    st.text_input("📍 **Actual bout strip** (optional)", value=default_strip,
                  placeholder="e.g. C3 · leave blank if unknown",
                  help="You can save the call without knowing the strip. To mark a coach busy with the athlete, enter the actual strip first.",
                  max_chars=16, key=strip_key)
    left, right = st.columns(2)
    for index, (status, label) in enumerate(CALL_ACTIONS):
        with (left if index % 2 == 0 else right):
            st.button(label, key=f"{base}_call_{status}", width="stretch",
                      type="primary" if athlete.get("call_status") == status else "secondary",
                      on_click=_save_call,
                      args=(db, event_id, athlete_id, actor, version, status, strip_key))

    coach_versions = {
        str(row["coach_name"]): int(row.get("version") or 0)
        for row in (coach_states if coach_states is not None
                    else db.list_coach_availability(meet["id"]))
    }
    if role in {"admin", "coordinator"}:
        coaches = list(dict.fromkeys(str(name).strip() for name in meet.get("active_coaches", [])
                                    if str(name).strip()))
        coach_key = f"{base}_cover_coach_v{version}"
        initial = coverage if coverage in coaches else actor if actor in coaches else ""
        options = ["", *coaches]
        st.selectbox("Coach with athlete", options, index=options.index(initial),
                     format_func=lambda name: name or "Choose a coach", key=coach_key)
        st.button("Mark coach busy", key=f"{base}_busy", width="stretch",
                  on_click=_mark_busy,
                  args=(db, event_id, athlete_id, actor, version, strip_key, None,
                        coach_key, coach_versions))
    elif not coverage or coverage == actor:
        st.button(f"I’m with {athlete['name']}", key=f"{base}_busy", width="stretch",
                  on_click=_mark_busy,
                  args=(db, event_id, athlete_id, actor, version, strip_key, actor,
                        None, coach_versions))

    if coverage and (role in {"admin", "coordinator"} or coverage == actor):
        st.button("Not covered", key=f"{base}_release", width="stretch",
                  on_click=_release, args=(db, event_id, athlete_id, actor, version))
