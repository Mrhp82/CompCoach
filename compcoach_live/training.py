"""Shared, interaction-driven practice competitions using the real app commands.

Training runs have separate competitions, days, athletes and links.  The engine
only writes to the run it is given; its metadata and scenario mutations commit
in one serialized transaction on SQLite and PostgreSQL.  Browser refreshes do
not create duplicate scenarios, and there is no timer that invents coach taps.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from unicodedata import category
from uuid import UUID, uuid4

try:
    from compcoach_live.storage import (
        CompCoachError, _coach_names, _dump, _load, _new_token,
        assigned_coaches, utc_now,
    )
except ModuleNotFoundError:  # direct Streamlit script
    from storage import (
        CompCoachError, _coach_names, _dump, _load, _new_token,
        assigned_coaches, utc_now,
    )

SYSTEM_ACTOR = "Training simulation"
DEFAULT_COACHES = ["Coach Alex", "Coach Taylor", "Coach Morgan"]
TRAINING_STEPS = [
    {"title": "Your identity and My Group", "instruction": "Open My Group. Check your event, athletes and planned pool strips.", "hint": "You have your own complete exercise. Other coaches can join later without changing your progress."},
    {"title": "Pool assignments and start signal", "instruction": "Open Team situation and check All assignments. The virtual coordinator has assigned your group and started Pools: the signal is green.", "hint": "My Group is your own work list; Team situation shows the shared situation, coach availability and assignments."},
    {"title": "Respond to shared help", "instruction": "A virtual colleague reports a pool emergency: an athlete has missed several bouts without coaching. Tap I’m coming on the shared request.", "hint": "During pools, everyone is busy. Request help only for a real emergency, such as several missed bouts or an athlete left without coaching who feels abandoned. Check location and elapsed time; acknowledging help is not physical coverage."},
    {"title": "Complete pool results", "instruction": "Record wins and losses for one athlete, for example 3 / 3. You can also practice marking an athlete absent.", "hint": "Completed athletes collapse below your current work. The other fictional pools finish after your result."},
    {"title": "Tell the team you are available", "instruction": "Your pools are finished. Tap I’m available to help and check the shared available-coach banner.", "hint": "Free coaches can be reassigned across events. The virtual coordinator will prepare your next assignment."},
    {"title": "Direct Elimination pod plan", "instruction": "Open Team situation to inspect the new DE pod assignments and green DE signal. Then return to My Group.", "hint": "DE coaches are equal. A pod reference such as A1 identifies the calling pod, not the actual bout strip."},
    {"title": "Call without a confirmed strip", "instruction": "A parent reports {primary_name} On deck. Save the call from My Group or Team situation. If the actual strip is unknown, leave it empty; a confirmed strip is also allowed.", "hint": "On deck, In the hole and Now save with their timestamp. An unknown strip is shown as Actual strip to confirm. Do not invent a strip from the pod."},
    {"title": "Actual strip and physical coverage", "instruction": "{primary_name} is now called on C3. Add the actual strip and tap I’m with {primary_name}.", "hint": "Coverage starts your busy timer and removes you from Available. Your other called athletes may need another coach."},
    {"title": "Busy coach: a colleague may help", "instruction": "You are with {primary_name}. Watch the alternate-coverage notice for {secondary_name}; a virtual colleague may respond after a short delay. When your bout finishes, record Won or Lost for {primary_name}.", "hint": "Some replays include a colleague who covers the other athlete; some leave the alert unresolved. Request help when needed. Your result releases you."},
    {"title": "A sudden call: take over", "instruction": "You are free. A new Now, On deck or In the hole call will appear for {secondary_name}, whose assigned coach is busy. Tap I’ll take over.", "hint": "A takeover immediately reserves you, even before physical coverage. Your name disappears from Available."},
    {"title": "Cover the athlete and request help", "instruction": "Add actual strip J2 for {secondary_name}, start physical coverage, and tap 🚨 Need help now. A virtual colleague will respond.", "hint": "Requests are global and include an elapsed timer. The coordinator can update calls, strips and covering coaches."},
    {"title": "One-tap DE result", "instruction": "Record Won or Lost for {secondary_name}. Check the wheel, Out list and your availability.", "hint": "A winner moves to the end of the wheel. A loss moves Out. Calls and coverage clear; no bout scores are required."},
    {"title": "An urgent request to available coaches", "instruction": "The virtual coordinator needs coverage for {reassigned_name}, On deck on E2. Tap I'll cover this bout on the request, open My Group, record your physical arrival, then tap Won or Lost.", "hint": "The request is offered to available coaches. The first acceptance takes this bout and closes the offer for everyone else. It does not change pod assignments or start a physical-coverage timer. Tap I’m with the athlete when you arrive."},
    {"title": "Record a bye", "instruction": "Record a Bye for {bye_name} and check the number of rounds passed.", "hint": "A bye advances the athlete without inventing a win. Mistakes can be corrected in Correct DE results."},
    {"title": "Mark a same-club bout", "instruction": "In Team situation → AFM vs AFM, mark {pair_a_name} and {pair_b_name} as opponents in this practice round.", "hint": "Without a bracket, the app cannot infer the pairing. Any coach who notices it can mark it."},
    {"title": "Resolve both athletes together", "instruction": "Record the winner of the paired AFM bout. Check that the opponent becomes Out and the winner moves to the end of the wheel.", "hint": "The result updates both athletes together. Dedicated correction controls restore mistakes safely."},
    {"title": "Next actual strip and final bout", "instruction": "{final_name} is fencing now on D4. Cover this new call, then record one final Won or Lost result.", "hint": "The next strip can differ from the previous strip and the calling pod. The exercise closes the fictional day after your result."},
    {"title": "Exercise complete", "instruction": "Review your completed skills and the final board. You may start Practice again while the exercise link remains active.", "hint": "Your real competition data was never changed. Each replay can create a different coverage response or sudden call."},
]

# The pinned mobile guide names only the next action. The full explanation
# stays available in the hint panel without making the guide cover the board.
_GUIDE_ACTIONS = (
    "Open My Group. Check your athletes and pool strips.",
    "Open Team situation → All assignments. Check strips and the green Pools signal.",
    "A pool emergency needs help. Tap I’m coming on the shared request.",
    "Record one athlete’s pool wins and losses in My Group, for example 3 / 3.",
    "Tap I’m available to help.",
    "Open Team situation to check your DE pod plan. Then return to My Group.",
    "Save {primary_name}’s call. Leave the actual strip empty if unknown; a known strip is allowed.",
    "Set {primary_name}’s actual strip to C3, then tap I’m with {primary_name}.",
    "You are with {primary_name}. When the bout finishes, tap Won or Lost.",
    "When {secondary_name}’s sudden call appears, tap I’ll take over.",
    "Set {secondary_name} to J2, start physical coverage, then tap Need help now.",
    "Tap Won or Lost for {secondary_name}. Check your availability.",
    "Tap I'll cover this bout for {reassigned_name}, cover them on E2, then tap Won or Lost.",
    "Record a Bye for {bye_name}.",
    "Open Team situation → AFM vs AFM. Mark {pair_a_name} and {pair_b_name} as opponents.",
    "Record the winner of the paired AFM bout.",
    "Cover {final_name}’s new call on D4, then tap Won or Lost.",
    "Review your completed skills. You may choose Practice again.",
)

_FIRST_NAMES = ["Leo", "Mila", "Eli", "Nora", "Theo", "Lena", "Finn", "Zoe", "Owen", "Iris", "Jude", "Ruby"]
_SURNAMES = ["RIVERA", "BROOKS", "PARK", "REED", "BELL", "MORRIS", "HAYES", "FOSTER", "KENT", "GRAY", "ELLIS", "SHAW"]
_MILESTONES = {
    "pool_result": "Pool result", "participation_absent": "Attendance",
    "absent": "Attendance", "help_request": "Help requested",
    "help_acknowledged": "Help acknowledged", "live_update": "Call reported",
    "coverage_start": "Physical coverage", "claim": "Physical coverage",
    "takeover": "Takeover", "won": "DE result", "lost": "DE result",
    "coverage_request_accepted": "Urgent coverage accepted",
    "bye": "Bye", "de_bout_create": "AFM pairing",
    "de_bout_result": "AFM result", "de_bout_resolve": "AFM result", "de_result_correction": "Result correction",
}


def _actor(actor: str) -> str:
    value = str(actor or "").strip()
    if not value:
        raise CompCoachError("Select who you are before managing the exercise.")
    return value


def _learner_name(value: str) -> str:
    """Accept a readable typed identity without adding it to real coach lists."""
    name = " ".join(str(value or "").split())
    if not name:
        raise CompCoachError("Enter your name to start your own practice.")
    if len(name) > 80:
        raise CompCoachError("Use a name with no more than 80 characters.")
    if any(category(character).startswith("C") for character in name) or not any(
        character.isalpha() for character in name
    ):
        raise CompCoachError("Enter a readable coach name to start your practice.")
    return name


def _participant_key(value: str) -> str:
    """Normalize the retry identifier; it is independent of the display name."""
    try:
        return UUID(str(value)).hex
    except (ValueError, TypeError, AttributeError):
        raise CompCoachError("Start your practice again from the shared practice link.") from None


def _row(conn: Any, meet_id: str, *, required: bool = True) -> dict | None:
    row = conn.execute("SELECT * FROM training_sessions WHERE meet_id = ?", (meet_id,)).fetchone()
    if row is None and required:
        raise CompCoachError("This competition day is not an exercise.")
    return dict(row) if row is not None else None


def _athletes(conn: Any, meet_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT athletes.* FROM athletes JOIN meet_events ON meet_events.event_id = athletes.event_id "
        "WHERE meet_events.meet_id = ? ORDER BY meet_events.sort_order, athletes.created_at, athletes.athlete_key",
        (meet_id,),
    ).fetchall()]


def _events(conn: Any, meet_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT events.* FROM events JOIN meet_events ON meet_events.event_id = events.id "
        "WHERE meet_events.meet_id = ? ORDER BY meet_events.sort_order", (meet_id,),
    ).fetchall()]


def _max_action(conn: Any, meet_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(actions.id), 0) AS n FROM actions "
        "JOIN meet_events ON meet_events.event_id = actions.event_id WHERE meet_events.meet_id = ?",
        (meet_id,),
    ).fetchone()
    return int(row["n"])


def _actions(conn: Any, meet_id: str, after: int = 0) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT actions.* FROM actions JOIN meet_events ON meet_events.event_id = actions.event_id "
        "WHERE meet_events.meet_id = ? AND actions.id > ? AND actions.actor <> ? "
        "AND actions.undone_at IS NULL ORDER BY actions.id", (meet_id, after, SYSTEM_ACTOR),
    ).fetchall()]


def _expired(state: dict) -> bool:
    value = state.get("expires_at")
    return bool(value and datetime.fromisoformat(str(value)) <= datetime.fromisoformat(utc_now()))


def _metadata(row: dict) -> dict:
    state = _load(row.get("state_json"), {})
    stage = max(0, min(int(row["stage"]), len(TRAINING_STEPS) - 1))
    step = TRAINING_STEPS[stage]
    substitutions = {key: str(state.get(key) or "the practice athlete") for key in (
        "primary_name", "secondary_name", "bye_name", "pair_a_name", "pair_b_name", "reassigned_name", "final_name",
    )}
    instruction = str(state.get("instruction_override") or step["instruction"]).format(**substitutions)
    participants = [{"name": name, **person} for name, person in state.get("participants", {}).items()]
    return {
        **{key: value for key, value in row.items() if key != "state_json"},
        "state": state, "kind": state.get("kind", "run"), "is_hub": state.get("kind") == "hub",
        "hub_meet_id": state.get("hub_meet_id", row["meet_id"]), "learner": state.get("learner", ""),
        "expires_at": state.get("expires_at", ""), "expired": _expired(state), "stage_index": stage, "stage_count": len(TRAINING_STEPS),
        "completed_runs": int(state.get("completed_runs", 0)), "last_completed_at": state.get("last_completed_at"),
        "stage_title": step["title"], "title": step["title"],
        "instruction": instruction, "hint": step["hint"],
        "guide_instruction": str(state.get("guide_instruction_override") or _GUIDE_ACTIONS[stage]).format(**substitutions),
        "scenario_message": str(state.get("scenario_message") or ""),
        "last_feedback": str(state.get("last_feedback") or ""),
        "participants": participants,
        "actor_tasks": {person["name"]: instruction for person in participants},
        "progress": 1.0 if row["status"] == "completed" else stage / (len(TRAINING_STEPS) - 1),
    }


def get_training(db: Any, meet_id: str) -> dict | None:
    with db._connection() as conn:
        row = _row(conn, meet_id, required=False)
        if row is None:
            return None
        result = _metadata(row)
        if result["is_hub"]:
            runs = []
            for child in conn.execute(
                "SELECT * FROM training_sessions WHERE source_meet_id = ? ORDER BY created_at, meet_id",
                (meet_id,),
            ).fetchall():
                child_metadata = _metadata(dict(child))
                if child_metadata["kind"] == "run":
                    runs.append(child_metadata)
            ordered_runs = []
            seen_runs = set()
            # Keep legacy invitation order while allowing every actual run to
            # appear, including separate entrants with the same display name.
            for name in result["state"].get("coaches", []):
                run = next((item for item in runs if item["learner"] == name), None)
                ordered_runs.append((name, run))
                if run:
                    seen_runs.add(run["meet_id"])
            ordered_runs.extend((run["learner"], run) for run in runs if run["meet_id"] not in seen_runs)
            participants = []
            for name, run in ordered_runs:
                own = next((person for person in run["participants"] if person["name"] == name), {}) if run else {}
                participants.append({
                    "name": name, "status": run["status"] if run else "not started",
                    "run_meet_id": run["meet_id"] if run else None,
                    "stage_index": run["stage_index"] if run else 0,
                    "stage_title": run["stage_title"] if run else "Not started",
                    "completed_runs": run["completed_runs"] if run else 0,
                    "last_completed_at": run["last_completed_at"] if run else None,
                    "views": own.get("views", []), "milestones": own.get("milestones", []),
                })
            result["participants"] = participants
        return result


def list_training_sessions(db: Any) -> list[dict]:
    with db._connection() as conn:
        rows = conn.execute("SELECT * FROM training_sessions ORDER BY created_at DESC, meet_id").fetchall()
    return [_metadata(dict(row)) for row in rows]


def _persist(conn: Any, row: dict, state: dict, *, stage: int | None = None, status: str | None = None) -> dict:
    stage = int(row["stage"]) if stage is None else stage
    status = str(row["status"]) if status is None else status
    encoded = _dump(state)
    if encoded == row["state_json"] and stage == row["stage"] and status == row["status"]:
        return row
    now = utc_now()
    conn.execute(
        "UPDATE training_sessions SET stage = ?, status = ?, state_json = ?, updated_at = ?, "
        "version = version + 1 WHERE meet_id = ? AND version = ?",
        (stage, status, encoded, now, row["meet_id"], row["version"]),
    )
    return {**row, "stage": stage, "status": status, "state_json": encoded, "updated_at": now, "version": int(row["version"]) + 1}


def _update(db: Any, conn: Any, athlete: dict, changes: dict, action: str) -> dict:
    return db._update_athlete(
        conn, event_id=athlete["event_id"], athlete_id=athlete["id"], changes=changes,
        action=action, actor=SYSTEM_ACTOR, expected_version=int(athlete["version"]),
    )


def _seed(db: Any, conn: Any, meet_id: str, coaches: list[str]) -> dict:
    events = _events(conn, meet_id)
    if len(events) != 2:
        raise CompCoachError("The exercise needs its two practice events. Restart it from the admin controls.")
    now, targets = utc_now(), []
    # At least three athletes per coach permits a same-club pairing after a loss.
    for index in range(max(6, len(coaches) * 3)):
        coach_index = (index // 3) % len(coaches)
        coach = coaches[coach_index]
        event = events[coach_index % 2]
        # A solo coach still sees two simultaneous events.
        if len(coaches) == 1 and index >= 3:
            event = events[1]
        name = f"{_SURNAMES[index % len(_SURNAMES)]} {_FIRST_NAMES[(index // len(_SURNAMES) + index) % len(_FIRST_NAMES)]}"
        if index >= len(_SURNAMES) * len(_FIRST_NAMES):
            name += f" {index // (len(_SURNAMES) * len(_FIRST_NAMES)) + 1}"
        athlete_id, key = uuid4().hex, f"training_{index:03d}"
        strip = f"{'A' if event['id'] == events[0]['id'] else 'B'}{coach_index + 1}"
        conn.execute(
            "INSERT INTO athletes (id,event_id,athlete_key,name,phase,pool_no,pod,source_strip,time_text,"
            "main_coach,side_coach,created_at,updated_at) VALUES (?,?,?,?,'pools',?,?,?,'9:00 AM',?,'',?,?)",
            (athlete_id,event["id"],key,name,str(coach_index + 1),strip[0],strip,coach,now,now),
        )
        athlete = dict(conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone())
        db._sync_athlete_assignment_history(conn, current=None, new=athlete, actor=SYSTEM_ACTOR, source="training_seed")
        targets.append(athlete)
    for event in events:
        db._sync_pool_waves(conn, event["id"], [{"phase": "pools", "time": "9:00 AM"}])
    return {
        "coaches": coaches, "participants": {}, "entered_action_id": _max_action(conn, meet_id),
        "scenario_message": "A fictional two-event practice day is ready. Use the ordinary app buttons; no real competition is affected.",
        "primary_id": targets[0]["id"], "primary_name": targets[0]["name"],
        "secondary_id": targets[1]["id"], "secondary_name": targets[1]["name"],
        "history": [], "generation": uuid4().hex,
    }


def _create_meet(conn: Any, coaches: list[str], coords: list[str], timezone_name: str, *, events: bool) -> str:
    now, meet_id, competition_id = utc_now(), uuid4().hex, uuid4().hex
    conn.execute(
        "INSERT INTO competitions (id,name,location,timezone,created_at,updated_at) VALUES (?,?,'Practice only',?,?,?)",
        (competition_id,"CompCoach Training · fictional competition",timezone_name,now,now),
    )
    conn.execute(
        "INSERT INTO meets (id,competition_id,name,timezone,status,active_coaches_json,coordinators_json,"
        "admin_token,coordinator_token,coach_token,day_status,created_at,updated_at) "
        "VALUES (?,?,?,?,'open',?,?,?,?,?,'active',?,?)",
        (meet_id,competition_id,"🧪 CompCoach Training",timezone_name,_dump(coaches),_dump(coords),_new_token(),_new_token(),_new_token(),now,now),
    )
    if events:
        for index,name in enumerate(("Practice Cadet Épée","Practice Junior Épée")):
            event_id = uuid4().hex
            conn.execute(
                "INSERT INTO events (id,name,timezone,active_coaches_json,coordinators_json,admin_token,coordinator_token,coach_token,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (event_id,name,timezone_name,_dump(coaches),_dump(coords),_new_token(),_new_token(),_new_token(),now,now),
            )
            conn.execute("INSERT INTO meet_events (meet_id,event_id,sort_order) VALUES (?,?,?)",(meet_id,event_id,index))
    return meet_id


def start_training(db: Any, source_meet_id: str | None = None, coach_names: Iterable[str] | None = None,
                   actor: str = "Admin", duration_days: int = 7, *, open_entry: bool = False) -> dict:
    """Activate a 7/14-day hub; every coach receives an autonomous personal run."""
    actor = _actor(actor)
    if isinstance(duration_days,bool) or duration_days not in (7,14):
        raise ValueError("Practice duration must be 7 or 14 days.")
    if isinstance(coach_names,(str,bytes)):
        raise TypeError("Coach names must be a list.")
    if not isinstance(open_entry, bool):
        raise TypeError("Open practice entry must be true or false.")
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        source = conn.execute("SELECT * FROM meets WHERE id = ?",(source_meet_id,)).fetchone() if source_meet_id else None
        if source_meet_id and source is None:
            raise CompCoachError("The source competition day was not found.")
        raw_names = list(coach_names) if coach_names is not None else _load(source["active_coaches_json"],[]) if source else DEFAULT_COACHES
        coaches = _coach_names(raw_names)
        if (not coaches and not open_entry) or len(coaches) > 40:
            raise CompCoachError("Select between one and 40 coaches for the exercise.")
        timezone_name = str(source["timezone"]) if source else "America/Los_Angeles"
        meet_id = _create_meet(conn,coaches,[],timezone_name,events=False)
        now = utc_now()
        expires_at = (datetime.fromisoformat(now) + timedelta(days=duration_days)).isoformat()
        state = {"kind":"hub","coaches":coaches,"participants":{},"open_entry":open_entry,"expires_at":expires_at,"duration_days":duration_days,
                 "scenario_message":"Autonomous practice is active. Each coach gets a complete individual course with virtual colleagues and a virtual coordinator."}
        conn.execute(
            "INSERT INTO training_sessions (meet_id,source_meet_id,status,stage,state_json,created_by,created_at,updated_at,version) VALUES (?,?,'running',0,?,?,?,?,0)",
            (meet_id,source_meet_id or None,_dump(state),actor,now,now),
        )
        conn.commit()
    return db.get_meet(meet_id)


def _check_hub(conn: Any, hub_meet_id: str) -> dict:
    hub = _row(conn,hub_meet_id)
    state = _load(hub["state_json"],{})
    if state.get("kind") != "hub":
        raise CompCoachError("This link does not identify an autonomous practice hub.")
    if hub["status"] not in {"running","paused"} or _expired(state):
        raise CompCoachError("This practice period has ended. Ask the admin to activate a new exercise.")
    return hub


def join_training(db: Any, hub_meet_id: str, coach: str, *, participant_key: str | None = None) -> dict:
    """Join an isolated course, retrying by identity key rather than typed name.

    The entry screen supplies a fresh UUID for each new entrant. Repeated
    submissions with that key return the same course, while equal names with
    different keys never share progress. Calls without a key retain the legacy
    invited-name behavior for existing integrations.
    """
    coach = _learner_name(coach)
    participant_key = _participant_key(participant_key) if participant_key is not None else None
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        hub = _check_hub(conn,hub_meet_id)
        hub_state = _load(hub["state_json"],{})
        if participant_key is None:
            canonical = next((name for name in hub_state["coaches"] if name.casefold() == coach.casefold()),None)
            if canonical is None:
                raise CompCoachError("Open the shared practice link and enter your name.")
            coach = canonical
        rows = conn.execute("SELECT * FROM training_sessions WHERE source_meet_id = ?",(hub_meet_id,)).fetchall()
        for existing in rows:
            old_state = _load(existing["state_json"],{})
            same_entrant = (
                old_state.get("participant_key") == participant_key if participant_key is not None
                else not old_state.get("participant_key") and old_state.get("learner") == coach
            )
            if old_state.get("kind") == "run" and same_entrant:
                existing_meet = conn.execute(
                    "SELECT meets.*, (SELECT COUNT(*) FROM meet_events WHERE meet_events.meet_id = meets.id) AS event_count "
                    "FROM meets WHERE meets.id = ?", (existing["meet_id"],),
                ).fetchone()
                conn.commit()
                return db._meet_dict(existing_meet)
        hub_meet = conn.execute("SELECT * FROM meets WHERE id = ?",(hub_meet_id,)).fetchone()
        virtuals = ["Coach Avery (virtual)","Coach Jordan (virtual)"]
        virtuals = [name + " 2" if name.casefold() == coach.casefold() else name for name in virtuals]
        coaches = [coach,*virtuals]
        coords = ["Coordinator Robin (virtual)"]
        meet_id = _create_meet(conn,coaches,coords,str(hub_meet["timezone"]),events=True)
        now = utc_now()
        base = {"kind":"run","hub_meet_id":hub_meet_id,"learner":coach,"expires_at":hub_state["expires_at"],"coaches":coaches}
        if participant_key is not None:
            base["participant_key"] = participant_key
        # The marker must exist before ordinary assignment/history helpers run.
        conn.execute(
            "INSERT INTO training_sessions (meet_id,source_meet_id,status,stage,state_json,created_by,created_at,updated_at,version) VALUES (?,?,'running',0,?,?,?,?,0)",
            (meet_id,hub_meet_id,_dump(base),coach,now,now),
        )
        state = {**_seed(db,conn,meet_id,coaches),**base,"variant":0,"pending_events":[]}
        row = _row(conn,meet_id)
        _persist(conn,row,state)
        participant_id = f"participant:{participant_key}" if participant_key else coach
        hub_state.setdefault("participants",{})[participant_id] = {"name":coach,"run_meet_id":meet_id,"joined_at":now,"milestones":[],"views":[]}
        _persist(conn,hub,hub_state)
        conn.commit()
    return db.get_meet(meet_id)


def _phase_started(conn: Any, meet_id: str, phase: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM phase_states JOIN meet_events ON meet_events.event_id = phase_states.event_id "
        "WHERE meet_events.meet_id = ? AND phase_states.phase = ? AND phase_states.started = 1 LIMIT 1", (meet_id,phase),
    ).fetchone())


def _eligible(athlete: dict) -> bool:
    return athlete["active_state"] == "active" and athlete["participation_status"] == "active"


def _target(athletes: list[dict], state: dict, key: str) -> dict | None:
    return next((athlete for athlete in athletes if athlete["id"] == state.get(key)), None)


def _choose_targets(athletes: list[dict], state: dict) -> None:
    eligible = [athlete for athlete in athletes if _eligible(athlete)]
    if not eligible:
        return
    participants = list(state.get("participants", {}))
    primary = next((athlete for athlete in eligible if any(coach in assigned_coaches(athlete) for coach in participants)), eligible[0])
    secondary = next((athlete for athlete in eligible if athlete["id"] != primary["id"] and athlete["event_id"] != primary["event_id"]), None)
    secondary = secondary or next((athlete for athlete in eligible if athlete["id"] != primary["id"]), primary)
    state.update(primary_id=primary["id"], primary_name=primary["name"], secondary_id=secondary["id"], secondary_name=secondary["name"])


def _help(db: Any, conn: Any, athlete: dict) -> None:
    if not _eligible(athlete) or athlete.get("help_requested_at"):
        return
    _update(db, conn, athlete, {
        "help_requested_by": SYSTEM_ACTOR, "help_requested_at": utc_now(),
        "help_location": athlete["live_location"] or athlete["source_strip"] or "Pod " + athlete["pod"],
        "help_acknowledged_by": "", "help_acknowledged_at": None,
    }, "help_request")


def _clear_help(db: Any, conn: Any, athletes: list[dict]) -> None:
    for athlete in athletes:
        if athlete.get("help_requested_at"):
            _update(db, conn, athlete, {
                "help_requested_by": "", "help_requested_at": None, "help_location": "",
                "help_acknowledged_by": "", "help_acknowledged_at": None,
            }, "help_resolved")


def _convert_to_de(db: Any, conn: Any, meet_id: str, state: dict) -> None:
    coaches = state["coaches"]
    for athlete in _athletes(conn, meet_id):
        if not _eligible(athlete) or athlete["phase"] == "de":
            continue
        old_coach = athlete["main_coach"]
        coach_index = coaches.index(old_coach) if old_coach in coaches else 0
        pod = chr(ord("A") + coach_index % 26)
        _update(db, conn, athlete, {
            "phase": "de", "pool_no": "", "time_text": "", "pod": pod, "source_strip": pod + "1",
            "main_coach": "", "side_coach": "", "de_coaches_json": "[]", "assignment_override": 0,
            "call_status": "waiting", "live_location": "", "reported_at": None, "reported_by": "",
            "covered_by": "", "covered_at": None, "takeover_coach": "", "takeover_at": None, "takeover_by": "",
            "de_wins": 0, "de_byes": 0, "de_awaiting_next": 0, "last_de_result": "", "last_de_result_at": None, "last_de_result_by": "",
        }, "import_updated")
    for event in _events(conn, meet_id):
        pods = conn.execute("SELECT DISTINCT pod FROM athletes WHERE event_id = ? AND phase = 'de'", (event["id"],)).fetchall()
        for row in pods:
            pod = str(row["pod"])
            coach_index = (ord(pod[0]) - ord("A")) % len(coaches)
            db._assign_de_pod_group(conn, event_id=event["id"], pod=pod, coaches=[coaches[coach_index]], actor=SYSTEM_ACTOR, source="training_de_plan")
    db._clear_available_coaches(conn, meet_id, coaches, SYSTEM_ACTOR)
    _choose_targets(_athletes(conn, meet_id), state)


def _finish(conn: Any, meet_id: str, actor: str) -> None:
    now = utc_now()
    conn.execute("UPDATE meets SET status = 'locked', day_status = 'closed', ended_at = COALESCE(ended_at, ?), ended_by = ?, updated_at = ? WHERE id = ?", (now,actor,now,meet_id))
    conn.execute("UPDATE events SET status = 'locked', updated_at = ? WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)", (now,meet_id))
    conn.execute("UPDATE coach_assignment_history SET ended_at = ?, ended_by = ? WHERE meet_id = ? AND ended_at IS NULL", (now,actor,meet_id))


def _phase(conn: Any, meet_id: str, phase: str) -> None:
    now = utc_now()
    for event in _events(conn,meet_id):
        conn.execute("INSERT INTO phase_states (event_id,phase,started,changed_at,changed_by,version) VALUES (?,?,1,?,?,1) ON CONFLICT(event_id,phase) DO UPDATE SET started = 1,changed_at = excluded.changed_at,changed_by = excluded.changed_by,version = phase_states.version + 1",(event["id"],phase,now,SYSTEM_ACTOR))


def _queue(state: dict, kind: str, seconds: int, **payload: Any) -> None:
    state.setdefault("pending_events",[]).append({"id":uuid4().hex,"kind":kind,"due_at":(datetime.fromisoformat(utc_now())+timedelta(seconds=seconds)).isoformat(),**payload})


def _virtual_cover(db: Any, conn: Any, athlete: dict, coach: str, strip: str) -> None:
    if not _eligible(athlete) or athlete.get("covered_by") or athlete.get("takeover_coach"):
        return
    db._set_live_coverage(conn,event_id=athlete["event_id"],current=athlete,coach=coach,actor=SYSTEM_ACTOR,
                         changes={"live_location":athlete["live_location"] or strip,
                                  "call_status":athlete["call_status"] if athlete["call_status"] != "waiting" else "now",
                                  "reported_at":athlete["reported_at"] or utc_now(),
                                  "reported_by":athlete["reported_by"] or SYSTEM_ACTOR},
                         action="coverage_start",expected_version=athlete["version"])


def _practice_request_recipients(db: Any, conn: Any, meet_id: str, state: dict, *, prepare: bool = False) -> list[str]:
    """Prepare a free virtual colleague, then use only currently available staff."""
    if prepare:
        virtual = state["coaches"][1]
        for athlete in _athletes(conn,meet_id):
            if not _eligible(athlete):
                continue
            changes = {}
            if athlete.get("covered_by") == virtual:
                changes.update(covered_by="", covered_at=None)
            if athlete.get("takeover_coach") == virtual:
                changes.update(takeover_coach="", takeover_at=None, takeover_by="")
            if changes:
                _update(db,conn,athlete,changes,"coverage_release")
        db._mark_released_coaches_available(conn,meet_id,[state["learner"],virtual],SYSTEM_ACTOR)
    athletes = _athletes(conn,meet_id)
    available = {str(row["coach_name"]) for row in conn.execute(
        "SELECT coach_name FROM coach_availability WHERE meet_id = ? AND is_available = 1",(meet_id,),
    ).fetchall()}
    recipients = [coach for coach in state["coaches"] if coach in available and not any(
        _eligible(athlete) and coach in {athlete.get("covered_by"),athlete.get("takeover_coach")}
        for athlete in athletes
    )]
    return recipients if state["learner"] in recipients else []


def _practice_request_timer(request: dict) -> dict:
    return {"id":uuid4().hex,"kind":"renew_coverage_request","due_at":request["expires_at"],
            "athlete_id":request["athlete_id"],"request_id":request["id"]}


def _send_practice_coverage_request(db: Any, conn: Any, row: dict, state: dict, athlete: dict,
                                  recipients: list[str]) -> dict:
    athlete = _update(db,conn,athlete,{"call_status":"on_deck","live_location":"E2", "de_awaiting_next":0,
                      "reported_at":utc_now(),"reported_by":SYSTEM_ACTOR},"live_update")
    request = db._create_coverage_request(
        conn,meet_id=row["meet_id"],event_id=athlete["event_id"],athlete_id=athlete["id"],
        coach_names=recipients,actor=SYSTEM_ACTOR,expected_version=athlete["version"],
    )
    state.update(reassigned_id=athlete["id"],reassigned_name=athlete["name"],coverage_request_id=request["id"])
    state["scenario_message"] = (
        f"Virtual coordinator: {athlete['name']} needs coverage · On deck · E2. "
        f"The offer is sent to {', '.join(recipients)}. The first acceptance takes this bout; the pod plan stays unchanged."
    )
    return _practice_request_timer(request)


def _renew_practice_coverage_request(db: Any, conn: Any, row: dict, state: dict, athlete: dict | None) -> dict | None:
    """Restore an unanswered lesson offer after expiry without reclaiming a bout."""
    if int(row["stage"]) != 12 or not athlete or not _eligible(athlete):
        return None
    if athlete.get("covered_by") or athlete.get("takeover_coach"):
        return None
    if any(action["athlete_id"] == athlete["id"] and action["action"] in {"won","lost"}
           for action in _actions(conn,row["meet_id"],int(state.get("entered_action_id",0)))):
        return None
    raw = conn.execute("SELECT * FROM coverage_requests WHERE id = ?",(state.get("coverage_request_id"),)).fetchone()
    request = dict(raw) if raw else None
    if request and request["status"] == "accepted":
        return None
    now = utc_now()
    if request and request["status"] == "pending" and request["expires_at"] > now:
        return _practice_request_timer(request)
    if request and request["status"] == "pending":
        conn.execute(
            "UPDATE coverage_requests SET status = 'expired', closed_at = ?, closed_by = ?, "
            "close_reason = 'expired', version = version + 1 WHERE id = ? AND status = 'pending' AND expires_at <= ?",
            (now,SYSTEM_ACTOR,request["id"],now),
        )
    recipients = _practice_request_recipients(db,conn,row["meet_id"],state)
    if recipients:
        return _send_practice_coverage_request(db,conn,row,state,athlete,recipients)
    return {"id":uuid4().hex,"kind":"renew_coverage_request","due_at":
            (datetime.fromisoformat(now)+timedelta(seconds=15)).isoformat(),"athlete_id":athlete["id"]}


def _process_pending(db: Any, conn: Any, row: dict, state: dict) -> None:
    now = datetime.fromisoformat(utc_now())
    retained = []
    for event in state.get("pending_events",[]):
        if event["kind"] == "renew_coverage_request":
            request = conn.execute("SELECT status, expires_at FROM coverage_requests WHERE id = ?",
                                   (state.get("coverage_request_id"),)).fetchone()
            if request and request["status"] == "pending" and datetime.fromisoformat(request["expires_at"]) > now:
                retained.append({**event,"due_at":request["expires_at"]})
            else:
                athlete = _target(_athletes(conn,row["meet_id"]),state,"reassigned_id")
                renewal = _renew_practice_coverage_request(db,conn,row,state,athlete)
                if renewal:
                    retained.append(renewal)
            continue
        if datetime.fromisoformat(event["due_at"]) > now:
            retained.append(event)
            continue
        athletes = _athletes(conn,row["meet_id"])
        athlete = next((a for a in athletes if a["id"] == event.get("athlete_id")),None)
        kind = event["kind"]
        if kind == "other_coverage":
            if athlete and _eligible(athlete) and not athlete["covered_by"] and not athlete["takeover_coach"]:
                _virtual_cover(db,conn,athlete,state["coaches"][1],"J2")
                state["scenario_message"] = f"{state['coaches'][1]} noticed the uncovered athlete and is now with {athlete['name']} on {athlete['live_location'] or 'J2'}."
            state["response_processed"] = True
        elif kind == "no_response":
            state["response_processed"] = True
            state["scenario_message"] = "Both virtual colleagues are occupied. The alternate-coverage alert remains open: request help if needed. Finish your current bout and record its result before taking a new athlete."
        elif kind == "sudden_call":
            if athlete and _eligible(athlete) and not athlete["covered_by"] and not athlete["takeover_coach"] and athlete["call_status"] == "waiting":
                status = ["now","on_deck","in_hole"][int(state.get("variant",0))%3]
                _update(db,conn,athlete,{"call_status":status,"live_location":"J2","de_awaiting_next":0,"reported_at":utc_now(),"reported_by":SYSTEM_ACTOR},"live_update")
                state["scenario_message"] = f"Sudden call: {athlete['name']} · {status.replace('_',' ')} · actual strip J2. Assigned coach is busy. You are available: take over the athlete."
        elif kind == "ack_help":
            if athlete and _eligible(athlete) and athlete["help_requested_at"] and not athlete["help_acknowledged_by"]:
                _update(db,conn,athlete,{"help_acknowledged_by":state["coaches"][2],"help_acknowledged_at":utc_now()},"help_acknowledged")
                state["scenario_message"] = f"{state['coaches'][2]} acknowledged your help request and is coming. Keep the call and coverage accurate."
    state["pending_events"] = retained


def _assign_to_learner(db: Any, conn: Any, athlete: dict, state: dict) -> dict:
    return _update(db,conn,athlete,{"de_coaches_json":_dump([state["learner"]]),"assignment_override":1},"assignment")


def _enter_stage(db: Any, conn: Any, row: dict, state: dict, stage: int) -> None:
    meet_id = row["meet_id"]
    state.pop("instruction_override",None)
    state.pop("guide_instruction_override",None)
    if stage != 12:
        state["pending_events"] = [event for event in state.get("pending_events",[]) if event["kind"] != "renew_coverage_request"]
    state["scenario_message"] = ""
    athletes = _athletes(conn,meet_id)
    if stage == 1:
        _phase(conn,meet_id,"pools")
        state["scenario_message"] = "The virtual coordinator has assigned the pool groups and changed the Pools signal to green. Open Team situation to inspect the common board."
    elif stage == 2:
        _choose_targets(athletes,state)
        primary = _target(athletes,state,"primary_id")
        if primary:
            _help(db,conn,primary)
            state["scenario_message"] = f"Pool emergency: {primary['name']} at {primary['source_strip']} has missed several bouts without coaching. A virtual colleague needs help now."
    elif stage == 3:
        _clear_help(db,conn,athletes)
    elif stage == 4:
        _clear_help(db,conn,athletes)
        for athlete in _athletes(conn,meet_id):
            if athlete["phase"] == "pools" and _eligible(athlete) and athlete["pool_result_at"] is None:
                _update(db,conn,athlete,{"pool_wins":3,"pool_losses":3,"pool_result_at":utc_now(),"pool_result_by":SYSTEM_ACTOR},"pool_result")
        state["scenario_message"] = "All other fictional pools are finished. Your practice result and attendance choices were retained. You can now mark yourself available."
    elif stage == 5:
        _clear_help(db,conn,athletes)
        _convert_to_de(db,conn,meet_id,state)
        _phase(conn,meet_id,"de")
        state["scenario_message"] = "The virtual coordinator imported the DE list, assigned equal-coach pod groups and started DE. Actual bout strips are still unknown."
    elif stage == 6:
        _choose_targets(athletes,state)
        state["scenario_message"] = f"Fictional parent message: {state['primary_name']} is on deck. Actual strip is not known yet."
    elif stage == 7:
        state["scenario_message"] = f"New fictional parent message: {state['primary_name']} is fencing now on C3. Add the actual strip and start physical coverage."
    elif stage == 8:
        primary = _target(athletes,state,"primary_id")
        secondary = _target(athletes,state,"secondary_id")
        if primary and secondary and _eligible(secondary):
            _update(db,conn,secondary,{"de_coaches_json":_dump([state["learner"]]),"assignment_override":1,"call_status":"on_deck","live_location":"J2","reported_at":utc_now(),"reported_by":SYSTEM_ACTOR},"live_update")
            state["busy_coach"] = state["learner"]
            state["scenario_message"] = f"{secondary['name']} was called in another event on J2 while you are with {primary['name']}. Watch whether a colleague responds."
            state["response_processed"] = False
            response_available = int(state.get("variant",0)) % 2 == 0
            if not response_available:
                other_athletes = [a for a in _athletes(conn,meet_id)
                                  if _eligible(a) and a["id"] not in {primary["id"],secondary["id"]}
                                  and not a["covered_by"] and not a["takeover_coach"]]
                for index,coach in enumerate(state["coaches"][1:]):
                    if index < len(other_athletes):
                        _virtual_cover(db,conn,other_athletes[index],coach,f"F{index+1}")
            _queue(state,"other_coverage" if response_available else "no_response",15,athlete_id=secondary["id"])
    elif stage == 9:
        # A new athlete belongs to a busy virtual coach; the learner can take over.
        candidates = [a for a in athletes if a["phase"] == "de" and _eligible(a) and not a["covered_by"] and not a["takeover_coach"] and a["id"] != state.get("primary_id")]
        if candidates:
            secondary = candidates[-1]
            secondary = _update(db,conn,secondary,{"de_coaches_json":_dump([state["coaches"][1]]),"assignment_override":1,"call_status":"waiting","live_location":"","reported_at":None,"reported_by":""},"assignment")
            state.update(secondary_id=secondary["id"],secondary_name=secondary["name"])
            others = [a for a in _athletes(conn,meet_id) if _eligible(a) and a["id"] not in {secondary["id"],state.get("primary_id")} and not a["covered_by"] and not a["takeover_coach"]]
            for index,coach in enumerate(state["coaches"][1:]):
                if index < len(others):
                    _virtual_cover(db,conn,others[index],coach,f"F{index+1}")
            _queue(state,"sudden_call",8,athlete_id=secondary["id"])
            state["scenario_message"] = "Your bout result freed you. A new call will arrive shortly; watch the shared alternate-coverage cards."
    elif stage == 10:
        state["scenario_message"] = f"Your takeover of {state['secondary_name']} is reserved. The confirmed actual strip is J2. Start physical coverage there, then request help."
    elif stage == 11:
        secondary = _target(athletes,state,"secondary_id")
        if secondary and secondary["help_requested_at"] and not secondary["help_acknowledged_by"]:
            _queue(state,"ack_help",5,athlete_id=secondary["id"])
        state["scenario_message"] = "Your alert is shared with the virtual team. Record the result when this bout finishes."
    elif stage == 12:
        _clear_help(db,conn,athletes)
        recipients = _practice_request_recipients(db,conn,meet_id,state,prepare=True)
        candidates = [a for a in _athletes(conn,meet_id) if a["phase"] == "de" and _eligible(a) and not a["covered_by"] and not a["takeover_coach"]]
        if candidates:
            newly_assigned = [a for a in candidates if state["learner"] not in assigned_coaches(a)]
            primary = _target(athletes,state,"primary_id")
            cross_event = [a for a in newly_assigned if primary and a["event_id"] != primary["event_id"]]
            athlete = (cross_event or newly_assigned or candidates)[0]
            if recipients:
                state.setdefault("pending_events",[]).append(
                    _send_practice_coverage_request(db,conn,row,state,athlete,recipients)
                )
    elif stage == 13:
        candidates = [a for a in athletes if a["phase"] == "de" and _eligible(a) and not a["covered_by"] and not a["takeover_coach"]]
        if candidates:
            athlete = _assign_to_learner(db,conn,candidates[0],state)
            state.update(bye_id=athlete["id"],bye_name=athlete["name"])
            state["scenario_message"] = f"The fictional tableau gives {athlete['name']} a bye. Mark it using the normal Bye button."
    elif stage == 14:
        eligible = [a for a in athletes if a["phase"] == "de" and _eligible(a)]
        pairs = [(a,b) for a in eligible for b in eligible if a["id"] != b["id"] and a["event_id"] == b["event_id"]]
        if pairs:
            a,b = pairs[0]
            state.update(pair_a_id=a["id"],pair_a_name=a["name"],pair_b_id=b["id"],pair_b_name=b["name"])
            state["scenario_message"] = f"A virtual colleague noticed {a['name']} and {b['name']} are opponents in their next practice round. Mark their AFM pairing."
        else:
            state["scenario_message"] = "Not enough eligible opponents remain; the virtual coordinator will finish this optional scenario automatically."
    elif stage == 16:
        _clear_help(db,conn,athletes)
        eligible = [a for a in _athletes(conn,meet_id) if a["phase"] == "de" and _eligible(a) and not a["covered_by"]]
        if eligible:
            athlete = _assign_to_learner(db,conn,eligible[0],state)
            _update(db,conn,athlete,{"call_status":"now","live_location":"D4","reported_at":utc_now(),"reported_by":SYSTEM_ACTOR,"de_awaiting_next":0},"live_update")
            state.update(final_id=athlete["id"],final_name=athlete["name"])
            state["scenario_message"] = f"Next practice call: {athlete['name']} is fencing now on D4. The old actual strip is no longer current."
    elif stage == 17:
        _clear_help(db,conn,athletes)
        _finish(conn,meet_id,SYSTEM_ACTOR)
        state["completed_runs"] = int(state.get("completed_runs", 0)) + 1
        state["last_completed_at"] = utc_now()
        state["scenario_message"] = "Exercise complete. The fictional day is read-only. Practice again starts a different variation while this activation remains valid."
    state["entered_action_id"] = _max_action(conn,meet_id)


def _completed(conn: Any, row: dict, state: dict) -> bool:
    stage,meet_id = int(row["stage"]),str(row["meet_id"])
    actions = [a for a in _actions(conn,meet_id,int(state.get("entered_action_id",0))) if a["actor"] == state["learner"]]
    kinds = {a["action"] for a in actions}
    athletes = _athletes(conn,meet_id)
    primary,secondary = _target(athletes,state,"primary_id"),_target(athletes,state,"secondary_id")
    person = state.get("participants",{}).get(state["learner"],{})
    views = person.get("stage_views",{}).get(str(stage),[])

    # Only lessons about seeing a particular screen depend on navigation.
    # Calls, coverage and results below use shared saved data, never the view.
    if stage == 0:
        return "My Group" in views
    if stage in {1,5}:
        return "Live" in views
    if stage == 2:
        return "help_acknowledged" in kinds
    if stage == 3:
        return bool(kinds & {"pool_result","participation_absent","absent","participation_withdrawn","withdrawn"})
    if stage == 4:
        return bool(conn.execute("SELECT 1 FROM coach_availability WHERE meet_id = ? AND coach_name = ? AND is_available = 1",(meet_id,state["learner"])).fetchone())
    if stage == 6:
        return bool(primary and primary["call_status"] != "waiting" and any(
            action["action"] == "live_update" and action["athlete_id"] == primary["id"]
            and _load(action["new_json"],{}).get("call_status") in {"on_deck","in_hole","now"}
            for action in actions
        ))
    if stage == 7:
        return bool(primary and primary["covered_by"] == state["learner"] and primary["live_location"])
    if stage == 8:
        return bool(state.get("response_processed") and any(a["action"] in {"won","lost"} and a["athlete_id"] == state.get("primary_id") for a in actions))
    if stage == 9:
        return bool(secondary and (secondary["takeover_coach"] == state["learner"] or secondary["covered_by"] == state["learner"]))
    if stage == 10:
        return bool(secondary and secondary["covered_by"] == state["learner"] and "help_request" in kinds)
    if stage == 11:
        return any(a["action"] in {"won","lost"} and a["athlete_id"] == state.get("secondary_id") for a in actions)
    if stage == 12:
        return any(a["action"] in {"won","lost"} and a["athlete_id"] == state.get("reassigned_id") and _load(a["previous_json"],{}).get("covered_by") == state["learner"] for a in actions)
    if stage == 13:
        return any(a["action"] == "bye" and a["athlete_id"] == state.get("bye_id") for a in actions)
    if stage in {14,15}:
        kind = "de_bout_create" if stage == 14 else "de_bout_resolve"
        pair = {state.get("pair_a_id"),state.get("pair_b_id")}
        return any(a["action"] == kind and {_load(a["new_json"],{}).get("athlete_a_id"),_load(a["new_json"],{}).get("athlete_b_id")} == pair for a in actions) or not state.get("pair_a_id")
    if stage == 16:
        return any(a["action"] in {"won","lost"} and a["athlete_id"] == state.get("final_id") and _load(a["previous_json"],{}).get("covered_by") == state["learner"] for a in actions) or not state.get("final_id")
    return False


def _observe(conn: Any, row: dict, state: dict, actor: str | None, view: str | None) -> None:
    meet = conn.execute("SELECT active_coaches_json, coordinators_json FROM meets WHERE id = ?", (row["meet_id"],)).fetchone()
    actors = {state["learner"]} if state.get("kind") == "run" else set(_load(meet["active_coaches_json"], []))
    if actor and actor in actors:
        people = state.setdefault("participants", {})
        person = people.setdefault(actor, {"first_seen": utc_now(), "views": [], "milestones": [], "stages_seen": []})
        person.setdefault("views",[])
        person.setdefault("milestones",[])
        person.setdefault("stages_seen",[])
        if view in {"My Group", "Live", "Activity"} and view not in person["views"]:
            person["views"].append(view)
        if view in {"My Group", "Live", "Activity"}:
            stage_views = person.setdefault("stage_views",{}).setdefault(str(row["stage"]),[])
            if view not in stage_views:
                stage_views.append(view)
        if int(row["stage"]) not in person["stages_seen"]:
            person["stages_seen"].append(int(row["stage"]))
    for action in _actions(conn, row["meet_id"]):
        if action["actor"] not in actors:
            continue
        person = state.setdefault("participants", {}).setdefault(action["actor"], {"first_seen": action["created_at"], "views": [], "milestones": [], "stages_seen": []})
        milestone = _MILESTONES.get(action["action"])
        if milestone and milestone not in person["milestones"]:
            person["milestones"].append(milestone)
    for available in conn.execute("SELECT coach_name FROM coach_availability WHERE meet_id = ? AND is_available = 1", (row["meet_id"],)).fetchall():
        person = state.get("participants", {}).get(available["coach_name"])
        if person and "Availability" not in person["milestones"]:
            person["milestones"].append("Availability")


def _stop_scope(conn: Any, row: dict, actor: str, *, expired: bool = False) -> dict:
    state = _load(row["state_json"],{})
    rows = [row]
    if state.get("kind") == "hub":
        rows += [dict(child) for child in conn.execute("SELECT * FROM training_sessions WHERE source_meet_id = ?",(row["meet_id"],)).fetchall()
                 if _load(child["state_json"],{}).get("hub_meet_id") == row["meet_id"]]
    result = row
    for current in rows:
        current_state = _load(current["state_json"],{})
        _finish(conn,current["meet_id"],actor)
        if current["status"] not in {"completed","stopped"}:
            current_state["last_feedback"] = "The practice period expired. This exercise is now read-only." if expired else "The instructor ended this practice period. This exercise is now read-only."
            updated = _persist(conn,current,current_state,status="stopped")
            if current["meet_id"] == row["meet_id"]:
                result = updated
    return result


def tick_training(db: Any, meet_id: str, actor: str | None = None, view: str | None = None) -> dict | None:
    """Observe learner actions and due virtual events; advance one task per tick."""
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn,meet_id,required=False)
        if row is None:
            conn.commit()
            return None
        state = deepcopy(_load(row["state_json"],{}))
        hub = row if state.get("kind") == "hub" else _row(conn,state["hub_meet_id"])
        hub_state = _load(hub["state_json"],{})
        if _expired(hub_state):
            _stop_scope(conn,hub,SYSTEM_ACTOR,expired=True)
            row = _row(conn,meet_id)
            conn.commit()
            return _metadata(row)
        if state.get("kind") == "hub":
            conn.commit()
            return _metadata(row)
        if hub["status"] not in {"running","paused"}:
            row = _stop_scope(conn,row,SYSTEM_ACTOR)
            conn.commit()
            return _metadata(row)
        if row["status"] in {"running","paused"}:
            _observe(conn,row,state,actor,view)
        if row["status"] == "running" and hub["status"] == "running":
            _process_pending(db,conn,row,state)
            if _completed(conn,row,state):
                next_stage = min(int(row["stage"])+1,len(TRAINING_STEPS)-1)
                state.setdefault("history",[]).append({"stage":row["stage"],"completed_at":utc_now(),"by":state["learner"]})
                state["last_feedback"] = f"Completed: {TRAINING_STEPS[int(row['stage'])]['title']}."
                _enter_stage(db,conn,row,state,next_stage)
                row = _persist(conn,row,state,stage=next_stage,status="completed" if next_stage == len(TRAINING_STEPS)-1 else "running")
            else:
                row = _persist(conn,row,state)
        else:
            row = _persist(conn,row,state)
        conn.commit()
    return _metadata(row)


def set_training_paused(db: Any, meet_id: str, paused: bool, actor: str) -> dict:
    _actor(actor)
    if not isinstance(paused, bool):
        raise TypeError("Paused must be true or false.")
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, meet_id)
        if row["status"] not in {"running", "paused"}:
            raise CompCoachError("Restart the finished exercise before resuming it.")
        state = _load(row["state_json"], {})
        state["last_feedback"] = "Automatic scenarios paused. Coach actions still save." if paused else "Automatic scenarios resumed."
        row = _persist(conn, row, state, status="paused" if paused else "running")
        conn.commit()
    return _metadata(row)


def advance_training(db: Any, meet_id: str, actor: str) -> dict:
    actor = _actor(actor)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, meet_id)
        if row["status"] not in {"running", "paused"}:
            raise CompCoachError("This exercise is finished. Restart it to practice again.")
        state = deepcopy(_load(row["state_json"], {}))
        next_stage = min(int(row["stage"]) + 1, len(TRAINING_STEPS) - 1)
        state.setdefault("history", []).append({"stage": row["stage"], "completed_at": utc_now(), "by": actor, "skipped": True})
        state["last_feedback"] = f"Instructor advanced past {TRAINING_STEPS[int(row['stage'])]['title']}."
        _enter_stage(db, conn, row, state, next_stage)
        row = _persist(conn, row, state, stage=next_stage, status="completed" if next_stage == len(TRAINING_STEPS) - 1 else str(row["status"]))
        conn.commit()
    return _metadata(row)


def stop_training(db: Any, meet_id: str, actor: str) -> dict:
    actor = _actor(actor)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn,meet_id)
        row = _stop_scope(conn,row,actor)
        conn.commit()
    return _metadata(row)


def restart_training(db: Any, meet_id: str, actor: str) -> dict:
    """Replace only fictional run data while retaining shared URL tokens."""
    actor = _actor(actor)
    with db._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, meet_id)
        state = _load(row["state_json"], {})
        if state.get("kind") != "run":
            raise CompCoachError("Activate a new practice period to restart the hub.")
        _check_hub(conn,state["hub_meet_id"])
        if actor != state["learner"] and actor != row["created_by"]:
            # Admin controls are checked by the UI; only the learner restarts from a coach link.
            raise CompCoachError("Restart your own exercise from your coach link.")
        db._assert_competition_open(conn, str(conn.execute("SELECT competition_id FROM meets WHERE id = ?", (meet_id,)).fetchone()["competition_id"]))
        event_ids = [event["id"] for event in _events(conn, meet_id)]
        now = utc_now()
        for event_id in event_ids:
            # Remove dependent audit rows before athletes; these are exercise-only.
            for table in ("de_bouts", "actions", "coach_assignment_history", "pod_assignments", "phase_states", "pool_waves"):
                conn.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
            conn.execute("DELETE FROM athletes WHERE event_id = ?", (event_id,))
            conn.execute("UPDATE events SET status = 'open', updated_at = ? WHERE id = ?", (now,event_id))
        conn.execute("DELETE FROM coach_availability WHERE meet_id = ?", (meet_id,))
        conn.execute("UPDATE meets SET status = 'open', day_status = 'active', ended_at = NULL, ended_by = '', updated_at = ? WHERE id = ?", (now,meet_id))
        seeded = {**_seed(db,conn,meet_id,list(state["coaches"])),
                  "kind":"run","hub_meet_id":state["hub_meet_id"],"learner":state["learner"],
                  "expires_at":state["expires_at"],"variant":int(state.get("variant",0))+1,"pending_events":[],
                  "completed_runs":int(state.get("completed_runs",0)),"last_completed_at":state.get("last_completed_at")}
        if state.get("participant_key"):
            seeded["participant_key"] = state["participant_key"]
        seeded["last_feedback"] = f"Exercise restarted by {actor}. The existing coach link still works."
        _persist(conn, row, seeded, stage=0, status="running")
        conn.commit()
    return db.get_meet(meet_id)
