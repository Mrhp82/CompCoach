"""CompCoach Live — mobile-first AFM coach coordination.

Run from the repository root with:

    streamlit run compcoach_live/app.py
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from functools import wraps
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

try:
    from compcoach_live.asset_store import AssetStoreError
    from compcoach_live.assignment_notices import assignment_notice_details
    from compcoach_live.attendance_controls import (
        render_absent_pool_athletes,
        render_pool_absence_control,
    )
    from compcoach_live.backend_config import (
        SETTING_NAMES,
        create_asset_store,
        create_database,
    )
    from compcoach_live.coach_setup import render_coach_management
    from compcoach_live.coach_picker import canonical_coach_names, setup_coach_options
    from compcoach_live.de_assignment_ui import (
        render_de_individual_assignments,
        render_de_pod_assignments,
    )
    from compcoach_live.de_bout_controls import (
        render_de_bouts, render_athlete_bout_badge, render_athlete_pairing_control,
    )
    from compcoach_live.de_bouts import list_de_bouts
    from compcoach_live.competition_setup import (
        render_competition_branding,
        render_competition_profile,
    )
    from compcoach_live.ocr_import import parse_screenshot
    from compcoach_live.parsers import parse_pasted_table, import_name_noise_reason
    from compcoach_live.import_cleanup import quarantine_import_noise
    from compcoach_live.live_controls import render_live_controls, render_pool_takeover_arrival
    from compcoach_live.missed_coaching_ui import (
        render_missed_coaching_control,
        render_missed_coaching_review,
    )
    from compcoach_live.busy_board import render_busy_coach_board
    from compcoach_live.de_rotation import ordered_de_athletes
    from compcoach_live.de_progress import describe_de_progress
    from compcoach_live.team_groups import group_team_athletes_by_coach
    from compcoach_live.de_corrections_ui import render_de_result_corrections
    from compcoach_live.schedule_setup import render_competition_schedule
    from compcoach_live.training import get_training, join_training, list_training_sessions, tick_training
    from compcoach_live.training_ui import render_training_panel, render_training_start
    from compcoach_live.read_cache import RenderReads
    from compcoach_live.result_styles import apply_result_styles
    from compcoach_live.refresh import training_hub_status, training_needs_tick, training_visible_revision
    from compcoach_live.storage import (
        NO_CHANGE,
        CompCoachDB,
        CompCoachError,
        ConcurrentUpdateError,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from asset_store import AssetStoreError
    from assignment_notices import assignment_notice_details
    from attendance_controls import (
        render_absent_pool_athletes,
        render_pool_absence_control,
    )
    from backend_config import SETTING_NAMES, create_asset_store, create_database
    from coach_setup import render_coach_management
    from coach_picker import canonical_coach_names, setup_coach_options
    from de_assignment_ui import render_de_individual_assignments, render_de_pod_assignments
    from de_bout_controls import (
        render_de_bouts, render_athlete_bout_badge, render_athlete_pairing_control,
    )
    from de_bouts import list_de_bouts
    from competition_setup import (
        render_competition_branding,
        render_competition_profile,
    )
    from ocr_import import parse_screenshot
    from parsers import parse_pasted_table, import_name_noise_reason
    from import_cleanup import quarantine_import_noise
    from live_controls import render_live_controls, render_pool_takeover_arrival
    from missed_coaching_ui import (
        render_missed_coaching_control,
        render_missed_coaching_review,
    )
    from busy_board import render_busy_coach_board
    from de_rotation import ordered_de_athletes
    from de_progress import describe_de_progress
    from team_groups import group_team_athletes_by_coach
    from de_corrections_ui import render_de_result_corrections
    from schedule_setup import render_competition_schedule
    from training import get_training, join_training, list_training_sessions, tick_training
    from training_ui import render_training_panel, render_training_start
    from read_cache import RenderReads
    from result_styles import apply_result_styles
    from refresh import training_hub_status, training_needs_tick, training_visible_revision
    from storage import (
        NO_CHANGE,
        CompCoachDB,
        CompCoachError,
        ConcurrentUpdateError,
    )


APP_VERSION = "0.10.8"
DEFAULT_COACHES = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]
DEFAULT_COORDINATORS = ["Irina"]
TIMEZONES = [
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
]

CALL_LABELS = {
    "waiting": "Waiting",
    "in_hole": "In the Hole",
    "on_deck": "On Deck",
    "now": "Now",
}
CALL_ICONS = {"waiting": "⚪", "in_hole": "🟡", "on_deck": "🟠", "now": "🔴"}
STALE_MINUTES = {"now": 15, "on_deck": 25, "in_hole": 35}


st.set_page_config(
    page_title="CompCoach Live",
    page_icon="🤺",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      [data-testid="stSidebar"], #MainMenu, footer {display:none !important;}
      .block-container {
        max-width: 760px;
        padding: calc(4rem + env(safe-area-inset-top)) .75rem 5rem;
      }
      h1 {font-size:1.7rem !important; margin-bottom:.15rem !important;}
      h2 {font-size:1.25rem !important;}
      h3 {font-size:1.05rem !important;}
      div.stButton > button, div.stLinkButton > a {
        min-height: 46px; border-radius: 12px; font-weight: 700;
      }
      [data-testid="stButtonGroup"] button {min-height:46px;}
      [data-testid="stForm"] {border-radius:16px;}
      [data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px;}
      .cc-topline {display:flex; justify-content:space-between; align-items:center; gap:.5rem;}
      .cc-role {font-size:.72rem; font-weight:800; letter-spacing:.04em; padding:.22rem .5rem;
                border-radius:999px; background:#eef2ff; color:#303f9f; white-space:nowrap;}
      .cc-identity {background:#f8fafc; border:1px solid #d0d5dd; border-radius:14px;
                    min-height:46px; padding:.65rem .8rem; display:flex; align-items:center;
                    font-weight:800; color:#344054;}
      .cc-identity-hint {font-size:.86rem; color:#667085; margin-bottom:.65rem;}
      .cc-summary {background:#f5f7fb; border-radius:14px; padding:.7rem .8rem;
                   margin:.35rem 0 .7rem; font-weight:650; text-align:center;}
      .cc-athlete {font-size:1.07rem; font-weight:800; margin-bottom:.12rem;}
      .cc-meta {font-size:.88rem; color:#5c6470; line-height:1.35;}
      .cc-now {color:#b42318; font-weight:850;}
      .cc-deck {color:#b54708; font-weight:850;}
      .cc-hole {color:#8a6d00; font-weight:850;}
      .cc-covered {color:#087443; font-weight:850;}
      .cc-stale {color:#7a271a; font-weight:800;}
      .cc-help {color:#b42318; font-weight:850;}
      .cc-roleline {font-size:.82rem; font-weight:750; color:#475467; margin:.1rem 0 .35rem;}
      .cc-result {font-size:.9rem; font-weight:800; color:#344054; margin:.25rem 0;}
      .cc-afm-bout-badge {font-size:.85rem; font-weight:750; color:#6941c6;
                           background:#f4f3ff; border:1px solid #d9d6fe; border-radius:10px;
                           padding:.4rem .55rem; margin:.3rem 0; overflow-wrap:anywhere;}
      .cc-rounds {font-size:.8rem; font-weight:700; color:#475467;}
      .cc-phasebar {display:grid; grid-template-columns:1fr 1fr; gap:.55rem; margin:.35rem 0 .65rem;}
      .cc-phase {border:1px solid; border-radius:14px; padding:.62rem .7rem; min-width:0;}
      .cc-phase-live {background:#ecfdf3; border-color:#75e0a7; color:#05603a;}
      .cc-phase-wait {background:#fff1f0; border-color:#fda29b; color:#912018;}
      .cc-phase-name {font-size:.72rem; font-weight:850; letter-spacing:.055em;}
      .cc-phase-state {font-size:.96rem; font-weight:900; margin-top:.08rem;}
      .cc-phase-meta {font-size:.7rem; line-height:1.2; opacity:.82; margin-top:.12rem;
                      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
      .cc-event-status {border:1px solid #d0d5dd; border-radius:14px; padding:.62rem .7rem;
                        margin:.42rem 0; background:#fff;}
      .cc-event-status-mine {border:2px solid #6172f3; background:#f5f8ff;}
      .cc-event-status-head {display:flex; justify-content:space-between; align-items:center;
                             gap:.4rem; font-weight:850;}
      .cc-event-lights {display:flex; gap:.45rem; flex-wrap:wrap; margin-top:.3rem;}
      .cc-light {font-size:.78rem; font-weight:800; border-radius:999px; padding:.2rem .48rem;}
      .cc-light-live {background:#dcfae6; color:#05603a;}
      .cc-light-wait {background:#fee4e2; color:#912018;}
      .cc-event-tag {display:inline-block; font-size:.7rem; font-weight:850; color:#3538cd;
                     background:#eef4ff; border-radius:999px; padding:.17rem .45rem;
                     margin-bottom:.28rem;}
      .cc-team-coach-label {font-size:.85rem; margin:.7rem 0 .4rem;}
      .cc-plan-group {border:1px solid #e4e7ec; border-radius:12px; padding:.55rem .65rem;
                      margin:.4rem 0; background:#fff;}
      .cc-plan-coach {font-weight:900; margin-bottom:.22rem;}
      .cc-plan-row {font-size:.87rem; line-height:1.35; padding:.24rem 0;
                    border-top:1px solid #f2f4f7;}
      .cc-plan-row:first-of-type {border-top:0;}
      .cc-plan-meta {color:#667085; font-size:.78rem;}
      .cc-availability {border:1px solid #d0d5dd; border-radius:14px; padding:.68rem .75rem;
                        margin:.35rem 0 .7rem; background:#fff;}
      .cc-availability-on {border-color:#75e0a7; background:#ecfdf3;}
      .cc-availability-title {font-weight:900; color:#101828;}
      .cc-availability-meta {font-size:.8rem; color:#667085; margin-top:.12rem;}
      .cc-new-assignment {font-size:1.05rem; font-weight:850; color:#3538cd;}
      .cc-assignment-name {font-size:1.12rem; font-weight:850; margin:.2rem 0;}
      .cc-coach-chips {display:flex; gap:.35rem; flex-wrap:wrap; margin:.4rem 0 .75rem;}
      .cc-coach-chip {border-radius:999px; padding:.28rem .55rem; font-size:.78rem;
                      font-weight:850; border:1px solid #d0d5dd; background:#f8fafc;
                      color:#475467;}
      .cc-coach-chip-on {border-color:#75e0a7; background:#dcfae6; color:#05603a;}
      .cc-available-coaches-banner {border:1px solid #75e0a7; border-radius:14px;
                                    background:#ecfdf3; padding:.6rem .7rem;
                                    margin:.35rem 0;}
      .cc-available-coaches-banner .cc-availability-title {color:#05603a;}
      .cc-available-coaches-banner .cc-coach-chips {margin:.35rem 0 0;}
      .cc-sector {border:1px solid #d0d5dd; border-radius:14px; padding:.62rem .7rem;
                  margin:.42rem 0; background:#fff;}
      .cc-sector-risk {border-color:#f97066; background:#fff8f7;}
      .cc-sector-head {display:flex; justify-content:space-between; gap:.5rem;
                       align-items:center; font-weight:900;}
      .cc-sector-coaches {font-size:.82rem; color:#475467; margin:.22rem 0 .32rem;}
      .cc-sector-row {font-size:.82rem; line-height:1.35; padding:.22rem 0;
                      border-top:1px solid #f2f4f7;}
      .cc-sector-row:first-of-type {border-top:0;}
      .cc-risk {color:#b42318; font-weight:850;}
      [class*="st-key-cc_uncovered_"] h2 {
        font-size:1.3rem !important; font-weight:850; padding:0 0 .35rem;
        margin:0; overflow-wrap:anywhere;
      }
      [class*="st-key-cc_uncovered_"] [data-testid="stMarkdownContainer"] p {
        font-size:1.05rem; line-height:1.4; margin-bottom:.4rem;
      }
      [class*="st-key-cc_uncovered_"] [data-testid="stMarkdownContainer"] p:last-child {
        margin-bottom:0;
      }
      .cc-pool-row {display:flex; justify-content:space-between; gap:.65rem;
                    border-top:1px solid #f2f4f7; padding:.42rem 0; font-size:.86rem;}
      .cc-pool-row:first-of-type {border-top:0;}
      .cc-pool-score {font-weight:900; white-space:nowrap; color:#344054;}
      .stToast {max-width:92vw;}
      @media (max-width: 520px) {
        .block-container {padding-left:.55rem; padding-right:.55rem;}
        h1 {font-size:1.48rem !important;}
        [data-testid="stHorizontalBlock"] {gap:.35rem;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)
apply_result_styles()


def secret_value(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value is not None:
        return str(env_value)
    try:
        return str(st.secrets.get(name, default))
    except (StreamlitSecretNotFoundError, KeyError, TypeError, AttributeError):
        return default


@st.cache_resource
def get_db() -> CompCoachDB:
    database = create_database({name: secret_value(name) for name in SETTING_NAMES})
    for child in database.list_events():
        quarantine_import_noise(database, child["id"])
    return database


@st.cache_resource
def get_assets():
    return create_asset_store({name: secret_value(name) for name in SETTING_NAMES})


try:
    db = RenderReads(get_db())
    asset_store = get_assets()
except (CompCoachError, AssetStoreError) as exc:
    st.error(f"CompCoach cannot start: {exc}")
    st.stop()


def esc(value: object) -> str:
    return html.escape(str(value or ""))


def safe_key(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value))


def query_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def select_nav(options: list[str], key: str) -> str:
    # Fragment reruns can temporarily omit the navigation widget. Keep its
    # last valid choice in non-widget state so a full refresh stays in place.
    saved_key = f"nav_choice_{key}"
    saved = st.session_state.get(saved_key)
    default = saved if saved in options else options[0]
    if hasattr(st, "segmented_control"):
        value = st.segmented_control("Navigation", options, default=default,
                                     key=key, label_visibility="collapsed",
                                     format_func=lambda option: "Team situation" if option == "Live" else option)
    else:
        value = st.radio("Navigation", options, index=options.index(default),
                         horizontal=True, key=key, label_visibility="collapsed",
                         format_func=lambda option: "Team situation" if option == "Live" else option)
    choice = value or default
    st.session_state[saved_key] = choice
    return choice


def migrate_live_navigation(meet_id: str, role: str) -> None:
    """Keep an open Situation session on the merged Live page after upgrading."""
    key = f"{role}_nav_{meet_id}"
    saved_key = f"nav_choice_{key}"
    if "Situation" not in {st.session_state.get(key), st.session_state.get(saved_key)}:
        return
    subkey = f"situation_view_{meet_id}_{role}"
    section = st.session_state.get(f"nav_choice_{subkey}") or st.session_state.get(subkey)
    if section in {"Assignments", "Pool Results"}:
        st.session_state[f"live_migrated_panel_{meet_id}_{role}"] = section
    st.session_state[key] = "Live"
    st.session_state[saved_key] = "Live"


def format_clock(iso_value: str | None, timezone_name: str) -> str:
    if not iso_value:
        return ""
    try:
        moment = datetime.fromisoformat(iso_value)
        return moment.astimezone(ZoneInfo(timezone_name)).strftime("%I:%M %p").lstrip("0")
    except (ValueError, TypeError, KeyError):
        return ""


def age_minutes(iso_value: str | None) -> int | None:
    if not iso_value:
        return None
    try:
        moment = datetime.fromisoformat(iso_value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - moment).total_seconds() // 60))
    except (ValueError, TypeError):
        return None


def age_text(iso_value: str | None) -> str:
    minutes = age_minutes(iso_value)
    if minutes is None:
        return ""
    if minutes == 0:
        return "just now"
    if minutes == 1:
        return "1 min ago"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours}h ago"


def is_stale(athlete: dict) -> bool:
    status = athlete.get("call_status")
    minutes = age_minutes(athlete.get("reported_at"))
    return bool(minutes is not None and status in STALE_MINUTES and minutes >= STALE_MINUTES[status])


def athlete_location(athlete: dict) -> str:
    if athlete.get("phase") == "de":
        return str(athlete.get("live_location") or "Actual strip TBD")
    return str(athlete.get("live_location") or athlete.get("source_strip") or "Location TBD")


def assigned_coach_names(athlete: dict) -> list[str]:
    """Read every equal DE coach while retaining the two Pool roles."""
    if athlete.get("phase") == "de":
        values = athlete.get("de_coaches", athlete.get("coaches"))
        if values is None:
            raw = athlete.get("de_coaches_json", athlete.get("coaches_json"))
            if raw is not None:
                try:
                    values = json.loads(raw)
                except (TypeError, ValueError):
                    values = None
        if isinstance(values, list):
            return list(dict.fromkeys(str(name).strip() for name in values if str(name).strip()))
    return list(dict.fromkeys(
        name for name in (athlete.get("main_coach"), athlete.get("side_coach")) if name
    ))


def connected_coaches(athlete: dict) -> set[str]:
    return set(assigned_coach_names(athlete)) | {
        str(athlete.get("covered_by") or ""),
        str(athlete.get("help_acknowledged_by") or ""),
        str(athlete.get("takeover_coach") or ""),
    }


def planned_coaches(athlete: dict) -> str:
    if athlete.get("phase") == "de":
        names = assigned_coach_names(athlete)
        return "Coaches: " + " / ".join(names) if names else "No planned coach"
    coaches = []
    if athlete.get("main_coach"):
        coaches.append(f"Main: {athlete['main_coach']}")
    if athlete.get("side_coach"):
        coaches.append(f"Side: {athlete['side_coach']}")
    return " · ".join(coaches) or "No planned coach"


def de_progress_text(athlete: dict, event: dict | None = None) -> str:
    context = event if event is not None else {"de_start_tableau": athlete.get("de_start_tableau")}
    return describe_de_progress(athlete, context)["progress_summary"]


def load_meet_context(meet_id: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    events = db.list_meet_events(meet_id)
    event_by_id = {event["id"]: event for event in events}
    athletes: list[dict] = []
    for event in events:
        for stored in db.list_athletes(event["id"]):
            athlete = dict(stored)
            athlete["event_name"] = event["name"]
            athlete["event_sort_order"] = int(event.get("sort_order") or 0)
            athlete["de_start_tableau"] = event.get("de_start_tableau")
            athletes.append(athlete)
    return events, athletes, event_by_id


def is_operational_athlete(athlete: dict) -> bool:
    """Return whether an athlete belongs in current operational workflows."""

    return (
        athlete.get("active_state") == "active"
        and athlete.get("participation_status", "active") == "active"
        and not import_name_noise_reason(athlete.get("name", ""))
    )


def is_competitive_out(athlete: dict) -> bool:
    """Keep Live Out limited to competitive elimination, not attendance state."""

    return (
        athlete.get("active_state") == "eliminated"
        and athlete.get("participation_status", "active") == "active"
        and not import_name_noise_reason(athlete.get("name", ""))
    )


def pool_wave_label_key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def operational_view_athletes(athletes: list[dict]) -> list[dict]:
    """Filter attendance and scheduled Pool waves for live operational views.

    Direct Elimination and legacy Pool rows without a start time are always
    visible. Timed Pool rows are visible only for the event's current wave.
    """

    return visible_pool_wave_rows(
        [row for row in athletes if is_operational_athlete(row)]
    )


def visible_pool_wave_rows(rows: list[dict]) -> list[dict]:
    """Keep athlete cards and assignment notices on the same visible Pool wave."""

    timed_event_ids = {
        str(row.get("event_id") or "")
        for row in rows
        if row.get("phase") == "pools" and str(row.get("time_text") or "").strip()
    }
    visible_by_event: dict[str, set[str] | None] = {}
    for event_id in timed_event_ids:
        waves = db.list_pool_waves(event_id)
        visible_by_event[event_id] = (
            {
                pool_wave_label_key(wave.get("label"))
                for wave in waves
                if wave.get("is_visible") or wave.get("is_active")
            }
            if waves
            else None
        )

    filtered: list[dict] = []
    for row in rows:
        time_text = str(row.get("time_text") or "").strip()
        if row.get("phase") != "pools" or not time_text:
            filtered.append(row)
            continue
        visible = visible_by_event.get(str(row.get("event_id") or ""))
        if visible is None or pool_wave_label_key(time_text) in visible:
            filtered.append(row)
    return filtered


def athlete_event_label(athlete: dict) -> str:
    return str(athlete.get("event_name") or "Event")


def has_inactive_assignment(athlete: dict, event: dict) -> bool:
    active = set(event["active_coaches"])
    assigned = set(assigned_coach_names(athlete))
    return bool(assigned - active)


def coach_workload(athletes: list[dict], coach: str) -> dict[str, object]:
    """Return a meet-wide operational workload without declaring availability.

    A recorded Pools W/L summary means that Pools duty is finished. A DE duty
    remains open while the athlete is active, including after a Won result.
    Temporary coverage, help response, and an unresolved help request made by
    the coach also keep that athlete in the open-duty set.
    """

    operational = operational_view_athletes(athletes)
    planned = [
        row
        for row in operational
        if coach in assigned_coach_names(row)
    ]
    unfinished: dict[str, dict] = {}
    for row in operational:
        planned_here = coach in assigned_coach_names(row)
        pools_pending = (
            row.get("phase") == "pools"
            and (row.get("pool_wins") is None or row.get("pool_losses") is None)
        )
        de_pending = row.get("phase") == "de"
        temporary_duty = coach in {
            row.get("covered_by"),
            row.get("help_acknowledged_by"),
            row.get("help_requested_by"),
            row.get("takeover_coach"),
        }
        if (planned_here and (pools_pending or de_pending)) or temporary_duty:
            unfinished[str(row["id"])] = row
    return {
        "assigned_total": len(planned),
        "unfinished": list(unfinished.values()),
        "unfinished_count": len(unfinished),
        "event_ids": {row["event_id"] for row in unfinished.values()},
    }


def athlete_sector(athlete: dict) -> str:
    """Return a compact DE sector derived from pod or exact strip."""

    pod = str(athlete.get("pod") or "").strip().upper()
    if pod:
        return pod
    raw = str(
        athlete.get("source_strip") or athlete.get("live_location") or ""
    ).strip().upper()
    raw = re.sub(r"^POD\s+", "", raw)
    match = re.match(r"([A-Z]+)", raw)
    return match.group(1) if match else "TBD"


def sector_assignment_summary(rows: list[dict]) -> dict[str, object]:
    mains = sorted({str(row.get("main_coach") or "").strip() for row in rows} - {""})
    sides = sorted({str(row.get("side_coach") or "").strip() for row in rows} - {""})
    covered = sorted({str(row.get("covered_by") or "").strip() for row in rows} - {""})
    uncovered_calls = sum(
        row.get("call_status") != "waiting"
        and not row.get("covered_by")
        and not is_stale(row)
        for row in rows
    )
    coaches = sorted({coach for row in rows for coach in assigned_coach_names(row)})
    groups = {frozenset(assigned_coach_names(row)) for row in rows}
    mixed = len(groups) > 1
    unassigned = any(not assigned_coach_names(row) for row in rows)
    return {
        "mains": mains,
        "sides": sides,
        "coaches": coaches,
        "covered": covered,
        "mixed": mixed,
        "unassigned": unassigned,
        "uncovered_calls": uncovered_calls,
    }


def public_link(event: dict, role: str) -> str:
    relative = f"?event={event['id']}&token={event[f'{role}_token']}"
    base = secret_value("COMPCOACH_PUBLIC_URL", "").strip().rstrip("/")
    if not base:
        try:
            request_url = str(st.context.url or "").strip()
            parsed = urlsplit(request_url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                base = urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
                ).rstrip("/")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            base = ""
    return f"{base}/{relative}" if base else relative


def set_open_event(
    event: dict,
    role: str = "admin",
    actor: str | None = None,
    *,
    archive: bool = False,
) -> None:
    candidate = str(actor if actor is not None else query_value("who")).strip()
    st.query_params.clear()
    st.query_params["event"] = event["id"]
    st.query_params["token"] = event[f"{role}_token"]
    if candidate and candidate in allowed_actors(event, role):
        st.query_params["who"] = candidate
    if archive and role == "admin":
        st.query_params["archive"] = "1"
    st.rerun(scope="app")


def set_open_home(event: dict, actor: str = "") -> None:
    """Return an authenticated Admin to Home without retaining a live-day URL."""

    clear_actor_state(event["id"], "admin")
    st.session_state["landing_admin_unlocked"] = True
    if actor in allowed_actors(event, "admin"):
        st.session_state["home_admin_actor"] = actor
    st.query_params.clear()
    st.rerun(scope="app")


def competition_is_closed(event: dict) -> bool:
    competition_id = str(event.get("competition_id") or "")
    parent = db.get_competition(competition_id) if competition_id else None
    return bool(parent and parent.get("status") == "closed")


def allowed_actors(event: dict, role: str) -> list[str]:
    practice = get_training(db, event["id"])
    if practice and role == "coach" and practice.get("learner"):
        # Virtual colleagues make the exercise realistic; they are not identities
        # a participant can select to bypass their own course.
        return [str(practice["learner"])]
    if role == "admin":
        people = ["Carmine", *event["coordinators"], *event["active_coaches"]]
    elif role == "coordinator":
        people = list(event["coordinators"])
    else:
        people = list(event["active_coaches"])
    return list(dict.fromkeys(str(person).strip() for person in people if str(person).strip()))


def clear_actor_state(event_id: str, role: str) -> None:
    st.session_state.pop(f"identity_{event_id}_{role}", None)
    transient_prefixes = (
        "nav_choice_",
        "live_migrated_panel_",
        "live_panel_",
        "de_result_confirm_",
        "call_reset_",
        "call_athlete_",
        "call_status_",
        "call_location_",
        "call_coverage_",
        "call_self_cover_",
        "live_view_",
        "dashboard_view_",
        "situation_view_",
        "availability_override_",
        "deploy_",
        "emergency_request_",
        "active_event_",
    )
    for key in list(st.session_state):
        if str(key).startswith(transient_prefixes):
            st.session_state.pop(key, None)


def require_actor(event: dict, role: str) -> str:
    options = allowed_actors(event, role)
    if not options:
        st.error("No authorized people are configured for this link yet.")
        return ""

    identity_key = f"identity_{event['id']}_{role}"
    requested = query_value("who")
    stored = str(st.session_state.get(identity_key) or "")
    actor = requested if requested in options else stored if not requested and stored in options else ""
    if actor:
        st.session_state[identity_key] = actor
        identity_column, change_column = st.columns([3, 1])
        with identity_column:
            st.markdown(
                f"<div class='cc-identity'>👤 You are {esc(actor)}</div>",
                unsafe_allow_html=True,
            )
        with change_column:
            if st.button(
                "Change",
                key=f"change_identity_{event['id']}_{role}",
                width="stretch",
            ):
                clear_actor_state(event["id"], role)
                params = st.query_params.to_dict()
                params.pop("who", None)
                st.query_params.from_dict(params)
                st.rerun()
        return actor

    with st.container(border=True):
        st.subheader("Who are you?")
        st.markdown(
            "<div class='cc-identity-hint'>Choose your name before opening the live board. "
            "Every update will show who sent it.</div>",
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        for index, person in enumerate(options):
            with columns[index % 2]:
                if st.button(
                    person,
                    key=f"choose_identity_{event['id']}_{role}_{index}",
                    width="stretch",
                ):
                    st.session_state[identity_key] = person
                    params = st.query_params.to_dict()
                    params["who"] = person
                    st.query_params.from_dict(params)
                    st.rerun()
    return ""


def show_error(exc: Exception) -> None:
    st.error(str(exc))


def render_landing() -> None:
    st.title("🤺 CompCoach Live")
    st.caption(f"AFM coach coordination · v{APP_VERSION}")
    admin_pin = secret_value("COMPCOACH_ADMIN_PIN", "")
    existing_events = db.list_meets()
    unlocked_key = "landing_admin_unlocked"
    already_authorized = bool(st.session_state.get(unlocked_key))
    if secret_value("COMPCOACH_PUBLIC_URL", "") and not admin_pin and not already_authorized:
        st.error(
            "Admin event creation is disabled until COMPCOACH_ADMIN_PIN is configured. "
            "Existing token links continue to work."
        )
        return
    if not admin_pin and existing_events and not already_authorized:
        st.error(
            "For security, the Admin event list is hidden when no Admin PIN is configured. "
            "Open the private Admin link you saved, or configure COMPCOACH_ADMIN_PIN."
        )
        return
    if admin_pin and not st.session_state.get(unlocked_key):
        with st.form("admin_pin_form"):
            entered = st.text_input("Admin PIN", type="password")
            submitted = st.form_submit_button("Continue", width="stretch")
        if submitted:
            if secrets_compare(entered, admin_pin):
                st.session_state[unlocked_key] = True
                st.rerun()
            st.error("Incorrect PIN.")
        return

    st.caption(
        "☁️ Cloud database connected"
        if getattr(db, "backend", "sqlite") == "postgres"
        else "💻 Local test database"
    )
    practice_sessions = list_training_sessions(db)
    practice_ids = {session["meet_id"] for session in practice_sessions}
    existing_events = [event for event in existing_events if event["id"] not in practice_ids]
    competitions = {parent["id"]: parent for parent in db.list_competitions()}
    active = [
        event for event in existing_events
        if not event.get("ended_at")
        and event.get("day_status", "active") == "active"
        and competitions.get(event.get("competition_id"), {}).get("status") != "closed"
    ]
    scheduled = [
        event for event in existing_events
        if not event.get("ended_at")
        and event.get("day_status") == "scheduled"
        and competitions.get(event.get("competition_id"), {}).get("status") != "closed"
    ]
    archived = [
        event for event in existing_events
        if event.get("ended_at") or event.get("day_status") == "closed"
        or competitions.get(event.get("competition_id"), {}).get("status") == "closed"
    ]
    if not active:
        st.subheader("⏸️ No active competition")
        st.info("Ready for a new competition. Your previous assignments and results are saved in History.")
    else:
        st.subheader("🟢 Active now")
        for event in active:
            render_home_day_card(event, competitions.get(event.get("competition_id"), {}))

    with st.expander("➕ New competition", expanded=not existing_events):
        directory = db.list_coaches()
        directory_names = [coach["name"] for coach in directory]
        coach_options = setup_coach_options(directory, DEFAULT_COACHES)
        default_coaches = [name for name in canonical_coach_names(DEFAULT_COACHES, directory_names) if name in coach_options]
        coordinator_options = setup_coach_options(directory, [*DEFAULT_COORDINATORS, *coach_options])
        default_coordinators = [name for name in canonical_coach_names(DEFAULT_COORDINATORS, directory_names) if name in coordinator_options]
        with st.form("create_event"):
            name = st.text_input("Competition or travel day", "AFM Competition")
            location = st.text_input(
                "Location",
                "",
                placeholder="Example: Salt Palace, Salt Lake City",
            )
            competition_date = st.date_input(
                "First competition day",
                value=datetime.now(ZoneInfo(TIMEZONES[0])).date(),
            )
            competition_end_date = st.date_input(
                "Last competition day",
                value=competition_date,
            )
            first_event_name = st.text_input(
                "First event (optional)",
                "",
                placeholder="Example: Cadet Foil",
                help="Leave this blank if the event schedule is not ready yet.",
            )
            coaches = st.multiselect(
                "Coaches present", coach_options, default=default_coaches,
                accept_new_options=True,
                placeholder="Select coaches or type a new name",
            )
            st.caption("To add a coach here, type the name and select Add. New names are saved when you create the competition.")
            coordinators = st.multiselect(
                "Coordinators", coordinator_options, default=default_coordinators,
                accept_new_options=True,
                placeholder="Select coordinators or type a new name",
            )
            timezone_name = st.selectbox("Competition timezone", TIMEZONES)
            create = st.form_submit_button("Create competition", width="stretch")
        if create:
            if competition_end_date < competition_date:
                st.error("Last competition day cannot be before the first day.")
                return
            coaches = canonical_coach_names(coaches, directory_names)
            coordinators = canonical_coach_names(coordinators, directory_names)
            selected_names = canonical_coach_names([*coaches, *coordinators])
            for person in directory:
                if not person["is_active"] and person["name"] in selected_names:
                    db.create_coach(person["name"])
            event = db.create_meet(
                name,
                coaches,
                coordinators,
                timezone_name,
                first_event_name=first_event_name.strip() or None,
                competition_date=competition_date.isoformat(),
                location=location,
            )
            db.update_competition(
                event["competition_id"],
                name=name,
                location=location,
                start_date=competition_date.isoformat(),
                end_date=competition_end_date.isoformat(),
                timezone_name=timezone_name,
            )
            set_open_event(event)

    if scheduled:
        with st.expander(f"🗓️ Scheduled days · {len(scheduled)}", expanded=not active):
            st.caption("Prepare these days now. Coaches see them only after activation.")
            for event in scheduled:
                render_home_day_card(event, competitions.get(event.get("competition_id"), {}))
    if archived:
        with st.expander(f"History · {len(archived)} day(s)", expanded=False):
            for event in archived:
                render_home_day_card(
                    event, competitions.get(event.get("competition_id"), {}), archive=True
                )

    with st.expander("🧪 Practice · coach training", expanded=False):
        st.caption("Practice uses a separate shared board and fictional athletes. Real competitions stay unchanged.")
        for session in practice_sessions:
            if session.get("kind") == "run" or session.get("learner"):
                continue
            practice = db.get_meet(session["meet_id"])
            if not practice:
                continue
            with st.container(border=True):
                st.markdown(f"**{esc(practice['name'])}**")
                st.caption(str(session.get("status", "running")).replace("_", " ").title())
                if st.button("Open practice", key=f"open_practice_{practice['id']}", width="stretch"):
                    set_open_event(practice, actor=str(st.session_state.get("home_admin_actor") or ""))
        render_training_start(
            db, None, str(st.session_state.get("home_admin_actor") or "Admin"),
            open_callback=lambda practice: set_open_event(practice),
        )


def render_home_day_card(event: dict, competition: dict, *, archive: bool = False) -> None:
    with st.container(border=True):
        st.markdown(f"**{esc(event['name'])}**")
        if competition.get("name") and competition["name"] != event["name"]:
            st.caption(str(competition["name"]))
        day = event.get("competition_date") or "Date not set"
        if archive:
            state = "Competition ended" if competition.get("status") == "closed" else "Day ended"
        elif event.get("day_status") == "scheduled":
            state = "Scheduled"
        else:
            state = "Active" if event["status"] == "open" else "Paused"
        st.caption(f"{state} · {day} · {int(event.get('event_count') or 0)} event(s)")
        label = "Open read-only archive" if archive else "Prepare day" if state == "Scheduled" else "Open Admin"
        if st.button(label, key=f"open_home_{event['id']}", width="stretch"):
            set_open_event(
                event,
                actor=str(st.session_state.get("home_admin_actor") or ""),
                archive=archive,
            )


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return bool(left and right and hmac.compare_digest(str(left), str(right)))


def render_header(event: dict, role: str) -> None:
    role_label = {"admin": "ADMIN", "coordinator": "COORDINATOR", "coach": "COACH"}[role]
    st.markdown(
        f"<div class='cc-topline'><h1>🤺 {esc(event['name'])}</h1>"
        f"<span class='cc-role'>{role_label}</span></div>",
        unsafe_allow_html=True,
    )
    render_competition_branding(db, event, asset_store=asset_store)
    if event.get("ended_at"):
        stamp = format_clock(event.get("ended_at"), event["timezone"])
        byline = f" by {event['ended_by']}" if event.get("ended_by") else ""
        at_time = f" at {stamp}" if stamp else ""
        st.info(
            f"🏁 Competition day ended{byline}{at_time}. "
            "This archive is read-only."
        )
    elif event["status"] == "locked":
        st.warning("Updates are temporarily paused. The board is read-only.")


def render_phase_status(event: dict, role: str, actor: str) -> None:
    events, athletes, _ = load_meet_context(event["id"])
    operational = operational_view_athletes(athletes)
    actor_event_ids = {
        athlete["event_id"]
        for athlete in operational
        if actor
        and actor
        in connected_coaches(athlete)
    }
    for child in events:
        states = db.get_phase_states(child["id"])
        mine = child["id"] in actor_event_ids
        lights = []
        for phase, label in (("pools", "Pools"), ("de", "DE")):
            state = states[phase]
            started = bool(state["started"])
            light_class = "cc-light-live" if started else "cc-light-wait"
            icon = "🟢" if started else "🔴"
            elapsed = age_text(state.get("changed_at"))
            suffix = f" · {esc(elapsed)}" if elapsed else ""
            lights.append(
                f"<span class='cc-light {light_class}'>{icon} {label}{suffix}</span>"
            )
        connected_count = sum(
            1
            for athlete in operational
            if athlete["event_id"] == child["id"]
            and actor
            in connected_coaches(athlete)
        )
        mine_note = (
            f"⭐ YOUR EVENT · {connected_count}"
            if mine
            else f"{child['athlete_count']} athletes"
        )
        status_class = " cc-event-status-mine" if mine else ""
        st.markdown(
            f"<div class='cc-event-status{status_class}'>"
            f"<div class='cc-event-status-head'><span>{esc(child['name'])}</span>"
            f"<span>{esc(mine_note)}</span></div>"
            f"<div class='cc-event-lights'>{''.join(lights)}</div></div>",
            unsafe_allow_html=True,
        )

    can_change = (
        role in {"admin", "coordinator"}
        and event["status"] == "open"
        and bool(actor)
        and bool(events)
    )
    if not can_change:
        return
    with st.expander("🚦 Change phase status", expanded=False):
        st.caption("Visible to every coach and refreshed automatically.")
        by_id = {child["id"]: child for child in events}
        selected_id = st.selectbox(
            "Event",
            list(by_id),
            format_func=lambda child_id: by_id[child_id]["name"],
            key=f"phase_event_{event['id']}",
        )
        states = db.get_phase_states(selected_id)
        columns = st.columns(2)
        for column, (phase, label) in zip(
            columns,
            (("pools", "Pools"), ("de", "Direct Elimination")),
            strict=True,
        ):
            phase_state = states[phase]
            current = bool(phase_state["started"])
            next_state = not current
            button_label = f"Set {label} not started" if current else f"Start {label}"
            with column:
                if st.button(
                    button_label,
                    key=f"phase_{phase}_{int(current)}_{selected_id}",
                    type="secondary" if current else "primary",
                    width="stretch",
                ):
                    try:
                        db.set_phase_started(
                            selected_id,
                            phase,
                            next_state,
                            actor,
                            expected_started=current,
                            expected_version=int(phase_state.get("version") or 0),
                        )
                        st.toast(f"{label}: {'started' if next_state else 'not started'}.")
                        st.rerun()
                    except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
                        show_error(exc)


def render_de_non_advancer_confirmation(
    event_id: str,
    records: list[dict],
    *,
    key: str,
    allow_empty: bool = False,
) -> tuple[dict[str, int] | None, int]:
    """Show the explicit whole-list confirmation for inferred DE omissions."""

    candidates = db.preview_de_non_advancers(
        event_id,
        records,
        allow_empty=allow_empty,
    )
    if not candidates:
        return None, 0

    if allow_empty:
        st.warning(
            "An empty Direct Elimination table usually means the list is not "
            "published yet. Continue only if the final DE list is complete and "
            "none of these athletes advanced."
        )
    else:
        st.warning(
            "Before marking anyone Out, confirm that this is the complete Direct "
            "Elimination list—not a partial or still-updating list."
        )
    st.markdown("**Still in Pools and absent from this DE list:**")
    for athlete in candidates:
        st.write(f"• {athlete['name']}")
    st.caption(
        "Check every name and spelling. A typo can make one athlete look absent "
        "and another look new."
    )
    confirmation_fingerprint = hashlib.sha256(
        repr(
            (
                sorted(
                    (str(record.get("athlete_id") or ""), str(record.get("name") or ""))
                    for record in records
                ),
                [
                    (str(athlete["id"]), int(athlete["version"]))
                    for athlete in candidates
                ],
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    confirmation_label = (
        "This is the final complete DE list and no athlete named above advanced. "
        "Mark them all Out."
        if allow_empty
        else "This is the complete Direct Elimination list. Mark every athlete "
        "named above Out."
    )
    confirmed = st.checkbox(
        confirmation_label,
        # A changed DE roster or Pools-athlete version gets a fresh unchecked
        # control, so an earlier confirmation can never silently carry over.
        key=f"{key}_{confirmation_fingerprint}",
    )
    if not confirmed:
        st.caption(
            "Unchecked: the DE rows will still import, but nobody missing from the "
            "list will be marked Out."
        )
        return None, len(candidates)
    return (
        {
            str(athlete["id"]): int(athlete["version"])
            for athlete in candidates
        },
        len(candidates),
    )


def import_source_fingerprint(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()[:16]


def clear_import_widget_state(event_id: str, *, screenshot: bool) -> None:
    prefixes = (
        (
            f"ocr_phase_{event_id}_",
            f"ocr_editor_{event_id}_",
            f"ocr_confirm_backward_{event_id}_",
            f"ocr_reviewed_{event_id}_",
            f"ocr_confirm_complete_de_{event_id}_",
            f"ocr_apply_{event_id}_",
            f"ocr_confirm_empty_de_{event_id}_",
            f"ocr_apply_empty_de_{event_id}_",
        )
        if screenshot
        else (
            f"confirm_backward_{event_id}_",
            f"confirm_complete_de_{event_id}_",
            f"apply_import_{event_id}_",
            f"confirm_empty_de_{event_id}_",
            f"apply_empty_de_{event_id}_",
        )
    )
    for state_key in list(st.session_state):
        if str(state_key).startswith(prefixes):
            st.session_state.pop(state_key, None)


def render_import(event: dict, actor: str) -> None:
    st.subheader("Import athletes")
    paste_tab, screenshot_tab, link_tab = st.tabs(
        ["Paste list", "Screenshot", "Source link"]
    )
    with paste_tab:
        raw = st.text_area(
            "Paste the Pools or Direct Elimination table",
            height=180,
            placeholder="Name\tStrip #\tTime\tPool # ...",
            key=f"paste_{event['id']}",
        )
        raw_fingerprint = import_source_fingerprint(raw)
        if st.button("Preview import", width="stretch", key=f"preview_btn_{event['id']}"):
            clear_import_widget_state(event["id"], screenshot=False)
            result = parse_pasted_table(raw)
            st.session_state[f"import_preview_{event['id']}"] = {
                "source_fingerprint": raw_fingerprint,
                "result": result.as_dict(),
            }

        preview_entry = st.session_state.get(f"import_preview_{event['id']}")
        preview = None
        if isinstance(preview_entry, dict):
            if preview_entry.get("source_fingerprint") == raw_fingerprint:
                preview = preview_entry.get("result")
            elif preview_entry:
                st.info("The pasted text changed. Tap Preview import again.")
        if preview:
            diagnostics = preview["diagnostics"]
            if diagnostics.get("errors"):
                for message in diagnostics["errors"]:
                    st.error(message)
            else:
                phase_label = "Pools" if preview["phase"] == "pools" else "Direct Elimination"
                count = len(preview["records"])
                empty_de = bool(
                    preview["phase"] == "de"
                    and diagnostics.get("empty_marker_found")
                    and not preview["records"]
                )
                if empty_de:
                    st.info(
                        "Empty Direct Elimination table recognized. By default, "
                        "existing data will not change."
                    )
                else:
                    st.success(f"{phase_label} detected · {count} athlete{'s' if count != 1 else ''}")
                for warning in diagnostics.get("warnings", []):
                    st.warning(warning)
                if preview["records"]:
                    frame = pd.DataFrame(preview["records"])[["name", "strip", "time", "pool", "pod"]]
                    frame.columns = ["Athlete", "Strip", "Time", "Pool", "Pod"]
                    st.dataframe(frame, hide_index=True, width="stretch")
                    existing_by_key = {
                        athlete["athlete_key"]: athlete
                        for athlete in db.list_athletes(event["id"])
                    }
                    backward = [
                        row["name"]
                        for row in preview["records"]
                        if preview["phase"] == "pools"
                        and existing_by_key.get(row["athlete_id"], {}).get("phase") == "de"
                    ]
                    allow_backward = True
                    if backward:
                        st.warning(
                            "This Pools list would move existing DE athletes back to Pools and clear their live DE calls."
                        )
                        allow_backward = st.checkbox(
                            "I intend to move these athletes back to Pools",
                            key=f"confirm_backward_{event['id']}_{raw_fingerprint}",
                        )
                    confirmed_non_advancers = None
                    non_advancer_count = 0
                    if preview["phase"] == "de":
                        (
                            confirmed_non_advancers,
                            non_advancer_count,
                        ) = render_de_non_advancer_confirmation(
                            event["id"],
                            preview["records"],
                            key=(
                                f"confirm_complete_de_{event['id']}_"
                                f"{raw_fingerprint}"
                            ),
                        )
                    import_label = f"Import {count} athletes"
                    if confirmed_non_advancers is not None:
                        import_label += f" · mark {non_advancer_count} Out"
                    if st.button(
                        import_label,
                        type="primary",
                        width="stretch",
                        key=f"apply_import_{event['id']}_{raw_fingerprint}",
                        disabled=event["status"] == "locked" or not actor or not allow_backward,
                    ):
                        try:
                            stats = db.merge_import(
                                event["id"],
                                preview["records"],
                                actor,
                                confirmed_non_advancers=confirmed_non_advancers,
                            )
                            st.session_state.pop(f"import_preview_{event['id']}", None)
                            saved_message = (
                                f"Saved: {stats['added']} new · {stats['updated']} updated · {stats['unchanged']} unchanged"
                            )
                            if stats.get("moved_out"):
                                saved_message += f" · {stats['moved_out']} moved Out"
                            st.toast(saved_message)
                            st.rerun()
                        except (CompCoachError, ValueError) as exc:
                            show_error(exc)
                elif empty_de:
                    (
                        confirmed_non_advancers,
                        non_advancer_count,
                    ) = render_de_non_advancer_confirmation(
                        event["id"],
                        [],
                        key=f"confirm_empty_de_{event['id']}_{raw_fingerprint}",
                        allow_empty=True,
                    )
                    if confirmed_non_advancers is not None and st.button(
                        f"Mark {non_advancer_count} athlete(s) Out",
                        type="primary",
                        width="stretch",
                        key=f"apply_empty_de_{event['id']}_{raw_fingerprint}",
                        disabled=event["status"] == "locked" or not actor,
                    ):
                        try:
                            stats = db.merge_import(
                                event["id"],
                                [],
                                actor,
                                confirmed_non_advancers=confirmed_non_advancers,
                            )
                            st.session_state.pop(
                                f"import_preview_{event['id']}", None
                            )
                            st.toast(f"{stats['moved_out']} athlete(s) moved Out")
                            st.rerun()
                        except (CompCoachError, ValueError) as exc:
                            show_error(exc)

    with screenshot_tab:
        st.caption(
            "Upload a Fencing Time table screenshot. Recognition runs locally; "
            "you will edit and confirm every row before anything is saved."
        )
        uploaded = st.file_uploader(
            "Screenshot",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"ocr_upload_{event['id']}",
        )
        uploaded_payload = uploaded.getvalue() if uploaded is not None else b""
        upload_fingerprint = import_source_fingerprint(uploaded_payload)
        if st.button(
            "Read screenshot",
            width="stretch",
            key=f"ocr_read_{event['id']}",
            disabled=uploaded is None,
        ):
            clear_import_widget_state(event["id"], screenshot=True)
            with st.spinner("Reading table on this server…"):
                result = parse_screenshot(uploaded_payload)
            st.session_state[f"ocr_preview_{event['id']}"] = {
                "source_fingerprint": upload_fingerprint,
                "result": result.as_dict(),
            }

        ocr_entry = st.session_state.get(f"ocr_preview_{event['id']}")
        ocr_preview = None
        if isinstance(ocr_entry, dict):
            if (
                uploaded is not None
                and ocr_entry.get("source_fingerprint") == upload_fingerprint
            ):
                ocr_preview = ocr_entry.get("result")
            elif ocr_entry:
                st.info("The screenshot changed. Tap Read screenshot again.")
        if ocr_preview:
            diagnostics = ocr_preview["diagnostics"]
            image_info = diagnostics.get("image", {})
            if image_info.get("decoded") is True:
                with st.expander("View uploaded screenshot", expanded=False):
                    st.image(uploaded_payload, width="stretch")
            for message in diagnostics.get("errors", []):
                st.error(message)
            failed_attempts = [
                attempt
                for attempt in diagnostics.get("engine_attempts", [])
                if attempt.get("status") == "failed" and attempt.get("detail")
            ]
            if failed_attempts:
                combined_details = " ".join(
                    str(attempt.get("detail") or "") for attempt in failed_attempts
                )
                if "libGL.so.1" in combined_details:
                    st.info(
                        "Codespace recovery: install the missing system library, "
                        "then restart CompCoach with the same Python interpreter."
                    )
                    st.code(
                        "sudo apt-get update\n"
                        "sudo apt-get install -y libgl1\n"
                        "python -m streamlit run compcoach_live/app.py "
                        "--server.address 0.0.0.0 --server.port 8501",
                        language="bash",
                    )
                with st.expander("OCR technical details", expanded=False):
                    st.caption(
                        "These details describe the server OCR engine, not the "
                        "quality of the uploaded screenshot."
                    )
                    for attempt in failed_attempts:
                        st.markdown(f"**{esc(attempt.get('engine') or 'OCR')}**")
                        st.code(str(attempt.get("detail") or ""), language=None)
            if not diagnostics.get("errors"):
                engine = diagnostics.get("engine") or "local OCR"
                review_count = len(diagnostics.get("review_cells", []))
                st.success(
                    f"{len(ocr_preview['rows'])} row(s) detected with {engine}."
                )
                if review_count:
                    st.warning(
                        f"OCR marked {review_count} cell(s) for careful review. "
                        "The original text is preserved and never guessed."
                    )
                for warning in diagnostics.get("warnings", []):
                    st.warning(warning)

                if diagnostics.get("empty_marker_found") and not ocr_preview["rows"]:
                    st.info(
                        "The Direct Elimination table is empty. By default, "
                        "existing data will not change."
                    )
                    (
                        confirmed_non_advancers,
                        non_advancer_count,
                    ) = render_de_non_advancer_confirmation(
                        event["id"],
                        [],
                        key=(
                            f"ocr_confirm_empty_de_{event['id']}_"
                            f"{upload_fingerprint}"
                        ),
                        allow_empty=True,
                    )
                    if confirmed_non_advancers is not None and st.button(
                        f"Mark {non_advancer_count} athlete(s) Out",
                        type="primary",
                        width="stretch",
                        key=(
                            f"ocr_apply_empty_de_{event['id']}_"
                            f"{upload_fingerprint}"
                        ),
                        disabled=event["status"] == "locked" or not actor,
                    ):
                        try:
                            stats = db.merge_import(
                                event["id"],
                                [],
                                actor,
                                confirmed_non_advancers=confirmed_non_advancers,
                            )
                            st.session_state.pop(
                                f"ocr_preview_{event['id']}", None
                            )
                            st.toast(f"{stats['moved_out']} athlete(s) moved Out")
                            st.rerun()
                        except (CompCoachError, ValueError) as exc:
                            show_error(exc)
                elif ocr_preview["rows"]:
                    detected_phase = ocr_preview.get("phase")
                    phase_options = ["Pools", "Direct Elimination"]
                    phase_label = st.selectbox(
                        "Confirm competition phase",
                        phase_options,
                        index=0 if detected_phase == "pools" else 1,
                        key=f"ocr_phase_{event['id']}_{upload_fingerprint}",
                    )
                    phase = "pools" if phase_label == "Pools" else "de"
                    editor_rows = []
                    for row in ocr_preview["rows"]:
                        editor_rows.append(
                            {
                                "Use": True,
                                "Name": row.get("name", ""),
                                "Strip #": row.get("strip", "") or row.get("pod", ""),
                                "Time": row.get("time", ""),
                                "Pool #": row.get("pool", ""),
                                "Review": "⚠ Check" if row.get("needs_review") else "",
                            }
                        )
                    edited = st.data_editor(
                        pd.DataFrame(editor_rows),
                        hide_index=True,
                        width="stretch",
                        num_rows="dynamic",
                        disabled=["Review"],
                        column_config={
                            "Use": st.column_config.CheckboxColumn(
                                "Use", help="Turn off a row to leave it out."
                            ),
                            "Review": st.column_config.TextColumn(
                                "OCR", help="Cells in this row need an extra check."
                            ),
                        },
                        key=f"ocr_editor_{event['id']}_{upload_fingerprint}",
                    )

                    def editor_text(value: object) -> str:
                        if value is None or pd.isna(value):
                            return ""
                        return str(value).replace("\t", " ").strip()

                    chosen_rows = []
                    for row in edited.to_dict("records"):
                        if not bool(row.get("Use")):
                            continue
                        name = editor_text(row.get("Name"))
                        if not name:
                            continue
                        chosen_rows.append(
                            {
                                "name": name,
                                "strip": editor_text(row.get("Strip #")),
                                "time": editor_text(row.get("Time")),
                                "pool": editor_text(row.get("Pool #")),
                            }
                        )

                    if phase == "pools":
                        lines = ["Name\tStrip #\tTime\tPool #"]
                        lines.extend(
                            "\t".join(
                                [row["name"], row["strip"], row["time"], row["pool"]]
                            )
                            for row in chosen_rows
                        )
                    else:
                        lines = ["Name\tStrip #"]
                        lines.extend(
                            "\t".join([row["name"], row["strip"]])
                            for row in chosen_rows
                        )
                    confirmed_text = "\n".join(lines)
                    confirmed_fingerprint = import_source_fingerprint(confirmed_text)
                    confirmed_preview = parse_pasted_table(
                        confirmed_text, phase_hint=phase
                    ).as_dict()
                    for warning in confirmed_preview["diagnostics"].get("warnings", []):
                        st.warning(warning)

                    existing_by_key = {
                        athlete["athlete_key"]: athlete
                        for athlete in db.list_athletes(event["id"])
                    }
                    backward = [
                        row["name"]
                        for row in confirmed_preview["records"]
                        if phase == "pools"
                        and existing_by_key.get(row["athlete_id"], {}).get("phase")
                        == "de"
                    ]
                    allow_backward = True
                    if backward:
                        st.warning(
                            "This screenshot would move existing DE athletes back to "
                            "Pools and clear their live DE calls."
                        )
                        allow_backward = st.checkbox(
                            "I intend to move these athletes back to Pools",
                            key=(
                                f"ocr_confirm_backward_{event['id']}_"
                                f"{confirmed_fingerprint}"
                            ),
                        )
                    reviewed = st.checkbox(
                        "I reviewed the athlete names, strips/pods and pool numbers",
                        key=(
                            f"ocr_reviewed_{event['id']}_{phase}_"
                            f"{confirmed_fingerprint}"
                        ),
                    )
                    import_count = len(confirmed_preview["records"])
                    confirmed_non_advancers = None
                    non_advancer_count = 0
                    if phase == "de" and import_count:
                        (
                            confirmed_non_advancers,
                            non_advancer_count,
                        ) = render_de_non_advancer_confirmation(
                            event["id"],
                            confirmed_preview["records"],
                            key=(
                                f"ocr_confirm_complete_de_{event['id']}_"
                                f"{confirmed_fingerprint}"
                            ),
                        )
                    import_label = f"Import {import_count} screenshot row(s)"
                    if confirmed_non_advancers is not None:
                        import_label += f" · mark {non_advancer_count} Out"
                    if st.button(
                        import_label,
                        type="primary",
                        width="stretch",
                        key=(
                            f"ocr_apply_{event['id']}_{phase}_"
                            f"{confirmed_fingerprint}"
                        ),
                        disabled=(
                            not import_count
                            or not reviewed
                            or not allow_backward
                            or event["status"] == "locked"
                            or not actor
                        ),
                    ):
                        try:
                            stats = db.merge_import(
                                event["id"],
                                confirmed_preview["records"],
                                actor,
                                confirmed_non_advancers=confirmed_non_advancers,
                            )
                            st.session_state.pop(f"ocr_preview_{event['id']}", None)
                            saved_message = (
                                f"Saved: {stats['added']} new · "
                                f"{stats['updated']} updated · "
                                f"{stats['unchanged']} unchanged"
                            )
                            if stats.get("moved_out"):
                                saved_message += f" · {stats['moved_out']} moved Out"
                            st.toast(saved_message)
                            st.rerun()
                        except (CompCoachError, ValueError) as exc:
                            show_error(exc)

    with link_tab:
        st.caption("The source URL is stored with the competition so it is always one tap away.")
        url = st.text_input(
            "Fencing Time Live link",
            value=event.get("source_url", ""),
            placeholder="https://www.fencingtimelive.com/rounds/strips/...",
            key=f"source_url_{event['id']}",
        )
        if st.button(
            "Save source link",
            width="stretch",
            disabled=event["status"] == "locked",
        ):
            try:
                db.update_event_source_url(event["id"], url)
                st.toast("Source link saved.")
                st.rerun()
            except CompCoachError as exc:
                show_error(exc)
        if url:
            st.link_button("Open Fencing Time Live", url, width="stretch")
        st.info(
            "Automatic link import is intentionally inactive: Fencing Time Live requires an authenticated account and no authorized data API is configured. Paste List remains available in parallel."
        )


def athlete_option(athlete: dict) -> str:
    location = athlete.get("source_strip") or (f"Pod {athlete.get('pod')}" if athlete.get("pod") else "TBD")
    extra = f"Pool {athlete['pool_no']}" if athlete.get("phase") == "pools" and athlete.get("pool_no") else "DE"
    event_prefix = f"[{athlete_event_label(athlete)}] " if athlete.get("event_name") else ""
    return f"{event_prefix}{athlete['name']} · {location} · {extra}"


def has_assignment(athlete: dict) -> bool:
    return bool(assigned_coach_names(athlete))


def natural_sort_key(value: object) -> tuple[tuple[int, object], ...]:
    """Return a case-insensitive key that sorts embedded numbers naturally."""

    normalized = str(value or "").strip().casefold()
    if normalized in {"", "tbd"}:
        return ((2, ""),)
    parts = re.split(r"(\d+)", normalized)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
        if part != ""
    )


def assignment_athlete_sort_key(athlete: dict) -> tuple[object, ...]:
    """Order assignment rows by strip/pod, then pool and athlete name."""

    location = athlete.get("source_strip") or athlete.get("pod") or ""
    return (
        natural_sort_key(location),
        natural_sort_key(athlete.get("pool_no") or ""),
        natural_sort_key(athlete.get("name") or ""),
    )


def order_coaches_by_assignment_usage(
    coaches: list[str],
    athletes: list[dict],
    pod_assignments: list[dict],
) -> tuple[list[str], set[str]]:
    """Put unused coaches first without preventing cross-event reassignment.

    ``athletes`` may contain multiple events from the same competition day.
    Only operational athletes contribute planned or live coverage duties. A
    stored pod assignment contributes only while its event/phase/pod still
    contains an operational athlete. The returned coach values stay unchanged
    so an already-used coach remains selectable across event boundaries.
    """

    used: set[str] = set()
    operational = [athlete for athlete in athletes if is_operational_athlete(athlete)]
    for athlete in operational:
        used.update(assigned_coach_names(athlete))
        used.add(str(athlete.get("covered_by") or "").strip())
    operational_pods = {
        (
            str(athlete.get("event_id") or ""),
            str(athlete.get("phase") or ""),
            str(athlete.get("pod") or "").strip().upper(),
        )
        for athlete in operational
        if athlete.get("pod")
    }
    for assignment in pod_assignments:
        assignment_pod = (
            str(assignment.get("event_id") or ""),
            str(assignment.get("phase") or ""),
            str(assignment.get("pod") or "").strip().upper(),
        )
        if assignment_pod not in operational_pods:
            continue
        used.update(assigned_coach_names(assignment))
    used.discard("")

    unique_coaches = list(dict.fromkeys(str(coach).strip() for coach in coaches))
    unique_coaches = [coach for coach in unique_coaches if coach]
    ordered = [coach for coach in unique_coaches if coach not in used]
    ordered.extend(coach for coach in unique_coaches if coach in used)
    return ordered, used


def coach_assignment_option(coach: str, used_coaches: set[str]) -> str:
    """Return the visible assignment-picker label for a coach value."""

    if coach in {"No change", "None"} or coach not in used_coaches:
        return coach
    return f"✓ {coach} · already assigned"


def assignment_option(athlete: dict) -> str:
    if not has_assignment(athlete):
        return athlete_option(athlete)
    coaches = " / ".join(assigned_coach_names(athlete))
    return f"✓ {athlete_option(athlete)} · {coaches}"


def render_assignment_card(athlete: dict, assigned: bool) -> None:
    with st.container(border=True):
        marker = "✅" if assigned else "○"
        st.markdown(f"**{marker} {esc(athlete['name'])}** · {esc(athlete_location(athlete))}")
        st.caption(planned_coaches(athlete))
        if athlete.get("phase") == "de" and (athlete.get("de_wins") or athlete.get("de_byes")):
            st.caption(de_progress_text(athlete))


def render_attendance_control(
    event: dict,
    phase: str,
    athletes: list[dict],
    actor: str,
) -> None:
    """Render reversible attendance controls without changing the saved plan."""

    phase_rows = [row for row in athletes if row.get("phase") == phase]
    active_rows = sorted(
        [row for row in phase_rows if is_operational_athlete(row)],
        key=assignment_athlete_sort_key,
    )
    status_rows = {
        status: sorted(
            [
                row
                for row in phase_rows
                if row.get("participation_status", "active") == status
            ],
            key=assignment_athlete_sort_key,
        )
        for status in ("absent", "withdrawn")
    }
    inactive_count = sum(len(rows) for rows in status_rows.values())
    label = "Attendance & withdrawals"
    if inactive_count:
        label += f" · {inactive_count}"
    with st.expander(label, expanded=False):
        st.caption(
            "Attendance affects live work only. Planned coach assignments and history are kept."
        )
        selection_key = f"attendance_selected_{event['id']}_{phase}"
        clear_key = f"{selection_key}_clear"
        if st.session_state.pop(clear_key, False):
            st.session_state[selection_key] = []
        by_id = {row["id"]: row for row in active_rows}
        selected = st.multiselect(
            "Athletes currently participating",
            list(by_id),
            format_func=lambda athlete_id: athlete_option(by_id[athlete_id]),
            key=selection_key,
            placeholder="Select one or more athletes",
        )
        absent_column, withdrawn_column = st.columns(2)
        with absent_column:
            mark_absent = st.button(
                "Mark absent",
                width="stretch",
                disabled=not selected or event["status"] != "open" or not actor,
                key=f"mark_absent_{event['id']}_{phase}",
            )
        with withdrawn_column:
            mark_withdrawn = st.button(
                "Mark withdrawn",
                width="stretch",
                disabled=not selected or event["status"] != "open" or not actor,
                key=f"mark_withdrawn_{event['id']}_{phase}",
            )
        if mark_absent or mark_withdrawn:
            status = "absent" if mark_absent else "withdrawn"
            try:
                for athlete_id in selected:
                    athlete = by_id[athlete_id]
                    db.set_athlete_participation(
                        event["id"],
                        athlete_id,
                        status,
                        actor,
                        expected_version=athlete["version"],
                    )
                st.session_state[clear_key] = True
                st.toast(
                    f"{len(selected)} athlete{'s' if len(selected) != 1 else ''} marked {status}."
                )
                st.rerun()
            except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
                show_error(exc)

    for status, title in (("absent", "Absent"), ("withdrawn", "Withdrawn")):
        rows = status_rows[status]
        if not rows:
            continue
        with st.expander(f"{title} · {len(rows)}", expanded=False):
            for athlete in rows:
                with st.container(border=True):
                    st.markdown(
                        f"**{esc(athlete['name'])}** · {esc(athlete_location(athlete))}"
                    )
                    if st.button(
                        "Restore",
                        width="stretch",
                        disabled=event["status"] != "open" or not actor,
                        key=f"restore_attendance_{athlete['id']}_{athlete['version']}",
                    ):
                        try:
                            db.restore_athlete_participation(
                                event["id"],
                                athlete["id"],
                                actor,
                                expected_version=athlete["version"],
                            )
                            st.toast(f"{athlete['name']} restored.")
                            st.rerun()
                        except (
                            CompCoachError,
                            ConcurrentUpdateError,
                            ValueError,
                        ) as exc:
                            show_error(exc)


def render_pool_wave_control(event: dict, actor: str) -> list[dict]:
    """Show the current Pool wave and allow an Admin to activate another."""

    waves = db.list_pool_waves(event["id"])
    if not waves:
        return []
    active_index = next(
        (index for index, wave in enumerate(waves) if wave.get("is_active")),
        0,
    )
    current = waves[active_index]
    with st.expander("Pool wave control", expanded=True):
        st.success(f"Current wave · {current['label']}")
        st.caption(
            "Only the current timed wave appears on live coach screens. Athletes without a time stay visible."
        )
        for index, wave in enumerate(waves):
            if wave.get("is_active"):
                continue
            state = "Scheduled" if index > active_index else "Inactive"
            with st.container(border=True):
                st.markdown(f"**{state} · {esc(wave['label'])}**")
                if st.button(
                    "Activate this wave",
                    type="primary" if state == "Scheduled" else "secondary",
                    width="stretch",
                    disabled=event["status"] != "open" or not actor,
                    key=f"activate_wave_{event['id']}_{wave['wave_key']}",
                ):
                    try:
                        db.activate_pool_wave(
                            event["id"],
                            wave["wave_key"],
                            actor,
                            expected_version=wave["version"],
                        )
                        st.toast(f"{wave['label']} is now the current wave.")
                        st.rerun()
                    except (
                        CompCoachError,
                        ConcurrentUpdateError,
                        ValueError,
                    ) as exc:
                        show_error(exc)
    return waves


def save_de_start_tableau_snapshot(
    event_id: str, actor: str, version: int, size_key: str
) -> None:
    try:
        db.set_event_de_start_tableau(
            event_id, st.session_state.get(size_key), actor,
            expected_version=version,
        )
        st.session_state[f"de_tableau_notice_{event_id}"] = (
            True, "Starting DE tableau saved."
        )
    except (CompCoachError, TypeError, ValueError) as exc:
        st.session_state[f"de_tableau_notice_{event_id}"] = (False, str(exc))


def render_de_start_tableau(event: dict, actor: str) -> None:
    with st.expander("DE round tracking", expanded=False):
        st.caption(
            "Optional: enter this event's first DE tableau to show each athlete's "
            "current round. Byes count as rounds passed, not DE wins."
        )
        notice = st.session_state.pop(f"de_tableau_notice_{event['id']}", None)
        if notice:
            (st.success if notice[0] else st.error)(notice[1])
        version = int(event.get("de_start_tableau_version") or 0)
        size_key = f"de_start_tableau_{event['id']}_v{version}"
        choices = [None, *(2 ** exponent for exponent in range(1, 13))]
        current = event.get("de_start_tableau")
        writable = event.get("status") == "open" and bool(actor)
        with st.form(f"de_start_tableau_form_{event['id']}_v{version}"):
            st.selectbox(
                "Starting DE tableau (optional)", choices,
                index=choices.index(current) if current in choices else 0,
                format_func=lambda size: "Not set" if size is None else f"T{size}",
                disabled=not writable, key=size_key,
            )
            st.form_submit_button(
                "Save DE starting tableau", width="stretch", disabled=not writable,
                key=f"save_de_start_tableau_{event['id']}_v{version}",
                on_click=save_de_start_tableau_snapshot,
                args=(event["id"], actor, version, size_key),
            )


def render_assignments(event: dict, actor: str) -> None:
    st.subheader("Coach assignments")
    all_athletes = db.list_athletes(event["id"])
    if not all_athletes:
        st.info("Import athletes first.")
        return
    phase_label = select_nav(["Pools", "Direct Elimination"], f"assignment_phase_{event['id']}")
    phase = "pools" if phase_label == "Pools" else "de"
    if phase == "de":
        render_de_start_tableau(event, actor)
        for athlete in all_athletes:
            athlete["de_start_tableau"] = event.get("de_start_tableau")
    render_attendance_control(event, phase, all_athletes, actor)
    athletes = [a for a in all_athletes if is_operational_athlete(a)]
    visible = [a for a in athletes if a["phase"] == phase]
    if phase == "pools":
        waves = render_pool_wave_control(event, actor)
        if waves:
            wave_labels = [str(wave["label"]) for wave in waves]
            wave_filter = st.selectbox(
                "Start time to assign",
                ["All start times", *wave_labels],
                key=f"assignment_wave_filter_{event['id']}",
            )
            if wave_filter != "All start times":
                selected_key = pool_wave_label_key(wave_filter)
                visible = [
                    athlete
                    for athlete in visible
                    if pool_wave_label_key(athlete.get("time_text")) == selected_key
                ]
    if not visible:
        st.info(f"No participating {phase_label.lower()} athletes match this view.")
        return

    meet_id = str(event.get("meet_id") or event["id"])
    meet_events, meet_athletes, _ = load_meet_context(meet_id)
    pod_assignments = [
        assignment
        for child in meet_events
        for assignment in db.list_pod_assignments(child["id"])
    ]
    coach_options, used_coaches = order_coaches_by_assignment_usage(
        list(event["active_coaches"]), meet_athletes, pod_assignments
    )

    def coach_format(coach: str) -> str:
        return coach_assignment_option(coach, used_coaches)

    if phase == "de":
        render_de_pod_assignments(
            db, event, actor, visible, coach_options, used_coaches,
            natural_sort_key=natural_sort_key, coach_format=coach_format,
        )
        render_de_individual_assignments(
            db, event, actor, visible, coach_options, used_coaches,
            natural_sort_key=natural_sort_key, coach_format=coach_format,
        )
        st.markdown("#### Current plan")
        unassigned = sorted(
            [a for a in visible if not has_assignment(a)], key=assignment_athlete_sort_key,
        )
        assigned = sorted(
            [a for a in visible if has_assignment(a)], key=assignment_athlete_sort_key,
        )
        if unassigned:
            st.markdown(f"**Still to assign · {len(unassigned)}**")
            for athlete in unassigned:
                render_assignment_card(athlete, assigned=False)
        else:
            st.success(f"All {len(visible)} direct elimination athletes have a coach assignment.")
        if assigned:
            with st.expander(f"✅ Assigned · {len(assigned)}", expanded=False):
                for athlete in assigned:
                    render_assignment_card(athlete, assigned=True)
        return

    unassigned = sorted(
        (a for a in visible if not has_assignment(a)),
        key=assignment_athlete_sort_key,
    )
    assigned = sorted(
        (a for a in visible if has_assignment(a)),
        key=assignment_athlete_sort_key,
    )
    ordered = [*unassigned, *assigned]

    with st.expander("Assign selected athletes", expanded=phase == "pools"):
        by_id = {a["id"]: a for a in ordered}
        selection_key = f"selected_assign_{event['id']}_{phase}"
        clear_key = f"{selection_key}_clear"
        if st.session_state.pop(clear_key, False):
            st.session_state[selection_key] = []
        st.caption("Unassigned athletes are first. Select a ✓ athlete again whenever you need to change its coaches.")
        selected = st.multiselect(
            "Athletes",
            list(by_id),
            format_func=lambda athlete_id: assignment_option(by_id[athlete_id]),
            key=selection_key,
        )
        main = st.selectbox(
            "Main coach",
            ["No change", "None", *coach_options],
            format_func=coach_format,
            key=f"main_{event['id']}_{phase}",
        )
        side = st.selectbox(
            "Side coach",
            ["No change", "None", *coach_options],
            format_func=coach_format,
            key=f"side_{event['id']}_{phase}",
        )
        if st.button(
            f"Apply to {len(selected)} athlete{'s' if len(selected) != 1 else ''}",
            type="primary",
            width="stretch",
            disabled=(
                not selected
                or (main == "No change" and side == "No change")
                or event["status"] == "locked"
                or not actor
            ),
            key=f"apply_assign_{event['id']}_{phase}",
        ):
            conflicts = []
            for athlete_id in selected:
                athlete = by_id[athlete_id]
                final_main = athlete.get("main_coach", "") if main == "No change" else ("" if main == "None" else main)
                final_side = athlete.get("side_coach", "") if side == "No change" else ("" if side == "None" else side)
                if final_main and final_main == final_side:
                    conflicts.append(athlete["name"])
            if conflicts:
                st.error(
                    "Main and Side coach must be different for: "
                    + ", ".join(conflicts)
                    + "."
                )
            else:
                main_value = NO_CHANGE if main == "No change" else ("" if main == "None" else main)
                side_value = NO_CHANGE if side == "No change" else ("" if side == "None" else side)
                try:
                    count = db.assign_athletes(
                        event["id"], selected, main_coach=main_value, side_coach=side_value, actor=actor
                    )
                    st.session_state[clear_key] = True
                    st.toast(f"{count} assignments saved.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)

    st.markdown("#### Current plan")
    if unassigned:
        st.markdown(f"**Still to assign · {len(unassigned)}**")
        for athlete in unassigned:
            render_assignment_card(athlete, assigned=False)
    else:
        st.success(f"All {len(visible)} {phase_label.lower()} athletes have a coach assignment.")

    if assigned:
        with st.expander(f"✅ Assigned · {len(assigned)}", expanded=False):
            st.caption("These athletes remain selectable above if an assignment needs to be changed.")
            for athlete in assigned:
                render_assignment_card(athlete, assigned=True)


def publish_call_snapshot(event_id, athlete_id, actor, version, status_key, location_key,
                          coverage_key, self_cover_key, reset_key, coach_versions):
    status = {"Not called": "waiting", "In the Hole": "in_hole", "On Deck": "on_deck", "Now": "now"}.get(st.session_state.get(status_key), "waiting")
    location = str(st.session_state.get(location_key) or "").strip()
    if status == "waiting":
        location = ""
    covered_by = None
    if coverage_key:
        choice = str(st.session_state.get(coverage_key) or "")
        if choice == "Needs coach":
            covered_by = ""
        elif choice and not choice.startswith("Keep current"):
            covered_by = choice
    elif self_cover_key and st.session_state.get(self_cover_key):
        covered_by = actor
    try:
        db.report_call(event_id, athlete_id, status=status, location=location, actor=actor,
                       covered_by=covered_by, expected_version=version,
                       expected_coach_version=coach_versions.get(covered_by) if covered_by else None)
        st.session_state[reset_key] = int(st.session_state.get(reset_key, 0)) + 1
        message = ("Call updated · Actual strip to confirm."
                   if status != "waiting" and not location else "Call updated.")
        st.session_state["de_fast_notice"] = (True, message)
    except (CompCoachError, ValueError) as exc:
        st.session_state["de_fast_notice"] = (False, str(exc))
    st.session_state["de_fast_refresh"] = True


def render_quick_update(event: dict, role: str, actor: str, *,
                        athletes: list[dict] | None = None, expanded: bool | None = None) -> None:
    if athletes is None:
        _, athletes, _ = load_meet_context(event["id"])
    active = operational_view_athletes(athletes)
    with st.expander("➕ Report athlete location",
                     expanded=role == "coordinator" if expanded is None else expanded):
        if not actor:
            st.warning("Choose your name before sending an update.")
            return
        if not active:
            st.info("No active athletes are available.")
            return
        reset_key = f"call_reset_{event['id']}_{role}"
        reset = int(st.session_state.get(reset_key, 0))
        by_id = {a["id"]: a for a in active}
        athlete_id = st.selectbox(
            "Athlete",
            list(by_id),
            format_func=lambda item: athlete_option(by_id[item]),
            key=f"call_athlete_{event['id']}_{role}_{reset}",
        )
        athlete = by_id[athlete_id]
        status_key = f"call_status_{event['id']}_{role}_{reset}"
        status_label = select_nav(
            ["Not called", "In the Hole", "On Deck", "Now"], status_key,
        )
        status = {"Not called": "waiting", "In the Hole": "in_hole", "On Deck": "on_deck", "Now": "now"}[status_label]
        suggested = athlete.get("live_location") or (athlete.get("source_strip") if athlete.get("phase") == "pools" else "") or ""
        location_key = f"call_location_{event['id']}_{role}_{athlete_id}_{reset}_v{athlete['version']}"
        location = st.text_input(
            "📍 **Actual bout strip** (optional)",
            value=suggested,
            placeholder="e.g. C3 · leave blank if unknown",
            help="Publish the call even if the strip is unknown. Add the actual bout strip when you know it.",
            key=location_key,
        )
        coverage_key = self_cover_key = None
        if role in {"admin", "coordinator"}:
            coverage_key = f"call_coverage_{event['id']}_{role}_{reset}_{athlete_id}_v{athlete['version']}"
            current_coverage = athlete.get("covered_by") or "nobody"
            coverage = st.selectbox(
                "Coverage",
                [f"Keep current ({current_coverage})", "Needs coach", *event["active_coaches"]],
                key=coverage_key,
            )
            if coverage.startswith("Keep current"):
                covered_by = None
            else:
                covered_by = "" if coverage == "Needs coach" else coverage
        else:
            if athlete.get("covered_by"):
                st.caption(f"Current coverage will stay with {athlete['covered_by']}.")
                covered_by = None
            else:
                self_cover_key = f"call_self_cover_{event['id']}_{reset}_{athlete_id}_v{athlete['version']}"
                covering = st.checkbox(
                    "I am already with this athlete",
                    key=self_cover_key,
                )
                covered_by = actor if covering else None
        coach_versions = {row["coach_name"]: int(row.get("version") or 0)
                          for row in db.list_coach_availability(event["id"])}
        st.button("Publish update", type="primary", width="stretch",
                  disabled=event["status"] != "open", key=f"publish_call_{event['id']}_{role}",
                  on_click=publish_call_snapshot,
                  args=(athlete["event_id"], athlete_id, actor, athlete["version"], status_key,
                        location_key, coverage_key, self_cover_key, reset_key, coach_versions))


def call_sort_key(athlete: dict):
    priority = {"now": 0, "on_deck": 1, "in_hole": 2}.get(athlete.get("call_status"), 3)
    return (is_stale(athlete), priority, athlete.get("reported_at") or "", athlete.get("name") or "")


def personal_role_text(athlete: dict, actor: str) -> str:
    roles = []
    if athlete.get("phase") == "de" and actor in assigned_coach_names(athlete):
        roles.append("◆ Pod coach")
    elif athlete.get("main_coach") == actor:
        roles.append("🔹 Main")
    if athlete.get("phase") != "de" and athlete.get("side_coach") == actor:
        roles.append("🔸 Side")
    if athlete.get("takeover_coach") == actor:
        roles.append("✓ Takeover accepted")
    if athlete.get("covered_by") == actor:
        roles.append("✓ Covering now")
    if athlete.get("help_acknowledged_by") == actor:
        roles.append("🚨 Responding")
    return " · ".join(roles)


def render_pool_result_editor(event: dict, actor: str, athlete: dict) -> None:
    recorded = athlete.get("pool_wins") is not None and athlete.get("pool_losses") is not None
    if recorded:
        byline = f" · saved by {athlete['pool_result_by']}" if athlete.get("pool_result_by") else ""
        st.markdown(
            f"<div class='cc-result'>Pools: {athlete['pool_wins']} W · "
            f"{athlete['pool_losses']} L{esc(byline)}</div>",
            unsafe_allow_html=True,
        )
    if athlete["phase"] != "pools" or event["status"] != "open" or not actor:
        return
    result_label = "Edit pool result" if recorded else "Add pool result"
    result_revision = f"{athlete['id']}_{athlete['version']}"
    with st.expander(result_label, expanded=False):
        with st.form(f"pool_result_{result_revision}"):
            choices = list(range(7))
            current_wins = min(max(int(athlete.get("pool_wins") or 0), 0), 6)
            current_losses = min(max(int(athlete.get("pool_losses") or 0), 0), 6)
            wins = st.segmented_control(
                "Wins",
                choices,
                default=current_wins,
                selection_mode="single",
                required=True,
                key=f"pool_wins_{result_revision}",
                width="stretch",
            )
            losses = st.segmented_control(
                "Losses",
                choices,
                default=current_losses,
                selection_mode="single",
                required=True,
                key=f"pool_losses_{result_revision}",
                width="stretch",
            )
            save = st.form_submit_button("Save pool result", width="stretch")
        if save:
            try:
                db.set_pool_result(
                    event["id"],
                    athlete["id"],
                    wins=int(wins),
                    losses=int(losses),
                    actor=actor,
                    expected_version=athlete["version"],
                )
                st.toast(f"{athlete['name']}: {int(wins)} W · {int(losses)} L saved.")
                st.rerun()
            except (CompCoachError, TypeError, ValueError) as exc:
                show_error(exc)


def render_help_alerts(event: dict, role: str, actor: str, athletes: list[dict]) -> None:
    operational = operational_view_athletes(athletes)
    alerts = sorted(
        [athlete for athlete in operational if athlete.get("help_requested_at")],
        key=lambda athlete: (
            bool(athlete.get("help_acknowledged_by")),
            athlete.get("help_requested_at") or "",
        ),
    )
    if not alerts:
        return
    st.markdown("### 🚨 Help needed now")
    for athlete in alerts:
        location = athlete.get("help_location") or athlete_location(athlete)
        elapsed = age_text(athlete.get("help_requested_at")) or "just now"
        requester = athlete.get("help_requested_by") or "A coach"
        responder = athlete.get("help_acknowledged_by") or ""
        with st.container(border=True):
            st.markdown(
                f"<span class='cc-event-tag'>{esc(athlete_event_label(athlete))}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='cc-help'>🚨 {esc(athlete['name'])} · {esc(location)}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(f"**{esc(requester)} needs help** · {esc(elapsed)}")
            if responder:
                response_age = age_text(athlete.get("help_acknowledged_at"))
                suffix = f" · {response_age}" if response_age else ""
                st.success(f"{responder} is coming{suffix}")

            writable = event["status"] == "open" and bool(actor)
            if not writable:
                continue
            can_acknowledge = not responder and actor != requester
            can_close = (
                actor in {requester, responder}
                or role in {"admin", "coordinator"}
            )
            if can_acknowledge and can_close:
                left, right = st.columns(2)
            else:
                left, right = st.container(), st.container()
            with left:
                if can_acknowledge and st.button(
                    "I’m coming",
                    type="primary",
                    width="stretch",
                    key=f"help_ack_{athlete['id']}",
                ):
                    try:
                        db.acknowledge_help(
                            athlete["event_id"],
                            athlete["id"],
                            actor,
                            expected_version=athlete["version"],
                        )
                        st.toast(f"{requester} can see that you are coming.")
                        st.rerun()
                    except CompCoachError as exc:
                        show_error(exc)
            with right:
                close_label = "Resolved" if responder else "Cancel request"
                if can_close and st.button(
                    close_label,
                    width="stretch",
                    key=f"help_close_{athlete['id']}",
                ):
                    try:
                        reason = "cancelled" if not responder and actor == requester else "resolved"
                        db.clear_help(
                            athlete["event_id"],
                            athlete["id"],
                            actor,
                            reason=reason,
                            expected_version=athlete["version"],
                        )
                        st.toast(f"Help request for {athlete['name']} closed.")
                        st.rerun()
                    except CompCoachError as exc:
                        show_error(exc)
    st.divider()


def request_athlete_help_snapshot(event_id: str, athlete_id: str, actor: str, version: int) -> None:
    try:
        saved = db.request_help(event_id, athlete_id, actor, expected_version=version)
        st.session_state["de_fast_notice"] = (True, f"Help request sent for {saved['name']}.")
    except (CompCoachError, TypeError, ValueError) as exc:
        st.session_state["de_fast_notice"] = (False, str(exc))
    st.session_state["de_fast_refresh"] = True


def render_athlete_help_control(
    event: dict, actor: str, athlete: dict, *, key_prefix: str = "",
) -> None:
    if event.get("status") != "open" or not actor or not is_operational_athlete(athlete):
        return
    if athlete.get("phase") == "pools":
        st.caption("Pools: use Need help only for a real emergency, such as several uncovered bouts or an athlete left without support. All coaches are busy; use it sparingly.")
    if athlete.get("help_requested_at"):
        st.caption("🚨 Help request active — status is shown at the top of the board.")
    else:
        st.button(
            "🚨 Need help now", type="primary", width="stretch",
            key=f"{key_prefix}help_request_{athlete['id']}",
            on_click=request_athlete_help_snapshot,
            args=(athlete["event_id"], athlete["id"], actor, int(athlete["version"])),
        )


def render_athlete_card(
    event: dict,
    role: str,
    actor: str,
    athlete: dict,
    *,
    personal: bool = False,
    allow_operational_actions: bool = True,
    afm_bouts: list[dict] | None = None,
    coach_states: list[dict] | None = None,
    meet_state: dict | None = None,
) -> None:
    status = athlete["call_status"]
    icon = CALL_ICONS.get(status, "⚪")
    label = CALL_LABELS.get(status, status)
    status_class = {"now": "cc-now", "on_deck": "cc-deck", "in_hole": "cc-hole"}.get(status, "")
    location = athlete_location(athlete)
    age = age_text(athlete.get("reported_at"))
    stale = is_stale(athlete)
    if athlete.get("phase") == "de" and afm_bouts is None:
        afm_bouts = list_de_bouts(db, event["id"])
    pending_bout = next((
        bout for bout in (afm_bouts or [])
        if bout["status"] == "pending"
        and athlete["id"] in {bout["athlete_a_id"], bout["athlete_b_id"]}
    ), None)
    opponent = None
    if pending_bout:
        opponent = pending_bout["athlete_b"] if pending_bout["athlete_a_id"] == athlete["id"] else pending_bout["athlete_a"]
    with st.container(border=True):
        if athlete.get("event_name"):
            st.markdown(
                f"<span class='cc-event-tag'>{esc(athlete_event_label(athlete))}</span>",
                unsafe_allow_html=True,
            )
        progress = describe_de_progress(athlete, event) if athlete.get("phase") == "de" else {}
        progress_tag = (
            f" <span class='cc-rounds'>· {esc(progress['round_label'])}</span>"
            if progress.get("round_label") else ""
        )
        st.markdown(f"<div class='cc-athlete'>{icon} {esc(athlete['name'])}{progress_tag}</div>", unsafe_allow_html=True)
        if personal:
            role_text = personal_role_text(athlete, actor)
            if role_text:
                st.markdown(
                    f"<div class='cc-roleline'>{esc(role_text)}</div>",
                    unsafe_allow_html=True,
                )
        if athlete["active_state"] == "eliminated":
            st.markdown("<span class='cc-stale'>Eliminated</span>", unsafe_allow_html=True)
        elif status != "waiting":
            suffix = f" · {esc(age)}" if age else ""
            st.markdown(
                f"<span class='{status_class}'>{esc(label)}</span> · <b>{esc(location)}</b>{suffix}",
                unsafe_allow_html=True,
            )
            if stale:
                st.markdown("<span class='cc-stale'>⚠ Possibly outdated — verify before moving</span>", unsafe_allow_html=True)
        else:
            phase = "Pools" if athlete["phase"] == "pools" else "Direct Elimination"
            detail = f"Pool {athlete['pool_no']}" if athlete.get("pool_no") else f"Pod {athlete['pod']}" if athlete.get("pod") else ""
            st.caption(" · ".join(x for x in [phase, detail, location if location != "Location TBD" else ""] if x))

        if athlete.get("phase") == "de":
            reference = str(athlete.get("source_strip") or "")
            st.caption(f"Pod {athlete.get('pod') or 'TBD'}" + (f" · Calling reference {reference}" if reference else ""))
        if athlete.get("reported_by"):
            st.caption(f"Reported by {athlete['reported_by']} · {format_clock(athlete['reported_at'], event['timezone'])}")
        st.markdown(f"<div class='cc-meta'>{esc(planned_coaches(athlete))}</div>", unsafe_allow_html=True)
        if athlete.get("phase") == "de":
            render_athlete_bout_badge(db, event["id"], athlete, bouts=afm_bouts)
        if has_inactive_assignment(athlete, event):
            st.warning("A planned coach is no longer in the active staff list. Reassign this athlete.")
        if athlete.get("takeover_coach"):
            st.caption(f"Takeover accepted by {athlete['takeover_coach']} · {age_text(athlete.get('takeover_at'))}")
        if athlete.get("covered_by"):
            st.markdown(
                f"<span class='cc-covered'>✓ With {esc(athlete['covered_by'])} · {esc(age_text(athlete.get('covered_at')))}</span>",
                unsafe_allow_html=True,
            )

        if personal:
            render_pool_result_editor(event, actor, athlete)
        if athlete.get("phase") == "de":
            last_result = str(athlete.get("last_de_result") or "").title()
            last_note = f" · last: {last_result}" if last_result else ""
            st.markdown(
                f"<div class='cc-result'>{esc(de_progress_text(athlete, event))}"
                f"{esc(last_note)}</div>",
                unsafe_allow_html=True,
            )
            if progress.get("inconsistent"):
                st.caption("Check the starting tableau and recorded DE results; this progress is inconsistent.")

        if not allow_operational_actions:
            return

        writable = event["status"] == "open" and bool(actor)
        if athlete["active_state"] == "eliminated":
            render_missed_coaching_control(
                db, event, actor, athlete,
                key_prefix="personal" if personal else "live", meet_state=meet_state,
            )
            st.caption("Use Correct DE results below to correct a result or restore this athlete.")
            return

        if athlete["phase"] == "pools":
            render_missed_coaching_control(
                db, event, actor, athlete,
                key_prefix="personal" if personal else "live", meet_state=meet_state,
            )
            if personal and writable:
                render_athlete_help_control(event, actor, athlete)

        render_pool_absence_control(
            db, event, actor, athlete,
            key_prefix="personal" if personal else "live",
        )

        if athlete["phase"] == "de":
            render_live_controls(db, event, role, actor, athlete,
                                 key_prefix="personal" if personal else "live", coach_states=coach_states, meet_state=meet_state)
        else:
            render_pool_takeover_arrival(
                db, event, role, actor, athlete,
                key_prefix="personal" if personal else "live", coach_states=coach_states,
            )
            if athlete.get("takeover_coach") == actor:
                pass  # The owner has the guarded physical-arrival control above.
            elif status != "waiting" and not athlete.get("covered_by") and writable and not stale:
                if st.button(
                    f"I’ll cover {athlete['name']}",
                    type="primary",
                    width="stretch",
                    key=f"claim_{athlete['id']}",
                ):
                    try:
                        ok, message = db.claim(event["id"], athlete["id"], actor)
                        (st.toast if ok else st.warning)(message)
                        if ok:
                            st.rerun()
                    except CompCoachError as exc:
                        show_error(exc)
            elif status != "waiting" and not athlete.get("covered_by") and stale:
                st.info("Verify this call before sending a coach. Publish a fresh update above.")

            can_release = writable and athlete.get("covered_by") and (
                role in {"admin", "coordinator"} or athlete.get("covered_by") == actor
            )
            if can_release and st.button(
                "Release coverage", key=f"release_{athlete['id']}", width="stretch"
            ):
                try:
                    db.release(
                        event["id"],
                        athlete["id"],
                        actor,
                        expected_version=athlete["version"],
                    )
                    st.toast(f"{athlete['name']} needs coverage again.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)

            can_clear = status != "waiting" and writable and (
                role in {"admin", "coordinator"} or athlete.get("reported_by") == actor
            )
            if can_clear and st.button(
                "Clear call · back to Waiting",
                key=f"clear_call_{athlete['id']}",
                width="stretch",
            ):
                try:
                    db.clear_call(
                        event["id"],
                        athlete["id"],
                        actor,
                        expected_version=athlete["version"],
                    )
                    st.toast(f"{athlete['name']} moved back to Waiting.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)

        if athlete["phase"] == "de" and writable:
            snapshot = {
                "version": athlete["version"],
                "bout_id": pending_bout["id"] if pending_bout else "",
                "bout_version": pending_bout["version"] if pending_bout else None,
                "opponent_version": opponent["version"] if opponent else None,
            }
            prefix = "personal" if personal else "live"
            with st.container(key=f"cc_result_actions_{prefix}_{athlete['id']}"):
                st.caption(f"{athlete['name']} · {athlete.get('live_location') or 'actual strip to confirm'} · Bout result")
                left, right = st.columns(2)
                for column, outcome in ((left, "won"), (right, "lost")):
                    with column:
                        st.button(outcome.title(), key=f"{outcome}_{athlete['id']}",
                                  width="stretch", on_click=apply_de_result_snapshot,
                                  args=(event["id"], athlete["id"], outcome, actor, snapshot))
            render_athlete_help_control(event, actor, athlete)
            with st.expander(f"More actions · {athlete['name']}", expanded=False):
                render_athlete_pairing_control(
                    db, event, actor, athlete, key_prefix=prefix, bouts=afm_bouts,
                )
                render_missed_coaching_control(
                    db, event, actor, athlete,
                    key_prefix=prefix, meet_state=meet_state,
                )
                if not pending_bout:
                    st.button("Bye", key=f"bye_{athlete['id']}", width="stretch",
                              on_click=apply_de_result_snapshot,
                              args=(event["id"], athlete["id"], "bye", actor, snapshot))


def is_de_advanced(athlete: dict) -> bool:
    return athlete.get("phase") == "de" and is_operational_athlete(athlete) and bool(athlete.get("de_awaiting_next"))


def de_result_snapshot(athlete: dict, bouts: list[dict]) -> dict:
    pending = next((
        bout for bout in bouts if bout["status"] == "pending"
        and athlete["id"] in {bout["athlete_a_id"], bout["athlete_b_id"]}
    ), None)
    opponent = (
        pending["athlete_b"] if pending and pending["athlete_a_id"] == athlete["id"]
        else pending["athlete_a"] if pending else None
    )
    return {
        "version": athlete["version"],
        "bout_id": pending["id"] if pending else "",
        "bout_version": pending["version"] if pending else None,
        "opponent_version": opponent["version"] if opponent else None,
    }


def render_current_bout_actions(meet: dict, actor: str, athlete: dict) -> None:
    """Finish physical coverage from the personal status bar, in one tap."""
    if athlete.get("phase") != "de" or athlete.get("covered_by") != actor:
        return
    if not is_operational_athlete(athlete):
        return
    snapshot = de_result_snapshot(athlete, list_de_bouts(db, athlete["event_id"]))
    child = db.get_event(athlete["event_id"])
    st.markdown(f"**{de_progress_text(athlete, child)}**")
    st.caption(f"{athlete_event_label(athlete)} · Record this bout to finish coverage")
    with st.container(key=f"cc_result_actions_current_{athlete['id']}"):
        won, lost = st.columns(2)
        for column, outcome in ((won, "won"), (lost, "lost")):
            with column:
                st.button(
                    outcome.title(), key=f"current_{outcome}_{athlete['id']}",
                    width="stretch", type="primary" if outcome == "won" else "secondary",
                    disabled=meet.get("status") != "open",
                    on_click=apply_de_result_snapshot,
                    args=(athlete["event_id"], athlete["id"], outcome, actor, snapshot),
                )
    render_athlete_help_control(meet, actor, athlete, key_prefix="current_")
    if child:
        with st.expander("Current bout details", expanded=False):
            st.caption("Edit this athlete's call or strip here. Won, Lost and Need help are above.")
            render_athlete_card(child, "coach", actor, athlete, personal=True, meet_state=meet)


def apply_de_result_snapshot(event_id, athlete_id, outcome, actor, snapshot):
    try:
        if outcome == "bye":
            db.mark_bye(event_id, athlete_id, actor=actor,
                        expected_version=snapshot["version"], expected_bout_id=snapshot["bout_id"])
        else:
            db.mark_result(event_id, athlete_id, outcome=outcome, actor=actor,
                           expected_version=snapshot["version"], expected_bout_id=snapshot["bout_id"],
                           expected_bout_version=snapshot["bout_version"],
                           expected_opponent_version=snapshot["opponent_version"])
        st.session_state["de_fast_notice"] = (True, f"{outcome.title()} saved.")
    except CompCoachError as exc:
        st.session_state["de_fast_notice"] = (False, str(exc))
    st.session_state["de_fast_refresh"] = True


def consume_de_fast_notice():
    if st.session_state.pop("de_fast_refresh", False):
        st.rerun(scope="app")
    notice = st.session_state.pop("de_fast_notice", None)
    if notice:
        (st.success if notice[0] else st.error)(notice[1])


def render_de_wheel(event, role, actor, athletes, event_by_id, *, personal=False, key_prefix="wheel", bouts_by_event=None, prioritize_calls=False, show_heading=True):
    rows = [row for row in athletes if row.get("phase") == "de" and is_operational_athlete(row)]
    if not rows:
        return
    if show_heading:
        st.markdown("### Direct Elimination · live queue")
        st.caption("Live calls come first. Results rotate the waiting queue; pod and actual bout strip stay separate." if prioritize_calls else "Results move to the end of the queue. Pod is the assignment reference; actual strip and call are updated for each bout.")
    ordered = ordered_de_athletes(rows)
    coach_states = db.list_coach_availability(event["id"])
    covered = []
    if prioritize_calls:
        reserved, urgent, verify, waiting, covered = [], [], [], [], []
        for row in ordered:
            if row.get("covered_by") or (row.get("takeover_coach") and row["takeover_coach"] != actor):
                covered.append(row)
            elif row.get("takeover_coach") == actor:
                reserved.append(row)
            elif row.get("call_status") != "waiting":
                (verify if is_stale(row) else urgent).append(row)
            else:
                waiting.append(row)
        for heading, group in (
            ("Your accepted takeovers", reserved),
            ("Next calls · Now → On deck → In the hole", sorted(urgent, key=personal_athlete_sort_key)),
            ("Calls to verify", sorted(verify, key=personal_athlete_sort_key)),
        ):
            if group:
                st.markdown(f"**{heading} · {len(group)}**")
                for row in group:
                    render_athlete_card(event_by_id[row["event_id"]], role, actor, row,
                                        personal=personal, afm_bouts=(bouts_by_event or {}).get(row["event_id"]),
                                        coach_states=coach_states, meet_state=event)
        ordered = waiting
        if waiting:
            st.markdown(f"**Not called yet · {len(waiting)}**")
    last_round = None
    for athlete in ordered:
        rounds = int(athlete.get("de_rounds_passed", int(athlete.get("de_wins") or 0) + int(athlete.get("de_byes") or 0)))
        if rounds != last_round:
            if last_round is not None:
                st.divider()
            st.markdown("**Current bout · no rounds passed**" if rounds == 0 else f"**Next bout · {rounds} round{'s' if rounds != 1 else ''} passed**")
            last_round = rounds
        render_athlete_card(event_by_id[athlete["event_id"]], role, actor, athlete,
                            personal=personal,
                            afm_bouts=(bouts_by_event or {}).get(athlete["event_id"]),
                            coach_states=coach_states, meet_state=event)
    if covered:
        with st.expander(f"Covered / taken by a colleague · {len(covered)}", expanded=False):
            for row in covered:
                render_athlete_card(event_by_id[row["event_id"]], role, actor, row,
                                    personal=personal, afm_bouts=(bouts_by_event or {}).get(row["event_id"]),
                                    coach_states=coach_states, meet_state=event)


def render_live_board(event: dict, role: str, actor: str) -> None:
    consume_de_fast_notice()
    events, athletes, event_by_id = load_meet_context(event["id"])
    bouts_by_event = {
        child["id"]: list_de_bouts(db, child["id"])
        for child in events if any(row["phase"] == "de" and row["event_id"] == child["id"] for row in athletes)
    }
    active = operational_view_athletes(athletes)
    out = [a for a in athletes if is_competitive_out(a)]
    needs = [a for a in active if a["call_status"] != "waiting" and not a["covered_by"]]
    stale_needs = [a for a in needs if is_stale(a)]
    current_needs = [a for a in needs if not is_stale(a)]
    covered = [a for a in active if a["covered_by"]]
    waiting = [a for a in active if a["call_status"] == "waiting"]
    active_de = [a for a in active if a["phase"] == "de"]
    statuses = db.list_coach_availability(event["id"])
    available_count = sum(bool(row.get("is_available")) for row in statuses)
    st.markdown("### Team situation")
    current_status = next((row for row in statuses if row["coach_name"] == actor), {})
    if current_status.get("is_busy"):
        render_availability_control(event, actor, athletes, compact=True)
    st.markdown(
        f"<div class='cc-summary'><span class='cc-now'>{len(current_needs)} need coverage</span> · "
        f"<span class='cc-covered'>{available_count} coach{'es' if available_count != 1 else ''} available</span> · "
        f"{len(active_de)} still in DE · {len(covered)} covered</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(stale_needs)} calls to verify · {len(waiting)} waiting · {len(out)} out")
    if role in {"admin", "coordinator"}:
        render_emergency_coverage_request(event, role, actor, athletes, statuses)
        render_assignment_confirmations(event, actor)
    views = ["Uncovered", "Needs Coach", "Covered Now", "No Current Call", "Out"]
    view = select_nav(views, f"live_view_{event['id']}_{role}")
    migrated_panel = st.session_state.get(f"live_migrated_panel_{event['id']}_{role}")
    panel_key = f"live_panel_{event['id']}_{role}"
    with st.expander("Coaches", expanded=False, key=f"{panel_key}_coaches", type="compact"):
        if actor in event["active_coaches"] and not current_status.get("is_busy"):
            render_availability_control(event, actor, athletes, compact=True)
        render_coach_availability_summary(event, role, actor, athletes, statuses=statuses)
        render_available_coach_deploy(event, role, actor, events, athletes, statuses)
    with st.expander("DE sector load · all assignments", expanded=False,
                     key=f"{panel_key}_pods", type="compact"):
        render_de_sector_overview(event, actor, events, athletes)
    with st.expander("All assignments", expanded=migrated_panel == "Assignments",
                     key=f"{panel_key}_assignments", type="compact"):
        render_team_plan(event, actor, show_heading=False)
    with st.expander("Pool results", expanded=migrated_panel == "Pool Results",
                     key=f"{panel_key}_results", type="compact"):
        render_pool_results_summary(events, athletes)
    render_missed_coaching_review(
        db, event, actor, role, read_only=event.get("status") != "open",
        include_training=bool(get_training(db, event["id"])),
    )
    render_quick_update(event, role, actor, athletes=athletes, expanded=False)
    for child in events:
        if any(row.get("phase") == "de" and row["event_id"] == child["id"] for row in athletes):
            render_de_bouts(db, child, actor, key_prefix="live")
    mapping = {
        "Uncovered": [row for row in active if not row.get("covered_by")],
        "Needs Coach": sorted(needs, key=call_sort_key),
        "Covered Now": sorted(covered, key=call_sort_key),
        "No Current Call": waiting,
        "Out": out,
    }
    selected = mapping[view]
    # The coach's current athlete already has its full controls in the hero.
    # Avoid rendering the same widgets twice when Covered Now is selected.
    if current_status.get("is_busy"):
        selected = [row for row in selected if row["id"] != current_status.get("busy_athlete_id")]
    if not selected:
        empty_messages = {
            "Uncovered": "Every active athlete is currently covered.",
            "Needs Coach": "No athlete currently needs coverage.",
            "Covered Now": (
                "Your current athlete is shown above. No other athlete is currently covered."
                if current_status.get("is_busy") else "No athlete is currently covered."
            ),
            "No Current Call": "Every active athlete currently has a live call.",
            "Out": "No athletes have been marked out.",
        }
        if view == "Needs Coach":
            st.success(empty_messages[view])
        else:
            st.info(empty_messages[view])
    for index, group in enumerate(group_team_athletes_by_coach(selected)):
        with st.container(key=f"team_coach_{event['id']}_{index}"):
            st.markdown(
                f"<span class='cc-event-tag cc-team-coach-label'>{esc(group['label'])}</span>",
                unsafe_allow_html=True,
            )
            for athlete in group["athletes"]:
                if athlete.get("phase") == "de" and is_operational_athlete(athlete):
                    continue
                render_athlete_card(
                    event_by_id[athlete["event_id"]], role, actor, athlete,
                    personal=False, afm_bouts=bouts_by_event.get(athlete["event_id"], []),
                )
            render_de_wheel(
                event, role, actor, group["athletes"], event_by_id,
                key_prefix="live", bouts_by_event=bouts_by_event, show_heading=False,
            )
    render_absent_pool_athletes(
        db, event, actor, athletes, event_by_id, key_prefix="live",
    )
    render_de_result_corrections(db, event, actor, athletes, event_by_id, key_prefix="live")
    synced_at = datetime.now(ZoneInfo(event["timezone"])).strftime("%I:%M:%S %p").lstrip("0")
    st.caption(f"Last synced {synced_at} · refreshes automatically")


def personal_athlete_sort_key(athlete: dict) -> tuple:
    call_priority = {"now": 0, "on_deck": 1, "in_hole": 2, "waiting": 3}.get(
        athlete.get("call_status"), 4
    )
    phase_priority = 0 if athlete.get("phase") == "pools" else 1
    return (
        athlete.get("active_state") != "active",
        call_priority,
        phase_priority,
        natural_sort_key(athlete.get("time_text") or "ZZZ"),
        natural_sort_key(athlete.get("pod") or "ZZZ"),
        natural_sort_key(athlete_location(athlete)),
        natural_sort_key(athlete.get("name") or ""),
    )


def personal_event_priority(event_id: str, rows: list[dict], actor: str) -> tuple:
    event_rows = [row for row in rows if row["event_id"] == event_id]
    priorities = []
    for row in event_rows:
        if row.get("covered_by") == actor or row.get("help_acknowledged_by") == actor:
            priorities.append(0)
        elif row.get("call_status") == "now":
            priorities.append(1)
        elif row.get("call_status") in {"on_deck", "in_hole"}:
            priorities.append(2)
        elif actor in assigned_coach_names(row) and (row.get("phase") == "de" or row.get("main_coach") == actor):
            priorities.append(3)
        else:
            priorities.append(4)
    return (min(priorities, default=9),)


def render_availability_control(
    event: dict,
    actor: str,
    athletes: list[dict],
    *,
    compact: bool = False,
) -> None:
    """Render the current coach's explicit, meet-wide availability control."""

    if actor not in event["active_coaches"]:
        return
    statuses = {
        row["coach_name"]: row for row in db.list_coach_availability(event["id"])
    }
    status = statuses.get(
        actor,
        {
            "coach_name": actor,
            "is_available": False,
            "available_since": None,
            "version": 0,
        },
    )
    workload = coach_workload(athletes, actor)
    unfinished = int(workload["unfinished_count"])
    available = bool(status.get("is_available"))
    busy = bool(status.get("is_busy"))
    takeover_count = int(status.get("takeover_count") or 0)
    since = age_text(status.get("available_since"))
    state_class = " cc-availability-on" if available else ""
    if busy:
        state = f"🔴 With {esc(status.get('busy_athlete_name') or 'an athlete')}"
        location = status.get("busy_location") or "TBD"
        elapsed = age_text(status.get("busy_since"))
        meta = f"Actual strip {location}" + (f" · {elapsed}" if elapsed else "")
    elif takeover_count:
        state = "🟠 Takeover accepted"
        name = status.get("takeover_athlete_name") or "Athlete"
        elapsed = age_text(status.get("takeover_since"))
        meta = str(name) + (f" · {elapsed}" if elapsed else "")
        if takeover_count > 1:
            meta += f" · {takeover_count - 1} other takeover(s)"
        meta += ". You are not available to help."
    elif available:
        state = "🟢 Available to help"
        meta = f"Declared {since}" if since else "Declared available"
    elif unfinished:
        state = "⚪ Not marked available"
        meta = f"The board still shows {unfinished} active dut{'y' if unfinished == 1 else 'ies'}."
    else:
        state = "⚪ Ready when you are"
        meta = "No unfinished duty is visible. Tap below to make yourself available to help."
    if busy:
        current = next((row for row in athletes if row["id"] == status.get("busy_athlete_id")), None)
        with st.container(border=True):
            st.markdown(
                f"<div class='cc-availability-title'>{state}</div>"
                f"<div class='cc-availability-meta'>{esc(meta)}</div>",
                unsafe_allow_html=True,
            )
            if current:
                render_current_bout_actions(event, actor, current)
    else:
        st.markdown(
            f"<div class='cc-availability{state_class}'>"
            f"<div class='cc-availability-title'>{state}</div>"
            f"<div class='cc-availability-meta'>{esc(meta)}</div></div>",
            unsafe_allow_html=True,
        )

    writable = event["status"] == "open"
    if busy or takeover_count:
        st.caption("Record the result or release your coverage/takeovers before becoming available again.")
        st.button(
            "I’m available to help",
            type="primary",
            key=f"availability_on_{event['id']}_{safe_key(actor)}",
            width="stretch",
            disabled=True,
        )
        return
    if available:
        if st.button(
            "I’m no longer available",
            key=f"availability_off_{event['id']}_{safe_key(actor)}",
            width="stretch",
            disabled=not writable,
        ):
            try:
                db.set_coach_availability(
                    event["id"],
                    actor,
                    False,
                    actor,
                    expected_version=int(status.get("version") or 0),
                )
                st.toast("Availability removed.")
                st.rerun()
            except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
                show_error(exc)
        return

    confirmed = not unfinished
    if unfinished:
        if not compact:
            st.warning(
                "Your data still shows active work. Mark yourself available only "
                "if the field situation has already changed."
            )
        confirmed = st.checkbox(
            f"I confirm I am free despite {unfinished} active "
            f"dut{'y' if unfinished == 1 else 'ies'}",
            key=(
                f"availability_override_{event['id']}_{safe_key(actor)}_"
                f"{int(status.get('version') or 0)}"
            ),
        )
    if st.button(
        "I’m available to help",
        type="primary",
        key=f"availability_on_{event['id']}_{safe_key(actor)}",
        width="stretch",
        disabled=not writable or not confirmed,
    ):
        try:
            db.set_coach_availability(
                event["id"],
                actor,
                True,
                actor,
                expected_version=int(status.get("version") or 0),
            )
            st.toast("You are now visible as available to help.")
            st.rerun()
        except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
            show_error(exc)


def render_my_group(event: dict, role: str, actor: str) -> None:
    consume_de_fast_notice()
    events, athletes, event_by_id = load_meet_context(event["id"])
    bouts_by_event = {
        child["id"]: list_de_bouts(db, child["id"])
        for child in events if any(row["phase"] == "de" and row["event_id"] == child["id"] for row in athletes)
    }
    operational_ids = {row["id"] for row in operational_view_athletes(athletes)}
    planned = [
        athlete
        for athlete in athletes
        if actor in assigned_coach_names(athlete)
    ]
    temporary = [
        athlete
        for athlete in athletes
        if athlete not in planned
        and actor in {athlete.get("covered_by"), athlete.get("help_acknowledged_by"), athlete.get("takeover_coach")}
    ]
    mine = [*planned, *temporary]
    participating_mine = [row for row in mine if is_operational_athlete(row)]
    active_mine = [
        row
        for row in participating_mine
        if row["id"] in operational_ids
    ]
    completed_pools = [
        row
        for row in participating_mine
        if row.get("phase") == "pools"
        and row.get("pool_wins") is not None
        and row.get("pool_losses") is not None
    ]
    completed_pool_ids = {row["id"] for row in completed_pools}
    working_mine = [
        row for row in active_mine if row["id"] not in completed_pool_ids
    ]
    out_mine = [row for row in mine if is_competitive_out(row)]
    main_count = sum(row.get("phase") == "pools" and row.get("main_coach") == actor for row in working_mine)
    side_count = sum(row.get("phase") == "pools" and row.get("side_coach") == actor for row in working_mine)
    de_count = sum(row.get("phase") == "de" and actor in assigned_coach_names(row) for row in working_mine)
    temporary_count = sum(row in temporary for row in working_mine)

    st.markdown("### My group")
    render_availability_control(event, actor, athletes)
    render_assignment_notice_panel(event, role, actor)
    render_busy_coach_board(db, event, event_by_id, athletes, role, actor, key_prefix="global")
    render_help_alerts(event, role, actor, athletes)
    current_de = [row for row in active_mine if row.get("phase") == "de" and row.get("covered_by") == actor]
    completed_label = (
        "1 completed pool"
        if len(completed_pools) == 1
        else f"{len(completed_pools)} completed pools"
    )
    st.caption(
        f"{len(working_mine)} active work · {completed_label}"
        + (f" · {main_count} Main · {side_count} Side" if main_count or side_count else "")
        + (f" · {de_count} DE" if de_count else "")
        + (f" · {temporary_count} temporary" if temporary_count else "")
    )
    if not mine:
        st.info("No athletes are assigned to you right now. Open Team situation → All assignments to see the full staff plan.")
        return

    visible_event_ids = {row["event_id"] for row in working_mine if row.get("phase") == "pools"}
    ordered_events = sorted(
        [child for child in events if child["id"] in visible_event_ids],
        key=lambda child: (
            *personal_event_priority(child["id"], mine, actor),
            int(child.get("sort_order") or 0),
        ),
    )
    for child in ordered_events:
        child_rows = sorted(
            [row for row in working_mine if row["event_id"] == child["id"] and row["phase"] == "pools"],
            key=personal_athlete_sort_key,
        )
        st.markdown(f"#### ⭐ {esc(child['name'])}")
        planned_rows = [row for row in child_rows if row in planned]
        temporary_rows = [row for row in child_rows if row in temporary]
        for phase, phase_label in (("pools", "Pools"), ("de", "Direct Elimination")):
            phase_rows = [row for row in planned_rows if row["phase"] == phase]
            if phase_rows:
                st.markdown(f"**{phase_label}**")
                if phase == "de":
                    render_de_bouts(db, child, actor, key_prefix="personal")
                for athlete in phase_rows:
                    render_athlete_card(
                        event_by_id[athlete["event_id"]],
                        role,
                        actor,
                        athlete,
                        personal=True,
                        afm_bouts=bouts_by_event.get(athlete["event_id"], []),
                    )
        if temporary_rows:
            st.markdown("**Temporary coverage / responding**")
            for athlete in temporary_rows:
                render_athlete_card(
                    event_by_id[athlete["event_id"]],
                    role,
                    actor,
                    athlete,
                    personal=True,
                    afm_bouts=bouts_by_event.get(athlete["event_id"], []),
                )

    queued_de = [row for row in active_mine if row not in current_de]
    render_de_wheel(event, role, actor, queued_de, event_by_id, personal=True,
                    key_prefix="personal", bouts_by_event=bouts_by_event, prioritize_calls=True)
    for child in events:
        if any(row["phase"] == "de" and row["event_id"] == child["id"] for row in active_mine):
            render_de_bouts(db, child, actor, key_prefix="personal")

    if completed_pools:
        completed_event_ids = {row["event_id"] for row in completed_pools}
        completed_events = [
            child for child in events if child["id"] in completed_event_ids
        ]
        with st.expander(
            f"Completed pool results · {len(completed_pools)}", expanded=False
        ):
            st.caption(
                "Finished pool entries stay editable here and no longer crowd "
                "the active coaching list."
            )
            for child in completed_events:
                st.markdown(f"**{esc(child['name'])}**")
                child_completed = sorted(
                    [
                        row
                        for row in completed_pools
                        if row["event_id"] == child["id"]
                    ],
                    key=personal_athlete_sort_key,
                )
                for athlete in child_completed:
                    render_athlete_card(
                        event_by_id[athlete["event_id"]],
                        role,
                        actor,
                        athlete,
                        personal=True,
                        allow_operational_actions=False,
                    )

    if out_mine:
        with st.expander(f"Completed / Out · {len(out_mine)}", expanded=False):
            for athlete in sorted(out_mine, key=personal_athlete_sort_key):
                st.markdown(
                    f"**{esc(athlete['name'])}** · {esc(athlete_event_label(athlete))}"
                )
            st.caption("Use Correct DE results below if a result needs to be corrected.")

    render_absent_pool_athletes(
        db, event, actor, mine, event_by_id, key_prefix="personal",
    )


    render_de_result_corrections(db, event, actor, athletes, event_by_id, key_prefix="personal")


def render_assignment_notice_panel(meet: dict, role: str, actor: str) -> None:
    if actor not in meet.get("active_coaches", []) or meet.get("status") != "open":
        return
    notices = visible_pool_wave_rows(db.list_coverage_requests(meet["id"], actor))
    if not notices:
        return
    own_status = next((row for row in db.list_coach_availability(meet["id"])
                       if row["coach_name"] == actor), {})
    with st.container(border=True):
        label = "Coverage request" if len(notices) == 1 else f"{len(notices)} coverage requests"
        st.markdown(f"<div class='cc-new-assignment'>🚨 {label}</div>", unsafe_allow_html=True)
        st.caption("First coach to accept takes responsibility. Confirm arrival when you are with the athlete.")
        for notice in sorted(notices, key=lambda row: ({"now": 0, "on_deck": 1, "in_hole": 2}.get(row.get("call_status"), 3), row["created_at"])):
            st.markdown(f"<div class='cc-assignment-name'>{esc(notice['athlete_name'])}</div>", unsafe_allow_html=True)
            call = CALL_LABELS.get(notice.get("call_status"), "Not called yet")
            st.markdown(f"**{esc(call)}** · {esc(assignment_notice_details(notice))}")
            st.caption(f"Requested by {notice.get('created_by') or 'Coordinator'} · {age_text(notice.get('created_at'))}")
            recipients = notice.get("recipients") or []
            names = [row["coach_name"] for row in recipients]
            if len(names) > 1:
                st.caption("Also notified: " + " / ".join(name for name in names if name != actor))
            if not own_status.get("is_available"):
                st.caption("You are not currently available. Finish or release your current responsibility first.")
            st.button(
                "I'll cover this bout", key=f"accept_coverage_request_{notice['id']}",
                width="stretch", type="primary", disabled=not own_status.get("is_available"),
                on_click=accept_coverage_request_snapshot,
                args=(meet["id"], notice["id"], actor,
                      int(notice["version"]), int(notice["athlete_version"]),
                      int(own_status.get("version") or 0)),
            )


def accept_coverage_request_snapshot(
    meet_id: str, request_id: str, actor: str,
    request_version: int, athlete_version: int, coach_version: int,
) -> None:
    try:
        db.accept_coverage_request(
            meet_id, request_id, actor, actor=actor,
            expected_request_version=request_version,
            expected_version=athlete_version,
            expected_coach_version=coach_version,
        )
        st.session_state["de_fast_notice"] = (True, "You have taken responsibility for this bout. Confirm arrival in the athlete card.")
    except (CompCoachError, ValueError) as exc:
        st.session_state["de_fast_notice"] = (False, str(exc))
    st.session_state["de_fast_refresh"] = True


def send_coverage_request_snapshot(
    meet_id: str, athlete: dict, coaches: list[str], actor: str,
    coach_versions: dict[str, int],
) -> None:
    try:
        db.create_coverage_request(
            meet_id, athlete["event_id"], athlete["id"], coach_names=coaches, actor=actor,
            expected_version=int(athlete["version"]),
            expected_coach_versions=coach_versions,
        )
        st.session_state["de_fast_notice"] = (True, f"Coverage requested for {athlete['name']} · " + " / ".join(coaches) + ".")
    except (CompCoachError, ValueError) as exc:
        st.session_state["de_fast_notice"] = (False, str(exc))
    st.session_state["de_fast_refresh"] = True


def render_emergency_coverage_request(
    meet: dict, role: str, actor: str, athletes: list[dict], statuses: list[dict],
) -> None:
    if role not in {"admin", "coordinator"} or meet.get("status") != "open":
        return
    with st.expander("🚨 Request coverage for a bout", expanded=False):
        st.caption("For an unexpected bout needing help. Notify one or more available coaches; the first acceptance closes the request for everyone else. Your ordinary pool and pod plan stays in place.")
        available = {row["coach_name"]: row for row in statuses if row.get("is_available")}
        if not available:
            st.info("No coach is currently marked available to help.")
            return
        pending_ids = {row["athlete_id"] for row in db.list_coverage_requests(meet["id"])}
        eligible = [row for row in operational_view_athletes(athletes)
                    if not row.get("covered_by") and not row.get("takeover_coach")
                    and row["id"] not in pending_ids
                    and not (row["phase"] == "pools" and row.get("pool_result_at"))]
        if not eligible:
            st.info("No uncovered athlete is available for a new request.")
            return
        by_id = {row["id"]: row for row in sorted(eligible, key=call_sort_key)}
        athlete_id = st.selectbox(
            "Athlete needing coverage", list(by_id),
            format_func=lambda item: athlete_option(by_id[item]),
            key=f"emergency_request_athlete_{meet['id']}_{role}",
        )
        athlete = by_id[athlete_id]
        st.markdown(f"**{esc(athlete['name'])}** · {esc(CALL_LABELS.get(athlete.get('call_status'), 'Not called yet'))} · {esc(assignment_notice_details(athlete))}")
        coaches = st.multiselect(
            "Available coaches to notify", list(available),
            key=f"emergency_request_coaches_{meet['id']}_{role}",
        )
        st.caption("Requests expire after 15 minutes. Cancel and send a new request if circumstances change.")
        st.button(
            "Request coverage", width="stretch", type="primary", disabled=not coaches or not actor,
            key=f"emergency_request_send_{meet['id']}_{role}",
            on_click=send_coverage_request_snapshot,
            args=(meet["id"], dict(athlete), list(coaches), actor,
                  {name: int(available[name].get("version") or 0) for name in coaches}),
        )


def render_assignment_confirmations(meet: dict, actor: str) -> None:
    notices = db.list_coverage_requests(meet["id"], pending_only=False)
    with st.expander("Coverage requests · responses", expanded=False):
        pending = sum(row["status"] == "pending" for row in notices)
        accepted = sum(row["status"] == "accepted" for row in notices)
        st.caption(f"{pending} awaiting response · {accepted} accepted")
        if not notices:
            st.info("No coverage requests. Ordinary pool and pod assignments need no acceptance.")
        recent = sorted(notices, key=lambda row: row["created_at"], reverse=True)
        for notice in sorted(recent, key=lambda row: row["status"] != "pending")[:40]:
            state = "✅ Taken by " + str(notice.get("accepted_by") or "") if notice["status"] == "accepted" else "🟠 Awaiting response" if notice["status"] == "pending" else notice["status"].title()
            st.markdown(f"**{esc(notice['athlete_name'])}** · {esc(state)}")
            st.caption(assignment_notice_details(notice))
            recipients = " / ".join(row["coach_name"] for row in notice.get("recipients") or [])
            st.caption(f"Notified: {recipients} · Requested by {notice.get('created_by')} · {age_text(notice['created_at'])}")
            if notice["status"] == "pending" and st.button("Cancel request", key=f"cancel_coverage_request_{notice['id']}", width="stretch"):
                try:
                    db.cancel_coverage_request(meet["id"], notice["id"], actor=actor, expected_version=int(notice["version"]))
                    st.toast("Coverage request cancelled.")
                    st.rerun(scope="app")
                except (CompCoachError, ValueError) as exc:
                    show_error(exc)


def assignment_details(athlete: dict) -> str:
    details = []
    if athlete.get("phase") == "pools":
        if athlete.get("time_text"):
            details.append(str(athlete["time_text"]))
        if athlete.get("source_strip"):
            details.append(str(athlete["source_strip"]))
        if athlete.get("pool_no"):
            details.append(f"Pool {athlete['pool_no']}")
    else:
        if athlete.get("pod"):
            details.append(f"Pod {athlete['pod']}")
        location = athlete_location(athlete)
        if location != "Location TBD" and location not in details:
            details.append(location)
        if athlete.get("de_wins") or athlete.get("de_byes"):
            details.append(de_progress_text(athlete))
    if athlete.get("call_status") != "waiting":
        details.append(CALL_LABELS.get(athlete["call_status"], athlete["call_status"]))
    return " · ".join(details) or "Location TBD"


def plan_group_html(coach: str, main_rows: list[dict], side_rows: list[dict]) -> str:
    row_html = []
    for diamond, rows, is_main in (("🔹", main_rows, True), ("🔸", side_rows, False)):
        for athlete in sorted(rows, key=is_de_advanced):
            if athlete.get("phase") == "de":
                others = [name for name in assigned_coach_names(athlete) if name != coach]
                other_note = " · With: " + esc(" / ".join(others)) if others else ""
                row_html.append(
                    f"<div class='cc-plan-row'>◆ {esc(athlete['name'])}{' · ✅ Next round' if is_de_advanced(athlete) else ''}<br>"
                    f"<span class='cc-plan-meta'>{esc(assignment_details(athlete))}{other_note}</span></div>"
                )
                continue
            name = f"<b>{esc(athlete['name'])}</b>" if is_main else esc(athlete["name"])
            other = athlete.get("side_coach") if is_main else athlete.get("main_coach")
            other_label = "Side" if is_main else "Main"
            other_note = f" · {other_label}: {esc(other)}" if other else ""
            row_html.append(
                f"<div class='cc-plan-row'>{diamond} {name}<br>"
                f"<span class='cc-plan-meta'>{esc(assignment_details(athlete))}{other_note}</span></div>"
            )
    return (
        "<div class='cc-plan-group'>"
        f"<div class='cc-plan-coach'>🤺 {esc(coach)} · {len(main_rows) + len(side_rows)}</div>"
        f"{''.join(row_html)}</div>"
    )


def render_team_plan(event: dict, actor: str, *, show_heading: bool = True) -> None:
    events, athletes, _ = load_meet_context(event["id"])
    active = operational_view_athletes(athletes)
    if show_heading:
        st.markdown("### Team plan")
    st.caption("Pools: 🔹 Main · 🔸 Side. Direct Elimination: ◆ equal pod coaches.")
    if not active:
        st.info("No active athletes have been imported yet.")
        return

    actor_event_ids = {
        row["event_id"]
        for row in active
        if actor in assigned_coach_names(row)
    }
    for index, child in enumerate(events):
        child_rows = [row for row in active if row["event_id"] == child["id"]]
        mine = child["id"] in actor_event_ids
        label = f"{'⭐ ' if mine else ''}{child['name']} · {len(child_rows)} active"
        with st.expander(label, expanded=mine or (len(events) == 1 and index == 0)):
            active_staff = set(event["active_coaches"])
            unassigned = [
                row
                for row in child_rows
                if not assigned_coach_names(row)
                or bool(set(assigned_coach_names(row)) - active_staff)
            ]
            if unassigned:
                st.warning(
                    "Needs assignment or reassignment: "
                    + ", ".join(row["name"] for row in unassigned)
                )
            for phase, phase_label in (("pools", "Pools"), ("de", "Direct Elimination")):
                phase_rows = [row for row in child_rows if row["phase"] == phase]
                if not phase_rows:
                    continue
                st.markdown(f"**{phase_label}**")
                coaches = list(event["active_coaches"])
                if actor in coaches:
                    coaches.remove(actor)
                    coaches.insert(0, actor)
                for coach in coaches:
                    main_rows = sorted(
                        [row for row in phase_rows if (coach in assigned_coach_names(row) if phase == "de" else row.get("main_coach") == coach)],
                        key=personal_athlete_sort_key,
                    )
                    side_rows = sorted(
                        [
                            row
                            for row in phase_rows
                            if phase == "pools" and row.get("side_coach") == coach
                            and row.get("main_coach") != coach
                        ],
                        key=personal_athlete_sort_key,
                    )
                    if main_rows or side_rows:
                        st.markdown(
                            plan_group_html(coach, main_rows, side_rows),
                            unsafe_allow_html=True,
                        )
            out_count = sum(
                row["event_id"] == child["id"] and is_competitive_out(row)
                for row in athletes
            )
            if out_count:
                st.caption(f"{out_count} completed/out athlete(s) are hidden from the active plan.")


def render_coach_availability_summary(
    event: dict,
    role: str,
    actor: str,
    athletes: list[dict],
    *,
    statuses: list[dict] | None = None,
) -> list[dict]:
    if statuses is None:
        statuses = db.list_coach_availability(event["id"])
    if not statuses:
        st.info("No active coaches are configured for this competition day.")
        return []

    chips = []
    contradictions = []
    for status in statuses:
        coach = str(status["coach_name"])
        workload = coach_workload(athletes, coach)
        unfinished = int(workload["unfinished_count"])
        if status.get("is_available"):
            elapsed = age_text(status.get("available_since"))
            suffix = f" · {elapsed}" if elapsed else ""
            chips.append(
                f"<span class='cc-coach-chip cc-coach-chip-on'>🟢 {esc(coach)}{esc(suffix)}</span>"
            )
            if unfinished:
                contradictions.append(f"{coach} ({unfinished} active)")
        elif status.get("is_busy"):
            suffix = f" · with {status.get('busy_athlete_name') or 'an athlete'}"
            chips.append(
                f"<span class='cc-coach-chip'>🔴 {esc(coach)}{esc(suffix)}</span>"
            )
        elif status.get("takeover_count"):
            count = int(status["takeover_count"])
            suffix = (f" · takeover · {status.get('takeover_athlete_name') or 'athlete'}"
                      if count == 1 else f" · {count} takeovers")
            chips.append(
                f"<span class='cc-coach-chip'>🟠 {esc(coach)}{esc(suffix)}</span>"
            )
        else:
            suffix = f" · {unfinished} active" if unfinished else ""
            chips.append(
                f"<span class='cc-coach-chip'>⚪ {esc(coach)}{esc(suffix)}</span>"
            )
    st.markdown(
        "<div class='cc-coach-chips'>" + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Availability applies across all events. Takeovers and physical coverage keep a coach unavailable.")
    if contradictions:
        st.warning(
            "Marked available but still connected to active work: "
            + ", ".join(contradictions)
            + ". Verify before deploying."
        )

    available = [row for row in statuses if row.get("is_available")]
    if role in {"admin", "coordinator"} and available and event["status"] == "open":
        with st.expander("Manage available coaches", expanded=False):
            by_name = {str(row["coach_name"]): row for row in available}
            selected = st.selectbox(
                "Coach",
                list(by_name),
                key=f"availability_manage_{event['id']}_{role}",
            )
            if st.button(
                "Mark coach no longer available",
                key=f"availability_manager_off_{event['id']}_{role}",
                width="stretch",
            ):
                status = by_name[selected]
                try:
                    db.set_coach_availability(
                        event["id"],
                        selected,
                        False,
                        actor,
                        expected_version=int(status.get("version") or 0),
                    )
                    st.toast(f"{selected} is no longer marked available.")
                    st.rerun()
                except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
                    show_error(exc)
    return statuses


def render_available_coaches_banner(event: dict) -> None:
    """Highlight explicitly available staff across every event on this day."""
    if (
        event.get("status") != "open"
        or event.get("ended_at")
        or event.get("day_status", "active") != "active"
    ):
        return
    available = [
        status for status in db.list_coach_availability(event["id"])
        if status.get("is_available")
    ]
    if not available:
        st.caption("No coach currently marked available to help.")
        return
    chips = []
    for status in available:
        elapsed = age_text(status.get("available_since"))
        suffix = f" · {elapsed}" if elapsed else ""
        chips.append(
            "<span class='cc-coach-chip cc-coach-chip-on'>"
            f"{esc(status['coach_name'])}{esc(suffix)}</span>"
        )
    st.markdown(
        "<div class='cc-available-coaches-banner'>"
        f"<div class='cc-availability-title'>🟢 Coaches available · {len(available)}</div>"
        "<div class='cc-coach-chips'>" + "".join(chips) + "</div></div>",
        unsafe_allow_html=True,
    )
    st.caption("Available to help across all events.")


def render_available_coach_deploy(
    event: dict,
    role: str,
    actor: str,
    events: list[dict],
    athletes: list[dict],
    statuses: list[dict],
) -> None:
    if role not in {"admin", "coordinator"} or event["status"] != "open":
        return
    available = [row for row in statuses if row.get("is_available")]
    if not available:
        st.info("No coach is currently marked available to help.")
        return

    eligible_by_event: dict[str, list[dict]] = {}
    for child in events:
        rows = [
            row
            for row in athletes
            if row["event_id"] == child["id"]
            and row["phase"] == "de"
            and is_operational_athlete(row)
            and str(row.get("pod") or "").strip()
        ]
        if rows:
            eligible_by_event[child["id"]] = rows
    if not eligible_by_event:
        st.caption("No active DE sector/pod is available for support assignment.")
        return

    with st.expander("➕ Send an available coach to a DE sector", expanded=False):
        status_by_name = {str(row["coach_name"]): row for row in available}
        coach = st.selectbox(
            "Available coach",
            list(status_by_name),
            key=f"deploy_coach_{event['id']}_{role}",
        )
        event_by_id = {child["id"]: child for child in events}
        target_event_id = st.selectbox(
            "Event",
            list(eligible_by_event),
            format_func=lambda child_id: event_by_id[child_id]["name"],
            key=f"deploy_event_{event['id']}_{role}",
        )
        target_rows = eligible_by_event[target_event_id]
        sectors = sorted(
            {str(row["pod"]).strip().upper() for row in target_rows},
            key=natural_sort_key,
        )
        sector = st.selectbox(
            "Sector / Pod",
            sectors,
            key=f"deploy_sector_{event['id']}_{role}_{target_event_id}",
        )
        sector_rows = [
            row for row in target_rows if str(row.get("pod") or "").strip().upper() == sector
        ]
        existing_coaches = sorted({
            name for row in sector_rows for name in assigned_coach_names(row)
        })
        if existing_coaches:
            st.caption("Current coaches: " + " / ".join(existing_coaches))
        st.caption(
            f"{len(sector_rows)} active athlete(s) · {coach} joins the pod's coach group."
        )
        if st.button(
            f"Send {coach} to Sector {sector}",
            type="primary",
            width="stretch",
            disabled=not actor,
            key=f"deploy_apply_{event['id']}_{role}_{target_event_id}_{sector}",
        ):
            status = status_by_name[coach]
            try:
                result = db.deploy_available_coach_to_pod(
                    target_event_id,
                    pod=sector,
                    coach=coach,
                    actor=actor,
                    expected_availability_version=int(status.get("version") or 0),
                )
                updated = int(result.get("updated") or 0)
                exceptions = int(result.get("exceptions_kept") or 0)
                message = f"{coach} sent to Sector {sector} · {updated} athlete(s) updated"
                if exceptions:
                    message += f" · {exceptions} individual exception(s) kept"
                st.toast(message + ".")
                st.rerun()
            except (CompCoachError, ConcurrentUpdateError, ValueError) as exc:
                show_error(exc)


def render_de_sector_overview(
    event: dict,
    actor: str,
    events: list[dict],
    athletes: list[dict],
) -> None:
    if not events:
        st.info("No events have been created yet.")
        return
    active_de = [
        row
        for row in athletes
        if row["phase"] == "de" and is_operational_athlete(row)
    ]
    actor_event_ids = {
        row["event_id"]
        for row in active_de
        if actor
        in connected_coaches(row)
    }
    for index, child in enumerate(events):
        all_rows = [row for row in active_de if row["event_id"] == child["id"]]
        rows = all_rows
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(athlete_sector(row), []).append(row)
        urgent = sum(
            row.get("call_status") != "waiting"
            and not row.get("covered_by")
            and not is_stale(row)
            for row in rows
        )
        out_de = sum(
            row["event_id"] == child["id"]
            and row["phase"] == "de"
            and is_competitive_out(row)
            for row in athletes
        )
        mine = child["id"] in actor_event_ids
        label = (
            f"{'⭐ ' if mine else ''}{child['name']} · {len(all_rows)} still in DE"
            + (f" · {urgent} need coach" if urgent else "")
        )
        expanded = mine or bool(urgent) or (len(events) == 1 and index == 0)
        with st.expander(label, expanded=expanded):
            if not rows:
                st.caption("Remaining athletes are waiting for the next bout." if all_rows else "No active DE athletes in this event yet.")
                if out_de:
                    st.caption(f"{out_de} athlete(s) are Out.")
                continue
            ordered_groups = sorted(
                groups.items(),
                key=lambda item: (
                    -int(sector_assignment_summary(item[1])["uncovered_calls"]),
                    -len(item[1]),
                    item[0] == "TBD",
                    natural_sort_key(item[0]),
                ),
            )
            for sector, sector_rows in ordered_groups:
                summary = sector_assignment_summary(sector_rows)
                coach_text = " / ".join(summary["coaches"]) or "Unassigned"
                flags = []
                if summary["mixed"]:
                    flags.append("Mixed assignments")
                if summary["unassigned"]:
                    flags.append("Unassigned athlete")
                if sector == "TBD":
                    flags.append("Sector unknown")
                risk_class = " cc-sector-risk" if flags or summary["uncovered_calls"] else ""
                status_counts = {
                    status: sum(row.get("call_status") == status for row in sector_rows)
                    for status in ("now", "on_deck", "in_hole", "waiting")
                }
                status_text = " · ".join(
                    part
                    for part in (
                        f"{status_counts['now']} Now" if status_counts["now"] else "",
                        f"{status_counts['on_deck']} On Deck" if status_counts["on_deck"] else "",
                        f"{status_counts['in_hole']} In the Hole" if status_counts["in_hole"] else "",
                        f"{status_counts['waiting']} no call" if status_counts["waiting"] else "",
                    )
                    if part
                )
                athlete_html = []
                for row in sorted(sector_rows, key=call_sort_key):
                    status = str(row.get("call_status") or "waiting")
                    icon = CALL_ICONS.get(status, "⚪")
                    label_text = CALL_LABELS.get(status, status)
                    call_note = "No current call" if status == "waiting" else label_text
                    if is_stale(row) and status != "waiting":
                        call_note += " · stale"
                    coverage = (
                        f" · Covered now: {esc(row['covered_by'])}"
                        if row.get("covered_by")
                        else (
                            " · <span class='cc-risk'>Needs coach</span>"
                            if status != "waiting" and not is_stale(row)
                            else ""
                        )
                    )
                    athlete_html.append(
                        f"<div class='cc-sector-row'>{icon} <b>{esc(row['name'])}</b> · "
                        f"{esc(athlete_location(row))} · {esc(call_note)}{coverage}"
                        + (f" · {esc(de_progress_text(row))}" if row.get("de_wins") or row.get("de_byes") else "")
                        + "</div>"
                    )
                flag_html = (
                    f"<div class='cc-risk'>⚠ {esc(' · '.join(flags))}</div>" if flags else ""
                )
                covered = list(summary["covered"])
                covered_text = (
                    f" · Covered now: {esc(' / '.join(covered))}" if covered else ""
                )
                st.markdown(
                    f"<div class='cc-sector{risk_class}'>"
                    f"<div class='cc-sector-head'><span>Sector {esc(sector)}</span>"
                    f"<span>{len(sector_rows)} still in</span></div>"
                    f"<div class='cc-sector-coaches'>◆ Coaches: {esc(coach_text)}{covered_text}</div>"
                    f"<div class='cc-meta'>{esc(status_text)}</div>{flag_html}"
                    f"{''.join(athlete_html)}</div>",
                    unsafe_allow_html=True,
                )
            if out_de:
                st.caption(f"{out_de} DE athlete(s) are Out and hidden from sector load.")




def render_pool_results_summary(events: list[dict], athletes: list[dict]) -> None:
    st.caption(
        "Recorded pool summaries only. This list does not infer advancement or final placement."
    )
    if not events:
        st.info("No events have been created yet.")
        return
    for index, child in enumerate(events):
        child_rows = [row for row in athletes if row["event_id"] == child["id"]]
        child_operational_ids = {
            row["id"] for row in operational_view_athletes(child_rows)
        }
        recorded = [
            row
            for row in child_rows
            if row.get("pool_wins") is not None and row.get("pool_losses") is not None
        ]
        awaiting = [
            row
            for row in child_rows
            if row["phase"] == "pools"
            and row["id"] in child_operational_ids
            and (row.get("pool_wins") is None or row.get("pool_losses") is None)
        ]
        label = f"{child['name']} · {len(recorded)} results entered"
        if awaiting:
            label += f" · {len(awaiting)} awaiting"
        with st.expander(label, expanded=len(events) == 1 and index == 0):
            if not recorded:
                st.caption("No pool W/L summaries have been recorded yet.")
            else:
                ordered = sorted(
                    recorded,
                    key=lambda row: (
                        -int(row["pool_wins"]),
                        int(row["pool_losses"]),
                        str(row["name"]),
                    ),
                )
                rows_html = []
                for row in ordered:
                    state = " · Out" if is_competitive_out(row) else ""
                    rows_html.append(
                        f"<div class='cc-pool-row'><span><b>{esc(row['name'])}</b>"
                        f"{esc(state)}</span><span class='cc-pool-score'>"
                        f"{int(row['pool_wins'])} W · {int(row['pool_losses'])} L</span></div>"
                    )
                st.markdown("".join(rows_html), unsafe_allow_html=True)
            if awaiting:
                st.caption(
                    "Awaiting result: " + ", ".join(row["name"] for row in awaiting)
                )


def rollover_actor(event: dict, role: str) -> str:
    options = allowed_actors(event, role)
    requested = query_value("who")
    if requested in options:
        return requested
    stored = str(st.session_state.get(f"identity_{event['id']}_{role}") or "")
    return stored if stored in options else ""


def lifecycle_fragment(
    meet_id: str, role: str, archived_view: bool, day_status: str = ""
) -> None:
    current = db.get_meet(meet_id)
    if not current:
        st.rerun(scope="app")
        return

    intentional_admin_archive = role == "admin" and query_value("archive") == "1"
    if intentional_admin_archive:
        return

    # The current ACTIVE day is the only live navigation target. A previously
    # prepared successor may itself be closed, so its legacy chain is never a
    # fallback when the competition has returned to its neutral state.
    active_day = (
        db.get_active_competition_day(current["competition_id"])
        if current.get("competition_id") else None
    )
    should_follow = role != "admin" or bool(current.get("ended_at"))
    if (
        should_follow and active_day and active_day["id"] != current["id"]
        and active_day.get("day_status") == "active" and not active_day.get("ended_at")
    ):
        actor = rollover_actor(current, role)
        clear_actor_state(meet_id, role)
        set_open_event(active_day, role, actor=actor)
        return

    # A fragment can discover that another device just finished the day while the
    # rest of this page still contains the old live controls. Refresh the whole app
    # once so non-admin users see only the closed waiting view and Admin sees the
    # current archive controls.
    if bool(current.get("ended_at")) != archived_view or (
        day_status and current.get("day_status") != day_status
    ):
        st.rerun(scope="app")


def reopen_training_run(meet: dict, role: str, actor: str) -> None:
    # Widget state resets at the beginning of the next full render, before
    # Streamlit instantiates any navigation or athlete-filter widgets.
    st.session_state[f"training_reset_view_{meet['id']}_{role}"] = True
    set_open_event(meet, role, actor=actor)


def current_training_view(meet_id: str, role: str) -> str:
    nav_key = f"{role}_nav_{meet_id}"
    # A navigation click updates the widget before the full script runs.
    return str(st.session_state.get(nav_key) or st.session_state.get(f"nav_choice_{nav_key}") or ("Live" if role == "coordinator" else "My Group"))


def render_snapshot(function):
    """Each manual fragment rerun reads current data; full renders share reads."""
    @wraps(function)
    def render(*args, **kwargs):
        with db.snapshot():
            return function(*args, **kwargs)
    return render


def open_training_task_view(meet_id: str, role: str, view: str) -> None:
    if view not in {"My Group", "Live"}:
        return
    nav_key = f"{role}_nav_{meet_id}"
    st.session_state[nav_key] = view
    st.session_state[f"nav_choice_{nav_key}"] = view
    if view == "Live":
        filter_key = f"live_view_{meet_id}_{role}"
        st.session_state[filter_key] = "Uncovered"
        st.session_state[f"nav_choice_{filter_key}"] = "Uncovered"
    st.rerun(scope="app")


@st.fragment
@render_snapshot
def training_fragment(meet_id: str, role: str, actor: str, metadata: dict) -> None:
    meet = db.get_meet(meet_id)
    if meet:
        render_training_panel(
            db, meet, metadata, role, actor,
            open_callback=lambda practice: reopen_training_run(practice, role, actor),
            home_callback=lambda: set_open_home(meet, actor),
            current_view=current_training_view(meet_id, role),
            navigate_callback=lambda view: open_training_task_view(meet_id, role, view),
        )


@st.fragment
@render_snapshot
def help_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event and event["status"] == "open":
        _, athletes, _ = load_meet_context(event_id)
        render_help_alerts(event, role, actor, athletes)


@st.fragment
@render_snapshot
def available_coaches_fragment(meet_id: str) -> None:
    event = db.get_meet(meet_id)
    if event:
        render_available_coaches_banner(event)


@st.fragment
@render_snapshot
def busy_coaches_fragment(meet_id: str, role: str, actor: str) -> None:
    consume_de_fast_notice()
    meet = db.get_meet(meet_id)
    if meet:
        _, athletes, event_by_id = load_meet_context(meet_id)
        render_busy_coach_board(db, meet, event_by_id, athletes, role, actor, key_prefix="global")


@st.fragment
@render_snapshot
def phase_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_phase_status(event, role, actor)


@st.fragment
@render_snapshot
def live_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_live_board(event, role, actor)


@st.fragment
@render_snapshot
def my_group_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_my_group(event, role, actor)


@st.fragment
@render_snapshot
def team_plan_fragment(event_id: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_team_plan(event, actor)


@st.fragment(run_every=5)
def live_refresh_fragment(
    meet_id: str, role: str, actor: str, rendered_revision: str | None,
    rendered_training: str, practice: bool, clock_bucket: int | None, epoch: int,
) -> None:
    """Poll without rebuilding cards, fields or navigation when unchanged.

    This fragment owns no board content: returning early cannot clear an
    athlete list. The ordinary UI fragments rerun only on taps or a material
    update. There is one automatic timer for training and real competitions.
    """
    initial_key = f"live_refresh_epoch_{meet_id}_{role}"
    if st.session_state.get(initial_key) != epoch:
        st.session_state[initial_key] = epoch
        return  # The full render already read and processed current data.
    try:
        revision = db.get_meet_revision(meet_id)
        changed = revision != rendered_revision
        if practice:
            metadata = get_training(db, meet_id)
            hub_status = training_hub_status(db, metadata)
            view = current_training_view(meet_id, role)
            if training_needs_tick(metadata, actor, view, board_changed=changed, hub_status=hub_status):
                metadata = tick_training(db, meet_id, actor=actor, view=view)
                db.invalidate()
                revision = db.get_meet_revision(meet_id)
                changed = revision != rendered_revision
            changed = changed or training_visible_revision(metadata) != rendered_training
        if changed or (clock_bucket is not None and int(time.time() // 60) != clock_bucket):
            st.rerun(scope="app")
    except CompCoachError:
        # A transient connection failure leaves the current board and drafts
        # intact. The next heartbeat retries instead of entering a rerun loop.
        error_key = f"live_refresh_error_{meet_id}"
        now = time.monotonic()
        if now - float(st.session_state.get(error_key, 0)) >= 60:
            st.session_state[error_key] = now
            st.toast("Live update temporarily unavailable. Retrying automatically.")


def has_live_timers(meet_id: str) -> bool:
    events, athletes, _ = load_meet_context(meet_id)
    if any(
        row.get("help_requested_at") or row.get("covered_at") or row.get("takeover_at")
        or (row.get("reported_at") and row.get("call_status") != "waiting")
        for row in athletes if is_operational_athlete(row)
    ):
        return True
    if any(row.get("is_available") and row.get("available_since") for row in db.list_coach_availability(meet_id)):
        return True
    return any(
        state.get("started") and state.get("changed_at")
        for event in events for state in db.get_phase_states(event["id"]).values()
    )


def render_live(event: dict, role: str, actor: str) -> None:
    live_fragment(event["id"], role, actor)


def generate_whatsapp(event: dict, coach_link: str) -> str:
    practice = get_training(db, event["id"])
    if practice:
        return "\n".join([
            "🧪 *COMPCOACH PRACTICE*", "",
            "Open the link, enter your name and tap Start my practice. You get your own course from the beginning, even if someone else uses the same name.",
            "The app plays the coordinator and virtual colleagues. Practice pools, live calls, coverage, help and DE results using the normal controls.",
            "No Admin supervision needed. You can practice again after finishing.",
            f"Practice access ends: {practice.get('expires_at', '')}", "", coach_link,
        ]).strip()
    timestamp = datetime.now(ZoneInfo(event["timezone"])).strftime("%I:%M %p").lstrip("0")
    lines = [
        f"🏆 *{event['name'].upper()}*",
        f"Updated {timestamp}",
        "",
    ]
    for child in db.list_meet_events(event["id"]):
        athletes = operational_view_athletes(db.list_athletes(child["id"]))
        lines.extend([f"📍 *{child['name'].upper()}*", ""])
        if not athletes:
            lines.extend(["No active athletes yet.", ""])
            continue
        for phase, title in (("pools", "POOLS"), ("de", "DIRECT ELIMINATION")):
            phase_rows = [a for a in athletes if a["phase"] == phase]
            if not phase_rows:
                continue
            lines.extend([f"*{title}*", ""])
            if phase == "pools":
                lines.extend(["🔹 *Main coach* · 🔸 Side coach", ""])
                phase_rows.sort(
                    key=lambda a: (
                        natural_sort_key(a["time_text"] or "ZZZ"),
                        natural_sort_key(a["pod"]),
                        natural_sort_key(a["source_strip"]),
                        natural_sort_key(a["name"]),
                    )
                )
            else:
                phase_rows.sort(
                    key=lambda a: (
                        natural_sort_key(a["pod"] or "ZZZ"),
                        natural_sort_key(a["source_strip"]),
                        natural_sort_key(a["name"]),
                    )
                )
            if phase == "de":
                pods = sorted({athlete_sector(row) for row in phase_rows}, key=natural_sort_key)
                for pod in pods:
                    pod_rows = [row for row in phase_rows if athlete_sector(row) == pod]
                    coaches = sorted({name for row in pod_rows for name in assigned_coach_names(row)})
                    lines.append(f"◆ *POD {pod}*")
                    lines.append("Coaches: " + (" / ".join(coaches) or "Unassigned"))
                    mixed = len({frozenset(assigned_coach_names(row)) for row in pod_rows}) > 1
                    for row in pod_rows:
                        note = " · Coaches: " + " / ".join(assigned_coach_names(row)) if mixed else ""
                        progress = " · " + de_progress_text(row, child) if row.get("de_wins") or row.get("de_byes") or child.get("de_start_tableau") else ""
                        lines.append(f"• {row['name']}{note}{progress}")
                    lines.append("")
                for bout in list_de_bouts(db, child["id"]):
                    if bout["status"] == "pending":
                        round_note = f" · {bout['round_label']}" if bout["round_label"] else ""
                        lines.append(f"⚔ AFM vs AFM: {bout['athlete_a']['name']} vs {bout['athlete_b']['name']}{round_note}")
                lines.append("")
            for coach in event["active_coaches"] if phase == "pools" else []:
                main_rows = [row for row in phase_rows if row["main_coach"] == coach]
                side_rows = [
                    row
                    for row in phase_rows
                    if row["side_coach"] == coach and row["main_coach"] != coach
                ]
                for side_block, rows in ((False, main_rows), (True, side_rows)):
                    if not rows:
                        continue
                    heading = coach.upper() if side_block else f"*{coach.upper()}*"
                    lines.append(f"🤺 {heading}")
                    for row in rows:
                        where = row["source_strip"] or (
                            f"Pod {row['pod']}" if row["pod"] else "TBD"
                        )
                        detail = (
                            f"Pool {row['pool_no']}"
                            if phase == "pools" and row["pool_no"]
                            else f"Pod {row['pod']}"
                            if row["pod"]
                            else ""
                        )
                        if side_block:
                            diamond = "🔸"
                            main_short = (
                                row["main_coach"][:3].upper() if row["main_coach"] else ""
                            )
                            role_note = f" [M: *{main_short}*]" if main_short else ""
                        else:
                            diamond = "🔹"
                            side_short = (
                                row["side_coach"][:3].upper() if row["side_coach"] else ""
                            )
                            role_note = f" [S: {side_short}]" if side_short else ""
                        time_note = (
                            f"{row['time_text']} · "
                            if phase == "pools" and row["time_text"]
                            else ""
                        )
                        lines.append(
                            f"{diamond} {time_note}{where}: {row['name']}{role_note}"
                            + (f" · {detail}" if detail and detail not in where else "")
                        )
                    lines.append("")
            active_coaches = set(event["active_coaches"])
            unassigned = [
                athlete
                for athlete in phase_rows
                if not assigned_coach_names(athlete)
                or bool(set(assigned_coach_names(athlete)) - active_coaches)
            ]
            if unassigned:
                lines.append("⚠️ *NEEDS COACH / REASSIGNMENT*")
                for row in unassigned:
                    where = row["source_strip"] or (
                        f"Pod {row['pod']}" if row["pod"] else "TBD"
                    )
                    time_note = (
                        f"{row['time_text']} · "
                        if phase == "pools" and row["time_text"]
                        else ""
                    )
                    lines.append(f"• {time_note}{where} · {row['name']}")
                lines.append("")
    if coach_link.startswith("http"):
        lines.extend(["Team situation:", coach_link])
    return "\n".join(lines).strip()


def render_share(event: dict) -> None:
    st.subheader("Share")
    practice = get_training(db, event["id"])
    coach = public_link(event, "coach")
    coordinator = public_link(event, "coordinator")
    st.markdown("**Coach practice link**" if practice else "**Coach link**")
    st.code(coach, language=None)
    if coach.startswith("http"):
        st.link_button("Open Coach Board", coach, width="stretch")
    if not practice:
        st.markdown("**Coordinator Board**")
        st.code(coordinator, language=None)
        if coordinator.startswith("http"):
            st.link_button("Open Coordinator Board", coordinator, width="stretch")
    if not coach.startswith("http"):
        st.warning(
            "Shared links are not active yet. Set COMPCOACH_PUBLIC_URL to "
            "generate complete test links."
        )

    st.divider()
    st.markdown("#### WhatsApp practice invitation" if practice else "#### WhatsApp schedule")
    message = generate_whatsapp(event, coach)
    st.code(message, language=None)
    st.link_button(
        "Open in WhatsApp",
        f"https://wa.me/?text={quote(message)}",
        width="stretch",
    )
    st.caption("Each coach has a separate course. This link is for practice only." if practice else "This is a static snapshot. Team situation remains the current source during the competition.")


ACTION_LABELS = {
    "de_start_tableau_set": "updated the starting DE tableau",
    "missed_coaching_reported": "recorded missed coaching",
    "missed_coaching_corrected": "corrected a missed-coaching report",
    "coverage_requested": "requested emergency coverage",
    "coverage_request_accepted": "accepted emergency coverage",
    "coverage_request_cancelled": "cancelled emergency coverage request",
    "assignment_accepted": "accepted assignment",
    "live_update": "updated live call",
    "claim": "took coverage",
    "release": "released coverage",
    "bye": "recorded a bye",
    "won": "marked Won",
    "lost": "marked Lost",
    "out": "marked Out",
    "de_import_not_advanced": "was marked Out by the complete DE import",
    "clear_call": "cleared live call",
    "restore": "restored athlete",
    "assignment": "changed assignment",
    "pod_assignment": "changed pod assignment",
    "sector_support": "added a coach to a pod",
    "pool_result": "saved pool result",
    "phase_started": "started a phase",
    "phase_reset": "reset a phase to not started",
    "help_request": "requested help",
    "help_acknowledged": "is responding to help request",
    "help_resolved": "resolved help request",
    "help_cancelled": "cancelled help request",
    "import_added": "imported athlete",
    "import_updated": "updated import",
    "undo": "undid an action",
}


COORDINATOR_UNDO_ACTIONS = {
    "live_update",
    "claim",
    "release",
    "won",
    "bye",
    "lost",
    "out",
    "de_import_not_advanced",
    "restore",
    "clear_call",
    "pool_result",
    "help_request",
    "help_acknowledged",
    "help_resolved",
    "help_cancelled",
}


def render_activity(event: dict, actor: str, role: str) -> None:
    st.subheader("Recent activity")
    actions = []
    for child in db.list_meet_events(event["id"]):
        for stored in db.recent_actions(child["id"], 40):
            action = dict(stored)
            action["event_name"] = child["name"]
            actions.append(action)
    actions.sort(key=lambda action: int(action["id"]), reverse=True)
    actions = actions[:40]
    if not actions:
        st.info("No activity yet.")
        return
    for action in actions:
        with st.container(border=True):
            who = action.get("athlete_name") or "Competition"
            label = ACTION_LABELS.get(action["action"], action["action"])
            st.markdown(f"**{esc(action['actor'])}** {esc(label)} · **{esc(who)}**")
            st.markdown(
                f"<span class='cc-event-tag'>{esc(action['event_name'])}</span>",
                unsafe_allow_html=True,
            )
            stamp = format_clock(action["created_at"], event["timezone"])
            suffix = f" · undone by {action['undone_by']}" if action.get("undone_at") else ""
            st.caption(f"{stamp}{suffix}")
            can_undo = (
                event["status"] == "open"
                and actor
                and bool(action.get("athlete_id"))
                and action.get("previous") is not None
                and not action.get("undone_at")
                and action["action"] not in {
                    "undo", "pod_assignment", "sector_support",
                    "missed_coaching_reported", "missed_coaching_corrected",
                }
                and (role == "admin" or action["action"] in COORDINATOR_UNDO_ACTIONS)
            )
            if can_undo and st.button("Undo", key=f"undo_action_{action['id']}", width="stretch"):
                try:
                    db.undo_action(action["event_id"], action["id"], actor)
                    st.toast("Action undone.")
                    st.rerun()
                except (CompCoachError, ConcurrentUpdateError) as exc:
                    show_error(exc)


def render_event_management(event: dict) -> None:
    st.subheader("Competition events")
    events = db.list_meet_events(event["id"])
    st.caption(f"{len(events)} of 4 event slots in use. Each event keeps separate athletes, phases and assignments.")
    writable = event["status"] == "open"
    if not writable:
        st.info("This archived or paused competition is read-only.")
    elif not events:
        st.info("No events yet. Add the first event when the competition schedule is ready.")
    for index, child in enumerate(events):
        with st.container(border=True):
            st.markdown(f"**Event {index + 1}** · {int(child.get('athlete_count') or 0)} athletes")
            with st.form(f"rename_event_{child['id']}"):
                name = st.text_input(
                    "Event name",
                    child["name"],
                    key=f"event_name_{child['id']}",
                    disabled=not writable,
                )
                save = st.form_submit_button(
                    "Save event name", width="stretch", disabled=not writable
                )
            if save:
                try:
                    db.rename_meet_event(event["id"], child["id"], name)
                    st.toast("Event name saved.")
                    st.rerun()
                except (CompCoachError, ValueError) as exc:
                    show_error(exc)
            render_de_start_tableau(child, rollover_actor(event, "admin"))
            if writable and not child.get("athlete_count"):
                with st.expander("Remove empty event", expanded=False):
                    confirm = st.checkbox(
                        f"Remove {child['name']}", key=f"confirm_delete_event_{child['id']}"
                    )
                    if st.button(
                        "Delete empty event",
                        disabled=not confirm,
                        key=f"delete_event_{child['id']}",
                        width="stretch",
                    ):
                        try:
                            db.delete_meet_event(event["id"], child["id"])
                            st.toast("Empty event removed.")
                            st.rerun()
                        except CompCoachError as exc:
                            show_error(exc)

    if len(events) < 4 and writable:
        with st.expander("➕ Add another event", expanded=not events):
            with st.form(f"add_event_{event['id']}"):
                name = st.text_input("New event name", f"Event {len(events) + 1}")
                add = st.form_submit_button("Add event", type="primary", width="stretch")
            if add:
                try:
                    db.add_meet_event(event["id"], name)
                    st.toast("Event added.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)
    elif len(events) >= 4:
        st.info("The four-event limit has been reached.")


def suggested_next_day(event: dict) -> tuple[str, date]:
    name = str(event.get("name") or "Competition").strip()
    day_match = re.search(r"(?i)(\bday\s*)(\d+)\b", name)
    if day_match:
        next_number = int(day_match.group(2)) + 1
        next_name = (
            name[: day_match.start(2)]
            + str(next_number)
            + name[day_match.end(2) :]
        )
    else:
        next_name = f"{name} — Day 2"
    try:
        current_day = date.fromisoformat(str(event.get("competition_date") or ""))
    except ValueError:
        current_day = datetime.now(ZoneInfo(event["timezone"])).date()
    return next_name, current_day + timedelta(days=1)


def meet_operational_summary(meet_id: str) -> tuple[list[dict], int, int, int]:
    events, athletes, _ = load_meet_context(meet_id)
    operational = operational_view_athletes(athletes)
    active = len(operational)
    out = sum(is_competitive_out(row) for row in athletes)
    unresolved_help = sum(bool(row.get("help_requested_at")) for row in operational)
    return events, active, out, unresolved_help


def prepared_successor(meet_id: str) -> dict | None:
    return db.get_latest_prepared_successor(meet_id)


def render_prepare_next_day(event: dict, actor: str) -> None:
    next_name, next_date = suggested_next_day(event)
    children = db.list_meet_events(event["id"])
    with st.form(f"prepare_next_day_{event['id']}"):
        name = st.text_input("Next day name", value=next_name)
        competition_date = st.date_input("Next competition date", value=next_date)
        names_text = st.text_area(
            "Events for the next day · one per line, maximum 4 · may be left blank",
            value="\n".join(child["name"] for child in children),
            height=120,
        )
        st.caption(
            "Leave this blank to prepare the day with no events; you can add them later."
        )
        confirm = st.checkbox(
            "I confirm that today is finished and the next day must start empty"
        )
        submit = st.form_submit_button(
            "Finish & prepare next day" if not event.get("ended_at") else "Prepare next day",
            type="primary",
            width="stretch",
        )
    if not submit:
        return
    if not confirm:
        st.warning("Confirm that the current day is finished before preparing the next day.")
        return
    event_names = [line.strip() for line in names_text.splitlines() if line.strip()]
    try:
        successor = db.prepare_next_day(
            event["id"],
            name,
            competition_date.isoformat(),
            event_names,
            actor,
        )
        clear_actor_state(event["id"], "admin")
        st.toast("New competition day created. All operational data starts clean.")
        set_open_event(successor, actor=actor)
    except (CompCoachError, ValueError) as exc:
        show_error(exc)


def render_finish_competition(event: dict, actor: str) -> None:
    competition_id = str(event.get("competition_id") or "")
    if not competition_id:
        return
    parent = db.get_competition(competition_id)
    if not parent or parent.get("status") == "closed":
        return
    with st.expander("🏁 Finish the whole competition", expanded=False):
        st.caption(
            "Close all days, including scheduled days, and return to Home. "
            "Assignments, results and coach history are saved."
        )
        confirm = st.checkbox(
            "I confirm that the whole competition is finished",
            key=f"confirm_competition_end_{competition_id}",
        )
        if st.button(
            "Finish competition & return Home",
            width="stretch",
            disabled=not confirm,
            key=f"finish_competition_{competition_id}",
        ):
            try:
                db.finish_competition(competition_id, actor)
                st.toast("Competition archived. All assignments and results were retained.")
                set_open_home(event, actor)
            except (CompCoachError, ValueError) as exc:
                show_error(exc)


def render_competition_day(event: dict, actor: str) -> None:
    st.divider()
    st.markdown("### Competition day")
    children, active, out, unresolved_help = meet_operational_summary(event["id"])
    st.caption(
        f"{len(children)} event(s) · {active} active athlete(s) · "
        f"{out} out · {unresolved_help} open help request(s)"
    )
    if competition_is_closed(event):
        st.info("This competition is finished. All days are saved in the read-only History.")
        return
    render_finish_competition(event, actor)
    if event.get("ended_at"):
        st.success(
            f"🏁 Day ended by {event.get('ended_by') or 'Admin'} · "
            "all data is retained in this read-only archive."
        )
        successor = (
            db.get_active_competition_day(event["competition_id"])
            if event.get("competition_id") else None
        )
        if successor:
            st.info(
                f"Next day already prepared: {successor['name']}"
                + (
                    f" · {successor['competition_date']}"
                    if successor.get("competition_date")
                    else ""
                )
            )
            if st.button(
                "Open next day",
                type="primary",
                width="stretch",
                key=f"open_successor_{event['id']}",
            ):
                clear_actor_state(event["id"], "admin")
                set_open_event(successor, actor=actor)
        else:
            with st.expander("➕ Prepare the next day", expanded=True):
                st.caption(
                    "Staff, coordinators and timezone are copied. Athletes, results, "
                    "calls, help alerts, source links and phase lights start empty."
                )
                render_prepare_next_day(event, actor)
        return

    if unresolved_help:
        st.warning(
            f"There {'is' if unresolved_help == 1 else 'are'} {unresolved_help} open "
            f"help request{'s' if unresolved_help != 1 else ''}. The archive will retain them."
        )
    st.warning(
        "Finishing the day is permanent: this board becomes read-only. "
        "No athlete or result is deleted."
    )
    with st.expander("🏁 Finish this day", expanded=False):
        confirm_finish = st.checkbox(
            "I confirm that the competition day is finished",
            key=f"confirm_finish_{event['id']}",
        )
        if st.button(
            "Finish day only",
            width="stretch",
            disabled=not confirm_finish,
            key=f"finish_only_{event['id']}",
        ):
            try:
                db.finish_meet(event["id"], actor)
                st.toast("Competition day archived. All data was retained.")
                set_open_home(event, actor)
            except (CompCoachError, ValueError) as exc:
                show_error(exc)

        st.divider()
        st.markdown("**Or prepare tomorrow in the same step**")
        st.caption(
            "Only staff, coordinators and timezone are copied. The new day receives "
            "new private links and clean operational data."
        )
        render_prepare_next_day(event, actor)


def render_settings(event: dict, actor: str) -> None:
    st.subheader("Competition day settings")
    all_people = list(dict.fromkeys([*DEFAULT_COORDINATORS, *DEFAULT_COACHES, *event["coordinators"], *event["active_coaches"]]))
    with st.form(f"settings_{event['id']}"):
        name = st.text_input(
            "Competition name", event["name"], disabled=event["status"] != "open"
        )
        coaches = st.multiselect(
            "Coaches present",
            all_people,
            default=event["active_coaches"],
            disabled=event["status"] != "open",
        )
        coordinators = st.multiselect(
            "Coordinators",
            all_people,
            default=event["coordinators"],
            disabled=event["status"] != "open",
        )
        timezone_name = st.selectbox(
            "Timezone",
            list(dict.fromkeys([event["timezone"], *TIMEZONES])),
            index=0,
            disabled=event["status"] != "open",
        )
        save = st.form_submit_button(
            "Save settings",
            width="stretch",
            disabled=event["status"] != "open",
        )
    if save:
        try:
            db.update_meet_staff(
                event["id"],
                name=name,
                active_coaches=coaches,
                coordinators=coordinators,
                timezone_name=timezone_name,
            )
            st.toast("Settings saved.")
            st.rerun()
        except CompCoachError as exc:
            show_error(exc)

    render_competition_day(event, actor)

    if not event.get("ended_at"):
        with st.expander("Advanced · temporarily pause updates"):
            if event["status"] == "open":
                st.caption(
                    "Use this for a temporary pause. Unlike Finish Day, it can be reopened."
                )
                if st.button("Pause all updates", width="stretch"):
                    try:
                        db.set_meet_locked(event["id"], True)
                        st.rerun()
                    except CompCoachError as exc:
                        show_error(exc)
            elif st.button(
                "Reopen paused competition", type="primary", width="stretch"
            ):
                try:
                    db.set_meet_locked(event["id"], False)
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)

    with st.expander("Link security"):
        st.caption("Regenerating a link immediately invalidates its previous version.")
        if event["status"] != "open":
            st.info("Links can only be regenerated while the competition day is open.")
            return
        confirm = st.checkbox(
            "I understand that the old link will stop working",
            key=f"confirm_rotate_{event['id']}",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Reset Coach link", disabled=not confirm, width="stretch"):
                try:
                    db.rotate_meet_token(event["id"], "coach")
                    st.toast("Coach link replaced.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)
        with c2:
            if st.button("Reset Coordinator link", disabled=not confirm, width="stretch"):
                try:
                    db.rotate_meet_token(event["id"], "coordinator")
                    st.toast("Coordinator link replaced.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)


def render_settings_hub(event: dict, actor: str) -> None:
    """Keep the expanded admin setup usable on a narrow phone screen."""

    section = select_nav(
        ["Day", "Competition", "Coaches", "Schedule", "Coaching review"],
        f"settings_section_{event['id']}",
    )
    if section == "Day":
        render_settings(event, actor)
    elif section == "Competition":
        if competition_is_closed(event):
            parent = db.get_competition(event["competition_id"]) or {}
            st.subheader("Competition profile")
            st.info("This competition is finished. Its profile is read-only.")
            st.markdown(f"**{esc(parent.get('name'))}**")
            if parent.get("location"):
                st.caption(str(parent["location"]))
            st.caption(
                f"{parent.get('start_date') or 'Date not set'}"
                f" — {parent.get('end_date') or parent.get('start_date') or 'Date not set'}"
            )
        else:
            render_competition_profile(db, event, actor, asset_store=asset_store)
    elif section == "Coaches":
        render_coach_management(db, event, actor)
    elif section == "Coaching review":
        st.subheader("Coaching review")
        render_missed_coaching_review(
            db, event, actor, "admin", read_only=event.get("status") != "open",
            include_training=bool(get_training(db, event["id"])),
        )
    else:
        def open_day(selected: dict) -> None:
            selected_day = dict(selected)
            set_open_event(
                selected_day,
                actor=actor,
                archive=bool(selected_day.get("ended_at")),
            )

        if competition_is_closed(event):
            st.subheader("Competition schedule")
            st.info("This competition is finished. Its saved days can be viewed in History.")
            for day in db.list_competition_days(event["competition_id"]):
                with st.container(border=True):
                    st.markdown(f"**{esc(day['name'])}**")
                    st.caption(day.get("competition_date") or "Date not set")
                    if st.button(
                        "Open read-only archive",
                        key=f"closed_schedule_open_{day['id']}",
                        width="stretch",
                    ):
                        open_day(day)
            return

        render_competition_schedule(
            db,
            event,
            actor,
            open_day_callback=open_day,
        )


def render_admin_setup(event: dict, actor: str, *, scheduled_day: bool = False) -> None:
    """Render setup tools, including safe pre-work for a future day."""

    practice = get_training(db, event["id"])
    options = ["Assign", "Activity", "Training"] if practice else ["Import", "Assign", "Events", "Settings", "Training"]
    if not scheduled_day and not practice:
        options.insert(3, "Activity")
    setup = select_nav(options, f"admin_setup_nav_{event['id']}")
    if setup in {"Import", "Assign"}:
        events = db.list_meet_events(event["id"])
        if not events:
            st.info(
                "No events yet. Create an event in Setup → Events before "
                "importing or assigning athletes."
            )
            return
        by_id = {child["id"]: child for child in events}
        child_id = st.selectbox(
            "Event to manage",
            list(by_id),
            format_func=lambda selected: by_id[selected]["name"],
            key=f"setup_event_{event['id']}",
        )
        st.markdown(
            f"<span class='cc-event-tag'>{esc(by_id[child_id]['name'])}</span>",
            unsafe_allow_html=True,
        )
        if setup == "Import":
            render_import(by_id[child_id], actor)
        else:
            render_assignments(by_id[child_id], actor)
    elif setup == "Events":
        render_event_management(event)
    elif setup == "Activity":
        render_activity(event, actor, "admin")
    elif setup == "Training":
        if practice:
            st.info("The autonomous practice task and access information are at the top of this page. Coaches use their own course; no instructor needs to advance it.")
        else:
            render_training_start(
                db, event, actor,
                open_callback=lambda training_meet: set_open_event(training_meet, actor=actor),
                link_callback=lambda practice_meet: public_link(practice_meet, "coach"),
            )
    else:
        render_settings_hub(event, actor)


def render_training_entry(event: dict, metadata: dict) -> None:
    st.subheader("Start your practice")
    st.caption("Enter your name. You get your own course from the beginning, even if someone else uses the same name.")
    if metadata.get("expired") or metadata.get("status") not in {"running", "paused"}:
        st.info("This practice period has ended. Ask the Admin for a new practice link.")
        return
    identity_key = f"training_entry_identity_{event['id']}"
    if identity_key not in st.session_state:
        st.session_state[identity_key] = uuid4().hex
    with st.form(f"training_entry_{event['id']}"):
        name = st.text_input("Your name", key=f"training_entry_name_{event['id']}", max_chars=80)
        submitted = st.form_submit_button("Start my practice", type="primary", width="stretch")
    if submitted:
        try:
            own = join_training(db, event["id"], name, participant_key=st.session_state[identity_key])
            db.invalidate()
            own_metadata = get_training(db, own["id"])
            actor = str((own_metadata or {}).get("learner") or own["active_coaches"][0])
            st.session_state.pop(identity_key, None)
            set_open_event(own, "coach", actor=actor)
        except (CompCoachError, TypeError, ValueError) as exc:
            show_error(exc)
    st.caption("Keep your personal practice URL to resume later. Reopening the common link lets you start a separate course.")


def render_event(event: dict, role: str) -> None:
    if st.session_state.pop(f"training_reset_view_{event['id']}_{role}", False):
        clear_actor_state(event["id"], role)
        nav_key = f"{role}_nav_{event['id']}"
        st.session_state[nav_key] = "My Group"
        st.session_state[f"nav_choice_{nav_key}"] = "My Group"
    revision = db.get_meet_revision(event["id"])
    # Anchor this render before reading its cards/configuration. Authentication
    # may have loaded the meet just before another device changed it.
    db.invalidate()
    event = db.get_meet(event["id"]) or event
    practice = get_training(db, event["id"])
    revision_key = f"live_revision_{event['id']}_{role}"
    known_actor = rollover_actor(event, role)
    if training_needs_tick(
        practice, known_actor, current_training_view(event["id"], role),
        board_changed=st.session_state.get(revision_key) != revision,
        hub_status=training_hub_status(db, practice),
    ):
        practice = tick_training(db, event["id"], actor=known_actor, view=current_training_view(event["id"], role)) or practice
        db.invalidate()
        revision = db.get_meet_revision(event["id"])
    if practice:
        event = db.get_meet(event["id"]) or event
    archived_view = bool(event.get("ended_at"))
    if not practice:
        lifecycle_fragment(event["id"], role, archived_view, str(event.get("day_status") or ""))
    st.session_state[revision_key] = revision
    clocks = bool(
        known_actor and not archived_view and event.get("day_status", "active") == "active"
        and (not practice or practice.get("status") == "running")
        and has_live_timers(event["id"])
    )
    live_refresh_fragment(
        event["id"], role, known_actor, revision, training_visible_revision(practice),
        bool(practice), int(time.time() // 60) if clocks else None, time.monotonic_ns(),
    )
    if archived_view and role != "admin" and not practice:
        render_header(event, role)
        st.subheader("⏸️ No active competition")
        if competition_is_closed(event):
            st.info("This competition has finished. There are no active assignments. All results are saved.")
        else:
            st.info(
                "This competition day is closed. No competition day is active. "
                "This page checks automatically every 5 seconds."
            )
        return

    render_header(event, role)
    if practice and not rollover_actor(event, role):
        st.warning("🧪 TRAINING · Practice only. This board uses fictional athletes and does not change your real competition.")
    if role == "admin" and st.button(
        "← Home",
        key=f"admin_home_{event['id']}",
        width="stretch",
    ):
        set_open_home(event, rollover_actor(event, role))
    scheduled_day = event.get("day_status") == "scheduled"
    if scheduled_day and role != "admin":
        st.subheader("🗓️ Competition day not active yet")
        st.info(
            "This day is still being prepared. The page checks automatically "
            "and will open the live board after an Admin activates it."
        )
        return

    if practice and role == "coach" and (practice.get("is_hub") or practice.get("kind") == "hub"):
        render_training_entry(event, practice)
        return
    actor = require_actor(event, role)
    if not actor:
        return
    if practice:
        training_fragment(event["id"], role, actor, practice)
        if practice.get("is_hub") or practice.get("kind") == "hub":
            if role == "admin":
                render_share(event)
                source = db.get_meet(str(practice.get("source_meet_id") or ""))
                if source and st.button("← Back to competition", key=f"training_source_{event['id']}", width="stretch"):
                    set_open_event(source, actor=actor)
            return
        if practice.get("status") in {"completed", "stopped"} or practice.get("expired"):
            if practice.get("status") == "completed" and not practice.get("expired"):
                with st.expander("Saved practice results"):
                    for child in db.list_meet_events(event["id"]):
                        st.markdown(f"**{esc(child['name'])}**")
                        for athlete in db.list_athletes(child["id"]):
                            pool_result = (
                                f"Pools {athlete['pool_wins']} W / {athlete['pool_losses']} L · "
                                if athlete.get("pool_wins") is not None else ""
                            )
                            state = "Out" if athlete["active_state"] == "eliminated" else "Still in"
                            st.markdown(f"**{esc(athlete['name'])}**")
                            st.caption(f"{pool_result}DE {athlete.get('de_wins', 0)} win(s) · {athlete.get('de_byes', 0)} bye(s) · {state}")
            if role == "admin":
                render_share(event)
            return
    if scheduled_day:
        st.warning(
            "Future-day setup · only Admin can see this page. Imports and coach "
            "assignments are saved now but stay out of the active competition board."
        )
        active_day = (
            db.get_active_competition_day(event["competition_id"])
            if event.get("competition_id")
            else None
        )
        if (
            active_day
            and active_day["id"] != event["id"]
            and st.button(
                "← Back to active day",
                type="primary",
                width="stretch",
                key=f"back_to_active_{event['id']}",
            )
        ):
            set_open_event(active_day, actor=actor)
        render_admin_setup(event, actor, scheduled_day=True)
        return

    phase_fragment(event["id"], role, actor)
    available_coaches_fragment(event["id"])
    migrate_live_navigation(event["id"], role)

    nav_options = ["My Group", "Live", "Setup", "Share"] if role == "admin" else ["Live", "Activity"] if role == "coordinator" else ["My Group", "Live"]
    nav = select_nav(nav_options, f"{role}_nav_{event['id']}")
    if nav != "My Group":
        render_assignment_notice_panel(event, role, actor)
        busy_coaches_fragment(event["id"], role, actor)
        help_fragment(event["id"], role, actor)

    if role == "admin":
        if nav == "My Group":
            my_group_fragment(event["id"], role, actor)
            render_quick_update(event, role, actor)
        elif nav == "Live":
            render_live(event, role, actor)
        elif nav == "Share":
            render_share(event)
        else:
            render_admin_setup(event, actor)
    elif role == "coordinator":
        if nav == "Live":
            render_live(event, role, actor)
        else:
            render_activity(event, actor, role)
    else:
        if nav == "My Group":
            my_group_fragment(event["id"], role, actor)
            render_quick_update(event, role, actor)
        else:
            render_live(event, role, actor)


def main() -> None:
    event_id = query_value("event")
    token = query_value("token")
    if not event_id or not token:
        render_landing()
        return
    event = db.get_meet(event_id)
    if event:
        role = db.authorize_meet(event["id"], token)
    else:
        event = db.get_meet_for_event(event_id)
        role = db.authorize_meet(event["id"], token) if event else None
    if not event or not role:
        st.title("CompCoach Live")
        st.error("This shared link is invalid or has been replaced.")
        return
    render_event(event, role)


if __name__ == "__main__":
    with db.snapshot():
        main()
