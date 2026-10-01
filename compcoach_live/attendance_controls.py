"""Small reversible Pool attendance controls for the mobile coach boards."""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

try:
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - Streamlit's direct script mode
    from storage import CompCoachError


def _notice_key(meet_id: object) -> str:
    return f"pool_attendance_notice_{meet_id}"


def _show_notice(meet_id: object) -> None:
    notice = st.session_state.pop(_notice_key(meet_id), None)
    if notice:
        kind, message = notice
        (st.error if kind == "error" else st.success)(message)


def _refresh_with_notice(meet_id: object, kind: str, message: str) -> None:
    st.session_state[_notice_key(meet_id)] = (kind, message)
    # Coach cards are also rendered inside fragments. Attendance changes both
    # the personal list and the shared board, so refresh the complete app.
    st.rerun(scope="app")


def _writable(event: dict[str, Any], actor: str) -> bool:
    return event.get("status") == "open" and bool(str(actor or "").strip())


def _queue_confirmation(key: str, snapshot: dict[str, Any]) -> None:
    st.session_state[key] = snapshot


def _cancel_confirmation(key: str) -> None:
    st.session_state.pop(key, None)


def render_pool_absence_control(
    db: Any,
    event: dict[str, Any],
    actor: str,
    athlete: dict[str, Any],
    *,
    key_prefix: str,
) -> None:
    """Offer a two-tap absence action on a participating Pool athlete card.

    The first tap captures the displayed revision. A subsequent athlete update
    cancels the confirmation rather than applying it to different information.
    All state written here belongs to non-widget keys.
    """

    athlete_id = str(athlete.get("id") or "")
    meet_id = event.get("meet_id") or event.get("id")
    pending_key = f"{key_prefix}_absence_pending_{athlete_id}"
    _show_notice(meet_id)
    eligible = (
        athlete.get("phase") == "pools"
        and athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
        and _writable(event, actor)
        and bool(athlete_id)
    )
    if not eligible:
        st.session_state.pop(pending_key, None)
        return

    revision = int(athlete["version"])
    snapshot = {
        "version": revision,
        "event_id": str(event["id"]),
        "actor": actor,
    }
    pending = st.session_state.get(pending_key)
    if pending is not None and pending != snapshot:
        st.session_state.pop(pending_key, None)
        pending = None
        st.info("This athlete was updated. Check the latest information before marking absent.")

    suffix = f"{athlete_id}_{revision}"
    if pending is None:
        st.button(
            "Mark absent",
            width="stretch",
            key=f"{key_prefix}_mark_absent_{suffix}",
            on_click=_queue_confirmation,
            args=(pending_key, snapshot),
        )
        return

    st.warning(
        f"Mark {athlete['name']} absent? They will leave the active lists. "
        "You can restore them below."
    )
    confirm_column, cancel_column = st.columns(2)
    with confirm_column:
        confirmed = st.button(
            "Confirm absent",
            width="stretch",
            key=f"{key_prefix}_confirm_absent_{suffix}",
        )
    with cancel_column:
        st.button(
            "Cancel",
            width="stretch",
            key=f"{key_prefix}_cancel_absent_{suffix}",
            on_click=_cancel_confirmation,
            args=(pending_key,),
        )
    if confirmed:
        st.session_state.pop(pending_key, None)
        try:
            db.set_athlete_participation(
                event["id"],
                athlete_id,
                "absent",
                actor,
                expected_version=revision,
            )
        except (CompCoachError, ValueError) as exc:
            _refresh_with_notice(meet_id, "error", str(exc))
        else:
            _refresh_with_notice(
                meet_id,
                "success",
                f"{athlete['name']} marked absent. Saved assignments and results are kept.",
            )


def render_absent_pool_athletes(
    db: Any,
    meet: dict[str, Any],
    actor: str,
    athletes: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
    *,
    key_prefix: str,
) -> None:
    """Keep absent Pool athletes in one collapsed list with safe restoration."""

    meet_id = meet.get("id")
    _show_notice(meet_id)
    absent = sorted(
        (
            row
            for row in athletes
            if row.get("phase") == "pools"
            and row.get("participation_status", "active") == "absent"
        ),
        key=lambda row: (
            str(row.get("event_name") or "").casefold(),
            str(row.get("name") or "").casefold(),
            str(row.get("id") or ""),
        ),
    )
    if not absent:
        return

    with st.expander(f"Absent pool athletes · {len(absent)}", expanded=False):
        for athlete in absent:
            event = event_by_id.get(str(athlete.get("event_id") or ""), {})
            with st.container(border=True):
                st.markdown(f"**{escape(str(athlete.get('name') or 'Athlete'))}** · Absent")
                details = [
                    str(athlete.get("event_name") or event.get("name") or ""),
                    f"Pool {athlete['pool_no']}" if athlete.get("pool_no") else "",
                    str(athlete.get("source_strip") or ""),
                ]
                st.caption(" · ".join(detail for detail in details if detail))
                coaches = [
                    f"Main: {athlete['main_coach']}" if athlete.get("main_coach") else "",
                    f"Side: {athlete['side_coach']}" if athlete.get("side_coach") else "",
                ]
                st.caption(" · ".join(coach for coach in coaches if coach) or "No planned coach")

                closed_day = (
                    meet.get("status") != "open"
                    or meet.get("day_status") == "closed"
                    or bool(meet.get("ended_at"))
                )
                if not _writable(event, actor) or closed_day:
                    continue
                if st.button(
                    "Restore to active list",
                    width="stretch",
                    key=f"{key_prefix}_restore_absent_{athlete['id']}_{athlete['version']}",
                ):
                    try:
                        db.restore_athlete_participation(
                            event["id"],
                            athlete["id"],
                            actor,
                            expected_version=athlete["version"],
                        )
                    except (CompCoachError, ValueError) as exc:
                        _refresh_with_notice(meet_id, "error", str(exc))
                    else:
                        _refresh_with_notice(
                            meet_id,
                            "success",
                            f"{athlete['name']} restored to the active list.",
                        )
