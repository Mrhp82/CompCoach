"""Lifecycle UI checks across independent Admin and public coach sessions."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB


APP_PATH = str(Path(__file__).parents[1] / "app.py")


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "neutral.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def button(app, label):
    matches = [item for item in app.button if item.label == label]
    assert len(matches) == 1, (label, [item.label for item in app.button])
    return matches[0]


def nav(app, prefix, value):
    item = next(item for item in app.get("button_group") if (item.key or "").startswith(prefix))
    item.set_value(value).run()


def open_day(day, role="admin", actor="Carmine", *, archive=False):
    app = AppTest.from_file(APP_PATH, default_timeout=20)
    app.query_params.update(event=day["id"], token=day[f"{role}_token"], who=actor)
    if archive:
        app.query_params["archive"] = "1"
    return app.run()


def qvalue(app, name):
    value = app.query_params.get(name)
    return value[0] if isinstance(value, list) and value else value


def create_day(database):
    day = database.create_meet(
        "October NAC", ["Carmine", "Sam"], ["Irina"],
        first_event_name="Cadet Epee", competition_date="2026-10-01",
    )
    event = database.list_meet_events(day["id"])[0]
    database.merge_import(
        event["id"], parse_pasted_table("Name\tStrip #\nEXAMPLE Alex\tB2").records, "Carmine"
    )
    return day, event


def settings_day(app):
    nav(app, "admin_nav_", "Setup")
    nav(app, "admin_setup_nav_", "Settings")


def test_authenticated_admin_home_clears_url_and_keeps_pin_gate_for_new_session(database):
    day, _ = create_day(database)
    app = open_day(day)
    button(app, "← Home").click().run()

    assert not app.exception
    assert dict(app.query_params) == {}
    assert any(item.value == "🟢 Active now" for item in app.subheader)
    assert not any(item.label == "Admin PIN" for item in app.text_input)
    assert database.get_meet(day["id"])["day_status"] == "active"

    fresh = AppTest.from_file(APP_PATH, default_timeout=20).run()
    assert any(item.label == "Admin PIN" for item in fresh.text_input)
    assert not any(item.label == "Open Admin" for item in fresh.button)


def test_finish_day_returns_home_and_preserves_read_only_history(database):
    day, event = create_day(database)
    app = open_day(day)
    settings_day(app)
    next(item for item in app.checkbox if item.key == f"confirm_finish_{day['id']}").set_value(True).run()
    button(app, "Finish day only").click().run()

    assert not app.exception
    assert dict(app.query_params) == {}
    assert any(item.value == "⏸️ No active competition" for item in app.subheader)
    assert database.get_active_competition_day(day["competition_id"]) is None
    assert database.list_athletes(event["id"])[0]["name"] == "EXAMPLE Alex"

    # Timed fragment widgets linger in AppTest's previous tree after a full
    # navigation. Open Home in a fresh authenticated session to test its link.
    home = AppTest.from_file(APP_PATH, default_timeout=20)
    home.session_state["landing_admin_unlocked"] = True
    home.session_state["home_admin_actor"] = "Carmine"
    home.run()
    app = button(home, "Open read-only archive").click().run()
    assert qvalue(app, "archive") == "1"
    assert qvalue(app, "who") == "Carmine"
    assert any("archive is read-only" in item.value for item in app.info)
    # AppTest also retains the previous Home form tree after query navigation.
    # Its follow-up widget snapshot is stale; a new browser session reads the
    # same saved archive and exercises its controls without that harness issue.
    app = open_day(database.get_meet(day["id"]), archive=True)
    settings_day(app)
    assert button(app, "Save settings").disabled
    assert not app.exception


def test_finish_competition_closes_future_days_and_leaves_public_neutral(database):
    day, event = create_day(database)
    future = database.create_competition_day(
        day["competition_id"], "Day 2", "2026-10-02", ["Carmine", "Sam"], ["Irina"],
        first_event_name="Junior Epee",
    )
    coach = open_day(day, "coach", "Sam")
    app = open_day(day)
    settings_day(app)
    assert button(app, "Finish competition & return Home").disabled
    next(
        item for item in app.checkbox
        if item.key == f"confirm_competition_end_{day['competition_id']}"
    ).set_value(True).run()
    button(app, "Finish competition & return Home").click().run()

    assert not app.exception
    assert dict(app.query_params) == {}
    assert database.get_competition(day["competition_id"])["status"] == "closed"
    assert database.get_meet(future["id"])["day_status"] == "closed"
    assert database.list_athletes(event["id"])[0]["name"] == "EXAMPLE Alex"
    assert any(item.value == "⏸️ No active competition" for item in app.subheader)
    assert not any(item.label == "Prepare day" for item in app.button)

    coach.run()
    assert not coach.exception
    assert any(item.value == "⏸️ No active competition" for item in coach.subheader)
    assert not coach.get("button_group")
    assert not any("EXAMPLE Alex" in item.value for item in coach.markdown)
    assert not any(item.label == "← Home" for item in coach.button)

    archived = open_day(database.get_meet(future["id"]), archive=True)
    settings_day(archived)
    assert not any(item.label == "Prepare next day" for item in archived.button)
    nav(archived, "settings_section_", "Competition")
    assert not any(item.label == "Save competition profile" for item in archived.button)
    assert not archived.get("file_uploader")
    nav(archived, "settings_section_", "Schedule")
    assert not any(item.label in {"Activate this day", "Create scheduled day"} for item in archived.button)
    assert not archived.exception


@pytest.mark.parametrize("role,actor", [("coach", "Sam"), ("coordinator", "Irina")])
def test_closed_successor_is_never_reopened_by_old_public_link(database, role, actor):
    first, _ = create_day(database)
    second = database.prepare_next_day(
        first["id"], "Day 2", "2026-10-02", ["Junior Epee"], "Carmine"
    )
    database.finish_meet(second["id"], "Carmine")
    app = open_day(first, role, actor)

    assert qvalue(app, "event") == first["id"]
    assert any(item.value == "⏸️ No active competition" for item in app.subheader)
    assert not app.get("button_group")
    assert not app.exception

    third = database.create_competition_day(
        first["competition_id"], "Day 3", "2026-10-03", ["Carmine", "Sam"], ["Irina"]
    )
    database.set_day_status(third["id"], "active", "Carmine")
    app.run()
    assert qvalue(app, "event") == third["id"]
    assert qvalue(app, "who") == actor
    assert app.get("button_group")
    assert not app.exception


def test_scheduled_day_is_separate_on_home_and_external_activation_refreshes_public_page(database):
    day, _ = create_day(database)
    database.finish_meet(day["id"], "Carmine")
    future = database.create_competition_day(
        day["competition_id"], "Day 2", "2026-10-02", ["Carmine", "Sam"], ["Irina"]
    )
    app = open_day(day, archive=True)
    button(app, "← Home").click().run()
    assert any(item.value == "⏸️ No active competition" for item in app.subheader)
    assert button(app, "Prepare day")
    assert not any(item.label == "Open Admin" for item in app.button)

    coach = open_day(future, "coach", "Sam")
    assert not coach.get("button_group")
    database.set_day_status(future["id"], "active", "Carmine")
    coach.run()
    assert coach.get("button_group")
    assert not coach.exception
