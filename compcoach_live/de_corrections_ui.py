"""Separate, reversible DE result corrections for the shared coach boards."""

from __future__ import annotations

from typing import Any

import streamlit as st

try:
    from compcoach_live.de_bouts import list_de_bouts
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from de_bouts import list_de_bouts
    from storage import CompCoachError


def _queue(key: str, snapshot: dict[str, Any]) -> None:
    st.session_state[key] = snapshot


def _cancel(key: str) -> None:
    st.session_state.pop(key, None)


def _refresh(meet_id: str, kind: str, message: str) -> None:
    st.session_state[f"de_correction_notice_{meet_id}"] = (kind, message)
    st.rerun(scope="app")


def _writable(meet: dict[str, Any], event: dict[str, Any], actor: str) -> bool:
    return (
        meet.get("status") == "open"
        and meet.get("day_status", "active") == "active"
        and not meet.get("ended_at")
        and event.get("status") == "open"
        and bool(str(actor or "").strip())
    )


def _rounds_text(athlete: dict[str, Any]) -> str:
    wins = int(athlete.get("de_wins") or 0)
    byes = int(athlete.get("de_byes") or 0)
    rounds = wins + byes
    return (
        f"{rounds} round{'s' if rounds != 1 else ''} passed"
        f" · {wins} win{'s' if wins != 1 else ''}"
        f" · {byes} bye{'s' if byes != 1 else ''}"
    )


def _paired_result_ids(bouts: list[dict[str, Any]]) -> set[str]:
    """Keep current paired outcomes out of individual result corrections.

    A later individual result has a different timestamp and remains eligible.
    The storage layer independently checks the result audit and its revision.
    """
    paired: set[str] = set()
    for bout in bouts:
        if bout.get("status") == "pending":
            paired.update((str(bout["athlete_a_id"]), str(bout["athlete_b_id"])))
        elif bout.get("status") == "resolved" and bout.get("resolved_at"):
            for side in ("a", "b"):
                athlete = bout.get(f"athlete_{side}") or {}
                if athlete.get("last_de_result_at") == bout["resolved_at"]:
                    paired.add(str(bout[f"athlete_{side}_id"]))
    return paired


def render_de_result_corrections(
    db: Any,
    meet: dict[str, Any],
    actor: str,
    athletes: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
    *,
    key_prefix: str,
) -> None:
    """Offer deliberate corrections away from the one-tap Won/Lost buttons.

    Corrections use the selected athlete's displayed revision. They never
    change planned coaches or a newer live call. AFM outcomes are corrected
    for both athletes together in the same collapsed section.
    """
    meet_id = str(meet["id"])
    notice = st.session_state.pop(f"de_correction_notice_{meet_id}", None)
    if notice:
        (st.error if notice[0] == "error" else st.success)(notice[1])

    de_events = {
        str(row["event_id"]) for row in athletes
        if row.get("phase") == "de" and str(row.get("event_id") or "") in event_by_id
    }
    bouts_by_event = {
        event_id: list_de_bouts(db, event_id, include_cancelled=True)
        for event_id in de_events
    }
    paired_ids = _paired_result_ids(
        [bout for bouts in bouts_by_event.values() for bout in bouts]
    )
    candidates = {
        str(row["id"]): row
        for row in sorted(
            athletes,
            key=lambda row: (
                str(row.get("event_name") or "").casefold(),
                str(row.get("name") or "").casefold(),
                str(row.get("id") or ""),
            ),
        )
        if row.get("phase") == "de"
        and str(row.get("event_id") or "") in event_by_id
        and str(row.get("id") or "") not in paired_ids
        and (
            row.get("last_de_result") in {"won", "lost", "bye"}
            or row.get("active_state") == "eliminated"
        )
    }
    joint_events = [
        event_by_id[event_id]
        for event_id, bouts in bouts_by_event.items()
        if any(bout.get("status") in {"resolved", "cancelled"} for bout in bouts)
    ]
    pending_key = f"{key_prefix}_de_correction_pending_{meet_id}"
    if not candidates and not joint_events:
        _cancel(pending_key)
        return

    with st.expander("Correct DE results", expanded=False):
        st.caption(
            "Choose an athlete to undo the latest win, loss or bye. "
            "AFM vs AFM results are corrected for both athletes together below."
        )
        if candidates:
            options = ["", *candidates]
            selection_key = f"{key_prefix}_de_correction_athlete_{meet_id}"
            if st.session_state.get(selection_key, "") not in options:
                st.session_state[selection_key] = ""

            def label(athlete_id: str) -> str:
                if not athlete_id:
                    return "Select an athlete"
                athlete = candidates[athlete_id]
                event = event_by_id[str(athlete["event_id"])]
                result = str(athlete.get("last_de_result") or "out").title()
                return (
                    f"{athlete.get('name') or 'Athlete'} · "
                    f"{athlete.get('event_name') or event.get('name') or 'Event'} · "
                    f"Last: {result} · {_rounds_text(athlete)}"
                )

            selected = st.selectbox(
                "Athlete to correct", options, format_func=label, key=selection_key,
            )
            athlete = candidates.get(selected)
            if athlete:
                event = event_by_id[str(athlete["event_id"])]
                revision = int(athlete["version"])
                result = str(athlete.get("last_de_result") or "")
                imported_out = result not in {"won", "lost", "bye"}
                snapshot = {
                    "athlete_id": str(athlete["id"]),
                    "event_id": str(event["id"]),
                    "version": revision,
                    "result": result,
                    "actor": actor,
                    "restore_out": imported_out,
                }
                pending = st.session_state.get(pending_key)
                if pending is not None and pending != snapshot:
                    _cancel(pending_key)
                    pending = None
                    st.info("This selection was updated. Review the latest result before correcting it.")
                st.caption(_rounds_text(athlete))
                if athlete.get("participation_status", "active") != "active":
                    st.caption("Attendance is unchanged by a result correction.")
                if not _writable(meet, event, actor):
                    _cancel(pending_key)
                    st.caption("Results are read-only while this day or event is closed or paused.")
                elif pending:
                    action_text = "Restore" if imported_out else "Undo the latest result for"
                    preservation = "Saved assignments are kept." if imported_out else "Saved assignments and newer live notes are kept."
                    st.warning(f"{action_text} {athlete['name']}? {preservation}")
                    left, right = st.columns(2)
                    with left:
                        confirmed = st.button(
                            "Confirm restore" if imported_out else "Confirm undo",
                            key=f"{key_prefix}_de_correction_confirm_{selected}_{revision}",
                            width="stretch",
                        )
                    with right:
                        st.button(
                            "Cancel", key=f"{key_prefix}_de_correction_cancel_{selected}_{revision}",
                            width="stretch", on_click=_cancel, args=(pending_key,),
                        )
                    if confirmed:
                        _cancel(pending_key)
                        try:
                            if imported_out:
                                db.restore_athlete(event["id"], selected, actor, expected_version=revision)
                            else:
                                db.correct_de_result(event["id"], selected, actor, expected_version=revision)
                        except (CompCoachError, ValueError) as exc:
                            _refresh(meet_id, "error", str(exc))
                        else:
                            _refresh(meet_id, "success", f"{athlete['name']}: result corrected.")
                else:
                    st.button(
                        "Restore to active list" if imported_out else "Undo last result",
                        key=f"{key_prefix}_de_correction_start_{selected}_{revision}",
                        width="stretch", on_click=_queue, args=(pending_key, snapshot),
                    )
            else:
                _cancel(pending_key)
        else:
            _cancel(pending_key)
            st.caption("Paired results must be corrected together below.")

        if joint_events:
            try:
                from compcoach_live.de_bout_controls import render_de_bout_corrections
            except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
                from de_bout_controls import render_de_bout_corrections
            for event in sorted(joint_events, key=lambda row: str(row.get("name") or "").casefold()):
                writable = _writable(meet, event, actor)
                # The joint controls use the event lifecycle guard too. Apply
                # the parent day's guard without altering the stored event.
                shown_event = event if writable else {**event, "status": "read_only"}
                render_de_bout_corrections(
                    db, shown_event, actor,
                    key_prefix=f"{key_prefix}_joint_correction",
                )
