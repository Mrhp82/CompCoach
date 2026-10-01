"""Exercise deliberate DE corrections separately from the fast result flow."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.de_bouts import create_de_bout, list_de_bouts, resolve_de_bout
from compcoach_live.storage import CompCoachDB, ConcurrentUpdateError


def _seed(tmp_path):
    path = tmp_path / "corrections.db"
    db = CompCoachDB(path)
    meet = db.create_meet(
        "Test Competition", ["Alex", "Taylor"], ["Jordan"],
        first_event_name="Cadet Epee",
    )
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [
        {"athlete_id": "athlete_casey", "name": "CASEY Athlete", "phase": "de", "strip": "B2", "pool": "", "pod": "B", "time": ""},
        {"athlete_id": "athlete_robin", "name": "ROBIN Athlete", "phase": "de", "strip": "B3", "pool": "", "pod": "B", "time": ""},
    ], "Jordan")
    athletes = db.list_athletes(event["id"])
    athlete = next(row for row in athletes if row["name"] == "CASEY Athlete")
    opponent = next(row for row in athletes if row["name"] == "ROBIN Athlete")
    db.assign_de_athletes(event["id"], [athlete["id"]], actor="Jordan", coaches=["Alex", "Taylor"])
    return db, path, meet, event, db.get_athlete(event["id"], athlete["id"]), opponent


def _app(path, meet, *, actor="Alex", meet_overrides=None, event_overrides=None):
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.de_corrections_ui import render_de_result_corrections
db = CompCoachDB({str(path)!r})
meet = db.get_meet({meet['id']!r})
meet.update({meet_overrides or {}!r})
events = db.list_meet_events(meet['id'])
for event in events:
    event.update({event_overrides or {}!r})
event_by_id = {{event['id']: event for event in events}}
athletes = []
for event in events:
    for stored in db.list_athletes(event['id']):
        athlete = dict(stored)
        athlete['event_name'] = event['name']
        athletes.append(athlete)
render_de_result_corrections(db, meet, {actor!r}, athletes, event_by_id, key_prefix='test')
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _button(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1, [button.label for button in app.button]
    return matches[0]


def _select(app, athlete):
    box = next(box for box in app.selectbox if box.label == "Athlete to correct")
    box.select(athlete["id"]).run()


@pytest.mark.parametrize("outcome", ["won", "lost", "bye"])
def test_select_cancel_then_undo_single_result_keeps_newer_plan_and_call(tmp_path, outcome):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    if outcome == "bye":
        db.mark_bye(event["id"], athlete["id"], actor="Alex")
    else:
        db.mark_result(event["id"], athlete["id"], outcome=outcome, actor="Alex")
    db.assign_de_athletes(event["id"], [athlete["id"]], actor="Jordan", coaches=["Taylor"])
    if outcome != "lost":
        db.report_call(event["id"], athlete["id"], status="on_deck", location="B7", actor="Jordan")
    before = db.get_athlete(event["id"], athlete["id"])
    app = _app(path, meet)
    assert not app.exception
    assert next(section for section in app.expander if section.label == "Correct DE results").proto.expanded is False
    _select(app, athlete)
    assert not app.exception
    assert any(outcome.title() in option for option in app.selectbox[0].options)
    _button(app, "Undo last result").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["last_de_result"] == outcome
    _button(app, "Cancel").click().run()
    assert not any(button.label == "Confirm undo" for button in app.button)
    _button(app, "Undo last result").click().run()
    _button(app, "Confirm undo").click().run()
    assert not app.exception
    restored = db.get_athlete(event["id"], athlete["id"])
    assert restored["last_de_result"] == ""
    assert restored["active_state"] == "active"
    assert restored["de_wins"] == restored["de_byes"] == 0
    assert restored["de_coaches"] == ["Taylor"]
    assert (restored["call_status"], restored["live_location"]) == (before["call_status"], before["live_location"])
    assert any("result corrected" in notice.value for notice in app.success)
    app.run()
    assert not app.success


def test_pending_confirmation_cancels_on_newer_call_update(tmp_path):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    app = _app(path, meet)
    _select(app, athlete)
    _button(app, "Undo last result").click().run()
    db.report_call(event["id"], athlete["id"], status="now", location="B9", actor="Jordan")
    app.run()
    assert not app.exception
    assert not any(button.label == "Confirm undo" for button in app.button)
    assert any("selection was updated" in notice.value for notice in app.info)
    assert db.get_athlete(event["id"], athlete["id"])["last_de_result"] == "won"


@pytest.mark.parametrize("meet_overrides,event_overrides,actor", [
    ({"status": "closed"}, {}, "Alex"),
    ({"day_status": "paused"}, {}, "Alex"),
    ({"day_status": "closed"}, {}, "Alex"),
    ({"ended_at": "2026-10-01T12:00:00Z"}, {}, "Alex"),
    ({}, {"status": "closed"}, "Alex"),
    ({}, {}, ""),
])
def test_closed_paused_or_unidentified_staff_read_only(tmp_path, meet_overrides, event_overrides, actor):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex")
    app = _app(path, meet, actor=actor, meet_overrides=meet_overrides, event_overrides=event_overrides)
    _select(app, athlete)
    assert not app.exception
    assert not app.button
    assert any("read-only" in caption.value for caption in app.caption)


def test_linked_pair_results_only_offer_joint_correction(tmp_path):
    db, path, meet, event, athlete, opponent = _seed(tmp_path)
    bout = create_de_bout(db, event["id"], athlete["id"], opponent["id"], actor="Jordan", round_label="T32")
    resolve_de_bout(db, event["id"], bout["id"], winner_id=athlete["id"], actor="Alex")
    app = _app(path, meet)
    assert not app.exception
    assert not any(box.label == "Athlete to correct" for box in app.selectbox)
    assert any(section.label == "Correct DE results" for section in app.expander)
    _button(app, "Undo both results").click().run()
    _button(app, "Confirm undo both").click().run()
    assert not app.exception
    assert list_de_bouts(db, event["id"])[0]["status"] == "pending"
    assert all(row["active_state"] == "active" for row in db.list_athletes(event["id"]))


def test_both_empty_and_current_pending_pair_hide_corrections_section(tmp_path):
    db, path, meet, event, athlete, opponent = _seed(tmp_path)
    assert not _app(path, meet).expander
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    db.report_call(event["id"], athlete["id"], status="now", location="B2", actor="Jordan")
    create_de_bout(db, event["id"], athlete["id"], opponent["id"], actor="Jordan")
    app = _app(path, meet)
    assert not app.exception
    assert not app.expander


def test_backend_error_notice_is_once_and_does_not_repeat_action(tmp_path, monkeypatch):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex")
    calls = []
    def reject(self, event_id, athlete_id, actor, *, expected_version=None):
        calls.append((event_id, athlete_id, actor, expected_version))
        raise ConcurrentUpdateError("A newer update exists. Check the latest result.")
    monkeypatch.setattr(CompCoachDB, "correct_de_result", reject)
    app = _app(path, meet)
    _select(app, athlete)
    _button(app, "Undo last result").click().run()
    _button(app, "Confirm undo").click().run()
    assert not app.exception
    assert len(calls) == 1
    assert any("newer update" in notice.value for notice in app.error)
    app.run()
    assert not app.error
    assert len(calls) == 1


def test_unrecorded_import_out_can_be_restored_from_corrections(tmp_path):
    db, path, meet, event, athlete, _ = _seed(tmp_path)
    with db._connection() as conn:
        conn.execute("UPDATE athletes SET active_state = 'eliminated', version = version + 1 WHERE id = ?", (athlete["id"],))
        conn.commit()
    app = _app(path, meet)
    _select(app, athlete)
    _button(app, "Restore to active list").click().run()
    _button(app, "Confirm restore").click().run()
    assert not app.exception
    assert db.get_athlete(event["id"], athlete["id"])["active_state"] == "active"
    assert db.get_athlete(event["id"], athlete["id"])["de_coaches"] == ["Alex", "Taylor"]
