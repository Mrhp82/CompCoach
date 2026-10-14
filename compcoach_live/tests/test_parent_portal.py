"""Phone-sized parent interactions use one shared link and real persistence."""

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.parent_access import (
    get_or_create_parent_access, get_parent_context, parent_context_revision,
    report_parent_call,
)
from compcoach_live.storage import CompCoachDB


@pytest.fixture
def portal(tmp_path):
    db = CompCoachDB(tmp_path / "portal.db")
    competition = db.create_competition("Example NAC")
    day = db.create_competition_day(competition["id"], "Saturday", "2026-10-10", ["Alex"], ["Casey"], first_event_name="Cadet ME", day_status="active")
    event = db.list_meet_events(day["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "avery", "name": "STONE Avery", "phase": "de", "pod": "B", "strip": "B1"}], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    access = get_or_create_parent_access(db, competition["id"], "Casey")
    return db, competition, day, event, athlete, access


def app_for(portal, token=None):
    db, competition, _, _, _, access = portal
    source = f'''from compcoach_live.storage import CompCoachDB
from compcoach_live.parent_portal import render_parent_portal
db = CompCoachDB({str(db.path)!r})
render_parent_portal(db, {competition['id']!r}, {(access['token'] if token is None else token)!r})
'''
    return AppTest.from_string(source, default_timeout=10).run()


def button(app, label):
    matches = [entry for entry in app.button if entry.label == label]
    assert len(matches) == 1
    return matches[0]


def select(app, athlete):
    app.selectbox[0].set_value(athlete["id"]).run()
    assert not app.exception
    return app


def test_entry_has_searchable_roster_without_name_role_or_login(portal):
    app = app_for(portal)
    assert not app.exception
    assert len(app.selectbox) == 1 and app.selectbox[0].label == "Athlete"
    assert len(app.text_input) == 0 and len(app.button) == 0
    assert app.selectbox[0].options == ["STONE Avery · Cadet ME"]


def test_de_calling_pod_is_not_shown_as_actual_strip(portal):
    app = select(app_for(portal), portal[4])
    assert app.text_input[0].value == ""
    visual = " ".join(entry.value for entry in app.markdown)
    assert "STONE Avery" in visual and "Strip ?" in visual
    assert "B1" not in visual
    assert [entry.label for entry in app.button] == ["Fencing now", "On deck", "In the hole"]


@pytest.mark.parametrize("label,status", [("Fencing now", "now"), ("On deck", "on_deck"), ("In the hole", "in_hole")])
def test_one_call_tap_saves_visible_confirmation_without_known_strip(portal, label, status):
    app = select(app_for(portal), portal[4])
    button(app, label).click().run()
    assert not app.exception
    db, _, _, event, athlete, _ = portal
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == status and saved["live_location"] == ""
    assert saved["reported_by"] == "Parent"
    assert any(label in entry.value and "Saved" in entry.value for entry in app.success)


def test_parent_saves_strip_with_call_and_can_correct_it_without_changing_status(portal):
    app = select(app_for(portal), portal[4])
    app.text_input[0].set_value("c3")
    button(app, "On deck").click().run()
    assert not app.exception and app.text_input[0].value == "C3"
    visual = " ".join(entry.value for entry in app.markdown)
    assert "C3" in visual and "font-size:1.75rem" in visual
    app.text_input[0].set_value("d4")
    button(app, "Save strip").click().run()
    db, _, _, event, athlete, _ = portal
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["live_location"] == "D4" and saved["call_status"] == "on_deck"


def test_current_pool_strip_can_prefill_actual_strip(portal):
    db, _, _, event, athlete, _ = portal
    db.merge_import(event["id"], [{"athlete_id": "avery", "name": "STONE Avery", "phase": "pools", "pool": "1", "strip": "C3", "pod": "C"}], "Casey")
    app = select(app_for(portal), athlete)
    assert app.text_input[0].value == "C3"


def test_old_browser_callback_after_won_does_not_report_previous_strip(portal):
    app = select(app_for(portal), portal[4])
    app.text_input[0].set_value("M1")
    db, _, _, event, athlete, _ = portal
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    button(app, "Fencing now").click().run()
    assert not app.exception
    assert any("changed" in entry.value for entry in app.warning)
    saved = db.get_athlete(event["id"], athlete["id"])
    assert saved["call_status"] == "waiting" and saved["live_location"] == ""
    assert app.text_input[0].value == ""


def test_no_coach_or_result_actions_are_exposed(portal):
    app = select(app_for(portal), portal[4])
    labels = [entry.label for entry in app.button]
    assert set(labels) == {"Fencing now", "On deck", "In the hole"}
    assert not app.radio and not app.multiselect


def test_heartbeat_does_not_rerun_for_another_parent_call_but_tracks_selected_and_day(portal, monkeypatch):
    from compcoach_live import parent_portal
    db, competition, day, event, athlete, access = portal
    db.merge_import(event["id"], [{"athlete_id": "other", "name": "REED Nora", "phase": "de", "pod": "F", "strip": "F1"}], "Casey")
    before = get_parent_context(db, competition["id"], access["token"])
    state = {"selection": athlete["id"], "revision": parent_context_revision(before, athlete["id"])}
    reruns = []
    monkeypatch.setattr(parent_portal.st, "session_state", state)
    monkeypatch.setattr(parent_portal.st, "rerun", lambda: reruns.append(True))
    other = next(row for row in before["athletes"] if row["id"] != athlete["id"])
    report_parent_call(db, competition["id"], access["token"], event["id"], other["id"],
                       status="now", location="G4", expected_version=other["version"],
                       expected_day_id=day["id"], expected_phase="de")
    parent_portal._parent_heartbeat.__wrapped__(db, competition["id"], access["token"], "revision", "selection")
    assert not reruns
    current = db.get_athlete(event["id"], athlete["id"])
    report_parent_call(db, competition["id"], access["token"], event["id"], athlete["id"],
                       status="on_deck", location="C3", expected_version=current["version"],
                       expected_day_id=day["id"], expected_phase="de")
    parent_portal._parent_heartbeat.__wrapped__(db, competition["id"], access["token"], "revision", "selection")
    assert len(reruns) == 1
    state["revision"] = parent_context_revision(get_parent_context(db, competition["id"], access["token"]), athlete["id"])
    db.finish_meet(day["id"], "Casey")
    parent_portal._parent_heartbeat.__wrapped__(db, competition["id"], access["token"], "revision", "selection")
    assert len(reruns) == 2


def test_invalid_link_has_no_roster_or_controls(portal):
    app = app_for(portal, token="wrong")
    assert not app.exception and len(app.error) == 1
    assert not app.selectbox and not app.text_input and not app.button


def test_closed_day_is_waiting_view_and_same_link_opens_next_day(portal):
    db, competition, day, _, _, access = portal
    db.finish_meet(day["id"], "Casey")
    app = app_for(portal)
    assert not app.exception and "No competition day is active" in app.info[0].value
    assert not app.button and not app.selectbox
    next_day = db.create_competition_day(competition["id"], "Sunday", "2026-10-11", ["Alex"], ["Casey"], first_event_name="Junior ME", day_status="active")
    next_event = db.list_meet_events(next_day["id"])[0]
    db.merge_import(next_event["id"], [{"athlete_id": "next", "name": "PARK Eli", "phase": "de", "strip": "F1", "pod": "F"}], "Casey")
    app.run()
    assert not app.exception
    assert app.selectbox[0].options == ["PARK Eli · Junior ME"]
    assert get_or_create_parent_access(db, competition["id"], "Casey") == access
