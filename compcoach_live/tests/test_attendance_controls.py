"""Exercise the coach attendance controls without the surrounding dashboard."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB, CompCoachError


def _seed(tmp_path):
    path = tmp_path / "attendance.db"
    db = CompCoachDB(path)
    meet = db.create_meet(
        "Test Competition", ["Alex", "Taylor"], ["Jordan"],
        first_event_name="Cadet Epee",
    )
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{
        "athlete_id": "athlete_casey", "name": "CASEY Athlete", "phase": "pools",
        "strip": "B2", "pool": "3", "pod": "B", "time": "",
    }], "Jordan")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], actor="Jordan", main_coach="Alex", side_coach="Taylor")
    return db, path, meet, event, db.get_athlete(event["id"], athlete["id"])


def _app(path, meet, *, actor="Alex", overrides=None, meet_overrides=None):
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.attendance_controls import render_pool_absence_control, render_absent_pool_athletes
db = CompCoachDB({str(path)!r})
meet = db.get_meet({meet['id']!r})
meet.update({meet_overrides or {}!r})
events = db.list_meet_events(meet['id'])
event_by_id = {{event['id']: event for event in events}}
athletes = []
for event in events:
    for stored in db.list_athletes(event['id']):
        athlete = dict(stored)
        athlete.update({overrides or {}!r})
        athlete['event_name'] = event['name']
        athletes.append(athlete)
        if athlete.get('participation_status', 'active') == 'active':
            render_pool_absence_control(db, event, {actor!r}, athlete, key_prefix='card')
render_absent_pool_athletes(db, meet, {actor!r}, athletes, event_by_id, key_prefix='list')
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _button(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1
    return matches[0]


def test_two_tap_absence_cancel_confirm_and_restore_keeps_plan_results(tmp_path):
    db, path, meet, event, athlete = _seed(tmp_path)
    db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Alex")
    db.report_call(event["id"], athlete["id"], status="now", location="B2", actor="Jordan")
    before = db.request_help(event["id"], athlete["id"], "Alex", location="B2")
    app = _app(path, meet)
    _button(app, "Mark absent").click().run()
    assert not app.exception
    assert db.get_athlete(event["id"], athlete["id"])["participation_status"] == "active"
    _button(app, "Cancel").click().run()
    assert not app.exception
    assert not any(button.label == "Confirm absent" for button in app.button)
    _button(app, "Mark absent").click().run()
    _button(app, "Confirm absent").click().run()
    assert not app.exception
    changed = db.get_athlete(event["id"], athlete["id"])
    assert changed["participation_status"] == "absent"
    assert changed["active_state"] == "active"
    assert (changed["main_coach"], changed["side_coach"]) == ("Alex", "Taylor")
    assert (changed["pool_wins"], changed["pool_losses"]) == (4, 2)
    assert changed["help_requested_at"] is None
    assert changed["call_status"] == "waiting"
    assert app.expander[0].label == "Absent pool athletes · 1"
    assert app.expander[0].proto.expanded is False
    assert not any(button.label == "Mark absent" for button in app.button)
    _button(app, "Restore to active list").click().run()
    assert not app.exception
    restored = db.get_athlete(event["id"], athlete["id"])
    assert restored["participation_status"] == "active"
    assert restored["help_requested_at"] == before["help_requested_at"]
    assert restored["call_status"] == "now"
    assert not app.expander
    assert _button(app, "Mark absent")


def test_pending_confirmation_cancels_when_other_phone_updates_athlete(tmp_path):
    db, path, meet, event, athlete = _seed(tmp_path)
    app = _app(path, meet)
    _button(app, "Mark absent").click().run()
    db.report_call(event["id"], athlete["id"], status="on_deck", location="B3", actor="Jordan")
    app.run()
    assert not app.exception
    assert any("athlete was updated" in message.value for message in app.info)
    assert not any(button.label == "Confirm absent" for button in app.button)
    assert db.get_athlete(event["id"], athlete["id"])["participation_status"] == "active"
    assert _button(app, "Mark absent")


@pytest.mark.parametrize("actor", ["", "Alex"])
def test_closed_day_has_read_only_absent_summary(tmp_path, actor):
    db, path, meet, event, athlete = _seed(tmp_path)
    db.mark_absent(event["id"], athlete["id"], "Jordan")
    db.finish_meet(meet["id"], "Jordan")
    app = _app(path, meet, actor=actor)
    assert not app.exception
    assert app.expander[0].label == "Absent pool athletes · 1"
    assert not app.button


@pytest.mark.parametrize("overrides", [
    {"phase": "de"}, {"active_state": "eliminated"},
    {"participation_status": "withdrawn"},
])
def test_nonparticipating_or_nonpool_athletes_have_no_absence_control(tmp_path, overrides):
    _, path, meet, _, _ = _seed(tmp_path)
    app = _app(path, meet, overrides=overrides)
    assert not app.exception
    assert not app.button
    assert not app.expander


def test_anonymous_board_has_no_marking_controls(tmp_path):
    _, path, meet, _, _ = _seed(tmp_path)
    app = _app(path, meet, actor="")
    assert not app.exception
    assert not app.button


def test_paused_parent_has_no_restore_even_when_child_event_is_open(tmp_path):
    db, path, meet, event, athlete = _seed(tmp_path)
    db.mark_absent(event["id"], athlete["id"], "Alex")
    assert db.get_event(event["id"])["status"] == "open"
    app = _app(path, meet, meet_overrides={"status": "locked"})
    assert not app.exception
    assert app.expander[0].label == "Absent pool athletes · 1"
    assert not app.button


def test_failed_absence_refreshes_once_shows_error_and_drops_confirmation(tmp_path, monkeypatch):
    db, path, meet, event, athlete = _seed(tmp_path)
    app = _app(path, meet)
    _button(app, "Mark absent").click().run()

    def fail(*args, **kwargs):
        raise CompCoachError("Another phone changed this athlete. Please try again.")

    monkeypatch.setattr(CompCoachDB, "set_athlete_participation", fail)
    _button(app, "Confirm absent").click().run()
    assert not app.exception
    assert len(app.error) == 1
    assert "Another phone changed" in app.error[0].value
    assert not any(button.label == "Confirm absent" for button in app.button)
    assert db.get_athlete(event["id"], athlete["id"])["participation_status"] == "active"
    app.run()
    assert not app.exception
    assert not app.error


def test_restore_blocks_newer_edit_and_keeps_athlete_absent(tmp_path):
    db, path, meet, event, athlete = _seed(tmp_path)
    db.mark_absent(event["id"], athlete["id"], "Alex")
    db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Jordan")
    app = _app(path, meet)
    _button(app, "Restore to active list").click().run()
    assert not app.exception
    assert any("newer update exists" in message.value for message in app.error)
    assert db.get_athlete(event["id"], athlete["id"])["participation_status"] == "absent"
    assert app.expander[0].label == "Absent pool athletes · 1"
    app.run()
    assert not app.exception
    assert not app.error


def test_compare_and_swap_conflict_keeps_other_phone_call_and_refreshes_once(tmp_path, monkeypatch):
    db, path, meet, event, athlete = _seed(tmp_path)
    app = _app(path, meet)
    _button(app, "Mark absent").click().run()
    original = CompCoachDB.set_athlete_participation
    calls = []

    def concurrent_call(self, event_id, athlete_id, status, actor, **kwargs):
        calls.append(kwargs["expected_version"])
        self.report_call(event_id, athlete_id, status="on_deck", location="B3", actor="Jordan")
        return original(self, event_id, athlete_id, status, actor, **kwargs)

    monkeypatch.setattr(CompCoachDB, "set_athlete_participation", concurrent_call)
    _button(app, "Confirm absent").click().run()
    assert not app.exception
    assert calls == [athlete["version"]]
    assert len(app.error) == 1
    assert "changed on another phone" in app.error[0].value
    changed = db.get_athlete(event["id"], athlete["id"])
    assert changed["participation_status"] == "active"
    assert (changed["call_status"], changed["live_location"]) == ("on_deck", "B3")
    assert not any(button.label == "Confirm absent" for button in app.button)
    app.run()
    assert not app.exception
    assert not app.error
    assert calls == [athlete["version"]]
