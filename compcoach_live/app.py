"""CompCoach Live — mobile-first AFM coach coordination.

Run from the repository root with:

    streamlit run compcoach_live/app.py
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

try:
    from compcoach_live.ocr_import import parse_screenshot
    from compcoach_live.parsers import parse_pasted_table
    from compcoach_live.storage import (
        NO_CHANGE,
        CompCoachDB,
        CompCoachError,
        ConcurrentUpdateError,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from ocr_import import parse_screenshot
    from parsers import parse_pasted_table
    from storage import (
        NO_CHANGE,
        CompCoachDB,
        CompCoachError,
        ConcurrentUpdateError,
    )


APP_VERSION = "0.4.1"
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
      .cc-plan-group {border:1px solid #e4e7ec; border-radius:12px; padding:.55rem .65rem;
                      margin:.4rem 0; background:#fff;}
      .cc-plan-coach {font-weight:900; margin-bottom:.22rem;}
      .cc-plan-row {font-size:.87rem; line-height:1.35; padding:.24rem 0;
                    border-top:1px solid #f2f4f7;}
      .cc-plan-row:first-of-type {border-top:0;}
      .cc-plan-meta {color:#667085; font-size:.78rem;}
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
    default_path = Path(__file__).resolve().parent / "data" / "compcoach.db"
    return CompCoachDB(secret_value("COMPCOACH_DB_PATH", str(default_path)))


db = get_db()


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
    if hasattr(st, "segmented_control"):
        value = st.segmented_control(
            "Navigation",
            options,
            default=options[0],
            key=key,
            label_visibility="collapsed",
        )
        return value or options[0]
    return st.radio(
        "Navigation",
        options,
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )


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
    return str(
        athlete.get("live_location")
        or athlete.get("source_strip")
        or (f"Pod {athlete.get('pod')}" if athlete.get("pod") else "Location TBD")
    )


def planned_coaches(athlete: dict) -> str:
    coaches = []
    if athlete.get("main_coach"):
        coaches.append(f"Main: {athlete['main_coach']}")
    if athlete.get("side_coach"):
        coaches.append(f"Side: {athlete['side_coach']}")
    return " · ".join(coaches) or "No planned coach"


def load_meet_context(meet_id: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    events = db.list_meet_events(meet_id)
    event_by_id = {event["id"]: event for event in events}
    athletes: list[dict] = []
    for event in events:
        for stored in db.list_athletes(event["id"]):
            athlete = dict(stored)
            athlete["event_name"] = event["name"]
            athlete["event_sort_order"] = int(event.get("sort_order") or 0)
            athletes.append(athlete)
    return events, athletes, event_by_id


def athlete_event_label(athlete: dict) -> str:
    return str(athlete.get("event_name") or "Event")


def has_inactive_assignment(athlete: dict, event: dict) -> bool:
    active = set(event["active_coaches"])
    assigned = {athlete.get("main_coach"), athlete.get("side_coach")} - {"", None}
    return bool(assigned - active)


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


def allowed_actors(event: dict, role: str) -> list[str]:
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
        "de_result_confirm_",
        "call_reset_",
        "call_athlete_",
        "call_status_",
        "call_location_",
        "call_coverage_",
        "call_self_cover_",
        "live_view_",
        "dashboard_view_",
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
    st.info("Create the competition once, then share the Coach and Coordinator links.")

    admin_pin = secret_value("COMPCOACH_ADMIN_PIN", "")
    existing_events = db.list_meets()
    if secret_value("COMPCOACH_PUBLIC_URL", "") and not admin_pin:
        st.error(
            "Admin event creation is disabled until COMPCOACH_ADMIN_PIN is configured. "
            "Existing token links continue to work."
        )
        return
    if not admin_pin and existing_events:
        st.error(
            "For security, the Admin event list is hidden when no Admin PIN is configured. "
            "Open the private Admin link you saved, or configure COMPCOACH_ADMIN_PIN."
        )
        return
    unlocked_key = "landing_admin_unlocked"
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

    with st.expander("➕ New competition", expanded=not existing_events):
        with st.form("create_event"):
            name = st.text_input("Competition or travel day", "AFM Competition")
            competition_date = st.date_input(
                "Competition date", value=datetime.now(ZoneInfo(TIMEZONES[0])).date()
            )
            first_event_name = st.text_input(
                "First event (optional)",
                "",
                placeholder="Example: Cadet Foil",
                help="Leave this blank if the event schedule is not ready yet.",
            )
            coaches = st.multiselect("Coaches present", DEFAULT_COACHES, default=DEFAULT_COACHES)
            coordinators = st.multiselect(
                "Coordinators", list(dict.fromkeys([*DEFAULT_COORDINATORS, *DEFAULT_COACHES])), default=DEFAULT_COORDINATORS
            )
            timezone_name = st.selectbox("Competition timezone", TIMEZONES)
            create = st.form_submit_button("Create competition", width="stretch")
        if create:
            event = db.create_meet(
                name,
                coaches,
                coordinators,
                timezone_name,
                first_event_name=first_event_name.strip() or None,
                competition_date=competition_date.isoformat(),
            )
            set_open_event(event)

    events = db.list_meets()
    if events:
        current = [event for event in events if not event.get("ended_at")]
        archived = [event for event in events if event.get("ended_at")]
        if current:
            st.subheader("Current competitions")
        for event in current[:8]:
            with st.container(border=True):
                st.markdown(f"**{esc(event['name'])}**")
                day = (
                    f" · {event['competition_date']}"
                    if event.get("competition_date")
                    else ""
                )
                status = "Open" if event["status"] == "open" else "Paused"
                st.caption(
                    f"{status}{day} · {int(event.get('event_count') or 0)} event(s) · "
                    f"created {format_clock(event['created_at'], event['timezone'])}"
                )
                if st.button("Open Admin", key=f"open_{event['id']}", width="stretch"):
                    set_open_event(event)
        if archived:
            with st.expander(
                f"Past competition days · {len(archived)}",
                expanded=not current,
            ):
                for event in archived[:20]:
                    with st.container(border=True):
                        st.markdown(f"**{esc(event['name'])}**")
                        day = event.get("competition_date") or "Date not set"
                        st.caption(
                            f"Ended · {day} · "
                            f"{int(event.get('event_count') or 0)} event(s)"
                        )
                        if st.button(
                            "Open read-only archive",
                            key=f"open_archive_{event['id']}",
                            width="stretch",
                        ):
                            set_open_event(event, archive=True)


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
    actor_event_ids = {
        athlete["event_id"]
        for athlete in athletes
        if actor
        and actor
        in {
            athlete.get("main_coach"),
            athlete.get("side_coach"),
            athlete.get("covered_by"),
            athlete.get("help_acknowledged_by"),
        }
        and athlete["active_state"] == "active"
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
            for athlete in athletes
            if athlete["event_id"] == child["id"]
            and athlete["active_state"] == "active"
            and actor
            in {
                athlete.get("main_coach"),
                athlete.get("side_coach"),
                athlete.get("covered_by"),
                athlete.get("help_acknowledged_by"),
            }
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
    return bool(athlete.get("main_coach") or athlete.get("side_coach"))


def assignment_option(athlete: dict) -> str:
    if not has_assignment(athlete):
        return athlete_option(athlete)
    coaches = " / ".join(
        coach for coach in [athlete.get("main_coach"), athlete.get("side_coach")] if coach
    )
    return f"✓ {athlete_option(athlete)} · {coaches}"


def render_assignment_card(athlete: dict, assigned: bool) -> None:
    with st.container(border=True):
        marker = "✅" if assigned else "○"
        st.markdown(f"**{marker} {esc(athlete['name'])}** · {esc(athlete_location(athlete))}")
        st.caption(planned_coaches(athlete))


def render_assignments(event: dict, actor: str) -> None:
    st.subheader("Coach assignments")
    athletes = [a for a in db.list_athletes(event["id"]) if a["active_state"] == "active"]
    if not athletes:
        st.info("Import athletes first.")
        return
    phase_label = select_nav(["Pools", "Direct Elimination"], f"assignment_phase_{event['id']}")
    phase = "pools" if phase_label == "Pools" else "de"
    visible = [a for a in athletes if a["phase"] == phase]
    if not visible:
        st.info(f"No {phase_label} athletes imported yet.")
        return

    if phase == "de":
        pods = sorted({a["pod"] for a in visible if a["pod"]})
        with st.expander("Assign an entire pod", expanded=True):
            if not pods:
                st.caption("No pod can be derived yet. Import a list containing values such as M1 or P3.")
            else:
                with st.form(f"pod_assign_{event['id']}"):
                    pod = st.selectbox("Pod", pods)
                    main = st.selectbox("Main coach", ["None", *event["active_coaches"]], key="pod_main")
                    side = st.selectbox("Side coach", ["None", *event["active_coaches"]], key="pod_side")
                    submit = st.form_submit_button("Assign pod", width="stretch")
                if submit:
                    if main == side and main != "None":
                        st.error("Main and Side coach must be different.")
                    else:
                        try:
                            count = db.assign_pod(
                                event["id"],
                                phase=phase,
                                pod=pod,
                                main_coach="" if main == "None" else main,
                                side_coach="" if side == "None" else side,
                                actor=actor,
                            )
                            st.toast(f"Pod {pod}: {count} athletes updated.")
                            st.rerun()
                        except CompCoachError as exc:
                            show_error(exc)

    unassigned = [a for a in visible if not has_assignment(a)]
    assigned = [a for a in visible if has_assignment(a)]
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
            ["No change", "None", *event["active_coaches"]],
            key=f"main_{event['id']}_{phase}",
        )
        side = st.selectbox(
            "Side coach",
            ["No change", "None", *event["active_coaches"]],
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


def render_quick_update(event: dict, role: str, actor: str) -> None:
    _, athletes, _ = load_meet_context(event["id"])
    active = [a for a in athletes if a["active_state"] == "active"]
    with st.expander("➕ Report athlete location", expanded=role == "coordinator"):
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
        status_label = select_nav(
            ["In the Hole", "On Deck", "Now"],
            f"call_status_{event['id']}_{role}_{reset}",
        )
        status = {"In the Hole": "in_hole", "On Deck": "on_deck", "Now": "now"}[status_label]
        suggested = athlete.get("live_location") or athlete.get("source_strip") or athlete.get("pod") or ""
        location = st.text_input(
            "Strip or pod",
            value=suggested,
            placeholder="M1 or Pod P",
            key=f"call_location_{event['id']}_{role}_{athlete_id}_{reset}",
        )
        if role in {"admin", "coordinator"}:
            current_coverage = athlete.get("covered_by") or "nobody"
            coverage = st.selectbox(
                "Coverage",
                [f"Keep current ({current_coverage})", "Needs coach", *event["active_coaches"]],
                key=f"call_coverage_{event['id']}_{role}_{reset}",
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
                covering = st.checkbox(
                    "I am already with this athlete",
                    key=f"call_self_cover_{event['id']}_{reset}",
                )
                covered_by = actor if covering else None
        if st.button(
            "Publish update",
            type="primary",
            width="stretch",
            disabled=event["status"] == "locked",
            key=f"publish_call_{event['id']}_{role}",
        ):
            try:
                db.report_call(
                    athlete["event_id"],
                    athlete_id,
                    status=status,
                    location=location,
                    actor=actor,
                    covered_by=covered_by,
                    expected_version=athlete["version"],
                )
                st.session_state[reset_key] = reset + 1
                st.toast(f"{athlete['name']} updated.")
                st.rerun()
            except (CompCoachError, ValueError) as exc:
                show_error(exc)


def call_sort_key(athlete: dict):
    priority = {"now": 0, "on_deck": 1, "in_hole": 2}.get(athlete.get("call_status"), 3)
    return (is_stale(athlete), priority, athlete.get("reported_at") or "", athlete.get("name") or "")


def personal_role_text(athlete: dict, actor: str) -> str:
    roles = []
    if athlete.get("main_coach") == actor:
        roles.append("🔹 Main")
    if athlete.get("side_coach") == actor:
        roles.append("🔸 Side")
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
    alerts = sorted(
        [athlete for athlete in athletes if athlete.get("help_requested_at")],
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


def render_athlete_card(
    event: dict,
    role: str,
    actor: str,
    athlete: dict,
    *,
    personal: bool = False,
) -> None:
    status = athlete["call_status"]
    icon = CALL_ICONS.get(status, "⚪")
    label = CALL_LABELS.get(status, status)
    status_class = {"now": "cc-now", "on_deck": "cc-deck", "in_hole": "cc-hole"}.get(status, "")
    location = athlete_location(athlete)
    age = age_text(athlete.get("reported_at"))
    stale = is_stale(athlete)
    with st.container(border=True):
        if athlete.get("event_name"):
            st.markdown(
                f"<span class='cc-event-tag'>{esc(athlete_event_label(athlete))}</span>",
                unsafe_allow_html=True,
            )
        st.markdown(f"<div class='cc-athlete'>{icon} {esc(athlete['name'])}</div>", unsafe_allow_html=True)
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

        if athlete.get("reported_by"):
            st.caption(f"Reported by {athlete['reported_by']} · {format_clock(athlete['reported_at'], event['timezone'])}")
        st.markdown(f"<div class='cc-meta'>{esc(planned_coaches(athlete))}</div>", unsafe_allow_html=True)
        if has_inactive_assignment(athlete, event):
            st.warning("A planned coach is no longer in the active staff list. Reassign this athlete.")
        if athlete.get("covered_by"):
            st.markdown(
                f"<span class='cc-covered'>✓ Covered by {esc(athlete['covered_by'])}</span>",
                unsafe_allow_html=True,
            )

        if personal:
            render_pool_result_editor(event, actor, athlete)
        if athlete.get("de_wins") or athlete.get("last_de_result"):
            last_result = str(athlete.get("last_de_result") or "").title()
            last_note = f" · last: {last_result}" if last_result else ""
            st.markdown(
                f"<div class='cc-result'>DE wins: {int(athlete.get('de_wins') or 0)}"
                f"{esc(last_note)}</div>",
                unsafe_allow_html=True,
            )

        writable = event["status"] == "open" and bool(actor)
        if athlete["active_state"] == "eliminated":
            if writable and st.button(
                "↩ Restore athlete", key=f"restore_{athlete['id']}", width="stretch"
            ):
                try:
                    db.restore_athlete(
                        event["id"],
                        athlete["id"],
                        actor,
                        expected_version=athlete["version"],
                    )
                    st.toast(f"{athlete['name']} restored.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)
            return

        if personal and writable:
            if athlete.get("help_requested_at"):
                st.caption("🚨 Help request active — status is shown at the top of the board.")
            elif st.button(
                "🚨 Need help now",
                type="primary",
                width="stretch",
                key=f"help_request_{athlete['id']}",
            ):
                try:
                    db.request_help(
                        event["id"],
                        athlete["id"],
                        actor,
                        expected_version=athlete["version"],
                    )
                    st.toast(f"Help request sent for {athlete['name']}.")
                    st.rerun()
                except CompCoachError as exc:
                    show_error(exc)

        if status != "waiting" and not athlete.get("covered_by") and writable and not stale:
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
            confirm_key = f"de_result_confirm_{event['id']}_{athlete['id']}"
            pending_state = st.session_state.get(confirm_key)
            if (
                isinstance(pending_state, dict)
                and pending_state.get("version") != athlete["version"]
            ):
                st.session_state.pop(confirm_key, None)
                pending_state = None
                st.info("This athlete changed on another phone. Choose the result again.")
            pending = (
                pending_state.get("outcome")
                if isinstance(pending_state, dict)
                else None
            )
            if pending in {"won", "lost"}:
                st.warning(f"Confirm {athlete['name']}: {pending.upper()}?")
                left, right = st.columns(2)
                with left:
                    confirm = st.button(
                        f"Confirm {pending.title()}",
                        type="primary",
                        key=f"confirm_{pending}_{athlete['id']}",
                        width="stretch",
                    )
                with right:
                    cancel = st.button(
                        "Cancel",
                        key=f"cancel_result_{athlete['id']}",
                        width="stretch",
                    )
                if cancel:
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
                if confirm:
                    try:
                        db.mark_result(
                            event["id"],
                            athlete["id"],
                            outcome=pending,
                            actor=actor,
                            expected_version=pending_state["version"],
                        )
                        st.session_state.pop(confirm_key, None)
                        st.toast(f"{athlete['name']}: {pending.title()} saved.")
                        st.rerun()
                    except CompCoachError as exc:
                        show_error(exc)
            else:
                left, right = st.columns(2)
                with left:
                    won = st.button("Won", key=f"won_{athlete['id']}", width="stretch")
                with right:
                    lost = st.button("Lost", key=f"lost_{athlete['id']}", width="stretch")
                if won or lost:
                    st.session_state[confirm_key] = {
                        "outcome": "won" if won else "lost",
                        "version": athlete["version"],
                    }
                    st.rerun()


def render_live_board(event: dict, role: str, actor: str) -> None:
    _, athletes, event_by_id = load_meet_context(event["id"])
    active = [a for a in athletes if a["active_state"] == "active"]
    out = [a for a in athletes if a["active_state"] == "eliminated"]
    needs = [a for a in active if a["call_status"] != "waiting" and not a["covered_by"]]
    stale_needs = [a for a in needs if is_stale(a)]
    current_needs = [a for a in needs if not is_stale(a)]
    covered = [a for a in active if a["covered_by"]]
    waiting = [a for a in active if a["call_status"] == "waiting"]
    st.markdown(
        f"<div class='cc-summary'><span class='cc-now'>{len(current_needs)} need coverage</span> · "
        f"{len(stale_needs)} verify · <span class='cc-covered'>{len(covered)} covered</span> · "
        f"{len(waiting)} waiting · {len(out)} out</div>",
        unsafe_allow_html=True,
    )
    views = ["Needs", "Covered", "Waiting", "Out"]
    view = select_nav(views, f"live_view_{event['id']}_{role}")
    mapping = {
        "Needs": sorted(needs, key=call_sort_key),
        "Covered": sorted(covered, key=call_sort_key),
        "Waiting": waiting,
        "Out": out,
    }
    selected = mapping[view]
    if not selected:
        empty_messages = {
            "Needs": "No athlete currently needs coverage.",
            "Covered": "No athlete is currently covered.",
            "Waiting": "No active athletes are waiting.",
            "Out": "No athletes have been marked out.",
        }
        if view == "Needs":
            st.success(empty_messages[view])
        else:
            st.info(empty_messages[view])
    for athlete in selected:
        render_athlete_card(
            event_by_id[athlete["event_id"]],
            role,
            actor,
            athlete,
            personal=False,
        )
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
        athlete.get("time_text") or "ZZZ",
        athlete.get("pod") or "ZZZ",
        athlete_location(athlete),
        athlete.get("name") or "",
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
        elif row.get("main_coach") == actor:
            priorities.append(3)
        else:
            priorities.append(4)
    return (min(priorities, default=9),)


def render_my_group(event: dict, role: str, actor: str) -> None:
    events, athletes, event_by_id = load_meet_context(event["id"])
    planned = [
        athlete
        for athlete in athletes
        if actor in {athlete.get("main_coach"), athlete.get("side_coach")}
    ]
    temporary = [
        athlete
        for athlete in athletes
        if athlete not in planned
        and actor in {athlete.get("covered_by"), athlete.get("help_acknowledged_by")}
    ]
    mine = [*planned, *temporary]
    active_mine = [row for row in mine if row["active_state"] == "active"]
    out_mine = [row for row in mine if row["active_state"] == "eliminated"]
    main_count = sum(row.get("main_coach") == actor for row in planned)
    side_count = sum(row.get("side_coach") == actor for row in planned)

    st.markdown("### My group")
    st.caption(
        f"{len(active_mine)} active · {main_count} Main · {side_count} Side"
        + (f" · {len(temporary)} temporary" if temporary else "")
    )
    if not mine:
        st.info("No athletes are assigned to you right now. Team Plan still shows the full staff plan.")
        return

    visible_event_ids = {row["event_id"] for row in mine}
    ordered_events = sorted(
        [child for child in events if child["id"] in visible_event_ids],
        key=lambda child: (
            *personal_event_priority(child["id"], mine, actor),
            int(child.get("sort_order") or 0),
        ),
    )
    if len(ordered_events) > 1:
        st.warning(
            "You are currently connected to more than one event: "
            + ", ".join(child["name"] for child in ordered_events)
            + "."
        )

    for child in ordered_events:
        child_rows = sorted(
            [row for row in active_mine if row["event_id"] == child["id"]],
            key=personal_athlete_sort_key,
        )
        st.markdown(f"#### ⭐ {esc(child['name'])}")
        planned_rows = [row for row in child_rows if row in planned]
        temporary_rows = [row for row in child_rows if row in temporary]
        for phase, phase_label in (("pools", "Pools"), ("de", "Direct Elimination")):
            phase_rows = [row for row in planned_rows if row["phase"] == phase]
            if phase_rows:
                st.markdown(f"**{phase_label}**")
                for athlete in phase_rows:
                    render_athlete_card(
                        event_by_id[athlete["event_id"]],
                        role,
                        actor,
                        athlete,
                        personal=True,
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
                )

    if out_mine:
        with st.expander(f"Completed / Out · {len(out_mine)}", expanded=False):
            for athlete in sorted(out_mine, key=personal_athlete_sort_key):
                st.markdown(
                    f"**{esc(athlete['name'])}** · {esc(athlete_event_label(athlete))}"
                )
            st.caption("Use Live → Out if an athlete needs to be restored.")


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
    if athlete.get("call_status") != "waiting":
        details.append(CALL_LABELS.get(athlete["call_status"], athlete["call_status"]))
    return " · ".join(details) or "Location TBD"


def plan_group_html(coach: str, main_rows: list[dict], side_rows: list[dict]) -> str:
    row_html = []
    for diamond, rows, is_main in (("🔹", main_rows, True), ("🔸", side_rows, False)):
        for athlete in rows:
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


def render_team_plan(event: dict, actor: str) -> None:
    events, athletes, _ = load_meet_context(event["id"])
    active = [row for row in athletes if row["active_state"] == "active"]
    st.markdown("### Team plan")
    st.caption("All current assignments · 🔹 Main · 🔸 Side")
    if not active:
        st.info("No active athletes have been imported yet.")
        return

    actor_event_ids = {
        row["event_id"]
        for row in active
        if actor in {row.get("main_coach"), row.get("side_coach")}
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
                if not ({row.get("main_coach"), row.get("side_coach")} - {"", None})
                or bool(
                    ({row.get("main_coach"), row.get("side_coach")} - {"", None})
                    - active_staff
                )
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
                        [row for row in phase_rows if row.get("main_coach") == coach],
                        key=personal_athlete_sort_key,
                    )
                    side_rows = sorted(
                        [
                            row
                            for row in phase_rows
                            if row.get("side_coach") == coach
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
                row["event_id"] == child["id"] and row["active_state"] == "eliminated"
                for row in athletes
            )
            if out_count:
                st.caption(f"{out_count} completed/out athlete(s) are hidden from the active plan.")


def rollover_actor(event: dict, role: str) -> str:
    options = allowed_actors(event, role)
    requested = query_value("who")
    if requested in options:
        return requested
    stored = str(st.session_state.get(f"identity_{event['id']}_{role}") or "")
    return stored if stored in options else ""


@st.fragment(run_every=5)
def lifecycle_fragment(meet_id: str, role: str, archived_view: bool) -> None:
    current = db.get_meet(meet_id)
    if not current:
        st.rerun(scope="app")
        return
    if not current.get("ended_at"):
        return

    intentional_admin_archive = role == "admin" and query_value("archive") == "1"
    if intentional_admin_archive:
        return

    successor = db.get_latest_prepared_successor(meet_id)
    if successor:
        actor = rollover_actor(current, role)
        clear_actor_state(meet_id, role)
        set_open_event(successor, role, actor=actor)
        return

    # A fragment can discover that another device just finished the day while the
    # rest of this page still contains the old live controls. Refresh the whole app
    # once so non-admin users see only the closed waiting view and Admin sees the
    # current archive controls.
    if not archived_view:
        st.rerun(scope="app")


@st.fragment(run_every=5)
def help_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event and event["status"] == "open":
        _, athletes, _ = load_meet_context(event_id)
        render_help_alerts(event, role, actor, athletes)


@st.fragment(run_every=5)
def phase_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_phase_status(event, role, actor)


@st.fragment(run_every=5)
def live_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_live_board(event, role, actor)


@st.fragment(run_every=5)
def my_group_fragment(event_id: str, role: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_my_group(event, role, actor)


@st.fragment(run_every=5)
def team_plan_fragment(event_id: str, actor: str) -> None:
    event = db.get_meet(event_id)
    if event:
        render_team_plan(event, actor)


def render_live(event: dict, role: str, actor: str) -> None:
    render_quick_update(event, role, actor)
    live_fragment(event["id"], role, actor)


def generate_whatsapp(event: dict, coach_link: str) -> str:
    timestamp = datetime.now(ZoneInfo(event["timezone"])).strftime("%I:%M %p").lstrip("0")
    lines = [
        f"🏆 *{event['name'].upper()}*",
        f"Updated {timestamp}",
        "🔹 *Main coach* · 🔸 Side coach",
        "",
    ]
    for child in db.list_meet_events(event["id"]):
        athletes = [
            athlete
            for athlete in db.list_athletes(child["id"])
            if athlete["active_state"] == "active"
        ]
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
                phase_rows.sort(
                    key=lambda a: (
                        a["time_text"] or "ZZZ",
                        a["pod"],
                        a["source_strip"],
                        a["name"],
                    )
                )
            else:
                phase_rows.sort(
                    key=lambda a: (a["pod"] or "ZZZ", a["source_strip"], a["name"])
                )
            for coach in event["active_coaches"]:
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
                if not ({athlete["main_coach"], athlete["side_coach"]} - {""})
                or bool(
                    ({athlete["main_coach"], athlete["side_coach"]} - {""})
                    - active_coaches
                )
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
        lines.extend(["Live board:", coach_link])
    return "\n".join(lines).strip()


def render_share(event: dict) -> None:
    st.subheader("Share")
    coach = public_link(event, "coach")
    coordinator = public_link(event, "coordinator")
    st.markdown("**Coach Live Board**")
    st.code(coach, language=None)
    if coach.startswith("http"):
        st.link_button("Open Coach Board", coach, width="stretch")
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
    st.markdown("#### WhatsApp schedule")
    message = generate_whatsapp(event, coach)
    st.code(message, language=None)
    st.link_button(
        "Open in WhatsApp",
        f"https://wa.me/?text={quote(message)}",
        width="stretch",
    )
    st.caption("This is a static snapshot. The shared Live Board remains the current source during the competition.")


ACTION_LABELS = {
    "live_update": "updated live call",
    "claim": "took coverage",
    "release": "released coverage",
    "won": "marked Won",
    "lost": "marked Lost",
    "out": "marked Out",
    "de_import_not_advanced": "was marked Out by the complete DE import",
    "clear_call": "cleared live call",
    "restore": "restored athlete",
    "assignment": "changed assignment",
    "pod_assignment": "changed pod assignment",
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
                and action["action"] not in {"undo", "pod_assignment"}
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
    active = sum(row["active_state"] == "active" for row in athletes)
    out = sum(row["active_state"] == "eliminated" for row in athletes)
    unresolved_help = sum(bool(row.get("help_requested_at")) for row in athletes)
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


def render_competition_day(event: dict, actor: str) -> None:
    st.divider()
    st.markdown("### Competition day")
    children, active, out, unresolved_help = meet_operational_summary(event["id"])
    st.caption(
        f"{len(children)} event(s) · {active} active athlete(s) · "
        f"{out} out · {unresolved_help} open help request(s)"
    )
    if event.get("ended_at"):
        st.success(
            f"🏁 Day ended by {event.get('ended_by') or 'Admin'} · "
            "all data is retained in this read-only archive."
        )
        successor = prepared_successor(event["id"])
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
                st.rerun()
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
    st.subheader("Competition settings")
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


def render_event(event: dict, role: str) -> None:
    archived_view = bool(event.get("ended_at"))
    lifecycle_fragment(event["id"], role, archived_view)
    if archived_view and role != "admin":
        render_header(event, role)
        st.subheader("⏸️ No active competition")
        st.info(
            "This competition day is closed. No new day has been prepared yet. "
            "This page checks automatically every 5 seconds."
        )
        return

    render_header(event, role)
    actor = require_actor(event, role)
    if not actor:
        return
    phase_fragment(event["id"], role, actor)
    help_fragment(event["id"], role, actor)

    if role == "admin":
        nav = select_nav(
            ["My Group", "Live", "Team Plan", "Setup", "Share"],
            f"admin_nav_{event['id']}",
        )
        if nav == "My Group":
            render_quick_update(event, role, actor)
            my_group_fragment(event["id"], role, actor)
        elif nav == "Live":
            render_live(event, role, actor)
        elif nav == "Team Plan":
            team_plan_fragment(event["id"], actor)
        elif nav == "Share":
            render_share(event)
        else:
            setup = select_nav(
                ["Import", "Assign", "Events", "Activity", "Settings"],
                f"admin_setup_nav_{event['id']}",
            )
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
                render_activity(event, actor, role)
            else:
                render_settings(event, actor)
    elif role == "coordinator":
        nav = select_nav(
            ["Live", "Team Plan", "Activity"],
            f"coordinator_nav_{event['id']}",
        )
        if nav == "Live":
            render_live(event, role, actor)
        elif nav == "Team Plan":
            team_plan_fragment(event["id"], actor)
        else:
            render_activity(event, actor, role)
    else:
        nav = select_nav(
            ["My Group", "Live", "Team Plan"],
            f"coach_nav_{event['id']}",
        )
        if nav == "My Group":
            render_quick_update(event, role, actor)
            my_group_fragment(event["id"], role, actor)
        elif nav == "Live":
            render_live(event, role, actor)
        else:
            team_plan_fragment(event["id"], actor)


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
    main()
