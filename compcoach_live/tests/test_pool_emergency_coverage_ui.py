"""An emergency pool volunteer can confirm arrival without DE call controls."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


@pytest.fixture
def emergency(tmp_path):
    path = tmp_path / "pool-emergency.sqlite"
    db = CompCoachDB(path)
    meet = db.create_meet("Pool emergency", ["Alex", "Taylor"], ["Jordan"],
                          first_event_name="Junior Epee")
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{
        "athlete_id": "pool-case", "name": "CASEY Athlete", "phase": "pools",
        "strip": "B3", "pod": "B", "pool": "2", "time": "",
    }], "Jordan")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Taylor", actor="Jordan")
    db.set_coach_availability(meet["id"], "Alex", True, "Alex")
    request = db.create_coverage_request(
        meet["id"], event["id"], athlete["id"], ["Alex"], "Jordan",
    )
    db.accept_coverage_request(meet["id"], request["id"], "Alex")
    return db, path, meet, event, athlete


def _app(emergency, *, role="coach", actor="Alex"):
    _, path, _, event, athlete = emergency
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.live_controls import render_pool_takeover_arrival
db = CompCoachDB({str(path)!r})
if st.session_state.pop("de_fast_refresh", False):
    st.rerun(scope="app")
notice = st.session_state.pop("de_fast_notice", None)
if notice:
    (st.success if notice[0] else st.error)(notice[1])
event = db.get_event({event['id']!r})
athlete = db.get_athlete(event['id'], {athlete['id']!r})
render_pool_takeover_arrival(db, event, {role!r}, {actor!r}, athlete,
                             key_prefix="personal")
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _arrive(app):
    matches = [button for button in app.button if button.label == "I’m with CASEY Athlete"]
    assert len(matches) == 1, [button.label for button in app.button]
    return matches[0]


def _status(db, meet):
    return next(row for row in db.list_coach_availability(meet["id"])
                if row["coach_name"] == "Alex")


@pytest.mark.parametrize("role", ["coach", "admin"])
def test_accepted_pool_emergency_arrival_starts_timer_and_result_releases_coach(emergency, role):
    db, _, meet, event, athlete = emergency
    accepted = db.get_athlete(event["id"], athlete["id"])
    assert accepted["takeover_coach"] == "Alex" and not accepted["covered_by"]
    assert not _status(db, meet)["is_available"] and not _status(db, meet)["is_busy"]
    app = _app(emergency, role=role)
    assert len(app.button) == 1 and not app.text_input
    _arrive(app).click().run()
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["covered_by"] == "Alex" and covered["covered_at"]
    assert covered["live_location"] == "B3" and covered["main_coach"] == "Taylor"
    assert _status(db, meet)["is_busy"] and not _status(db, meet)["is_available"]
    assert not app.button and app.success and not app.exception
    db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Alex")
    completed = db.get_athlete(event["id"], athlete["id"])
    assert not completed["takeover_coach"] and not completed["covered_by"]
    assert completed["covered_at"] is None and completed["main_coach"] == "Taylor"
    assert _status(db, meet)["is_available"] and not _status(db, meet)["is_busy"]
    app.run()
    assert not app.button and not app.exception


def test_arrival_uses_live_strip_before_imported_pool_strip(emergency):
    db, _, _, event, athlete = emergency
    db.report_call(event["id"], athlete["id"], status="now", location="C4", actor="Jordan")
    app = _app(emergency)
    _arrive(app).click().run()
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["live_location"] == "C4" and covered["source_strip"] == "B3"
    assert covered["covered_by"] == "Alex" and not app.exception


def test_unknown_pool_strip_does_not_block_true_physical_arrival(emergency):
    db, _, _, event, athlete = emergency
    with db._connection() as conn:
        conn.execute("UPDATE athletes SET source_strip = '', live_location = '', version = version + 1 WHERE id = ?",
                     (athlete["id"],))
        conn.commit()
    app = _app(emergency)
    assert not _arrive(app).disabled
    assert any("Strip not known yet" in caption.value for caption in app.caption)
    _arrive(app).click().run()
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["covered_by"] == "Alex" and covered["covered_at"]
    assert covered["live_location"] == "" and not app.button and not app.exception


def test_only_the_accepted_owner_sees_the_pool_arrival_action(emergency):
    app = _app(emergency, actor="Taylor")
    assert not app.button and not app.exception


def test_stale_arrival_cannot_overwrite_a_newer_location(emergency):
    db, _, _, event, athlete = emergency
    app = _app(emergency)
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Jordan")
    _arrive(app).click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert not saved["covered_by"] and saved["takeover_coach"] == "Alex"
    assert saved["live_location"] == "D4" and app.error and not app.exception
    assert _arrive(app)


def test_released_emergency_takeover_hides_arrival_action(emergency):
    db, _, meet, event, athlete = emergency
    app = _app(emergency)
    assert _arrive(app)
    db.release_takeover(event["id"], athlete["id"], "Alex", "Alex")
    app.run()
    assert not app.button and _status(db, meet)["is_available"]
    assert not app.exception


def test_stale_arrival_cannot_move_a_coach_now_busy_in_another_event(emergency):
    db, _, meet, event, athlete = emergency
    app = _app(emergency)
    second_event = db.add_meet_event(meet["id"], "Cadet Epee")
    db.merge_import(second_event["id"], [{
        "athlete_id": "other-case", "name": "ROBIN Athlete", "phase": "de",
        "strip": "D1", "pod": "D", "pool": "", "time": "",
    }], "Jordan")
    other = db.list_athletes(second_event["id"])[0]
    db.cover_athlete(second_event["id"], other["id"], "Alex", "Jordan", location="D4")
    _arrive(app).click().run()
    original = db.get_athlete(event["id"], athlete["id"])
    assert not original["covered_by"]
    assert db.get_athlete(second_event["id"], other["id"])["covered_by"] == "Alex"
    assert app.error and not app.exception


def test_paused_competition_hides_pool_arrival_action(emergency):
    db, _, meet, _, _ = emergency
    db.set_meet_locked(meet["id"], True)
    app = _app(emergency)
    assert not app.button and not app.exception
