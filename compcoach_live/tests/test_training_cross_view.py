"""Shared coach actions teach the same lesson from My Group or Team situation."""

from datetime import datetime, timedelta, timezone

import pytest

from compcoach_live import training
from compcoach_live.storage import CompCoachDB


@pytest.fixture
def course(tmp_path, monkeypatch):
    now = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training, "utc_now", lambda: now[0].isoformat())
    database = CompCoachDB(tmp_path / "cross-view.sqlite")
    source = database.create_meet("Actual competition", ["Learner", "Colleague"], [])
    hub = training.start_training(database, source["id"], actor="Learner")
    run = training.join_training(database, hub["id"], "Learner")

    def advance(seconds):
        now[0] += timedelta(seconds=seconds)

    return database, run, advance


def _tick(database, run, view="Live", actor="Learner"):
    return training.tick_training(database, run["id"], actor, view)


def _target(database, run, key="primary_id"):
    state = training.get_training(database, run["id"])["state"]
    for event in database.list_meet_events(run["id"]):
        athlete = database.get_athlete(event["id"], state[key])
        if athlete:
            return athlete
    raise AssertionError(key)


def _enter_call_lesson(database, run, view="Live"):
    assert _tick(database, run, "My Group")["stage"] == 1
    assert _tick(database, run, "Live")["stage"] == 2
    athlete = _target(database, run)
    database.acknowledge_help(athlete["event_id"], athlete["id"], "Learner")
    assert _tick(database, run, view)["stage"] == 3
    database.set_pool_result(athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Learner")
    assert _tick(database, run, view)["stage"] == 4
    database.set_coach_availability(run["id"], "Learner", True, "Learner")
    assert _tick(database, run, view)["stage"] == 5
    assert _tick(database, run, "Live")["stage"] == 6
    return _target(database, run)


@pytest.mark.parametrize("view", ["Live", "My Group"])
@pytest.mark.parametrize("location", ["", "C3"])
@pytest.mark.parametrize("status", ["on_deck", "in_hole", "now"])
def test_saved_target_call_advances_from_either_view_with_known_or_unknown_strip(course, view, location, status):
    database, run, _advance = course
    athlete = _enter_call_lesson(database, run, view)
    saved = database.report_call(
        athlete["event_id"], athlete["id"], status=status, location=location, actor="Learner",
    )
    progressed = _tick(database, run, view)
    assert progressed["stage"] == 7
    assert "Completed: Call without a confirmed strip" in progressed["last_feedback"]
    # Training observes the same shared athlete row; it does not rewrite the
    # saved call or require the athlete to be edited a second time after moving.
    assert database.get_athlete(athlete["event_id"], athlete["id"]) == saved
    assert _tick(database, run, "My Group")["stage"] == 7
    assert database.get_athlete(athlete["event_id"], athlete["id"]) == saved


def test_virtual_coach_cannot_complete_the_learner_call_lesson(course):
    database, run, _advance = course
    athlete = _enter_call_lesson(database, run)
    virtual = training.get_training(database, run["id"])["state"]["coaches"][1]
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="C3", actor=virtual)
    assert _tick(database, run)["stage"] == 6
    assert _tick(database, run, "My Group")["stage"] == 6
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="C3", actor="Learner")
    assert _tick(database, run)["stage"] == 7


def test_waiting_snapshot_followed_by_virtual_call_does_not_count_as_learner_call(course):
    database, run, _advance = course
    athlete = _enter_call_lesson(database, run)
    virtual = training.get_training(database, run["id"])["state"]["coaches"][1]
    database.report_call(athlete["event_id"], athlete["id"], status="waiting", location="", actor="Learner")
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="", actor=virtual)
    assert _tick(database, run)["stage"] == 6
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="", actor="Learner")
    assert _tick(database, run)["stage"] == 7


def test_call_for_a_different_athlete_does_not_complete_the_target_lesson(course):
    database, run, _advance = course
    athlete = _enter_call_lesson(database, run)
    another = _target(database, run, "secondary_id")
    assert another["id"] != athlete["id"]
    database.report_call(another["event_id"], another["id"], status="on_deck", location="C3", actor="Learner")
    assert _tick(database, run)["stage"] == 6


def test_observation_lessons_still_require_the_learner_to_visit_the_named_view(course):
    database, run, _advance = course
    virtual = training.get_training(database, run["id"])["state"]["coaches"][1]
    assert _tick(database, run, "My Group", actor=virtual)["stage"] == 0
    assert _tick(database, run, "Live")["stage"] == 0
    assert _tick(database, run, "My Group")["stage"] == 1
    assert _tick(database, run, "My Group")["stage"] == 1
    assert _tick(database, run, "Live", actor=virtual)["stage"] == 1
    assert _tick(database, run, "Live")["stage"] == 2


@pytest.mark.parametrize("view", ["Live", "My Group"])
def test_physical_coverage_and_result_progress_from_the_current_operational_view(course, view):
    database, run, advance = course
    athlete = _enter_call_lesson(database, run, view)
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="C3", actor="Learner")
    assert _tick(database, run, view)["stage"] == 7
    database.cover_athlete(athlete["event_id"], athlete["id"], "Learner", "Learner", location="C3")
    assert _tick(database, run, view)["stage"] == 8
    advance(16)
    assert _tick(database, run, view)["stage"] == 8
    database.mark_result(athlete["event_id"], athlete["id"], outcome="won", actor="Learner")
    assert _tick(database, run, view)["stage"] == 9
    assert database.get_athlete(athlete["event_id"], athlete["id"])["de_wins"] == 1


def test_de_plan_observation_does_not_advance_from_my_group_only(course):
    database, run, _advance = course
    assert _tick(database, run, "My Group")["stage"] == 1
    assert _tick(database, run, "Live")["stage"] == 2
    athlete = _target(database, run)
    database.acknowledge_help(athlete["event_id"], athlete["id"], "Learner")
    assert _tick(database, run, "My Group")["stage"] == 3
    database.set_pool_result(athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Learner")
    assert _tick(database, run, "My Group")["stage"] == 4
    database.set_coach_availability(run["id"], "Learner", True, "Learner")
    assert _tick(database, run, "My Group")["stage"] == 5
    assert _tick(database, run, "My Group")["stage"] == 5
    assert _tick(database, run, "Live")["stage"] == 6
