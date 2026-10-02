"""Missed-coaching controls are independent, snapshot-guarded and reversible."""

import pytest
from streamlit.testing.v1 import AppTest
from uuid import uuid4

from compcoach_live.missed_coaching_ui import STAFF_TIME_NOTE, _time, missed_bout_context
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.training import join_training, start_training
from compcoach_live.tests.test_app_smoke import create_multi_event, keyed


@pytest.fixture
def board(tmp_path):
    database = CompCoachDB(tmp_path / "missed-ui.db")
    meet, events = create_multi_event(
        database, ["Junior Epee", "Cadet Epee"],
        coaches=("Carmine", "Sam", "Taylor", "Jordan"),
    )
    database.merge_import(
        events[0]["id"],
        parse_pasted_table(
            "Name\tStrip #\nEXAMPLE Target\tP1\nEXAMPLE Reserved\tP1\nEXAMPLE Other\tR1"
        ).records, "Carmine",
    )
    database.merge_import(
        events[1]["id"],
        parse_pasted_table("Name\tStrip #\tPool #\nEXAMPLE Pool\tF3\t7").records,
        "Carmine",
    )
    athletes = {
        athlete["name"]: athlete
        for event in events for athlete in database.list_athletes(event["id"])
    }
    for event in events:
        rows = database.list_athletes(event["id"])
        if rows[0]["phase"] == "de":
            database.assign_de_pods(
                event["id"], pods=list({row["pod"] for row in rows}),
                coaches=["Sam"], actor="Carmine",
            )
        else:
            database.assign_athletes(event["id"], [row["id"] for row in rows], main_coach="Sam", actor="Carmine")
    target, reserved = athletes["EXAMPLE Target"], athletes["EXAMPLE Reserved"]
    database.cover_athlete(target["event_id"], target["id"], "Sam", "Sam", location="J4")
    database.take_over_athlete(reserved["event_id"], reserved["id"], "Jordan", "Jordan")
    database.set_coach_availability(meet["id"], "Taylor", True, "Taylor")
    return database, database.get_meet(meet["id"]), events, athletes


def _app(board, athlete_name="EXAMPLE Target", *, read_only=False, include_training=False):
    database, meet, _events, athletes = board
    target = athletes[athlete_name]
    code = f'''
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.missed_coaching_ui import render_missed_coaching_control, render_missed_coaching_review
db = CompCoachDB({str(database.path)!r})
meet = db.get_meet({meet['id']!r})
event = db.get_event({target['event_id']!r})
athlete = db.get_athlete({target['event_id']!r}, {target['id']!r})
if not {read_only!r}:
    render_missed_coaching_control(db, event, "Sam", athlete, key_prefix="card", meet_state=meet)
render_missed_coaching_review(db, meet, "Sam", "coach", read_only={read_only!r}, include_training={include_training!r})
notice = st.session_state.get("de_fast_notice")
if notice:
    (st.success if notice[0] else st.error)(notice[1])
'''
    return AppTest.from_string(code, default_timeout=15).run()


def _texts(app):
    return "\n".join(item.value for item in [*app.markdown, *app.caption, *app.warning])


def _named(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1
    return matches[0]


def _report_button(app, athlete):
    return keyed(app.button, f"card_missed_coaching_{athlete['id']}")


def test_one_tap_report_keeps_result_coverage_timer_plan_and_coach_status(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    before = db.get_athlete(target["event_id"], target["id"])
    coach_states = db.list_coach_availability(meet["id"])
    app = _app(board)
    _report_button(app, target).click().run()

    assert not app.exception and app.success
    assert db.get_athlete(target["event_id"], target["id"]) == before
    assert db.list_coach_availability(meet["id"]) == coach_states
    incident = db.list_missed_coaching(meet["id"])[0]
    assert incident["bout_kind"] == "current" and incident["live_location"] == "J4"
    assert incident["summary"]["target_coverage_was_recorded"]
    assert "App still showed coverage by Sam at reporting time" in _texts(app)
    assert not any(button.label.startswith("Confirm") for button in app.button)
    assert any(section.label == "⚠️ Missed coaching · 1 athletes" for section in app.expander)


def test_report_uses_rendered_version_and_cannot_capture_newer_bout_from_other_phone(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    app = _app(board)
    db.report_call(target["event_id"], target["id"], status="on_deck", location="K2", actor="Irina")
    _report_button(app, target).click().run()
    assert app.error and not app.exception
    assert not db.list_missed_coaching(meet["id"])
    assert db.get_athlete(target["event_id"], target["id"])["live_location"] == "K2"


def test_staff_snapshot_is_report_time_evidence_with_explicit_unknown_and_reservation(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    app = _app(board)
    _report_button(app, target).click().run()
    text = _texts(app)
    assert STAFF_TIME_NOTE in text
    assert "Sam · Marked covering athletes" in text and "With EXAMPLE Target" in text
    assert "Actual strip J4" in text and "since " in text
    assert "Jordan · Reserved for coverage" in text and "Reserved for EXAMPLE Reserved" in text
    assert "Taylor · Declared available" in text
    assert "Carmine · Availability unconfirmed" in text
    assert "1 marked covering athletes · 1 reserved · 1 declared available · 1 availability unconfirmed" in text

    # A later release does not reinterpret the saved snapshot as availability
    # at the actual fencing bout time.
    db.release(target["event_id"], target["id"], "Sam")
    newer = _app(board)
    assert "Sam · Marked covering athletes" in _texts(newer)


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_after_result_one_tap_reports_last_bout_and_preserves_its_previous_strip(board, outcome):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    db.mark_result(target["event_id"], target["id"], outcome=outcome, actor="Sam")
    before = db.get_athlete(target["event_id"], target["id"])
    app = _app(board)
    _report_button(app, target).click().run()
    incident = db.list_missed_coaching(meet["id"])[0]
    assert incident["bout_kind"] == "last_completed"
    assert incident["bout_outcome"] == outcome and incident["live_location"] == "J4"
    assert incident["athlete_snapshot"]["covered_by"] == ""
    assert incident["bout_snapshot"]["covered_by"] == "Sam"
    assert db.get_athlete(target["event_id"], target["id"]) == before
    assert "most recent completed bout" in _texts(app)
    assert "this is not the bout start time" in _texts(app)
    assert not app.exception


def test_manual_late_report_includes_out_athlete_and_optional_note(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    db.mark_result(target["event_id"], target["id"], outcome="lost", actor="Sam")
    app = _app(board, "EXAMPLE Other")
    selector = _named(app.selectbox, "Athlete to report")
    assert any("EXAMPLE Target" in label and "Out" in label for label in selector.options)
    selector.set_value(target["id"]).run()
    assert _named(app.radio, "Bout to report").options == ["Most recent completed bout"]
    _named(app.text_input, "Notes (optional)").input("Reported after leaving strip").run()
    _named(app.button, "Record missed coaching").click().run()
    incident = db.list_missed_coaching(meet["id"])[0]
    assert incident["note"] == "Reported after leaving strip"
    assert incident["bout_kind"] == "last_completed"
    assert db.get_athlete(target["event_id"], target["id"])["active_state"] == "eliminated"
    assert _named(app.text_input, "Notes (optional)").value == ""
    assert not app.exception


def test_new_de_call_and_a_pool_to_de_import_keep_distinct_reporting_contexts(board):
    db, meet, events, athletes = board
    target, pool = athletes["EXAMPLE Target"], athletes["EXAMPLE Pool"]
    app = _app(board)
    _report_button(app, target).click().run()
    db.mark_result(target["event_id"], target["id"], outcome="won", actor="Sam")
    db.report_call(target["event_id"], target["id"], status="now", location="C3", actor="Irina")
    second = _app(board)
    _report_button(second, target).click().run()
    target_reports = [row for row in db.list_missed_coaching(meet["id"]) if row["athlete_id"] == target["id"]]
    assert {row["bout_number"] for row in target_reports} == {1, 2}
    assert {row["live_location"] for row in target_reports} == {"J4", "C3"}

    db.set_pool_result(pool["event_id"], pool["id"], wins=3, losses=3, actor="Sam")
    completed = _app(board, "EXAMPLE Pool")
    _named(completed.selectbox, "Athlete to report").set_value(pool["id"]).run()
    _named(completed.button, "Record missed coaching").click().run()
    db.merge_import(
        events[1]["id"], parse_pasted_table("Name\tStrip #\nEXAMPLE Pool\tF1").records, "Carmine",
    )
    de = _app(board, "EXAMPLE Pool")
    _report_button(de, pool).click().run()
    rows = [row for row in db.list_missed_coaching(meet["id"]) if row["athlete_id"] == pool["id"]]
    assert {row["phase"] for row in rows} == {"pools", "de"}
    assert not completed.exception and not de.exception


def test_pool_retry_id_changes_only_after_success_allowing_another_missed_bout(board):
    db, meet, _events, athletes = board
    pool = athletes["EXAMPLE Pool"]
    app = _app(board, "EXAMPLE Pool")
    _report_button(app, pool).click().run()
    _report_button(app, pool).click().run()
    rows = db.list_missed_coaching(meet["id"])
    assert len(rows) == 2 and len({row["idempotency_key"] for row in rows}) == 2
    assert not app.exception


def test_bye_cannot_be_chosen_as_completed_bout_in_manual_reporting(board):
    db, meet, _events, athletes = board
    other = athletes["EXAMPLE Other"]
    db.mark_bye(other["event_id"], other["id"], actor="Sam")
    app = _app(board, "EXAMPLE Other")
    _named(app.selectbox, "Athlete to report").set_value(other["id"]).run()
    assert not any(radio.label == "Bout to report" for radio in app.radio)
    assert "A bye is not a fenced bout" in _texts(app)
    _named(app.button, "Record missed coaching").click().run()
    incident = db.list_missed_coaching(meet["id"])[0]
    assert incident["bout_kind"] == "current" and incident["bout_number"] == 2
    assert incident["bout_outcome"] == "" and not app.exception


def test_correction_keeps_evidence_and_read_only_review_has_no_write_controls(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    app = _app(board)
    _report_button(app, target).click().run()
    incident = db.list_missed_coaching(meet["id"])[0]
    _named(app.text_input, "Correction note (optional)").input("Tapped the wrong athlete").run()
    _named(app.button, "Mark report as incorrect").click().run()
    assert not db.list_missed_coaching(meet["id"])
    corrected = db.list_missed_coaching(meet["id"], include_corrected=True)[0]
    assert corrected["id"] == incident["id"]
    assert corrected["athlete_snapshot"] == incident["athlete_snapshot"]
    assert corrected["coaches_snapshot"] == incident["coaches_snapshot"]
    assert corrected["correction_note"] == "Tapped the wrong athlete"
    assert any(section.label == "Marked incorrect · 1 reports" for section in app.expander)

    readonly = _app(board, read_only=True)
    assert not readonly.button and not readonly.selectbox and not readonly.text_input
    assert "Tapped the wrong athlete" in _texts(readonly)
    assert "Sam · Marked covering athletes" in _texts(readonly)
    assert not readonly.exception


def test_context_inference_never_treats_bye_or_new_call_as_previous_fenced_bout():
    assert missed_bout_context({"phase": "pools", "last_de_result": "won"}) == "current"
    assert missed_bout_context({"phase": "de", "last_de_result": "bye", "de_awaiting_next": 1}) == "current"
    assert missed_bout_context({"phase": "de", "last_de_result": "won", "de_awaiting_next": 1}) == "last_completed"
    assert missed_bout_context({"phase": "de", "last_de_result": "lost", "active_state": "eliminated"}) == "last_completed"
    assert missed_bout_context({"phase": "de", "last_de_result": "won", "de_awaiting_next": 0, "call_status": "now"}) == "current"


def test_duplicate_de_report_displays_already_recorded_instead_of_a_second_incident(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    app = _app(board)
    _report_button(app, target).click().run()
    _report_button(app, target).click().run()
    assert len(db.list_missed_coaching(meet["id"])) == 1
    assert "already recorded for this bout" in app.success[0].value
    assert not app.exception


def test_manual_duplicate_keeps_new_note_visible_and_explicitly_reports_it_was_not_saved(board):
    db, meet, _events, athletes = board
    target = athletes["EXAMPLE Target"]
    app = _app(board)
    _report_button(app, target).click().run()
    _named(app.selectbox, "Athlete to report").set_value(target["id"]).run()
    _named(app.text_input, "Notes (optional)").input("Additional context").run()
    _named(app.button, "Record missed coaching").click().run()
    assert db.list_missed_coaching(meet["id"])[0]["note"] == ""
    assert len(db.list_missed_coaching(meet["id"])) == 1
    assert _named(app.text_input, "Notes (optional)").value == "Additional context"
    assert "new note was not added" in app.success[0].value
    assert not app.exception


def test_practice_review_opt_in_shows_reports_without_mixing_real_day(board):
    db, real, _events, _athletes = board
    hub = start_training(db, real["id"], [], "Carmine", open_entry=True)
    practice = join_training(db, hub["id"], "Sam", participant_key=uuid4().hex)
    events = db.list_meet_events(practice["id"])
    athlete = db.list_athletes(events[0]["id"])[0]
    db.report_missed_coaching(athlete["event_id"], athlete["id"], "Sam")
    practice_board = (db, practice, events, {athlete["name"]: athlete})
    hidden = _app(practice_board, athlete["name"])
    visible = _app(practice_board, athlete["name"], include_training=True)
    assert any(section.label == "⚠️ Missed coaching · 0 athletes" for section in hidden.expander)
    assert any(section.label == "⚠️ Missed coaching · 1 athletes" for section in visible.expander)
    assert not db.list_missed_coaching(real["id"], include_training=True)
    assert not hidden.exception and not visible.exception


def test_snapshot_clock_uses_competition_timezone_and_keeps_absolute_since_labels():
    assert _time("2026-10-02T15:30:00+00:00", "America/Los_Angeles") == "02 Oct 08:30:00 PDT"
    assert _time("2026-12-02T15:30:00+00:00", "America/Los_Angeles") == "02 Dec 07:30:00 PST"
    assert _time("2026-10-02T15:30:00+00:00", "bad/zone") == "02 Oct 15:30:00 UTC"
