"""Autonomous practice uses real domain commands without admin intervention."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from compcoach_live import training
from compcoach_live.de_bouts import create_de_bout
from compcoach_live.storage import CompCoachDB, CompCoachError, EventLockedError


@pytest.fixture
def db(tmp_path):
    return CompCoachDB(tmp_path / "practice.sqlite")


@pytest.fixture
def clock(monkeypatch):
    value = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training, "utc_now", lambda: value[0].isoformat())
    def advance(seconds):
        value[0] += timedelta(seconds=seconds)
    return advance


def make_run(db, names=("Coach Alex", "Coach Taylor")):
    real = db.create_meet("Actual competition", names, coordinators=[])
    event = db.list_meet_events(real["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "actual", "name": "ACTUAL Athlete", "phase": "pools", "strip": "Z9", "pod": "Z", "pool": "2"}], "Coach Alex")
    db.assign_athletes(event["id"], [db.list_athletes(event["id"])[0]["id"]], main_coach=names[0], actor=names[0])
    hub = training.start_training(db, real["id"], actor=names[0], duration_days=14)
    run = training.join_training(db, hub["id"], names[0])
    return real, hub, run


def target(db, run, key):
    state = training.get_training(db, run["id"])["state"]
    for event in db.list_meet_events(run["id"]):
        athlete = db.get_athlete(event["id"], state[key])
        if athlete:
            return athlete
    raise AssertionError(f"Missing exercise target {key}")


def step(db, run, expected, view="My Group"):
    result = training.tick_training(db, run["id"], training.get_training(db, run["id"])["learner"], view)
    assert result["stage"] == expected, result
    return result


def first_half(db, run, clock, *, outcome="won"):
    coach = training.get_training(db, run["id"])["learner"]
    step(db,run,1)
    assert all(db.get_phase_states(event["id"])["pools"]["started"] for event in db.list_meet_events(run["id"]))
    step(db,run,2,"Live")
    athlete = target(db,run,"primary_id")
    db.acknowledge_help(athlete["event_id"],athlete["id"],coach)
    step(db,run,3)
    athlete = target(db,run,"primary_id")
    db.set_pool_result(athlete["event_id"],athlete["id"],wins=4,losses=2,actor=coach)
    step(db,run,4)
    db.set_coach_availability(run["id"],coach,True,coach)
    step(db,run,5)
    step(db,run,6,"Live")
    athlete = target(db,run,"primary_id")
    assert athlete["pool_wins"] == 4  # retained in historical pool summary
    db.report_call(athlete["event_id"],athlete["id"],status="on_deck",location="",actor=coach)
    step(db,run,7)
    db.cover_athlete(athlete["event_id"],athlete["id"],coach,coach,location="C3")
    step(db,run,8)
    before = training.get_training(db,run["id"])
    assert len(before["state"]["pending_events"]) == 1
    clock(16)
    step(db,run,8)
    secondary = target(db,run,"secondary_id")
    if before["state"]["variant"] % 2 == 0:
        assert secondary["covered_by"] == before["state"]["coaches"][1]
    else:
        assert not secondary["covered_by"]
    db.mark_result(athlete["event_id"],athlete["id"],outcome=outcome,actor=coach)
    step(db,run,9)
    clock(9)
    step(db,run,9)
    secondary = target(db,run,"secondary_id")
    assert secondary["call_status"] in {"now","on_deck","in_hole"}
    db.take_over_athlete(secondary["event_id"],secondary["id"],coach,coach)
    step(db,run,10)
    assert not next(row for row in db.list_coach_availability(run["id"]) if row["coach_name"] == coach)["is_available"]
    db.cover_athlete(secondary["event_id"],secondary["id"],coach,coach,location="J2")
    db.request_help(secondary["event_id"],secondary["id"],coach)
    step(db,run,11)
    clock(6)
    step(db,run,11)
    assert target(db,run,"secondary_id")["help_acknowledged_by"]
    db.mark_result(secondary["event_id"],secondary["id"],outcome=outcome,actor=coach)
    step(db,run,12)
    offer = next(request for request in db.list_coverage_requests(run["id"],coach)
                 if request["id"] == training.get_training(db,run["id"])["state"]["coverage_request_id"])
    db.accept_coverage_request(run["id"],offer["id"],coach,actor=coach)


@pytest.mark.parametrize("variant,outcome", [(0,"won"),(1,"lost"),(2,"won")])
def test_full_course_without_admin_or_other_real_coach(db,clock,variant,outcome):
    real,hub,run = make_run(db)
    real_before = db.get_meet(real["id"])
    events_before = [db.list_athletes(event["id"]) for event in db.list_meet_events(real["id"])]
    directory_before = db.list_coaches()
    if variant:
        with db._connection() as conn:
            row = training._row(conn,run["id"])
            state = training._load(row["state_json"],{})
            state["variant"] = variant
            training._persist(conn,row,state)
    first_half(db,run,clock,outcome=outcome)
    coach = training.get_training(db,run["id"])["learner"]
    reassigned = target(db,run,"reassigned_id")
    assert coach not in reassigned["de_coaches"]  # Temporary coverage does not change the pod plan.
    assert reassigned["takeover_coach"] == coach
    assert reassigned["live_location"] == "E2"
    db.cover_athlete(reassigned["event_id"],reassigned["id"],coach,coach,location="E2")
    db.mark_result(reassigned["event_id"],reassigned["id"],outcome="won",actor=coach)
    step(db,run,13)
    athlete = target(db,run,"bye_id")
    db.mark_bye(athlete["event_id"],athlete["id"],actor=coach)
    step(db,run,14)
    a,b = target(db,run,"pair_a_id"),target(db,run,"pair_b_id")
    create_de_bout(db,a["event_id"],a["id"],b["id"],actor=coach,round_label="Practice round")
    step(db,run,15)
    db.mark_result(a["event_id"],a["id"],outcome="won",actor=coach)
    step(db,run,16)
    athlete = target(db,run,"final_id")
    db.cover_athlete(athlete["event_id"],athlete["id"],coach,coach,location="D4")
    db.mark_result(athlete["event_id"],athlete["id"],outcome="won",actor=coach)
    completed = step(db,run,17)
    assert completed["status"] == "completed"
    assert db.get_meet(run["id"])["status"] == "locked"
    assert db.get_meet(hub["id"])["status"] == "open"
    assert db.get_meet(real["id"]) == real_before
    assert [db.list_athletes(event["id"]) for event in db.list_meet_events(real["id"])] == events_before
    assert db.list_coaches() == directory_before
    hub_progress = training.get_training(db,hub["id"])["participants"]
    assert hub_progress[0]["status"] == "completed"
    assert "Takeover" in hub_progress[0]["milestones"]
    assert hub_progress[1]["status"] == "not started"
    # Season history excludes fictional assignments, even after completion.
    assert all(row["meet_id"] == real["id"] for row in db.list_assignment_history())


def test_late_join_and_parallel_join_are_isolated_and_idempotent(db):
    real,hub,run = make_run(db)
    step(db,run,1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        joined = list(pool.map(lambda _: training.join_training(db,hub["id"],"Coach Taylor"),range(4)))
    assert len({meet["id"] for meet in joined}) == 1
    late = joined[0]
    assert late["competition_id"] not in {real["competition_id"],run["competition_id"],hub["competition_id"]}
    assert training.get_training(db,late["id"])["stage"] == 0
    assert training.get_training(db,run["id"])["stage"] == 1
    assert run["coach_token"] != late["coach_token"]


def test_repeated_ticks_do_not_duplicate_calls_or_virtual_events(db,clock):
    _,_,run = make_run(db)
    first_half(db,run,clock)
    metadata = training.get_training(db,run["id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(lambda _: training.tick_training(db,run["id"],metadata["learner"],"My Group"),range(8)))
    assert all(output["stage"] == 12 for output in outputs)
    actions = [action for event in db.list_meet_events(run["id"]) for action in db.recent_actions(event["id"],200)]
    reassigned_updates = [action for action in actions if action["action"] == "live_update" and action["actor"] == training.SYSTEM_ACTOR and action["athlete_id"] == metadata["state"]["reassigned_id"] and action["new"]["live_location"] == "E2"]
    assert len(reassigned_updates) == 1


def test_restart_preserves_link_and_other_coaches_progress(db):
    _,hub,run = make_run(db)
    late = training.join_training(db,hub["id"],"Coach Taylor")
    step(db,run,1)
    step(db,late,1)
    old_ids = {athlete["id"] for event in db.list_meet_events(run["id"]) for athlete in db.list_athletes(event["id"])}
    restarted = training.restart_training(db,run["id"],"Coach Alex")
    assert restarted["coach_token"] == run["coach_token"]
    assert training.get_training(db,run["id"])["stage"] == 0
    assert training.get_training(db,run["id"])["state"]["variant"] == 1
    assert training.get_training(db,late["id"])["stage"] == 1
    assert not old_ids & {athlete["id"] for event in db.list_meet_events(run["id"]) for athlete in db.list_athletes(event["id"])}


@pytest.mark.parametrize("reason",["stop","expiry"])
def test_end_period_locks_all_runs_and_rejects_stale_taps(db,reason):
    real,hub,run = make_run(db)
    other = training.join_training(db,hub["id"],"Coach Taylor")
    athlete = target(db,run,"primary_id")
    if reason == "stop":
        training.stop_training(db,hub["id"],"Coach Alex")
    else:
        with db._connection() as conn:
            row = training._row(conn,hub["id"])
            state = training._load(row["state_json"],{})
            state["expires_at"] = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
            training._persist(conn,row,state)
        with pytest.raises(EventLockedError):
            db.set_pool_result(athlete["event_id"],athlete["id"],wins=3,losses=3,actor="Coach Alex")
        training.tick_training(db,run["id"],"Coach Alex","My Group")
    assert db.get_meet(real["id"])["status"] == "open"
    assert all(db.get_meet(meet["id"])["status"] == "locked" for meet in (hub,run,other))
    with pytest.raises(EventLockedError):
        db.set_pool_result(athlete["event_id"],athlete["id"],wins=3,losses=3,actor="Coach Alex")
    with pytest.raises(CompCoachError):
        training.join_training(db,hub["id"],"Coach Taylor")
    with pytest.raises(CompCoachError):
        training.restart_training(db,run["id"],"Coach Alex")


def test_virtual_names_cannot_collide_with_the_learner(db):
    _,hub,run = make_run(db,names=("Coach Avery (virtual)",))
    roster = run["active_coaches"]
    assert len(set(name.casefold() for name in roster)) == 3
    assert training.get_training(db,run["id"])["learner"] == "Coach Avery (virtual)"
    assert len(db.list_coaches()) == 1


def test_invalid_period_roster_and_real_day_never_become_training(db):
    real = db.create_meet("Real",["Coach Alex"],coordinators=[])
    assert training.tick_training(db,real["id"],"Coach Alex","My Group") is None
    with pytest.raises(ValueError):
        training.start_training(db,real["id"],duration_days=10)
    with pytest.raises(CompCoachError):
        training.start_training(db,real["id"],coach_names=[])
    with pytest.raises(CompCoachError):
        training.stop_training(db,real["id"],"Coach Alex")
    assert db.get_meet(real["id"])["status"] == "open"


def test_absence_is_preserved_and_next_task_chooses_an_active_athlete(db):
    _,_,run = make_run(db)
    step(db,run,1)
    step(db,run,2,"Live")
    athlete = target(db,run,"primary_id")
    db.acknowledge_help(athlete["event_id"],athlete["id"],"Coach Alex")
    step(db,run,3)
    db.mark_absent(athlete["event_id"],athlete["id"],"Coach Alex")
    step(db,run,4)
    db.set_coach_availability(run["id"],"Coach Alex",True,"Coach Alex")
    step(db,run,5)
    step(db,run,6,"Live")
    absent = db.get_athlete(athlete["event_id"],athlete["id"])
    assert absent["participation_status"] == "absent"
    assert absent["phase"] == "pools"
    assert target(db,run,"primary_id")["id"] != absent["id"]


def test_wrong_pair_cannot_complete_the_named_pairing_task(db,clock):
    _,_,run = make_run(db)
    first_half(db,run,clock)
    reassigned = target(db,run,"reassigned_id")
    db.cover_athlete(reassigned["event_id"],reassigned["id"],"Coach Alex","Coach Alex",location="E2")
    db.mark_result(reassigned["event_id"],reassigned["id"],outcome="won",actor="Coach Alex")
    step(db,run,13)
    athlete = target(db,run,"bye_id")
    db.mark_bye(athlete["event_id"],athlete["id"],actor="Coach Alex")
    step(db,run,14)
    named = target(db,run,"pair_a_id")
    other_event = next(event for event in db.list_meet_events(run["id"]) if event["id"] != named["event_id"])
    others = [athlete for athlete in db.list_athletes(other_event["id"]) if athlete["active_state"] == "active"]
    create_de_bout(db,other_event["id"],others[0]["id"],others[1]["id"],actor="Coach Alex")
    step(db,run,14)
    named_b = target(db,run,"pair_b_id")
    create_de_bout(db,named["event_id"],named["id"],named_b["id"],actor="Coach Alex")
    step(db,run,15)


def test_virtual_response_preserves_a_call_reported_by_the_learner(db,clock):
    _,_,run = make_run(db)
    step(db,run,1)
    step(db,run,2,"Live")
    athlete = target(db,run,"primary_id")
    db.acknowledge_help(athlete["event_id"],athlete["id"],"Coach Alex")
    step(db,run,3)
    db.set_pool_result(athlete["event_id"],athlete["id"],wins=3,losses=3,actor="Coach Alex")
    step(db,run,4)
    db.set_coach_availability(run["id"],"Coach Alex",True,"Coach Alex")
    step(db,run,5)
    step(db,run,6,"Live")
    athlete = target(db,run,"primary_id")
    db.report_call(athlete["event_id"],athlete["id"],status="on_deck",location="",actor="Coach Alex")
    step(db,run,7)
    db.cover_athlete(athlete["event_id"],athlete["id"],"Coach Alex","Coach Alex",location="C3")
    step(db,run,8)
    secondary = target(db,run,"secondary_id")
    db.report_call(secondary["event_id"],secondary["id"],status="in_hole",location="L8",actor="Coach Alex")
    clock(16)
    step(db,run,8)
    secondary = target(db,run,"secondary_id")
    assert secondary["live_location"] == "L8"
    assert secondary["call_status"] == "in_hole"
    assert secondary["reported_by"] == "Coach Alex"
