"""The complete practice course uses coherent targets and ordinary commands."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from compcoach_live import training
from compcoach_live.de_bouts import create_de_bout
from compcoach_live.storage import CompCoachDB


@pytest.fixture
def course(tmp_path, monkeypatch):
    now = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training,"utc_now",lambda:now[0].isoformat())
    database = CompCoachDB(tmp_path / "coherent-course.sqlite")
    real = database.create_meet("Real competition",["Actual coach"],[])
    hub = training.start_training(database,real["id"],[],actor="Actual coach",open_entry=True)
    run = training.join_training(database,hub["id"],"Learner",participant_key=uuid4().hex)

    def advance(seconds):
        now[0] += timedelta(seconds=seconds)

    return database,real,run,advance


def _tick(database,run,view="My Group"):
    return training.tick_training(database,run["id"],"Learner",view)


def _target(database,run,key):
    state = training.get_training(database,run["id"])["state"]
    for event in database.list_meet_events(run["id"]):
        athlete = database.get_athlete(event["id"],state[key])
        if athlete:
            return athlete
    raise AssertionError(key)


def _set_state(database,run,**changes):
    with database._connection() as conn:
        row = training._row(conn,run["id"])
        state = training._load(row["state_json"],{})
        state.update(changes)
        training._persist(conn,row,state)
        conn.commit()


def _remove_state_keys(database,run,*keys):
    with database._connection() as conn:
        row = training._row(conn,run["id"])
        state = training._load(row["state_json"],{})
        for key in keys:
            state.pop(key,None)
        training._persist(conn,row,state)
        conn.commit()


def _reach_de_plan(database,run):
    assert _tick(database,run)["stage"] == 1
    assert _tick(database,run,"Live")["stage"] == 2
    athlete = _target(database,run,"primary_id")
    database.acknowledge_help(athlete["event_id"],athlete["id"],"Learner")
    assert _tick(database,run,"Live")["stage"] == 3
    database.set_pool_result(athlete["event_id"],athlete["id"],wins=3,losses=3,actor="Learner")
    assert _tick(database,run)["stage"] == 4
    database.set_coach_availability(run["id"],"Learner",True,"Learner")
    assert _tick(database,run)["stage"] == 5


def _reach_takeover(database,run,advance):
    _reach_de_plan(database,run)
    assert _tick(database,run,"Live")["stage"] == 6
    primary = _target(database,run,"primary_id")
    database.report_call(primary["event_id"],primary["id"],status="on_deck",location="",actor="Learner")
    assert _tick(database,run,"Live")["stage"] == 7
    database.cover_athlete(primary["event_id"],primary["id"],"Learner","Learner",location="C3")
    assert _tick(database,run)["stage"] == 8
    advance(16)
    assert _tick(database,run,"Live")["stage"] == 8
    database.mark_result(primary["event_id"],primary["id"],outcome="won",actor="Learner")
    assert _tick(database,run)["stage"] == 9
    advance(9)
    assert _tick(database,run,"Live")["stage"] == 9
    secondary = _target(database,run,"secondary_id")
    database.take_over_athlete(secondary["event_id"],secondary["id"],"Learner","Learner")
    assert _tick(database,run,"Live")["stage"] == 10
    return secondary


@pytest.mark.parametrize("variant,outcome",[(0,"won"),(1,"lost"),(2,"won")])
def test_complete_course_guides_one_coherent_action_at_a_time(course,variant,outcome):
    database,real,run,advance = course
    real_before = database.get_meet(real["id"])
    directory_before = database.list_coaches()
    _set_state(database,run,variant=variant)
    _reach_de_plan(database,run)
    assert _tick(database,run,"Live")["stage"] == 6
    primary = _target(database,run,"primary_id")
    assert "On deck" in training.get_training(database,run["id"])["guide_instruction"]
    database.report_call(primary["event_id"],primary["id"],status="on_deck",location="",actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 7
    assert primary["name"] in lesson["guide_instruction"] and "C3" in lesson["guide_instruction"]
    database.report_call(primary["event_id"],primary["id"],status="on_deck",location="C3",actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 7
    assert lesson["guide_instruction"] == f"In My Group, tap I’m with {primary['name']} when you arrive on C3."
    database.cover_athlete(primary["event_id"],primary["id"],"Learner","Learner",location="C3")
    lesson = _tick(database,run)
    assert lesson["stage"] == 8 and "Won or Lost" in lesson["guide_instruction"]
    # A quick result is accepted once; while the simulated colleague responds,
    # the guide never asks the coach to repeat that saved result.
    database.mark_result(primary["event_id"],primary["id"],outcome=outcome,actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 8
    assert "is saved" in lesson["guide_instruction"] and "tap Won" not in lesson["guide_instruction"]
    advance(16)
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 9 and "wait" in lesson["guide_instruction"]
    secondary = _target(database,run,"secondary_id")
    advance(9)
    lesson = _tick(database,run,"Live")
    secondary = database.get_athlete(secondary["event_id"],secondary["id"])
    assert {"now":"Now","on_deck":"On deck","in_hole":"In the hole"}[secondary["call_status"]] in lesson["guide_instruction"]
    assert "I’ll take over" in lesson["guide_instruction"]
    database.take_over_athlete(secondary["event_id"],secondary["id"],"Learner","Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 10 and lesson["guide_target_id"] == secondary["id"]
    assert f"I’m with {secondary['name']}" in lesson["guide_instruction"]
    database.cover_athlete(secondary["event_id"],secondary["id"],"Learner","Learner",location="J2")
    lesson = _tick(database,run)
    assert lesson["stage"] == 10
    third = _target(database,run,"help_target_id")
    assert third["id"] not in {primary["id"],secondary["id"]}
    assert third["call_status"] == "now" and third["live_location"] == "K4"
    assert not third["covered_by"] and "Learner" in third["de_coaches"]
    assert lesson["guide_target_id"] == third["id"]
    assert third["name"] in lesson["guide_instruction"] and "🚨 Need help now" in lesson["guide_instruction"]
    for virtual in lesson["state"]["coaches"][1:]:
        assert any(athlete["covered_by"] == virtual for event in database.list_meet_events(run["id"])
                   for athlete in database.list_athletes(event["id"]))
    database.request_help(third["event_id"],third["id"],"Learner",location="K4")
    lesson = _tick(database,run)
    assert lesson["stage"] == 11 and lesson["guide_target_id"] == secondary["id"]
    assert secondary["name"] in lesson["guide_instruction"] and "Won or Lost" in lesson["guide_instruction"]
    assert not database.get_athlete(secondary["event_id"],secondary["id"])["help_requested_at"]
    advance(6)
    assert _tick(database,run)["stage"] == 11
    helped = database.get_athlete(third["event_id"],third["id"])
    assert helped["help_acknowledged_by"] and helped["covered_by"] == lesson["state"]["coaches"][2]
    assert database.get_athlete(secondary["event_id"],secondary["id"])["covered_by"] == "Learner"
    database.mark_result(secondary["event_id"],secondary["id"],outcome="lost",actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 12 and "I'll cover this bout" in lesson["guide_instruction"]
    requested = _target(database,run,"reassigned_id")
    plan = requested["de_coaches"]
    request = next(item for item in database.list_coverage_requests(run["id"],"Learner")
                   if item["id"] == lesson["state"]["coverage_request_id"])
    database.accept_coverage_request(run["id"],request["id"],"Learner",actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 12 and f"I’m with {requested['name']}" in lesson["guide_instruction"]
    assert lesson["guide_view"] == "My Group"
    database.cover_athlete(requested["event_id"],requested["id"],"Learner","Learner",location="E2")
    lesson = _tick(database,run)
    assert "Won or Lost" in lesson["guide_instruction"] and "I'll cover" not in lesson["guide_instruction"]
    assert database.get_athlete(requested["event_id"],requested["id"])["de_coaches"] == plan
    database.mark_result(requested["event_id"],requested["id"],outcome="won",actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 13
    bye = _target(database,run,"bye_id")
    assert not bye["de_awaiting_next"] and f"More actions · {bye['name']}" in lesson["guide_instruction"]
    assert bye["de_wins"] == bye["de_byes"] == 0 and bye["call_status"] == "waiting"
    database.mark_bye(bye["event_id"],bye["id"],actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 14 and lesson["guide_view"] == "Live"
    pair_a,pair_b = _target(database,run,"pair_a_id"),_target(database,run,"pair_b_id")
    assert "Review pairing" in lesson["guide_instruction"] and "Confirm pairing" in lesson["instruction"]
    assert not pair_a["covered_by"] and not pair_b["covered_by"]
    assert not pair_a["takeover_coach"] and not pair_b["takeover_coach"]
    create_de_bout(database,pair_a["event_id"],pair_a["id"],pair_b["id"],actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 15 and f"{pair_a['name']} wins" in lesson["guide_instruction"]
    database.mark_result(pair_a["event_id"],pair_a["id"],outcome="won",actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 16
    final = _target(database,run,"final_id")
    assert "Now on D4" in lesson["guide_instruction"] and f"I’m with {final['name']}" in lesson["guide_instruction"]
    database.cover_athlete(final["event_id"],final["id"],"Learner","Learner",location="D4")
    lesson = _tick(database,run)
    assert lesson["stage"] == 16 and "Won or Lost" in lesson["guide_instruction"]
    database.mark_result(final["event_id"],final["id"],outcome="won",actor="Learner")
    lesson = _tick(database,run)
    assert lesson["stage"] == 17 and lesson["status"] == "completed"
    assert {item["stage"] for item in lesson["state"]["history"]} == set(range(17))
    assert database.get_meet(run["id"])["coach_token"] == run["coach_token"]
    assert database.get_meet(real["id"]) == real_before
    assert database.list_coaches() == directory_before


@pytest.mark.parametrize("location",["","C3"])
def test_call_saved_during_de_plan_moves_directly_to_arrival_without_repeat(course,location):
    database,_real,run,_advance = course
    _reach_de_plan(database,run)
    primary = _target(database,run,"primary_id")
    saved = database.report_call(primary["event_id"],primary["id"],status="on_deck",location=location,actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 7
    assert "I’m with" in lesson["guide_instruction"] and "tap On deck" not in lesson["guide_instruction"]
    assert database.get_athlete(primary["event_id"],primary["id"]) == saved
    assert {item["stage"] for item in lesson["state"]["history"]} >= {5,6}


def test_legacy_stage6_with_discarded_action_boundary_uses_its_saved_de_call(course):
    database,_real,run,_advance = course
    _reach_de_plan(database,run)
    assert _tick(database,run,"Live")["stage"] == 6
    primary = _target(database,run,"primary_id")
    database.report_call(primary["event_id"],primary["id"],status="on_deck",location="C3",actor="Learner")
    with database._connection() as conn:
        cutoff = training._max_action(conn,run["id"])
    _set_state(database,run,entered_action_id=cutoff)
    _remove_state_keys(database,run,"de_started_action_id")
    assert _tick(database,run,"Live")["stage"] == 7


def test_known_different_strip_names_modify_call_before_the_arrival_button(course):
    database,_real,run,_advance = course
    _reach_de_plan(database,run)
    assert _tick(database,run,"Live")["stage"] == 6
    primary = _target(database,run,"primary_id")
    database.report_call(primary["event_id"],primary["id"],status="on_deck",location="G4",actor="Learner")
    lesson = _tick(database,run,"Live")
    assert lesson["stage"] == 7
    assert "Modify call" in lesson["guide_instruction"] and "C3" in lesson["guide_instruction"]
    assert f"I’m with {primary['name']}" in lesson["guide_instruction"]


def test_new_help_lesson_rejects_alert_for_the_athlete_already_covered(course):
    database,_real,run,advance = course
    secondary = _reach_takeover(database,run,advance)
    database.cover_athlete(secondary["event_id"],secondary["id"],"Learner","Learner",location="J2")
    lesson = _tick(database,run)
    third = _target(database,run,"help_target_id")
    database.request_help(secondary["event_id"],secondary["id"],"Learner",location="J2")
    assert _tick(database,run)["stage"] == 10
    database.clear_help(secondary["event_id"],secondary["id"],"Learner")
    database.request_help(third["event_id"],third["id"],"Learner",location="K4")
    assert _tick(database,run)["stage"] == 11
    assert lesson["state"]["help_flow_version"] == 2


@pytest.mark.parametrize("already_sent_help",[False,True])
def test_legacy_stage10_upgrades_without_reset_or_losing_a_saved_alert(course,already_sent_help):
    database,_real,run,advance = course
    secondary = _reach_takeover(database,run,advance)
    metadata = training.get_training(database,run["id"])
    history = metadata["state"]["history"]
    _remove_state_keys(database,run,"help_flow_version","help_target_id","help_target_name")
    database.cover_athlete(secondary["event_id"],secondary["id"],"Learner","Learner",location="J2")
    if already_sent_help:
        database.request_help(secondary["event_id"],secondary["id"],"Learner",location="J2")
    lesson = _tick(database,run)
    assert database.get_meet(run["id"])["coach_token"] == run["coach_token"]
    assert lesson["state"]["history"][:len(history)] == history
    assert database.get_athlete(secondary["event_id"],secondary["id"])["covered_by"] == "Learner"
    if already_sent_help:
        assert lesson["stage"] == 11 and lesson["state"]["help_flow_version"] == 1
        assert lesson["state"]["help_target_id"] == secondary["id"]
    else:
        assert lesson["stage"] == 10 and lesson["state"]["help_flow_version"] == 2
        assert lesson["state"]["help_target_id"] != secondary["id"]
