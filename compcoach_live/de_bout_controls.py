"""Compact staff controls for manually reported AFM vs AFM DE bouts."""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

try:
    from compcoach_live.de_bouts import (
        cancel_de_bout, create_de_bout, list_de_bouts, resolve_de_bout,
        restore_de_bout_pairing, undo_de_bout_result,
    )
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from de_bouts import (
        cancel_de_bout, create_de_bout, list_de_bouts, resolve_de_bout,
        restore_de_bout_pairing, undo_de_bout_result,
    )
    from storage import CompCoachError


def _queue(key: str, snapshot: dict) -> None:
    st.session_state[key] = snapshot


def _cancel(key: str) -> None:
    st.session_state.pop(key, None)


def _refresh(event_id: str, kind: str, message: str) -> None:
    st.session_state[f"de_bout_notice_{event_id}"] = (kind, message)
    st.rerun(scope="app")


def _apply(event_id: str, action: Any, message: str) -> None:
    try:
        action()
    except (CompCoachError, ValueError) as exc:
        _refresh(event_id, "error", str(exc))
    else:
        _refresh(event_id, "success", message)


def _consume_result(db: Any, event: dict, prefix: str) -> None:
    """Save the snapshot from the rendered button before reading a fresh board."""
    pending = st.session_state.pop(f"{prefix}_result_{event['id']}", None)
    if pending is None:
        return
    _apply(event["id"], lambda: resolve_de_bout(
        db, event["id"], pending["bout_id"], winner_id=pending["winner_id"], actor=pending["actor"],
        expected_version=pending["bout_version"], expected_a_version=pending["a_version"],
        expected_b_version=pending["b_version"],
    ), "Both results saved: winner advances, other athlete out.")


def _active_de(athlete: dict) -> bool:
    return (
        athlete.get("phase") == "de" and athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
    )


def render_athlete_bout_badge(db: Any, event_id: str, athlete: dict, *, bouts: list[dict] | None = None) -> None:
    """Expose the opponent on every current athlete card without extra controls."""
    if athlete.get("phase") != "de":
        return
    for bout in bouts if bouts is not None else list_de_bouts(db, event_id):
        if bout["status"] != "pending" or athlete["id"] not in {bout["athlete_a_id"], bout["athlete_b_id"]}:
            continue
        opponent = bout["athlete_b"] if athlete["id"] == bout["athlete_a_id"] else bout["athlete_a"]
        label = " · " + str(bout["round_label"]) if bout["round_label"] else ""
        st.markdown(
            '<div class="cc-afm-bout-badge">⚔ AFM vs AFM · '
            + escape(str(opponent["name"])) + escape(label) + "</div>",
            unsafe_allow_html=True,
        )
        return


def _render_create(db: Any, event: dict, actor: str, bouts: list[dict], prefix: str) -> None:
    event_id = event["id"]
    occupied = {
        athlete_id for bout in bouts if bout["status"] == "pending"
        for athlete_id in (bout["athlete_a_id"], bout["athlete_b_id"])
    }
    candidates = {
        athlete["id"]: athlete for athlete in sorted(db.list_athletes(event_id), key=lambda a: a["name"].casefold())
        if _active_de(athlete) and athlete["id"] not in occupied
    }
    pending_key = f"{prefix}_create_pending_{event_id}"
    pending = st.session_state.get(pending_key)
    if pending:
        current = [candidates.get(pending["a"]), candidates.get(pending["b"])]
        if any(a is None for a in current) or [int(a["version"]) for a in current] != pending["versions"]:
            _cancel(pending_key)
            pending = None
            st.info("The athletes changed. Select the pairing again after reviewing the latest information.")
    with st.expander("Mark an AFM bout", expanded=bool(pending)):
        if pending:
            a, b = candidates[pending["a"]], candidates[pending["b"]]
            label = f" · {pending['round']}" if pending["round"] else ""
            st.warning(f"Pair {a['name']} vs {b['name']}{label}? Results will be recorded for both together.")
            left, right = st.columns(2)
            with left:
                confirm = st.button("Confirm pairing",key=f"{prefix}_create_confirm_{event_id}",width="stretch")
            with right:
                st.button("Cancel",key=f"{prefix}_create_cancel_{event_id}",width="stretch",on_click=_cancel,args=(pending_key,))
            if confirm:
                _cancel(pending_key)
                _apply(event_id,lambda:create_de_bout(
                    db,event_id,pending["a"],pending["b"],actor=actor,round_label=pending["round"],
                    expected_a_version=pending["versions"][0],expected_b_version=pending["versions"][1],
                ),"AFM vs AFM pairing saved. Every coach can see the opponent.")
            return
        if len(candidates) < 2:
            st.caption("At least two unpaired active DE athletes are needed.")
            return
        with st.form(f"{prefix}_create_form_{event_id}"):
            options = ["",*candidates]
            # A just-paired athlete is excluded from the next pairing. Reset
            # stale selections before the widgets are instantiated again.
            for selection in ("first", "second"):
                widget_key = f"{prefix}_{selection}_{event_id}"
                if st.session_state.get(widget_key, "") not in options:
                    st.session_state[widget_key] = ""
            format_name = lambda value: "Select an athlete" if not value else candidates[value]["name"]
            a_id = st.selectbox("First athlete",options,format_func=format_name,key=f"{prefix}_first_{event_id}")
            b_id = st.selectbox("Second athlete",options,format_func=format_name,key=f"{prefix}_second_{event_id}")
            round_label = st.text_input("Round (optional)",placeholder="T64, T32, semifinal…",max_chars=40,key=f"{prefix}_round_{event_id}")
            review = st.form_submit_button("Review pairing",width="stretch")
        if review:
            if not a_id or not b_id or a_id == b_id:
                st.error("Select two different AFM athletes.")
            else:
                _queue(pending_key,{
                    "a":a_id,"b":b_id,"round":round_label.strip(),
                    "versions":[int(candidates[a_id]["version"]),int(candidates[b_id]["version"])],
                })
                st.rerun(scope="app")


def _render_bout(db: Any,event: dict,actor: str,bout: dict,prefix: str,writable: bool,*,allow_corrections: bool = False) -> None:
    event_id,bout_id = event["id"],bout["id"]
    a,b = bout["athlete_a"],bout["athlete_b"]
    suffix = f"{prefix}_{bout_id}_{bout['version']}"
    key = f"{prefix}_action_pending_{bout_id}"
    base = {"bout_version":int(bout["version"]),"a_version":int(a["version"]),"b_version":int(b["version"]),"actor":actor}
    pending = st.session_state.get(key)
    if pending and pending.get("action") == "resolve":
        _cancel(key)
        pending = None
    if pending and any(pending.get(field) != value for field,value in base.items()):
        _cancel(key)
        pending = None
        st.info("This bout or an athlete changed. Review both before confirming.")
    with st.container(border=True):
        st.markdown(f"**{a['name']} vs {b['name']}**")
        if bout["round_label"]:
            st.caption(f"AFM vs AFM · {bout['round_label']}")
        if bout["status"] == "resolved":
            winner,loser = (a,b) if bout["winner_id"] == a["id"] else (b,a)
            st.success(f"{winner['name']} won · {loser['name']} out")
        elif bout["status"] == "cancelled":
            st.caption("Pairing removed · Athlete results unchanged")
        elif not (_active_de(a) and _active_de(b)):
            st.warning("One athlete is no longer active in DE. Review or remove this pairing.")
        if not writable:
            _cancel(key)
            return
        if pending:
            action = pending["action"]
            if action == "undo":
                st.warning("Undo both results and return this AFM bout to pending?")
                label = "Confirm undo both"
            else:
                st.warning("Remove this pairing? Athlete results and assignments will stay as they are.")
                label = "Confirm removal"
            left,right = st.columns(2)
            with left:
                confirmed = st.button(label,key=f"{suffix}_confirm",width="stretch")
            with right:
                st.button("Cancel",key=f"{suffix}_cancel",width="stretch",on_click=_cancel,args=(key,))
            if confirmed:
                _cancel(key)
                if action == "undo":
                    _apply(event_id,lambda:undo_de_bout_result(db,event_id,bout_id,actor=actor,expected_version=pending["bout_version"]),"Both results restored. The AFM bout is pending again.")
                else:
                    _apply(event_id,lambda:cancel_de_bout(db,event_id,bout_id,actor=actor,expected_version=pending["bout_version"]),"Pairing removed. Athlete results unchanged.")
            return
        if bout["status"] == "pending":
            if _active_de(a) and _active_de(b):
                left,right = st.columns(2)
                result_key = f"{prefix}_result_{event_id}"
                with left:
                    st.button(f"{a['name']} wins",key=f"{suffix}_a_wins",width="stretch",on_click=_queue,
                              args=(result_key,{**base,"bout_id":bout_id,"winner_id":a["id"]}))
                with right:
                    st.button(f"{b['name']} wins",key=f"{suffix}_b_wins",width="stretch",on_click=_queue,
                              args=(result_key,{**base,"bout_id":bout_id,"winner_id":b["id"]}))
            st.button("Remove pairing",key=f"{suffix}_remove",width="stretch",on_click=_queue,args=(key,{**base,"action":"remove"}))
        elif bout["status"] == "resolved" and allow_corrections:
            st.button("Undo both results",key=f"{suffix}_undo",width="stretch",on_click=_queue,args=(key,{**base,"action":"undo"}))
        elif bout["status"] == "cancelled" and allow_corrections:
            if st.button("Restore pairing",key=f"{suffix}_restore",width="stretch"):
                _apply(event_id,lambda:restore_de_bout_pairing(db,event_id,bout_id,actor=actor,expected_version=bout["version"]),"Pairing restored. Athlete results unchanged.")


def render_de_bouts(db: Any,event: dict,actor: str,*,key_prefix: str = "de_bouts",allow_create: bool = True,allow_corrections: bool = False) -> None:
    """Shared DE section, available to coaches, coordinators and administrators."""
    event_id = event["id"]
    _consume_result(db,event,key_prefix)
    notice = st.session_state.pop(f"de_bout_notice_{event_id}",None)
    if notice:
        (st.error if notice[0] == "error" else st.success)(notice[1])
    bouts = list_de_bouts(db,event_id,include_cancelled=True)
    pending = [bout for bout in bouts if bout["status"] == "pending"]
    previous = [bout for bout in bouts if bout["status"] != "pending"]
    writable = event.get("status") == "open" and bool(str(actor or "").strip())
    with st.expander(f"AFM vs AFM · {event.get('name', 'Event')} · {len(pending)} pending",expanded=bool(pending)):
        st.caption("Mark only the AFM pairings you have checked. No bracket or score is required.")
        for bout in pending:
            _render_bout(db,event,actor,bout,key_prefix,writable)
        if writable and allow_create:
            _render_create(db,event,actor,bouts,key_prefix)
        if previous and allow_corrections:
            with st.expander(f"Previous AFM bouts · {len(previous)}",expanded=False):
                for bout in previous:
                    _render_bout(db,event,actor,bout,key_prefix,writable,allow_corrections=True)


def render_de_bout_corrections(db: Any,event: dict,actor: str,*,key_prefix: str = "de_bout_corrections") -> None:
    """Previous paired outcomes are corrected outside the hurried live board."""
    event_id = event["id"]
    notice = st.session_state.pop(f"de_bout_notice_{event_id}",None)
    if notice:
        (st.error if notice[0] == "error" else st.success)(notice[1])
    previous = [bout for bout in list_de_bouts(db,event_id,include_cancelled=True) if bout["status"] != "pending"]
    if not previous:
        return
    writable = event.get("status") == "open" and bool(str(actor or "").strip())
    with st.expander(f"AFM bout corrections · {event.get('name', 'Event')} · {len(previous)}",expanded=False):
        st.caption("A paired result is corrected for both athletes together. Current assignments and field information are kept.")
        for bout in previous:
            _render_bout(db,event,actor,bout,key_prefix,writable,allow_corrections=True)
