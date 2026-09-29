from datetime import date

import pytest
import streamlit as st

from compcoach_live import competition_setup
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named,
    markdown_values,
    nav_named,
    open_board,
    query_value,
    selectbox_named,
)


def _input_named(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"Expected one {label!r} input, found {len(matches)}"
    return matches[0]


def _open_settings(meet, section):
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()
    nav_named(app, "settings_section_").set_value(section).run()
    return app


def test_admin_competition_profile_is_visible_and_saves_metadata(
    tmp_path, monkeypatch
):
    path = tmp_path / "competition-profile.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setattr(competition_setup, "DEFAULT_ASSET_ROOT", tmp_path / "assets")
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Epee",
        competition_date="2030-10-09",
    )

    app = _open_settings(meet, "Competition")

    assert any(item.value == "Competition profile" for item in app.subheader)
    assert _input_named(app.text_input, "Competition name").value == "Day 1"
    _input_named(app.text_input, "Competition name").set_value("October NAC")
    _input_named(app.text_input, "Location").set_value("Salt Palace")
    _input_named(app.date_input, "Start date").set_value(date(2030, 10, 9))
    _input_named(app.date_input, "End date").set_value(date(2030, 10, 12))
    selectbox_named(app, "Competition timezone").set_value("America/Denver")
    button_named(app, "Save competition profile").click().run()

    assert not app.exception
    saved = database.get_competition(meet["competition_id"])
    assert saved is not None
    assert saved["name"] == "October NAC"
    assert saved["location"] == "Salt Palace"
    assert saved["start_date"] == "2030-10-09"
    assert saved["end_date"] == "2030-10-12"
    assert saved["timezone"] == "America/Denver"


def test_admin_coaches_panel_shows_roster_and_updates_day_status(
    tmp_path, monkeypatch
):
    path = tmp_path / "coach-management.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "October NAC · Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Epee",
        competition_date="2030-10-09",
    )
    sam = next(row for row in database.list_coaches() if row["name"] == "Sam")

    app = _open_settings(meet, "Coaches")

    assert any(item.value == "Coach management" for item in app.subheader)
    markup = "\n".join(markdown_values(app))
    assert "Today's roster" in markup
    assert "Carmine" in markup
    assert "Sam" in markup
    assert "Irina" in markup
    coach_picker = selectbox_named(app, "Coach to manage")
    sam_option = next(option for option in coach_picker.options if option.startswith("Sam"))

    coach_picker.select(sam_option).run()
    selectbox_named(app, "Status today").set_value("absent")
    button_named(app, "Save today's status").click().run()

    assert not app.exception
    saved = next(
        row
        for row in database.list_day_coaches(meet["id"])
        if row["coach_id"] == sam["id"]
    )
    assert saved["presence_status"] == "absent"


@pytest.mark.parametrize("event_name", [None, "Junior Epee"])
def test_admin_schedule_creates_future_day_with_zero_or_one_event(
    tmp_path, monkeypatch, event_name
):
    path = tmp_path / f"schedule-{'empty' if event_name is None else 'one'}.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "October NAC · Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Epee",
        competition_date="2030-10-09",
    )

    app = _open_settings(meet, "Schedule")

    assert any(item.value == "Competition schedule" for item in app.subheader)
    _input_named(app.date_input, "Competition date").set_value(date(2030, 10, 10))
    _input_named(app.text_input, "Day name").set_value("October NAC · Day 2")
    if event_name is not None:
        _input_named(app.text_input, "Event 1 (optional)").set_value(event_name)
    button_named(app, "Create scheduled day").click().run()

    assert not app.exception
    days = database.list_competition_days(meet["competition_id"])
    assert len(days) == 2
    future = next(day for day in days if day["id"] != meet["id"])
    assert future["day_status"] == "scheduled"
    assert future["competition_date"] == "2030-10-10"
    assert future["name"] == "October NAC · Day 2"
    events = database.list_meet_events(future["id"])
    assert [event["name"] for event in events] == (
        [] if event_name is None else [event_name]
    )


def test_admin_cannot_activate_future_day_while_another_day_is_active(
    tmp_path, monkeypatch
):
    path = tmp_path / "blocked-activation.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    active = database.create_meet(
        "October NAC · Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Epee",
        competition_date="2030-10-09",
    )
    future = database.create_competition_day(
        active["competition_id"],
        "October NAC · Day 2",
        "2030-10-10",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Junior Epee",
        day_status="scheduled",
    )

    app = _open_settings(active, "Schedule")

    blocked = button_named(app, "Finish active day first")
    assert blocked.disabled
    assert not any(button.label == "Activate this day" for button in app.button)
    assert database.get_active_competition_day(active["competition_id"])["id"] == active["id"]
    assert database.get_meet(future["id"])["day_status"] == "scheduled"
    assert not app.exception


def test_coach_link_to_scheduled_day_hides_future_athletes_and_live_navigation(
    tmp_path, monkeypatch
):
    path = tmp_path / "scheduled-coach-link.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    future = database.create_meet(
        "October NAC · Future Day",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Junior Epee",
        competition_date="2030-10-10",
        day_status="scheduled",
    )
    child = database.list_meet_events(future["id"])[0]
    database.merge_import(
        child["id"],
        parse_pasted_table("Name\tStrip #\nFUTURE ATHLETE\tP1").records,
        "Carmine",
    )

    app = open_board(future["id"], future["coach_token"], "Sam")

    assert query_value(app, "event") == future["id"]
    assert any(
        item.value == "🗓️ Competition day not active yet" for item in app.subheader
    )
    assert any("still being prepared" in item.value for item in app.info)
    assert not any((group.key or "").startswith("coach_nav_") for group in app.get("button_group"))
    assert "FUTURE ATHLETE" not in "\n".join(markdown_values(app))
    assert not app.exception
