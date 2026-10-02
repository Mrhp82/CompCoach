"""Compact call progression preserves identity, corrections and live guards."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "compact-calls.sqlite"
    db = CompCoachDB(path)
    meet = db.create_meet("Compact calls", ["Alex", "Taylor"], ["Jordan"],
                          first_event_name="Junior Epee")
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{
        "athlete_id": "compact-case", "name": "CASEY Athlete", "phase": "de",
        "strip": "B1", "pod": "B", "pool": "", "time": "",
    }], "Jordan")
    athlete = db.list_athletes(event["id"])[0]
    return db, path, meet, event, athlete


def _app(board, *, role="coach", actor="Alex"):
    _, path, _, event, athlete = board
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
render_live_controls(db, event, {role!r}, {actor!r}, athlete, key_prefix="compact")
'''
    return AppTest.from_string(source, default_timeout=10).run()


def _button(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1, [button.label for button in app.button]
    return matches[0]


def _calls(app):
    return [button.label for button in app.button
            if button.label in {"Now", "On deck", "In the hole", "Not called"}]


@pytest.mark.parametrize("status,labels", [
    ("waiting", ["In the hole", "On deck", "Now"]),
    ("in_hole", ["On deck", "Now"]),
    ("on_deck", ["Now"]),
    ("now", []),
])
def test_normal_view_shows_only_forward_actions_in_one_native_row(board, status, labels):
    db, _, _, event, athlete = board
    if status != "waiting":
        db.report_call(event["id"], athlete["id"], status=status, location="C3", actor="Jordan")
    app = _app(board)
    assert _calls(app) == labels
    assert not app.toggle[0].value
    rows = [item for item in app.get("flex_container")
            if item.proto.id.endswith(f"-cc_call_actions_live_compact_{athlete['id']}")]
    if labels:
        wrapper, = rows
        row, = wrapper.children.values()
        columns = list(row.children.values())
        assert len(columns) == len(labels)
        assert all(column.type == "column" for column in columns)
        assert all(column.proto.weight == 1 / len(labels) for column in columns)
    else:
        assert not rows
    assert not app.exception


def test_progressing_unknown_strip_preserves_takeover_and_keeps_name_at_actions(board):
    db, _, _, event, athlete = board
    db.take_over_athlete(event["id"], athlete["id"], "Alex", "Alex")
    app = _app(board)
    for label, status, following in (
        ("In the hole", "in_hole", ["On deck", "Now"]),
        ("On deck", "on_deck", ["Now"]),
        ("Now", "now", []),
    ):
        _button(app, label).click().run()
        saved = db.get_athlete(event["id"], athlete["id"])
        assert saved["call_status"] == status and saved["live_location"] == ""
        assert saved["source_strip"] == "B1" and saved["pod"] == "B"
        assert saved["takeover_coach"] == "Alex" and not saved["covered_by"]
        assert _calls(app) == following and app.text_input[0].value == ""
        assert any("CASEY Athlete" in item.value and "Actual strip <b>TBD</b>" in item.value
                   for item in app.markdown)
        assert _button(app, "I’m with CASEY Athlete").proto.type == "primary"
        assert not app.exception


def test_known_strip_survives_forward_call_when_editor_is_unmounted(board):
    db, _, _, event, athlete = board
    db.report_call(event["id"], athlete["id"], status="in_hole", location="C3", actor="Jordan")
    app = _app(board)
    assert not app.text_input
    app.run()  # Includes widget cleanup without a location textbox.
    _button(app, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "C3"
    assert not app.text_input and not app.exception


def test_modify_exposes_all_statuses_and_can_correct_backwards_or_clear_call(board):
    db, _, _, event, athlete = board
    db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Jordan")
    app = _app(board)
    assert not _calls(app) and not app.text_input
    app.toggle[0].set_value(True).run()
    assert _calls(app) == ["Now", "On deck", "In the hole", "Not called"]
    assert _button(app, "Now").proto.type == "primary"
    assert app.text_input[0].value == "C3"
    _button(app, "In the hole").click().run()
    assert not app.toggle[0].value and _calls(app) == ["On deck", "Now"]
    corrected = db.get_athlete(event["id"], athlete["id"])
    assert corrected["call_status"] == "in_hole" and corrected["live_location"] == "C3"
    app.toggle[0].set_value(True).run()
    _button(app, "Not called").click().run()
    reset = db.get_athlete(event["id"], athlete["id"])
    assert reset["call_status"] == "waiting" and reset["live_location"] == ""
    assert reset["source_strip"] == "B1" and app.text_input[0].value == ""
    assert not app.exception


@pytest.mark.parametrize("role", ["coach", "admin"])
def test_own_primary_arrival_uses_known_strip_without_editing_and_then_disappears(board, role):
    db, _, _, event, athlete = board
    db.report_call(event["id"], athlete["id"], status="on_deck", location="C3", actor="Jordan")
    app = _app(board, role=role)
    assert not app.text_input
    _button(app, "I’m with CASEY Athlete").click().run()
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["covered_by"] == "Alex" and covered["live_location"] == "C3"
    assert covered["covered_at"]
    assert not any(button.label == "I’m with CASEY Athlete" for button in app.button)
    assert not app.exception


def test_coordinator_coverage_picker_is_collapsed_without_restricting_day_staff(board):
    db, _, _, event, athlete = board
    db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Jordan")
    app = _app(board, role="coordinator", actor="Jordan")
    coverage = next(item for item in app.expander if item.label == "Coach coverage")
    assert not coverage.proto.expanded
    assert app.selectbox[0].options == ["Choose a coach", "Alex", "Taylor"]
    assert not app.text_input
    app.selectbox[0].select("Taylor")
    _button(app, "Mark coach busy").click().run()
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == "Taylor"
    assert not app.exception


def test_forward_button_from_stale_phone_cannot_overwrite_a_newer_call(board):
    db, _, _, event, athlete = board
    db.report_call(event["id"], athlete["id"], status="in_hole", location="C3", actor="Jordan")
    app = _app(board)
    db.report_call(event["id"], athlete["id"], status="now", location="D4", actor="Jordan")
    _button(app, "On deck").click().run()
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "now" and saved["live_location"] == "D4"
    assert app.error and not app.exception
