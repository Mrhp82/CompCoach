"""Admin-only competition-day scheduling UI for CompCoach Live.

The renderer deliberately depends on the small public database protocol used by
``CompCoachDB`` instead of importing the application or storage modules.  This
keeps it reusable from ``app.py`` without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

import streamlit as st


class ScheduleDB(Protocol):
    """Database calls required by :func:`render_competition_schedule`."""

    def list_competition_days(self, competition_id: str) -> list[dict[str, Any]]: ...

    def list_meet_events(self, meet_id: str) -> list[dict[str, Any]]: ...

    def get_active_competition_day(
        self, competition_id: str
    ) -> dict[str, Any] | None: ...

    def create_competition_day(
        self,
        competition_id: str,
        name: str,
        competition_date: str,
        active_coaches: Sequence[str],
        coordinators: Sequence[str],
        *,
        first_event_name: str | None = None,
        day_status: str = "scheduled",
    ) -> dict[str, Any]: ...

    def add_meet_event(self, meet_id: str, name: str) -> dict[str, Any]: ...

    def set_day_status(
        self, meet_id: str, day_status: str, actor: str
    ) -> dict[str, Any]: ...


OpenDayCallback = Callable[[Mapping[str, Any]], None]

_STATUS_LABELS = {
    "scheduled": "🗓️ Scheduled",
    "active": "🟢 Active",
    "closed": "⚫ Closed",
}


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value))


def _suggested_day_date(
    days: Sequence[Mapping[str, Any]], *, today: date | None = None
) -> date:
    """Suggest the day after the latest dated entry, never before today."""

    base = today or datetime.now(timezone.utc).date()
    parsed_dates: list[date] = []
    for day in days:
        raw_value = str(day.get("competition_date") or "").strip()
        if not raw_value:
            continue
        try:
            parsed_dates.append(date.fromisoformat(raw_value))
        except ValueError:
            continue
    return max(base, max(parsed_dates) + timedelta(days=1)) if parsed_dates else base


def _clean_event_names(values: Sequence[str]) -> list[str]:
    """Return nonblank event names and reject ambiguous duplicates."""

    names = [" ".join(str(value or "").split()) for value in values]
    names = [name for name in names if name]
    normalized = [name.casefold() for name in names]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Event names must be unique within a competition day.")
    if len(names) > 4:
        raise ValueError("A competition day can contain at most 4 events.")
    return names


def _format_day_date(value: object) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return "Date not set"
    try:
        return date.fromisoformat(raw_value).strftime("%a, %b %-d, %Y")
    except (ValueError, OSError):
        return raw_value


def _call_open_day(
    callback: OpenDayCallback | None, day: Mapping[str, Any]
) -> None:
    if callback is None:
        return
    callback(day)
    st.rerun()


def render_competition_schedule(
    db: ScheduleDB,
    meet: Mapping[str, Any],
    actor: str,
    *,
    open_day_callback: OpenDayCallback | None = None,
) -> None:
    """Render the admin-only schedule for one parent competition.

    ``open_day_callback`` receives the full selected day mapping.  The caller
    can use its id/token to update navigation.  Scheduled future days remain in
    this Setup view and are not promoted to the live workflow until an admin
    explicitly activates one.
    """

    competition_id = str(meet.get("competition_id") or "").strip()
    if not competition_id:
        st.error("This competition does not have a parent schedule yet.")
        return

    try:
        days = db.list_competition_days(competition_id)
        active_day = db.get_active_competition_day(competition_id)
    except (RuntimeError, ValueError) as exc:
        st.error(str(exc))
        return

    active_day_id = str((active_day or {}).get("id") or "")
    current_day_id = str(meet.get("id") or "")
    competition_key = _key_part(competition_id)

    st.subheader("Competition schedule")
    st.caption(
        "Future scheduled days are admin-only. Coaches see them only after "
        "you activate the day."
    )
    if active_day:
        active_name = str(active_day.get("name") or "Competition day")
        st.success(f"Active now: {active_name}")
    else:
        st.warning(
            "No active day. Live coach views stay empty until you activate a "
            "scheduled day."
        )

    if not days:
        st.info("No competition days have been created yet.")

    for day in days:
        day_id = str(day.get("id") or "")
        if not day_id:
            continue
        status = str(day.get("day_status") or "scheduled").lower()
        label = _STATUS_LABELS.get(status, status.title())
        day_name = str(day.get("name") or "Competition day")
        day_date = _format_day_date(day.get("competition_date"))
        try:
            events = db.list_meet_events(day_id)
        except (RuntimeError, ValueError):
            events = []

        with st.container(border=True):
            st.markdown(f"**{day_name}**  \n{day_date} · {label}")
            if events:
                event_names = ", ".join(str(event.get("name") or "Event") for event in events)
                st.caption(f"Events ({len(events)}): {event_names}")
            else:
                st.caption("No events yet — ready for day-of imports.")

            if day_id == current_day_id:
                st.caption("Currently open in Setup")

            if open_day_callback is not None and st.button(
                "Open this day",
                key=f"schedule_open_{competition_key}_{_key_part(day_id)}",
                use_container_width=True,
            ):
                _call_open_day(open_day_callback, day)

            if status != "scheduled":
                continue

            blocking_active = bool(active_day_id and active_day_id != day_id)
            if blocking_active:
                active_name = str((active_day or {}).get("name") or "the active day")
                st.caption(f"Finish {active_name} before activating this day.")
                st.button(
                    "Finish active day first",
                    key=f"schedule_blocked_{competition_key}_{_key_part(day_id)}",
                    disabled=True,
                    use_container_width=True,
                )
                continue

            if st.button(
                "Activate this day",
                key=f"schedule_activate_{competition_key}_{_key_part(day_id)}",
                type="primary",
                use_container_width=True,
            ):
                # Re-check at click time.  CompCoachDB's low-level method can
                # replace an active day, while this admin workflow must never
                # demote one silently.
                latest_active = db.get_active_competition_day(competition_id)
                latest_active_id = str((latest_active or {}).get("id") or "")
                if latest_active_id and latest_active_id != day_id:
                    latest_name = str(latest_active.get("name") or "the active day")
                    st.error(f"Finish {latest_name} before activating this day.")
                    continue
                try:
                    activated = db.set_day_status(day_id, "active", actor)
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))
                    continue
                st.toast("Competition day activated.")
                if open_day_callback is not None:
                    _call_open_day(open_day_callback, activated)
                st.rerun()

    with st.expander("Add scheduled day", expanded=not days):
        st.caption(
            "Staff and coordinators are copied from the day currently open. "
            "Leave all event fields blank to prepare an empty day."
        )
        with st.form(f"schedule_create_{competition_key}"):
            proposed_date = st.date_input(
                "Competition date",
                value=_suggested_day_date(days),
                key=f"schedule_date_{competition_key}",
            )
            proposed_name = st.text_input(
                "Day name",
                value=f"Day {len(days) + 1}",
                key=f"schedule_name_{competition_key}",
            )
            event_values = [
                st.text_input(
                    f"Event {index} (optional)",
                    key=f"schedule_event_{competition_key}_{index}",
                )
                for index in range(1, 5)
            ]
            submitted = st.form_submit_button(
                "Create scheduled day", use_container_width=True
            )

        if submitted:
            clean_name = " ".join(proposed_name.split())
            if not clean_name:
                st.error("Day name is required.")
                return
            date_text = proposed_date.isoformat()
            if any(
                str(day.get("competition_date") or "") == date_text for day in days
            ):
                st.error("A competition day already exists on this date.")
                return
            try:
                event_names = _clean_event_names(event_values)
                created = db.create_competition_day(
                    competition_id,
                    clean_name,
                    date_text,
                    list(meet.get("active_coaches") or []),
                    list(meet.get("coordinators") or []),
                    first_event_name=event_names[0] if event_names else None,
                    day_status="scheduled",
                )
                for event_name in event_names[1:]:
                    db.add_meet_event(str(created["id"]), event_name)
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
                return
            st.toast("Scheduled day created.")
            st.rerun()
