"""Missed coaching is shared review evidence, independent of competition results."""

from copy import deepcopy

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    create_multi_event,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def missed_scenario(tmp_path, monkeypatch):
    path = tmp_path / "missed-coaching-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    db = CompCoachDB(path)
    meet, events = create_multi_event(
        db,
        ["Junior Epee", "Cadet Epee", "Cadet Women"],
        coaches=("Carmine", "Sam", "Taylor", "Alex"),
    )
    for event, table in zip(events, (
        "Name\tStrip #\nEXAMPLE Missed\tM1\nEXAMPLE Out\tN1",
        "Name\tStrip #\nEXAMPLE Sam busy\tJ1\nEXAMPLE Carmine busy\tD1",
        "Name\tStrip #\tPool #\nEXAMPLE Finished pool\tF3\t3\nEXAMPLE Open pool\tF4\t4",
    )):
        db.merge_import(event["id"], parse_pasted_table(table).records, "Carmine")
    athletes = {
        row["name"]: row
        for event in events for row in db.list_athletes(event["id"])
    }
    for name, coach in (
        ("EXAMPLE Missed", "Sam"), ("EXAMPLE Out", "Sam"),
        ("EXAMPLE Sam busy", "Sam"), ("EXAMPLE Carmine busy", "Carmine"),
    ):
        athlete = athletes[name]
        db.assign_de_athletes(
            athlete["event_id"], [athlete["id"]], coaches=[coach], actor="Carmine",
        )
    for name in ("EXAMPLE Finished pool", "EXAMPLE Open pool"):
        athlete = athletes[name]
        db.assign_athletes(
            athlete["event_id"], [athlete["id"]], main_coach="Sam", actor="Carmine",
        )
    missed = athletes["EXAMPLE Missed"]
    db.report_call(
        missed["event_id"], missed["id"], status="now", location="M4", actor="Irina",
    )
    for name, coach, strip in (
        ("EXAMPLE Sam busy", "Sam", "J4"),
        ("EXAMPLE Carmine busy", "Carmine", "D1"),
    ):
        athlete = athletes[name]
        db.cover_athlete(
            athlete["event_id"], athlete["id"], coach, coach, location=strip,
        )
    db.set_coach_availability(meet["id"], "Taylor", True, "Taylor")
    yield db, meet, events, athletes
    st.cache_resource.clear()


def _text(view):
    return "\n".join([
        *markdown_values(view),
        *(item.value for item in view.caption),
        *(item.value for item in view.info),
        *(item.value for item in view.warning),
        *(item.value for item in view.success),
    ])


def _missed_button(app, athlete):
    matches = [
        button for button in app.button
        if "Missed coaching" in button.label
        and athlete["id"] in (button.key or "")
    ]
    assert len(matches) == 1, [(button.label, button.key) for button in app.button]
    return matches[0]


def _review(app):
    matches = [
        section for section in app.expander
        if "Missed coaching" in section.label
    ]
    assert len(matches) == 1, [section.label for section in app.expander]
    assert not matches[0].proto.expanded
    return matches[0]


def _live(meet, actor="Taylor", role="coach"):
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    assert not app.exception
    return app


def _review_selector(section):
    matches = [
        box for box in section.selectbox
        if "Athlete" in box.label
    ]
    assert len(matches) == 1, [box.label for box in section.selectbox]
    return matches[0]


def _report_retrospective(section):
    matches = [
        button for button in section.button
        if "Record missed coaching" in button.label
    ]
    assert len(matches) == 1, [button.label for button in section.button]
    return matches[0]


def test_personal_one_tap_report_is_shared_and_keeps_reporting_time_evidence(missed_scenario):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Missed"]
    before_athlete = db.get_athlete(athlete["event_id"], athlete["id"])
    before_coaches = deepcopy(db.list_coach_availability(meet["id"]))
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    _missed_button(app, athlete).click().run()
    assert not app.exception
    incidents = db.list_missed_coaching(meet["id"])
    assert len(incidents) == 1 and incidents[0]["recorded_by"] == "Sam"
    incident = deepcopy(incidents[0])
    assert db.get_athlete(athlete["event_id"], athlete["id"]) == before_athlete
    assert db.list_coach_availability(meet["id"]) == before_coaches
    assert not any("Confirm missed" in button.label for button in app.button)

    # New calls and released coaches must not rewrite the incident's evidence.
    busy = athletes["EXAMPLE Sam busy"]
    db.mark_result(busy["event_id"], busy["id"], outcome="won", actor="Sam")
    db.set_coach_availability(meet["id"], "Taylor", False, "Taylor")
    db.report_call(
        athlete["event_id"], athlete["id"], status="on_deck", location="P7", actor="Irina",
    )
    assert db.list_missed_coaching(meet["id"])[0] == incident
    other = _live(meet)
    section = _review(other)
    text = _text(section)
    assert athlete["name"] in text
    assert "EXAMPLE Sam busy" in text and "J4" in text
    assert "EXAMPLE Carmine busy" in text and "D1" in text
    assert "Taylor" in text and "Alex" in text
    assert "when this report was entered" in text
    assert "actual bout time" in text
    assert "P7" not in text


@pytest.mark.parametrize("kind", ["out", "completed_pool"])
def test_retrospective_report_keeps_out_and_completed_pool_results(missed_scenario, kind):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Out" if kind == "out" else "EXAMPLE Finished pool"]
    if kind == "out":
        db.mark_result(athlete["event_id"], athlete["id"], outcome="lost", actor="Sam")
    else:
        db.set_pool_result(
            athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Sam",
        )
    before = db.get_athlete(athlete["event_id"], athlete["id"])
    app = _live(meet, actor="Irina", role="coordinator")
    _review_selector(_review(app)).select(athlete["id"]).run()
    _report_retrospective(_review(app)).click().run()
    assert not app.exception
    incidents = db.list_missed_coaching(meet["id"])
    assert len(incidents) == 1
    assert incidents[0]["athlete_id"] == athlete["id"]
    assert incidents[0]["recorded_by"] == "Irina"
    assert db.get_athlete(athlete["event_id"], athlete["id"]) == before
    if kind == "out":
        assert incidents[0]["bout_kind"] == "last_completed"
        assert incidents[0]["bout_outcome"] == "lost"


def test_admin_archive_retains_evidence_and_only_offers_read_only_review(missed_scenario):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Missed"]
    db.report_missed_coaching(
        athlete["event_id"], athlete["id"], "Sam", note="Parent reported after the bout.",
    )
    incident = db.list_missed_coaching(meet["id"])[0]
    db.finish_meet(meet["id"], "Carmine")
    app = open_board(meet["id"], meet["admin_token"], "Carmine", archive=True)
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()
    nav_named(app, "settings_section_").set_value("Coaching review").run()
    assert not app.exception
    section = _review(app)
    assert athlete["name"] in _text(section)
    assert "Parent reported after the bout." in _text(section)
    assert "read-only" in _text(app).lower()
    assert not section.button
    assert not section.selectbox
    assert db.list_missed_coaching(meet["id"])[0] == incident


def test_incident_actions_have_no_generic_athlete_undo_in_admin_activity(missed_scenario):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Missed"]
    incident = db.report_missed_coaching(athlete["event_id"], athlete["id"], "Sam")
    db.correct_missed_coaching(
        meet["id"], incident["id"], "Carmine", note="Entered for the wrong bout.",
    )
    incident_actions = [
        action for action in db.recent_actions(athlete["event_id"])
        if action["action"] in {"missed_coaching_reported", "missed_coaching_corrected"}
    ]
    assert len(incident_actions) == 2
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Activity").run()
    assert not app.exception
    keys = {button.key for button in app.button}
    assert all(f"undo_action_{action['id']}" not in keys for action in incident_actions)
    assert any((button.key or "").startswith("undo_action_") for button in app.button)


def test_correcting_report_keeps_fencing_state_and_original_snapshot(missed_scenario):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Missed"]
    original = db.report_missed_coaching(athlete["event_id"], athlete["id"], "Sam")
    before = db.get_athlete(athlete["event_id"], athlete["id"])
    app = _live(meet, actor="Sam")
    section = _review(app)
    note = next(item for item in section.text_input if item.label == "Correction note (optional)")
    note.set_value("I selected the wrong athlete.").run()
    section = _review(app)
    correct = next(button for button in section.button if button.label == "Mark report as incorrect")
    correct.click().run()
    assert not app.exception
    assert db.list_missed_coaching(meet["id"]) == []
    corrected = db.list_missed_coaching(meet["id"], include_corrected=True)[0]
    assert corrected["corrected_by"] == "Sam"
    assert corrected["correction_note"] == "I selected the wrong athlete."
    assert corrected["coaches_snapshot"] == original["coaches_snapshot"]
    assert db.get_athlete(athlete["event_id"], athlete["id"]) == before
    review = _review(_live(meet))
    assert review.label == "⚠️ Missed coaching · 0 athletes"
    history = next(section for section in review.expander if section.label == "Marked incorrect · 1 reports")
    assert "I selected the wrong athlete." in _text(history)
    assert not history.button


def test_stale_phone_cannot_record_the_new_bout_or_call_as_old_evidence(missed_scenario):
    db, meet, _events, athletes = missed_scenario
    athlete = athletes["EXAMPLE Missed"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    old_control = _missed_button(app, athlete)
    db.report_call(
        athlete["event_id"], athlete["id"], status="on_deck", location="Q8", actor="Irina",
    )
    newer = db.get_athlete(athlete["event_id"], athlete["id"])
    old_control.click().run()
    assert not app.exception and app.error
    assert db.list_missed_coaching(meet["id"]) == []
    assert db.get_athlete(athlete["event_id"], athlete["id"]) == newer
