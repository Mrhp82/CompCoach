"""Shared live coverage and one-tap acceptance of additional athletes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import streamlit as st

try:
    from compcoach_live.parsers import import_name_noise_reason
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct Streamlit script mode
    from parsers import import_name_noise_reason
    from storage import CompCoachError


CALL_LABELS = {
    "now": "Right now",
    "on_deck": "On deck",
    "in_hole": "In the hole",
    "waiting": "Not called yet",
}
STALE_MINUTES = {"now": 15, "on_deck": 25, "in_hole": 35}


def _moment(value: object) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _minutes(value: object, now: datetime) -> int | None:
    moment = _moment(value)
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds() // 60))


def _elapsed(minutes: int | None) -> str:
    if minutes is None:
        return "Start time unavailable"
    if minutes == 0:
        return "just started"
    if minutes == 1:
        return "1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining = divmod(minutes, 60)
    return f"{hours}h {remaining}m" if remaining else f"{hours}h"


def _coaches(row: Mapping[str, Any]) -> list[str]:
    """Read all equal DE peers while keeping legacy Pool assignments."""
    values: object = None
    if row.get("phase") == "de":
        values = row.get("de_coaches", row.get("coaches"))
        if values is None:
            raw = row.get("de_coaches_json", row.get("coaches_json"))
            try:
                values = json.loads(raw) if raw is not None else None
            except (TypeError, ValueError):
                pass
    if not isinstance(values, (list, tuple)):
        values = [row.get("main_coach"), row.get("side_coach")]
    return list(dict.fromkeys(str(name).strip() for name in values if name and str(name).strip()))


def _actual_strip(row: Mapping[str, Any]) -> str:
    # A DE source strip is the pod's call point, never the actual bout strip.
    strip = str(row.get("live_location") or "").strip()
    if not strip and row.get("phase") != "de":
        strip = str(row.get("source_strip") or "").strip()
    return f"Actual strip {strip}" if strip else "Actual strip TBD"


def _live_row(row: Mapping[str, Any], event_by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    event_id = str(row.get("event_id") or "")
    event = event_by_id.get(event_id, {})
    return (
        row.get("active_state", "active") == "active"
        and row.get("participation_status", "active") == "active"
        and event.get("status", "open") == "open"
        and bool(str(row.get("name") or "").strip())
        and not import_name_noise_reason(row.get("name", ""))
        and not (
            row.get("phase") == "pools"
            and row.get("pool_wins") is not None
            and row.get("pool_losses") is not None
            and not row.get("covered_by")
            and row.get("call_status", "waiting") == "waiting"
            and not row.get("help_requested_at")
        )
    )


def _details(row: Mapping[str, Any], event_by_id: Mapping[str, Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    event_id = str(row.get("event_id") or "")
    event = event_by_id.get(event_id, {})
    status = str(row.get("call_status") or "waiting")
    call_minutes = _minutes(row.get("reported_at"), now)
    stale = bool(status in STALE_MINUTES and call_minutes is not None and call_minutes >= STALE_MINUTES[status])
    pod = str(row.get("pod") or "").strip()
    return {
        "athlete_id": str(row.get("id") or ""),
        "version": int(row.get("version") or 0),
        "takeover_coach": str(row.get("takeover_coach") or ""),
        "takeover_at": row.get("takeover_at"),
        "athlete_name": str(row.get("name") or "Athlete"),
        "event_id": event_id,
        "event_name": str(row.get("event_name") or event.get("name") or "Event"),
        "actual_strip": _actual_strip(row),
        "pod": pod,
        "call_status": status,
        "call_label": CALL_LABELS.get(status, "Not called yet"),
        "call_minutes": call_minutes,
        "stale": stale,
        "fresh_call": bool(status in STALE_MINUTES and not stale),
    }


def build_busy_coach_board(
    athletes: Iterable[Mapping[str, Any]],
    event_by_id: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Project live coverage without treating a plan as actual coverage.

    A coach is busy only when explicitly with an active athlete. Their other
    planned athletes remain visible across events, including before a call.
    Another planned peer who is not currently busy is reported separately;
    this does not claim that the peer has explicitly declared availability.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = [dict(row) for row in athletes if _live_row(row, event_by_id)]
    occupied: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        coach = str(row.get("covered_by") or "").strip()
        if not coach:
            continue
        details = _details(row, event_by_id, now)
        minutes = _minutes(row.get("covered_at"), now)
        details.update(coach=coach, busy_minutes=minutes, busy_elapsed=_elapsed(minutes))
        occupied.setdefault(coach, []).append(details)

    busy = sorted(
        [item for items in occupied.values() for item in items],
        key=lambda item: (item["coach"].casefold(), item["athlete_name"].casefold(), item["athlete_id"]),
    )
    needs_cover = []
    accepted_cover = []
    for row in rows:
        # Already covered by a different coach is an effective resolution.
        if row.get("covered_by"):
            continue
        planned = _coaches(row)
        owner = str(row.get("takeover_coach") or "")
        if owner and owner not in occupied:
            details = _details(row, event_by_id, now)
            details["accepted_elapsed"] = _elapsed(_minutes(row.get("takeover_at"), now))
            accepted_cover.append(details)
            continue
        busy_coaches = [coach for coach in dict.fromkeys([*planned, owner]) if coach and coach in occupied]
        if not busy_coaches:
            continue
        peers = [coach for coach in planned if coach not in occupied]
        details = _details(row, event_by_id, now)
        details.update(
            busy_coaches=busy_coaches,
            busy_assignments=[item for coach in busy_coaches for item in occupied[coach]],
            unoccupied_peers=peers,
            needs_alternate=not bool(peers),
            urgent=bool(details["fresh_call"] and not peers),
        )
        needs_cover.append(details)
    priority = {"now": 0, "on_deck": 1, "in_hole": 2, "waiting": 3}
    needs_cover.sort(key=lambda item: (
        0 if item["urgent"] else 1,
        4 if item["stale"] else priority.get(item["call_status"], 3),
        item["event_name"].casefold(), item["athlete_name"].casefold(), item["athlete_id"],
    ))
    return {"busy": busy, "needs_cover": needs_cover, "accepted_cover": accepted_cover}


def _place_text(item: Mapping[str, Any]) -> str:
    parts = [item["actual_strip"]]
    if item.get("pod"):
        parts.append(f"Pod {item['pod']}")
    parts.append(item["event_name"])
    return " · ".join(parts)


def _takeover_notice(success: bool, message: str) -> None:
    st.session_state["de_fast_notice"] = (success, message)
    st.session_state["de_fast_refresh"] = True


def _take_over_snapshot(db, item, actor, coach_version):
    try:
        db.take_over_athlete(item["event_id"], item["athlete_id"], actor, actor,
                             expected_version=item["version"], expected_coach_version=coach_version)
    except (CompCoachError, ValueError) as exc:
        _takeover_notice(False, str(exc))
    else:
        _takeover_notice(True, f"{item['athlete_name']}: taken by {actor}.")


def _release_takeover_snapshot(db, item, actor):
    try:
        db.release_takeover(item["event_id"], item["athlete_id"], item["takeover_coach"], actor,
                            expected_version=item["version"])
    except (CompCoachError, ValueError) as exc:
        _takeover_notice(False, str(exc))
    else:
        _takeover_notice(True, f"{item['athlete_name']}: takeover released.")


def _markdown_text(value):
    """Keep imported names and notes literal inside alert Markdown."""
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~\-])", r"\\\1", str(value).replace("\n", " "))


def render_busy_coach_board(db, meet, event_by_id, athletes, role, actor, *, key_prefix):
    projection = build_busy_coach_board(athletes, event_by_id)
    if not any(projection.values()):
        return
    writable = bool(db and actor and meet.get("status", "open") == "open"
                    and meet.get("day_status", "active") == "active" and not meet.get("ended_at"))
    is_coach = actor in meet.get("active_coaches", [])
    states = db.list_coach_availability(meet["id"]) if writable and is_coach else []
    own_state = next((row for row in states if row["coach_name"] == actor), {})
    actor_busy = bool(own_state.get("is_busy"))

    def action(item):
        if item.get("takeover_coach"):
            if writable and (item["takeover_coach"] == actor or role in {"admin", "coordinator"}):
                st.button("Release takeover", key=f"{key_prefix}_release_takeover_{item['athlete_id']}",
                          width="stretch", on_click=_release_takeover_snapshot, args=(db, item, actor))
        elif writable and is_coach:
            st.button("I’ll take over", key=f"{key_prefix}_takeover_{item['athlete_id']}",
                      type="primary", width="stretch", disabled=actor_busy,
                      on_click=_take_over_snapshot,
                      args=(db, item, actor, int(own_state.get("version") or 0)))
            if actor_busy:
                st.caption("You are busy with another athlete.")

    with st.container(border=True):
        if projection["busy"]:
            st.markdown("**Coaches busy now**")
        for item in projection["busy"]:
            st.markdown(f"🔴 **{item['coach']}** · with **{item['athlete_name']}** · {item['busy_elapsed']}")
            st.caption(_place_text(item))
        if projection["needs_cover"]:
            st.markdown(f"**Other athletes needing cover · {len(projection['needs_cover'])}**")
        for item in projection["needs_cover"]:
            label = item["call_label"]
            if item["stale"]:
                label += " · previous call — update needed"
            assignments = "; ".join(f"{busy['coach']} with {busy['athlete_name']} · {busy['actual_strip']}"
                                    for busy in item["busy_assignments"])
            note = f"Assigned coach busy: {assignments}."
            if item.get("takeover_coach"):
                note += f" Taken by {item['takeover_coach']}, who is now busy elsewhere."
            if item["unoccupied_peers"]:
                note += " Other planned coach not marked busy: " + " / ".join(item["unoccupied_peers"]) + "."
            elif item["call_status"] == "waiting":
                note += " Alternate coverage needed if called."
            else:
                note += " Alternate coverage needed."
            text = (f"## {_markdown_text(item['athlete_name'])}\n\n"
                    f"**{_markdown_text(label)} · {_markdown_text(_place_text(item))}**\n\n"
                    f":small[{_markdown_text(note)}]")
            left, right = st.columns([4, 2], vertical_alignment="center")
            with left:
                with st.container(key=f"cc_uncovered_{key_prefix}_{item['athlete_id']}"):
                    (st.warning if item["urgent"] else st.info)(text)
            with right:
                action(item)
        if projection["accepted_cover"]:
            st.markdown(f"**Coverage accepted · {len(projection['accepted_cover'])}**")
        for item in projection["accepted_cover"]:
            left, right = st.columns([4, 2], vertical_alignment="center")
            with left:
                st.success(f"{item['athlete_name']} · Taken by {item['takeover_coach']} · {item['accepted_elapsed']}")
                st.caption(f"{item['call_label']} · {_place_text(item)}")
            with right:
                action(item)
