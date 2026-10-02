"""Busy confirmation is a single visible action in real and practice sessions."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB
from compcoach_live.training import join_training, start_training


@pytest.fixture(params=["real", "practice"])
def session(tmp_path, request):
    path = tmp_path / "coverage-visibility.sqlite"
    db = CompCoachDB(path)
    meet = db.create_meet("Coverage test", ["Alex", "Taylor"], ["Jordan"],
                          first_event_name="Junior Epee")
    if request.param == "practice":
        hub = start_training(db, meet["id"], actor="Alex")
        meet = join_training(db, hub["id"], "Alex")
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{
        "athlete_id": "coverage-case", "name": "CASEY Athlete", "phase": "de",
        "strip": "B1", "pod": "B", "pool": "", "time": "",
    }], "Alex")
    athlete = next(row for row in db.list_athletes(event["id"])
                   if row["name"] == "CASEY Athlete")
    return db, path, meet, event, athlete


def _app(session, *, role="coach", actor="Alex"):
    _, path, _, event, athlete = session
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.live_controls import render_live_controls
db = CompCoachDB({str(path)!r})
if st.session_state.pop("de_fast_refresh", False):
    st.rerun(scope="app")
notice = st.session_state.pop("de_fast_notice", None)
if notice:
    (st.success if notice[0] else st.error)(notice[1])
event = db.get_event({event['id']!r})
athlete = db.get_athlete(event['id'], {athlete['id']!r})
render_live_controls(db, event, {role!r}, {actor!r}, athlete, key_prefix="visibility")
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _button(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1, [button.label for button in app.button]
    return matches[0]


def _has_busy_button(app):
    return any(button.label == "I’m with CASEY Athlete" for button in app.button)


def test_physical_coverage_hides_repeated_busy_confirmation_and_release_restores_it(session):
    db, _, _, event, athlete = session
    app = _app(session)
    app.text_input[0].input("C3")
    _button(app, "I’m with CASEY Athlete").click().run()
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["covered_by"] == "Alex" and covered["covered_at"]
    assert not _has_busy_button(app)
    assert any("Alex is with CASEY Athlete" in caption.value for caption in app.caption)
    _button(app, "Not covered").click().run()
    assert _has_busy_button(app)
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == ""
    assert not app.exception


def test_failed_coverage_without_strip_keeps_the_confirmation_visible(session):
    db, _, _, event, athlete = session
    app = _app(session)
    _button(app, "I’m with CASEY Athlete").click().run()
    assert _has_busy_button(app) and app.error
    assert not db.get_athlete(event["id"], athlete["id"])["covered_by"]
    assert not app.exception


def test_accepted_takeover_still_needs_physical_confirmation(session):
    db, _, _, event, athlete = session
    db.take_over_athlete(event["id"], athlete["id"], "Alex", "Alex")
    before = db.get_athlete(event["id"], athlete["id"])
    assert before["takeover_coach"] == "Alex" and not before["covered_by"]
    app = _app(session)
    assert _has_busy_button(app)
    app.text_input[0].input("J4")
    _button(app, "I’m with CASEY Athlete").click().run()
    assert not _has_busy_button(app)
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == "Alex"
    assert not app.exception


def test_next_bout_after_win_restores_confirmation_and_clears_previous_strip(session):
    db, _, _, event, athlete = session
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    app = _app(session)
    assert not _has_busy_button(app)
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    app.run()
    assert _has_busy_button(app) and app.text_input[0].value == ""
    assert not app.exception


def test_busy_coach_can_update_call_and_strip_without_resetting_coverage_timer(session):
    db, _, _, event, athlete = session
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    before = db.get_athlete(event["id"], athlete["id"])
    app = _app(session)
    app.text_input[0].input("J4")
    _button(app, "Now").click().run()
    after = db.get_athlete(event["id"], athlete["id"])
    assert after["covered_at"] == before["covered_at"]
    assert after["live_location"] == "J4" and after["covered_by"] == "Alex"
    assert not _has_busy_button(app) and not app.exception


@pytest.mark.parametrize("role", ["admin", "coordinator"])
def test_coordinator_selector_remains_available_for_deliberate_coverage_changes(session, role):
    db, _, meet, event, athlete = session
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    app = _app(session, role=role)
    assert not _has_busy_button(app)
    assert app.selectbox[0].value == "Alex"
    replacement = next(name for name in meet["active_coaches"] if name != "Alex")
    app.selectbox[0].select(replacement)
    _button(app, "Mark coach busy").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == replacement
    assert _button(app, "Not covered")
    assert not app.exception
