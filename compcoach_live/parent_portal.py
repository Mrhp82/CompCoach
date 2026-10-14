"""One-screen parent reporting, reached through the shared competition link."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st

try:
    from .parent_access import get_parent_context, parent_context_revision, report_parent_call
    from .storage import CompCoachError
except ImportError:
    from parent_access import get_parent_context, parent_context_revision, report_parent_call
    from storage import CompCoachError


CALL_LABELS = {"now": "Fencing now", "on_deck": "On deck", "in_hole": "In the hole", "waiting": "Not called yet"}
COACH_AVAILABILITY_LABELS = {
    "available": "Available", "busy": "Busy", "committed": "Committed",
    "unavailable": "Unavailable", "not_reported": "Not reported",
}


def _time_label(timestamp: str | None, timezone_name: str) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(timezone_name)).strftime("%H:%M")
    except (ValueError, KeyError):
        return "just now"


def _elapsed_label(timestamp: str | None) -> str:
    if not timestamp:
        return "No update reported"
    try:
        updated = datetime.fromisoformat(timestamp)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - updated).total_seconds()))
    except (TypeError, ValueError):
        return "Update time unavailable"
    if seconds < 60:
        return "Updated just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"Updated {minutes} min ago"
    hours, minutes = divmod(seconds // 60, 60)
    return f"Updated {hours}h {minutes}m ago"


@st.fragment(run_every="30s")
def _render_parent_coaches(coaches: list[dict[str, Any]]) -> None:
    """Refresh elapsed labels without interrupting the athlete reporting form."""
    st.markdown("**Coach availability**")
    for coach in coaches:
        availability = COACH_AVAILABILITY_LABELS.get(str(coach.get("availability")), "Not reported")
        strip = str(coach.get("last_declared_strip") or "")
        location = f"Last declared strip {strip}" if strip else "Strip not reported"
        st.markdown(
            "<div style='padding:.45rem 0;border-bottom:1px solid #d8dee8;line-height:1.3'>"
            "<div style='display:flex;justify-content:space-between;gap:.7rem'>"
            f"<strong>{escape(str(coach['coach_name']))}</strong><span>{escape(availability)}</span></div>"
            f"<div style='font-size:.85rem;color:#64748b;margin-top:.15rem'>{escape(location)}"
            f" · {escape(_elapsed_label(coach.get('updated_at')))}</div></div>",
            unsafe_allow_html=True,
        )
    st.caption("The strip is the last location declared by staff; it may have changed.")


def _optional_coaches(context: dict[str, Any]) -> None:
    if context.get("show_coach_status") and context.get("coaches"):
        _render_parent_coaches(context["coaches"])


def _send(
    db: Any, competition_id: str, token: str, athlete: dict, day_id: str,
    status: str, strip_key: str, notice_key: str,
) -> None:
    try:
        saved = report_parent_call(
            db, competition_id, token, str(athlete["event_id"]), str(athlete["id"]),
            status=status, location=str(st.session_state.get(strip_key) or ""),
            expected_version=int(athlete["version"]), expected_day_id=day_id,
            expected_phase=str(athlete["phase"]),
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        st.session_state[notice_key] = (False, str(athlete["id"]), str(exc), "")
    else:
        detail = saved.get("live_location") or "Strip not known yet"
        st.session_state[notice_key] = (
            True, str(athlete["id"]), f"{CALL_LABELS[status]} · {detail} · Saved",
            saved["reported_at"],
        )


@st.fragment(run_every="5s")
def _parent_heartbeat(
    db: Any, competition_id: str, token: str, revision_key: str, selection_key: str,
) -> None:
    context = get_parent_context(db, competition_id, token)
    selected = str(st.session_state.get(selection_key) or "") if selection_key else ""
    if st.session_state.get(revision_key) != parent_context_revision(context, selected):
        st.rerun()


def _observe_page(
    db: Any, competition_id: str, token: str, context: dict,
    revision_key: str, selection_key: str = "",
) -> None:
    # Stamp after the selector is instantiated, so a newly chosen athlete
    # cannot compare against a different form target and force a rerun loop.
    selected = str(st.session_state.get(selection_key) or "") if selection_key else ""
    st.session_state[revision_key] = parent_context_revision(context, selected)
    _parent_heartbeat(db, competition_id, token, revision_key, selection_key)


def render_parent_portal(db: Any, competition_id: str, token: str) -> None:
    """Render the common restricted page, without identity/role/login routing."""
    context = get_parent_context(db, competition_id, token)
    base = f"parent_{competition_id}"
    revision_key, notice_key = f"{base}_revision", f"{base}_notice"
    if not context["authorized"]:
        st.error("This parent link is not valid. Please open the link shared by your club.")
        return
    competition = context["competition"]
    st.markdown("## Parent updates")
    st.caption(str(competition["name"]))
    if context["reason"] == "competition_closed":
        st.info("This competition has ended. Thank you for your updates.")
        return
    if context["reason"] == "no_active_day":
        _observe_page(db, competition_id, token, context, revision_key)
        st.info("No competition day is active. Keep this link: today's athletes will appear when the club opens the day.")
        return
    day = context["day"]
    date = str(day.get("competition_date") or "")
    if date:
        st.caption(date)
    if not context["athletes"]:
        _observe_page(db, competition_id, token, context, revision_key)
        st.info("There are no athletes awaiting a call right now. This page updates automatically.")
        notice = st.session_state.get(notice_key)
        if notice and not notice[0]:
            st.warning(notice[2])
        _optional_coaches(context)
        return
    athletes = {str(athlete["id"]): athlete for athlete in context["athletes"]}
    selection_key = f"{base}_athlete_{day['id']}"
    if st.session_state.get(selection_key) not in athletes:
        st.session_state.pop(selection_key, None)
    selected = st.selectbox(
        "Athlete", options=list(athletes), index=None, key=selection_key,
        placeholder="Search your athlete's name",
        format_func=lambda athlete_id: f"{athletes[athlete_id]['name']} · {athletes[athlete_id]['event_name']}",
    )
    _observe_page(db, competition_id, token, context, revision_key, selection_key)
    if not selected:
        st.caption("Select your athlete, enter the strip if known, then tap the call.")
        _optional_coaches(context)
        return
    athlete = athletes[selected]
    strip = str(athlete.get("live_location") or "")
    if athlete.get("phase") == "pools" and not strip:
        strip = str(athlete.get("source_strip") or "")
    st.markdown(
        "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;"
        "margin:.6rem 0;font-size:1.75rem;font-weight:750;line-height:1.2'>"
        f"<span>{escape(str(athlete['name']))}</span>"
        f"<span style='white-space:nowrap'>{escape(strip or 'Strip ?')}</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(str(athlete["event_name"]))
    current = CALL_LABELS.get(athlete.get("call_status"), "Not called yet")
    when = _time_label(athlete.get("reported_at"), str(competition.get("timezone") or "America/Los_Angeles"))
    st.markdown(f"**{current}**" + (f" · {when} · {athlete.get('reported_by') or 'Staff'} update" if when else ""))
    strip_key = f"{base}_strip_{selected}_v{athlete['version']}"
    st.text_input("Strip (optional)", value=strip, max_chars=16,
                  placeholder="e.g. C3 · leave blank if unknown", key=strip_key)
    for status in ("now", "on_deck", "in_hole"):
        st.button(
            CALL_LABELS[status], width="stretch", key=f"{base}_{selected}_{status}",
            type="primary" if status == "now" else "secondary", on_click=_send,
            args=(db, competition_id, token, athlete, str(day["id"]), status, strip_key, notice_key),
        )
    # A parent can correct a strip without having to announce a different call.
    if athlete.get("call_status") in {"now", "on_deck", "in_hole"}:
        st.button(
            "Save strip", width="stretch", key=f"{base}_{selected}_save_strip", on_click=_send,
            args=(db, competition_id, token, athlete, str(day["id"]), athlete["call_status"], strip_key, notice_key),
        )
    notice = st.session_state.get(notice_key)
    if notice and notice[1] == selected:
        if notice[0]:
            timestamp = _time_label(notice[3], str(competition.get("timezone") or "America/Los_Angeles"))
            st.success(f"{notice[2]} at {timestamp}. Coaches can now see your update.")
        else:
            st.warning(notice[2])
    st.caption("Update only when you receive a new call. The strip can change for the next bout.")
    _optional_coaches(context)
