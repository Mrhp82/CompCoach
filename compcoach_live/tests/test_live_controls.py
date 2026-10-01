"""Exercise the mobile call/physical-coverage controls without app navigation."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


def _seed(tmp_path):
    path = tmp_path / "live-controls.db"
    db = CompCoachDB(path)
    meet = db.create_meet("Test Tournament", ["Alex", "Taylor"], ["Jordan"],
                          first_event_name="Junior Epee")
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [
        {"athlete_id": "casey", "name": "CASEY Athlete", "phase": "de", "strip": "B1",
         "pod": "B", "pool": "", "time": ""},
        {"athlete_id": "robin", "name": "ROBIN Athlete", "phase": "de", "strip": "B1",
         "pod": "B", "pool": "", "time": ""},
    ], "Jordan")
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Alex", "Taylor"], actor="Jordan")
    athletes = {row["name"]: row for row in db.list_athletes(event["id"])}
    return db, path, meet, event, athletes["CASEY Athlete"], athletes["ROBIN Athlete"]


def _app(path, event, athlete, *, role="coach", actor="Alex", event_override=None,
         meet_override=None):
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.live_controls import render_live_controls
db = CompCoachDB({str(path)!r})
original_meet = db.get_meet_for_event
db.get_meet_for_event = lambda event_id: {{**original_meet(event_id), **{meet_override or {}!r}}}
if st.session_state.pop("de_fast_refresh", False):
    st.rerun(scope="app")
notice = st.session_state.pop("de_fast_notice", None)
if notice:
    (st.success if notice[0] else st.error)(notice[1])
event = {{**db.get_event({event['id']!r}), **{event_override or {}!r}}}
athlete = db.get_athlete(event['id'], {athlete['id']!r})
render_live_controls(db, event, {role!r}, {actor!r}, athlete, key_prefix="test")
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _button(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1, [button.label for button in app.button]
    return matches[0]


@pytest.mark.parametrize("label,status", [("Now", "now"), ("On deck", "on_deck"),
                                           ("In the hole", "in_hole")])
def test_call_is_one_tap_on_actual_strip_not_the_imported_pod(tmp_path, label, status):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete)
    assert not app.exception
    assert app.text_input[0].label == "📍 **Actual bout strip** (optional)"
    assert app.text_input[0].value == ""
    app.text_input[0].input("c3")
    _button(app, label).click().run()
    assert not app.exception
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == status
    assert saved["live_location"] == "C3"
    assert saved["pod"] == "B" and saved["source_strip"] == "B1"
    assert saved["reported_at"] and saved["reported_by"] == "Alex"
    assert saved["de_coaches"] == ["Alex", "Taylor"]
    assert app.text_input[0].value == "C3"
    assert not any("Confirm" in button.label for button in app.button)


@pytest.mark.parametrize("label,status", [("Now", "now"), ("On deck", "on_deck"),
                                           ("In the hole", "in_hole")])
def test_call_with_unknown_strip_is_saved_and_visibly_needs_location(tmp_path, label, status):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete)
    _button(app, label).click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == status and saved["live_location"] == ""
    assert saved["reported_at"] and saved["reported_by"] == "Alex"
    assert saved["source_strip"] == "B1" and saved["pod"] == "B"
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert any(f"{label} saved" in message.value and "Actual strip to confirm" in message.value
               for message in app.success)
    assert any("Actual strip to confirm" in message.value for message in app.warning)
    assert app.text_input[0].value == "" and not app.error and not app.exception
    # The saved state remains visible after the transient success message clears.
    app.run()
    assert any("Actual strip to confirm" in message.value for message in app.warning)
    assert _button(app, label).proto.type == "primary"


@pytest.mark.parametrize("role,actor", [("coach", "Alex"), ("admin", "Taylor"),
                                       ("coordinator", "Jordan")])
def test_known_strip_can_be_added_to_existing_call_without_changing_status(tmp_path, role, actor):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete, role=role, actor=actor)
    _button(app, "On deck").click().run()
    first = db.get_athlete(event["id"], athlete["id"])
    assert first["call_status"] == "on_deck" and first["live_location"] == ""
    app.text_input[0].input("c3")
    _button(app, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "C3"
    assert saved["version"] > first["version"] and saved["reported_by"] == actor
    assert saved["source_strip"] == "B1" and saved["pod"] == "B"
    assert any("On deck saved" in message.value and "C3" in message.value
               for message in app.success)
    assert not any("Actual strip to confirm" in message.value for message in app.warning)
    assert not app.error and not app.exception


def test_stale_unknown_strip_call_cannot_erase_newer_known_strip_call(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    first = _app(path, event, athlete)
    second = _app(path, event, athlete, actor="Taylor")
    first.text_input[0].input("C3")
    _button(first, "Now").click().run()
    before = db.get_athlete(event["id"], athlete["id"])
    # The older phone has no strip entered and has not seen Alex's new call.
    assert second.text_input[0].value == ""
    _button(second, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "now" and saved["live_location"] == "C3"
    assert saved["version"] == before["version"]
    assert saved["reported_at"] == before["reported_at"] and saved["reported_by"] == "Alex"
    assert second.error and not first.exception and not second.exception


def test_not_called_clears_actual_strip_but_keeps_the_calling_pod(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Jordan")
    app = _app(path, event, athlete)
    _button(app, "Not called").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "waiting" and saved["live_location"] == ""
    assert saved["reported_at"] and saved["reported_by"] == "Alex"
    assert saved["source_strip"] == "B1" and saved["pod"] == "B"
    assert app.text_input[0].value == ""
    assert not app.exception


def test_next_call_after_a_win_can_save_unknown_strip_without_reusing_old_bout_location(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Jordan")
    app = _app(path, event, athlete)
    assert app.text_input[0].value == "D4"
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    app.run()
    assert app.text_input[0].value == ""
    _button(app, "In the hole").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["de_wins"] == 1 and saved["call_status"] == "in_hole"
    assert saved["live_location"] == "" and saved["source_strip"] == "B1"
    assert saved["reported_at"] and saved["reported_by"] == "Alex"
    assert any("Actual strip to confirm" in message.value for message in app.warning)
    assert not app.error and not app.exception


def test_new_bout_does_not_keep_previous_actual_strip_in_widget_state(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Jordan")
    app = _app(path, event, athlete)
    assert app.text_input[0].value == "D4"
    old_key = app.text_input[0].key
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    app.run()
    assert app.text_input[0].key != old_key and app.text_input[0].value == ""
    app.text_input[0].input("J4")
    _button(app, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["de_wins"] == 1 and saved["live_location"] == "J4"
    assert not app.exception


def test_coach_can_be_busy_with_waiting_athlete_and_release_in_one_tap(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete)
    app.text_input[0].input("j4")
    _button(app, "I’m with CASEY Athlete").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["covered_by"] == "Alex" and saved["covered_at"]
    assert saved["call_status"] == "waiting" and saved["live_location"] == "J4"
    assert any("Alex is with CASEY Athlete · J4 · since" in caption.value for caption in app.caption)
    _button(app, "Not covered").click().run()
    released = db.get_athlete(event["id"], athlete["id"])
    assert released["covered_by"] == "" and released["covered_at"] is None
    assert not app.exception


def test_coordinator_can_mark_any_day_coach_busy_despite_different_event_list(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete, role="coordinator", actor="Jordan",
               event_override={"active_coaches": ["Alex"]})
    assert app.selectbox[0].options == ["Choose a coach", "Alex", "Taylor"]
    app.selectbox[0].select("Taylor")
    app.text_input[0].input("P3")
    _button(app, "Mark coach busy").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["covered_by"] == "Taylor" and saved["live_location"] == "P3"
    assert saved["reported_by"] == "Jordan"
    assert saved["de_coaches"] == ["Alex", "Taylor"]
    assert not app.exception


def test_two_phones_cannot_overwrite_a_newer_call_with_old_rendered_buttons(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    first, second = _app(path, event, athlete), _app(path, event, athlete, actor="Taylor")
    first.text_input[0].input("C3")
    second.text_input[0].input("P3")
    _button(first, "Now").click().run()
    _button(second, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["live_location"] == "C3" and saved["call_status"] == "now"
    assert second.error
    assert not first.exception and not second.exception


def test_second_busy_tap_from_stale_phone_cannot_replace_current_covering_coach(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    first, second = _app(path, event, athlete), _app(path, event, athlete, actor="Taylor")
    first.text_input[0].input("C3")
    second.text_input[0].input("C3")
    _button(first, "I’m with CASEY Athlete").click().run()
    _button(second, "I’m with CASEY Athlete").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == "Alex"
    assert second.error
    assert not first.exception and not second.exception


def test_marking_busy_on_another_athlete_releases_previous_coverage(tmp_path):
    db, path, _, event, athlete, other = _seed(tmp_path)
    first = _app(path, event, athlete)
    first.text_input[0].input("C3")
    _button(first, "I’m with CASEY Athlete").click().run()
    second = _app(path, event, other)
    second.text_input[0].input("J4")
    _button(second, "I’m with ROBIN Athlete").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == ""
    assert db.get_athlete(event["id"], other["id"])["covered_by"] == "Alex"
    assert not first.exception and not second.exception


def test_stale_phone_cannot_move_a_coach_who_started_another_athlete(tmp_path):
    db, path, _, event, athlete, other = _seed(tmp_path)
    first = _app(path, event, athlete)
    second = _app(path, event, other, role="coordinator", actor="Jordan")
    first.text_input[0].input("C3")
    second.selectbox[0].select("Alex")
    second.text_input[0].input("J4")
    _button(first, "I’m with CASEY Athlete").click().run()
    _button(second, "Mark coach busy").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == "Alex"
    assert db.get_athlete(event["id"], other["id"])["covered_by"] == ""
    assert second.error
    assert not first.exception and not second.exception


def test_republishing_same_coach_location_does_not_reset_busy_timer(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete)
    app.text_input[0].input("C3")
    _button(app, "I’m with CASEY Athlete").click().run()
    before = db.get_athlete(event["id"], athlete["id"])
    app.text_input[0].input("J4")
    _button(app, "I’m with CASEY Athlete").click().run()
    after = db.get_athlete(event["id"], athlete["id"])
    assert after["covered_at"] == before["covered_at"]
    assert after["live_location"] == "J4" and after["covered_by"] == "Alex"
    assert not app.exception


def test_prefetched_meet_and_coach_states_avoid_per_athlete_read_queries(tmp_path):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    source = f'''\
from compcoach_live.storage import CompCoachDB
from compcoach_live.live_controls import render_live_controls
db = CompCoachDB({str(path)!r})
meet = db.get_meet({meet['id']!r})
coach_states = db.list_coach_availability(meet['id'])
event = db.get_event({event['id']!r})
athlete = db.get_athlete(event['id'], {athlete['id']!r})
def unnecessary_query(*args, **kwargs):
    raise AssertionError("Per-athlete query should use prefetched meet/coach states")
db.get_meet_for_event = unnecessary_query
db.list_coach_availability = unnecessary_query
render_live_controls(db, event, "coordinator", "Jordan", athlete, key_prefix="test",
                     meet_state=meet, coach_states=coach_states)
'''
    app = AppTest.from_string(source, default_timeout=10).run()
    assert not app.exception
    assert app.text_input[0].value == ""
    assert app.selectbox[0].options == ["Choose a coach", "Alex", "Taylor"]


def test_coach_cannot_accidentally_replace_another_current_covering_coach(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    db.cover_athlete(event["id"], athlete["id"], "Taylor", "Jordan", location="D4")
    app = _app(path, event, athlete, actor="Alex")
    assert not any(button.label in {"I’m with CASEY Athlete", "Not covered"}
                   for button in app.button)
    assert any("Taylor is with CASEY Athlete · D4 · since" in caption.value
               for caption in app.caption)
    # A coordinator can deliberately hand over the same athlete to a peer.
    coordinator = _app(path, event, athlete, role="coordinator", actor="Jordan")
    coordinator.selectbox[0].select("Alex")
    _button(coordinator, "Mark coach busy").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == "Alex"
    assert not app.exception and not coordinator.exception


def test_physical_coverage_requires_explicit_actual_strip(tmp_path):
    db, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete)
    _button(app, "I’m with CASEY Athlete").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["version"] == athlete["version"]
    assert saved["covered_by"] == "" and saved["live_location"] == ""
    assert any("actual bout strip" in error.value for error in app.error)
    assert not app.exception


@pytest.mark.parametrize("event_override,meet_override,actor", [
    ({"status": "closed"}, {}, "Alex"),
    ({}, {"day_status": "paused"}, "Alex"),
    ({}, {"ended_at": "2026-10-01T12:00:00Z"}, "Alex"),
    ({}, {}, ""),
])
def test_closed_paused_or_unidentified_staff_have_no_actions(tmp_path, event_override, meet_override, actor):
    _, path, _, event, athlete, _ = _seed(tmp_path)
    app = _app(path, event, athlete, actor=actor, event_override=event_override,
               meet_override=meet_override)
    assert not app.button and not app.text_input and not app.selectbox
    assert not app.exception
