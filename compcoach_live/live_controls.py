"""Fast mobile calls and actual coach coverage for individual athletes.

Imported DE strips identify the calling pod.  They must never become a new
bout's live strip without a coach explicitly entering that strip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
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


def _strip_value(key: str, default: str = "") -> str:
    # A known strip's editor is hidden in the compact view. Widget cleanup
    # must not turn that saved location into an empty new call/coverage.
    return str(st.session_state.get(key, default) or "").strip().upper()


def _save_call(db: Any, event_id: str, athlete_id: str, actor: str,
               version: int, status: str, strip_key: str,
               default_strip: str = "") -> None:
    # The version comes from the rendered button, before any fresh board read.
    # Two phones cannot silently overwrite each other's more recent updates.
    location = "" if status == "waiting" else _strip_value(strip_key, default_strip)
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
               coach_versions: dict[str, int] | None = None,
               default_strip: str = "") -> None:
    selected = str(st.session_state.get(coach_key) or "") if coach_key else str(coach or "")
    location = _strip_value(strip_key, default_strip)
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


def _arrive_at_pool_snapshot(db: Any, event_id: str, athlete_id: str,
                             actor: str, version: int, location: str,
                             coach_version: int) -> None:
    """Confirm physical arrival without changing the ordinary pool plan."""
    try:
        db.cover_athlete(
            event_id, athlete_id, actor, actor, location=location,
            expected_version=version, expected_coach_version=coach_version,
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        _notice(False, str(exc))
    else:
        detail = location or "actual strip to confirm"
        _notice(True, f"You are with this athlete · {detail}.")


def render_pool_takeover_arrival(
    db: Any, event: dict, role: str, actor: str, athlete: dict, *,
    key_prefix: str, coach_states: list[dict] | None = None,
) -> None:
    """The accepted emergency coach confirms arrival with one pool action."""
    if (
        athlete.get("phase") != "pools"
        or athlete.get("active_state") != "active"
        or athlete.get("participation_status", "active") != "active"
        or athlete.get("pool_result_at")
        or (athlete.get("pool_wins") is not None and athlete.get("pool_losses") is not None)
        or not actor or athlete.get("takeover_coach") != actor
        or athlete.get("covered_by")
    ):
        return
    meet = db.get_meet_for_event(str(event["id"])) or event
    if (
        event.get("status") != "open" or meet.get("status") != "open"
        or meet.get("day_status", "active") != "active" or meet.get("ended_at")
    ):
        return
    states = coach_states if coach_states is not None else db.list_coach_availability(meet["id"])
    own_state = next((row for row in states if row["coach_name"] == actor), {})
    location = str(athlete.get("live_location") or athlete.get("source_strip") or "").strip().upper()
    if not location:
        st.caption("Strip not known yet; confirm your arrival and update the location when you know it.")
    st.button(
        f"I’m with {athlete['name']}",
        key=f"{key_prefix}_pool_arrive_{athlete['id']}", width="stretch",
        on_click=_arrive_at_pool_snapshot,
        args=(db, str(event["id"]), str(athlete["id"]), actor,
              int(athlete["version"]), location, int(own_state.get("version") or 0)),
    )


def render_live_controls(db: Any, event: dict, role: str, actor: str,
                         athlete: dict, *, key_prefix: str,
                         coach_states: list[dict] | None = None,
                         meet_state: dict | None = None) -> None:
    """Compact forward call updates, with corrections and coverage available."""
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
    status = str(athlete.get("call_status") or "waiting")
    labels = dict(CALL_ACTIONS)
    st.markdown(
        f"<div class='cc-live-action-title'><b>{escape(str(athlete['name']))}</b> · "
        f"Actual strip <b>{escape(default_strip or 'TBD')}</b></div>",
        unsafe_allow_html=True,
    )
    call_age = f" · {_elapsed(athlete.get('reported_at'))}" if athlete.get("reported_at") else ""
    st.caption(f"{labels.get(status, 'Not called')}{call_age}")
    editing = st.toggle("Modify call", key=f"{base}_modify_v{version}")
    if status != "waiting" and not default_strip:
        label = labels.get(status, "Call reported")
        st.warning(f"{label} · **Actual strip to confirm**. Add it when known.")
    if editing or not default_strip:
        st.text_input("📍 **Actual bout strip** (optional)", value=default_strip,
                      placeholder="e.g. C3 · leave blank if unknown",
                      help="You can save the call without knowing the strip. To mark a coach busy with the athlete, enter the actual strip first.",
                      max_chars=16, key=strip_key)
    forward = {
        "waiting": ("in_hole", "on_deck", "now"),
        "in_hole": ("on_deck", "now"),
        "on_deck": ("now",),
        "now": (),
    }
    choices = tuple(key for key, _ in CALL_ACTIONS) if editing else forward.get(status, forward["waiting"])
    if choices:
        with st.container(key=f"cc_call_actions_{base}"):
            columns = st.columns(len(choices))
            for column, next_status in zip(columns, choices):
                with column:
                    st.button(labels[next_status], key=f"{base}_call_{next_status}", width="stretch",
                              type="primary" if editing and status == next_status else "secondary",
                              on_click=_save_call,
                              args=(db, event_id, athlete_id, actor, version, next_status,
                                    strip_key, default_strip))

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
        with st.expander("Coach coverage", expanded=False):
            st.selectbox("Coach with athlete", options, index=options.index(initial),
                         format_func=lambda name: name or "Choose a coach", key=coach_key)
            st.button("Mark coach busy", key=f"{base}_busy", width="stretch",
                      on_click=_mark_busy,
                      args=(db, event_id, athlete_id, actor, version, strip_key, None,
                            coach_key, coach_versions, default_strip))
        if role == "admin" and actor in coaches and not coverage:
            st.button(f"I’m with {athlete['name']}", key=f"{base}_self_busy",
                      width="stretch", type="primary", on_click=_mark_busy,
                      args=(db, event_id, athlete_id, actor, version, strip_key,
                            actor, None, coach_versions, default_strip))
    elif not coverage:
        # Physical coverage is already confirmed once covered_by is set. A
        # pending takeover is only a promise, so it still needs this button.
        # Keep call/location edits and release available for the busy coach.
        st.button(f"I’m with {athlete['name']}", key=f"{base}_busy", width="stretch", type="primary",
                  on_click=_mark_busy,
                  args=(db, event_id, athlete_id, actor, version, strip_key, actor,
                        None, coach_versions, default_strip))

    if coverage and (role in {"admin", "coordinator"} or coverage == actor):
        st.button("Not covered", key=f"{base}_release", width="stretch",
                  on_click=_release, args=(db, event_id, athlete_id, actor, version))
