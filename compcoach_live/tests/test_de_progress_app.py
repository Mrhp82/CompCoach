"""Optional DE tableau edits and athlete progress through the real app."""

from __future__ import annotations

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import nav_named, open_board


@pytest.fixture
def progress_board(tmp_path, monkeypatch):
    path = tmp_path / "de-progress-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "Example competition", ["Alex", "Taylor"], ["Jordan"],
        first_event_name="Cadet Epee",
    )
    database.add_meet_event(meet["id"], "Junior Epee")
    events = database.list_meet_events(meet["id"])
    athletes = []
    for event, name in zip(events, ["RIVERA Casey", "MORRIS Robin"]):
        database.merge_import(
            event["id"],
            parse_pasted_table(f"Name\tStrip #\n{name}\tB1").records,
            "Alex",
        )
        athlete = database.list_athletes(event["id"])[0]
        database.assign_de_athletes(
            event["id"], [athlete["id"]], coaches=["Alex"], actor="Alex",
        )
        athletes.append(database.get_athlete(event["id"], athlete["id"]))
    yield database, database.get_meet(meet["id"]), events, athletes
    st.cache_resource.clear()


def tableau_box(app, event):
    matches = [
        widget for widget in app.selectbox
        if (widget.key or "").startswith(f"de_start_tableau_{event['id']}_v")
    ]
    assert len(matches) == 1, [widget.key for widget in app.selectbox]
    assert matches[0].label == "Starting DE tableau (optional)"
    return matches[0]


def tableau_submit(app, event):
    tableau_box(app, event)
    # Forms have one submit button per event, with an event-scoped key.
    matches = [
        button for button in app.button
        if button.label == "Save DE starting tableau"
        and event["id"] in str(button.key)
    ]
    assert len(matches) == 1, [(button.label, button.key) for button in app.button]
    return matches[0]


def open_admin_events(meet):
    app = open_board(meet["id"], meet["admin_token"], "Alex")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Events").run()
    assert not app.exception
    return app


def markup(app):
    return "\n".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.caption]
    )


def clear_tableau_and_submit(app, event):
    box = tableau_box(app, event)
    box.set_value(None)
    tableau_submit(app, event).click()
    # AppTest omits string_value for any None-valued selectbox option. The
    # browser sends the selected formatted option, including "Not set".
    # Replay that real frontend message so this covers the clearing callback.
    states = app._tree.get_widget_states()
    widget_state = next(state for state in states.widgets if state.id == box.id)
    widget_state.string_value = "Not set"
    app._run(states)


def advance_to_third_bout(database, event, athlete):
    database.mark_bye(event["id"], athlete["id"], actor="Alex")
    for _ in range(2):
        database.report_call(
            event["id"], athlete["id"], status="now", location="C3", actor="Jordan",
        )
        database.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")


def test_admin_optional_tableau_is_event_scoped_and_does_not_change_results(progress_board):
    database, meet, events, athletes = progress_board
    app = open_admin_events(meet)
    assert tableau_box(app, events[0]).value is None
    assert "Not set" in tableau_box(app, events[0]).options
    assert "T256" in tableau_box(app, events[0]).options
    tableau_box(app, events[0]).set_value(256)
    tableau_submit(app, events[0]).click().run()
    tableau_box(app, events[1]).set_value(128)
    tableau_submit(app, events[1]).click().run()
    assert not app.exception
    assert database.get_event(events[0]["id"])["de_start_tableau"] == 256
    assert database.get_event(events[1]["id"])["de_start_tableau"] == 128
    for event, athlete in zip(events, athletes):
        stored = database.get_athlete(event["id"], athlete["id"])
        assert (stored["de_wins"], stored["de_byes"]) == (0, 0)


def test_coach_progress_counts_bye_separately_and_uses_own_event_tableau(progress_board):
    database, meet, events, athletes = progress_board
    database.set_event_de_start_tableau(events[0]["id"], 256, "Alex")
    database.set_event_de_start_tableau(events[1]["id"], 128, "Alex")
    for event, athlete in zip(events, athletes):
        advance_to_third_bout(database, event, athlete)
    app = open_board(meet["id"], meet["coach_token"], "Alex")
    assert not app.exception
    text = markup(app)
    assert "1 bye · 2 DE wins · Waiting for DE bout 3 · T32" in text
    assert "1 bye · 2 DE wins · Waiting for DE bout 3 · T16" in text
    assert not any(widget.label == "Starting DE tableau (optional)" for widget in app.selectbox)
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert not app.exception
    assert "1 bye · 2 DE wins · Waiting for DE bout 3 · T32" in markup(app)


def test_clear_tableau_keeps_bye_and_win_counts_without_invented_round(progress_board):
    database, meet, events, athletes = progress_board
    event, athlete = events[0], athletes[0]
    database.set_event_de_start_tableau(event["id"], 256, "Alex")
    advance_to_third_bout(database, event, athlete)
    app = open_admin_events(meet)
    clear_tableau_and_submit(app, event)
    assert not app.exception
    assert database.get_event(event["id"])["de_start_tableau"] is None
    stored = database.get_athlete(event["id"], athlete["id"])
    assert (stored["de_byes"], stored["de_wins"]) == (1, 2)
    coach = open_board(meet["id"], meet["coach_token"], "Alex")
    progress_lines = [item.value for item in coach.markdown if "cc-result" in item.value]
    assert any("1 bye · 2 DE wins · Waiting for DE bout 3" in line for line in progress_lines)
    assert not any("T32" in line for line in progress_lines)
    assert not coach.exception


def test_called_and_physically_covered_athlete_shows_current_bout_in_busy_hero(progress_board):
    database, meet, events, athletes = progress_board
    event, athlete = events[0], athletes[0]
    database.set_event_de_start_tableau(event["id"], 256, "Alex")
    advance_to_third_bout(database, event, athlete)
    database.report_call(event["id"], athlete["id"], status="on_deck", location="C3", actor="Jordan")
    database.cover_athlete(event["id"], athlete["id"], coach="Alex", actor="Alex")
    app = open_board(meet["id"], meet["coach_token"], "Alex")
    assert not app.exception
    lines = [item.value for item in app.markdown if "1 bye · 2 DE wins" in item.value]
    assert any("DE bout 3 · T32" in line for line in lines)
    assert not any("Waiting for DE bout 3" in line for line in lines)
    assert any(button.label == "Won" for button in app.button)
    assert any(button.label == "Lost" for button in app.button)


def test_out_round_is_visible_in_team_situation_without_next_bout(progress_board):
    database, meet, events, athletes = progress_board
    event, athlete = events[0], athletes[0]
    database.set_event_de_start_tableau(event["id"], 256, "Alex")
    advance_to_third_bout(database, event, athlete)
    database.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Jordan")
    database.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex")
    app = open_board(meet["id"], meet["coach_token"], "Alex")
    nav_named(app, "coach_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("Out").run()
    assert not app.exception
    progress_lines = [item.value for item in app.markdown if "cc-result" in item.value]
    assert any("1 bye · 2 DE wins · Out at T32" in line for line in progress_lines)
    assert not any("Waiting for DE bout 3" in line for line in progress_lines)


def test_archived_admin_tableau_form_is_read_only(progress_board):
    database, meet, events, _athletes = progress_board
    event = events[0]
    database.set_event_de_start_tableau(event["id"], 256, "Alex")
    database.finish_meet(meet["id"], "Alex")
    app = open_board(meet["id"], meet["admin_token"], "Alex", archive=True)
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Events").run()
    assert not app.exception
    assert tableau_box(app, event).disabled
    assert tableau_submit(app, event).disabled
    assert tableau_box(app, event).value == 256


def test_stale_admin_form_cannot_overwrite_tableau_changed_on_another_phone(progress_board):
    database, meet, events, _athletes = progress_board
    event = events[0]
    app = open_admin_events(meet)
    assert tableau_box(app, event).value is None
    # The pending form still carries version zero; another admin saves first.
    tableau_box(app, event).set_value(128)
    database.set_event_de_start_tableau(event["id"], 256, "Alex", expected_version=0)
    tableau_submit(app, event).click().run()
    assert not app.exception
    assert database.get_event(event["id"])["de_start_tableau"] == 256
    assert any("changed on another phone" in item.value for item in app.error)


def test_de_assignment_setup_exposes_optional_round_tracking(progress_board):
    database, meet, events, _athletes = progress_board
    app = open_board(meet["id"], meet["admin_token"], "Alex")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    event = events[0]
    nav_named(app, f"assignment_phase_{event['id']}").set_value("Direct Elimination").run()
    assert not app.exception
    assert any(section.label == "DE round tracking" for section in app.expander)
    tableau_box(app, event).set_value(256)
    tableau_submit(app, event).click().run()
    assert not app.exception
    assert database.get_event(event["id"])["de_start_tableau"] == 256
