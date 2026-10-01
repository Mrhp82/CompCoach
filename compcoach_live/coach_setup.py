"""Mobile-first coach directory and competition-day roster controls.

The renderer deliberately depends only on Streamlit and the public storage
API.  Keeping it outside :mod:`app` makes the setup screen easier to test and
avoids coupling staff management to the live-board rendering helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

try:
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from storage import CompCoachError


ROLE_LABELS = {
    "coach": "Coach",
    "coordinator": "Coordinator",
    "admin": "Admin",
}
PRESENCE_LABELS = {
    "present": "Present",
    "scheduled": "Scheduled",
    "absent": "Absent",
}
PRESENCE_ICONS = {
    "present": "🟢",
    "scheduled": "🟡",
    "absent": "⚪",
}
PRESENCE_ORDER = {"present": 0, "scheduled": 1, "absent": 2}


def _coach_state(db: Any, meet: dict[str, Any]) -> dict[str, Any]:
    """Load and join the directory, competition roster, and day roster."""

    meet_id = str(meet.get("id") or "")
    competition_id = str(meet.get("competition_id") or "")
    events = db.list_meet_events(meet_id)
    directory = db.list_coaches()
    competition_rows = (
        db.list_competition_coaches(competition_id) if competition_id else []
    )
    day_rows = db.list_day_coaches(meet_id)

    competition_by_id = {
        str(row["coach_id"]): row for row in competition_rows
    }
    day_by_id = {str(row["coach_id"]): row for row in day_rows}
    coaches: list[dict[str, Any]] = []
    for directory_row in directory:
        coach_id = str(directory_row["id"])
        competition_row = competition_by_id.get(coach_id, {})
        day_row = day_by_id.get(coach_id, {})
        coaches.append(
            {
                **directory_row,
                "coach_id": coach_id,
                "roles": list(competition_row.get("roles") or []),
                "in_competition": coach_id in competition_by_id,
                "presence_status": day_row.get("presence_status"),
                "home_event_id": day_row.get("home_event_id"),
                "home_event_name": day_row.get("home_event_name"),
                "assignment_count": int(day_row.get("assignment_count") or 0),
                "is_used": bool(day_row.get("is_used")),
            }
        )
    coaches.sort(
        key=lambda row: (
            not bool(row.get("is_active")),
            str(row.get("name") or "").casefold(),
            str(row.get("coach_id") or ""),
        )
    )
    roster = sorted(
        day_rows,
        key=lambda row: (
            PRESENCE_ORDER.get(str(row.get("presence_status") or ""), 9),
            bool(row.get("is_used")),
            str(row.get("name") or "").casefold(),
        ),
    )
    return {
        "meet_id": meet_id,
        "competition_id": competition_id,
        "events": events,
        "coaches": coaches,
        "roster": roster,
    }


def _coach_option(coach: dict[str, Any]) -> str:
    name = str(coach.get("name") or "Coach")
    suffixes: list[str] = []
    if not coach.get("is_active", True):
        suffixes.append("inactive")
    status = str(coach.get("presence_status") or "")
    if status:
        suffixes.append(PRESENCE_LABELS.get(status, status.title()))
    assignments = int(coach.get("assignment_count") or 0)
    if assignments:
        word = "assignment" if assignments == 1 else "assignments"
        suffixes.append(f"{assignments} {word}")
    if not coach.get("in_competition"):
        suffixes.append("not in this competition")
    return f"{name} · {' · '.join(suffixes)}" if suffixes else name


def _show_flash(meet_id: str) -> None:
    key = f"coach_setup_flash_{meet_id}"
    message = st.session_state.pop(key, "")
    if message:
        st.success(message)


def _apply_change(
    action: Callable[[], Any],
    *,
    meet_id: str,
    success: str,
) -> bool:
    """Run one storage change and leave actionable failures on screen."""

    try:
        action()
    except (CompCoachError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return False
    st.session_state[f"coach_setup_flash_{meet_id}"] = success
    st.rerun()
    return True


def _render_roster(roster: list[dict[str, Any]]) -> None:
    st.markdown("#### Today's roster")
    if not roster:
        st.info("No coaches have been scheduled for this day yet.")
        return

    present = sum(row.get("presence_status") == "present" for row in roster)
    scheduled = sum(row.get("presence_status") == "scheduled" for row in roster)
    absent = sum(row.get("presence_status") == "absent" for row in roster)
    active_assignments = sum(int(row.get("assignment_count") or 0) for row in roster)
    left, right = st.columns(2)
    left.metric("Present", present)
    right.metric("Active assignments", active_assignments)
    st.caption(f"Scheduled: {scheduled} · Absent: {absent}")

    for row in roster:
        status = str(row.get("presence_status") or "scheduled")
        icon = PRESENCE_ICONS.get(status, "⚪")
        label = PRESENCE_LABELS.get(status, status.title())
        assignments = int(row.get("assignment_count") or 0)
        role_text = ", ".join(
            ROLE_LABELS.get(role, role.title()) for role in row.get("roles") or []
        ) or "No competition role"
        home = str(row.get("home_event_name") or "Any event")
        used = (
            f" · {assignments} active assignment"
            f"{'s' if assignments != 1 else ''}"
            if assignments
            else " · Not assigned yet"
        )
        with st.container(border=True):
            st.markdown(f"**{icon} {row.get('name', 'Coach')}**{used}")
            st.caption(f"{label} · {role_text} · Home: {home}")


def _render_add_coach(
    db: Any,
    *,
    competition_id: str,
    meet_id: str,
    actor: str,
    writable: bool,
) -> None:
    with st.expander("Add coach"):
        with st.form(f"coach_setup_add_{meet_id}"):
            name = st.text_input(
                "Coach name",
                placeholder="First and last name",
                disabled=not writable,
            )
            scope = st.radio(
                "Add to",
                (
                    ["This day · Present", "This competition only", "General directory only"]
                    if competition_id else ["General directory only"]
                ),
                disabled=not writable,
            )
            submitted = st.form_submit_button(
                "Add coach",
                width="stretch",
                disabled=not writable,
            )
        if submitted:
            created: dict[str, Any] = {}

            def create() -> None:
                nonlocal created
                created = db.create_coach(name)
                if scope != "General directory only" and competition_id:
                    existing = next(
                        (
                            row for row in db.list_competition_coaches(competition_id)
                            if row["coach_id"] == created["id"]
                        ),
                        {},
                    )
                    db.set_competition_coach_roles(
                        competition_id,
                        created["id"],
                        list(dict.fromkeys([*existing.get("roles", []), "coach"])),
                    )
                    if scope == "This day · Present":
                        db.set_day_coach_presence(
                            meet_id,
                            created["id"],
                            actor=actor,
                            presence_status="present",
                        )
                # The coach picker has already been rendered in this run.
                # Apply its new selection before rendering it on the next run.
                st.session_state[f"coach_setup_pending_selection_{meet_id}"] = created["id"]

            _apply_change(
                create,
                meet_id=meet_id,
                success=(
                    f"{name.strip()} is present for this day and ready to assign."
                    if scope == "This day · Present"
                    else f"{name.strip()} saved to this competition. Mark Present in Today when needed."
                    if scope == "This competition only"
                    else f"{name.strip()} saved to the general coach directory only."
                ),
            )


def _render_profile_form(
    db: Any,
    *,
    coach: dict[str, Any],
    meet_id: str,
    writable: bool,
) -> None:
    with st.form(f"coach_setup_profile_{meet_id}_{coach['coach_id']}"):
        name = st.text_input(
            "Name",
            value=str(coach.get("name") or ""),
            disabled=not writable,
        )
        active = st.toggle(
            "Active in directory",
            value=bool(coach.get("is_active", True)),
            disabled=not writable,
            help="Inactive coaches remain in history but are clearly marked.",
        )
        submitted = st.form_submit_button(
            "Save coach profile",
            width="stretch",
            disabled=not writable,
        )
    if submitted:
        _apply_change(
            lambda: db.update_coach(coach["coach_id"], name=name, active=active),
            meet_id=meet_id,
            success="Coach profile saved.",
        )


def _render_roles_form(
    db: Any,
    *,
    coach: dict[str, Any],
    competition_id: str,
    meet_id: str,
    writable: bool,
) -> None:
    existing = set(coach.get("roles") or [])
    with st.form(f"coach_setup_roles_{meet_id}_{coach['coach_id']}"):
        st.caption("A person can hold more than one role.")
        as_coach = st.checkbox(
            "Coach",
            value="coach" in existing or not existing,
            disabled=not writable,
        )
        as_coordinator = st.checkbox(
            "Coordinator",
            value="coordinator" in existing,
            disabled=not writable,
        )
        as_admin = st.checkbox(
            "Admin",
            value="admin" in existing,
            disabled=not writable,
        )
        submitted = st.form_submit_button(
            "Save competition roles",
            width="stretch",
            disabled=not writable or not competition_id,
        )
    if submitted:
        roles = [
            role
            for role, selected in (
                ("coach", as_coach),
                ("coordinator", as_coordinator),
                ("admin", as_admin),
            )
            if selected
        ]
        if not roles:
            st.error("Select at least one competition role.")
            return
        _apply_change(
            lambda: db.set_competition_coach_roles(
                competition_id, coach["coach_id"], roles
            ),
            meet_id=meet_id,
            success="Competition roles saved.",
        )


def _render_today_form(
    db: Any,
    *,
    coach: dict[str, Any],
    events: list[dict[str, Any]],
    meet_id: str,
    actor: str,
    writable: bool,
) -> None:
    status_options = ["present", "scheduled", "absent"]
    current_status = str(coach.get("presence_status") or "scheduled")
    status_index = (
        status_options.index(current_status)
        if current_status in status_options
        else 1
    )
    event_ids = ["", *[str(event["id"]) for event in events]]
    event_names = {
        "": "Any event",
        **{str(event["id"]): str(event.get("name") or "Event") for event in events},
    }
    current_home = str(coach.get("home_event_id") or "")
    home_index = event_ids.index(current_home) if current_home in event_ids else 0

    with st.form(f"coach_setup_today_{meet_id}_{coach['coach_id']}"):
        status = st.selectbox(
            "Status today",
            status_options,
            index=status_index,
            format_func=lambda value: PRESENCE_LABELS[value],
            disabled=not writable,
        )
        home_event_id = st.selectbox(
            "Home event (priority only)",
            event_ids,
            index=home_index,
            format_func=lambda value: event_names[value],
            disabled=not writable,
            help=(
                "This only highlights the coach's usual event. The coach can still "
                "be assigned to any other event at any time."
            ),
        )
        submitted = st.form_submit_button(
            "Save today's status",
            width="stretch",
            disabled=not writable or not actor,
        )
    if submitted:
        _apply_change(
            lambda: db.set_day_coach_presence(
                meet_id,
                coach["coach_id"],
                actor=actor,
                presence_status=status,
                home_event_id=home_event_id,
            ),
            meet_id=meet_id,
            success="Today's coach status saved.",
        )


def render_coach_management(
    db: Any,
    meet: dict[str, Any],
    actor: str,
) -> None:
    """Render coach setup for one competition day.

    ``home_event_id`` is passed to storage only as planning metadata.  Nothing
    in this renderer filters assignment eligibility by that field.
    """

    meet_id = str(meet.get("id") or "")
    if not meet_id:
        st.error("Competition day not found.")
        return

    st.subheader("Coach management")
    st.caption(
        "Maintain the directory, competition roles, and who is here today. "
        "A home event is a visual priority—not an assignment restriction."
    )
    _show_flash(meet_id)

    try:
        state = _coach_state(db, meet)
    except (CompCoachError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return

    writable = meet.get("status") == "open" and bool(actor)
    if not writable:
        st.warning("This competition day is read-only.")

    _render_roster(state["roster"])
    st.divider()
    st.markdown("#### Manage one coach")
    coaches = state["coaches"]
    if not coaches:
        st.info("The coach directory is empty. Add the first coach below.")
        _render_add_coach(
            db,
            competition_id=state["competition_id"],
            meet_id=meet_id,
            actor=actor,
            writable=writable,
        )
        return

    coach_by_id = {str(row["coach_id"]): row for row in coaches}
    coach_ids = list(coach_by_id)
    selected_key = f"coach_setup_selected_{meet_id}"
    pending_selection = st.session_state.pop(
        f"coach_setup_pending_selection_{meet_id}", None
    )
    if pending_selection in coach_by_id:
        st.session_state[selected_key] = pending_selection
    if st.session_state.get(selected_key) not in coach_by_id:
        st.session_state[selected_key] = coach_ids[0]
    selected_id = st.selectbox(
        "Coach to manage",
        coach_ids,
        key=selected_key,
        format_func=lambda coach_id: _coach_option(coach_by_id[coach_id]),
    )
    selected = coach_by_id[selected_id]
    section = st.radio(
        "Edit coach",
        ["Today", "Competition roles", "Profile"],
        horizontal=True,
        key=f"coach_setup_section_{meet_id}",
        label_visibility="collapsed",
    )
    if section == "Today":
        _render_today_form(
            db,
            coach=selected,
            events=state["events"],
            meet_id=meet_id,
            actor=actor,
            writable=writable,
        )
    elif section == "Competition roles":
        _render_roles_form(
            db,
            coach=selected,
            competition_id=state["competition_id"],
            meet_id=meet_id,
            writable=writable,
        )
    else:
        _render_profile_form(
            db,
            coach=selected,
            meet_id=meet_id,
            writable=writable,
        )

    _render_add_coach(
        db,
        competition_id=state["competition_id"],
        meet_id=meet_id,
        actor=actor,
        writable=writable,
    )
