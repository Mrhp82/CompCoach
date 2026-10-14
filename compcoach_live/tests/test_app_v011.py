"""Integrated day, parent, pool-closing and focused WhatsApp workflows."""

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.parent_access import get_or_create_parent_access
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.pool_results import pool_results_are_unknown
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    APP_PATH, button_named, keyed, nav_named, open_board, query_value,
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "app-v011.sqlite"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach.example")
    for variable in ("COMPCOACH_DATABASE_URL", "COMPCOACH_REQUIRE_CLOUD"):
        monkeypatch.delenv(variable, raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _meet(database):
    meet = database.create_meet("October NAC", ["Carmine", "Taylor"], ["Jordan"],
                                first_event_name="Morning Epee", competition_date="2026-10-10")
    second = database.add_meet_event(meet["id"], "Afternoon Foil")
    children = database.list_meet_events(meet["id"])
    for child, name, clock in zip(children, ["EXAMPLE Morning", "EXAMPLE Afternoon"], ["8:00 AM", "1:00 PM"]):
        database.merge_import(child["id"], parse_pasted_table(
            f"Name\tStrip #\tTime\tPool #\n{name}\tB1\t{clock}\t1"
        ).records, "Carmine")
    return database.get_meet(meet["id"]), children


def _whatsapp_message(app):
    return next((code.value for code in app.code if code.value.startswith("🏆")), "")


def _open_settings(app):
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()


def test_whatsapp_all_one_none_and_parent_link_stays_separate(database):
    meet, children = _meet(database)
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Share").run()
    assert "MORNING EPEE" in _whatsapp_message(app) and "AFTERNOON FOIL" in _whatsapp_message(app)
    next(widget for widget in app.radio if widget.label == "Events to include").set_value("Choose events").run()
    selector = next(widget for widget in app.multiselect if widget.label == "Include these events")
    selector.set_value([children[0]["id"]]).run()
    message = _whatsapp_message(app)
    assert "MORNING EPEE" in message and "EXAMPLE Morning" in message
    assert "AFTERNOON FOIL" not in message and "EXAMPLE Afternoon" not in message
    parent_url = next(code.value for code in app.code if "?competition=" in code.value)
    parent_query = parse_qs(urlsplit(parent_url).query)
    assert parent_query["competition"] == [meet["competition_id"]]
    assert "event" not in parent_query and "token" not in parent_query and "who" not in parent_query
    selector = next(widget for widget in app.multiselect if widget.label == "Include these events")
    selector.set_value([]).run()
    assert not _whatsapp_message(app)
    assert not any("wa.me" in str(getattr(widget, "url", "")) for widget in app.get("link_button"))
    assert any(code.value == parent_url for code in app.code)
    assert len(database.list_meet_events(meet["id"])) == 2
    assert not app.exception


def test_close_day_keeps_competition_actor_archive_and_manual_next_day(database):
    meet, children = _meet(database)
    original = database.list_athletes(children[0]["id"])[0]
    database.assign_athletes(children[0]["id"], [original["id"]], main_coach="Taylor", actor="Carmine")
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    _open_settings(app)
    keyed(app.checkbox, "confirm_finish_").check()
    button_named(app, "Finish day only").click().run()
    assert not app.exception
    assert query_value(app, "event") == meet["id"]
    assert query_value(app, "who") == "Carmine" and query_value(app, "archive") == "1"
    assert nav_named(app, "admin_archive_nav_").value == "Days"
    assert database.get_competition(meet["competition_id"])["status"] == "open"
    assert database.get_active_competition_day(meet["competition_id"]) is None
    assert database.get_athlete(children[0]["id"], original["id"])["main_coach"] == "Taylor"
    # AppTest retains removed fragment elements for the interrupted navigation
    # render; reopen the resulting URL to exercise its actual archive controls.
    app = open_board(meet["id"], meet["admin_token"], "Carmine", archive=True)
    button_named(app, "View day archive").click().run()
    assert not app.exception and nav_named(app, "admin_archive_nav_").value == "Overview"
    assert any(original["name"] in item.value for item in app.markdown)
    nav_named(app, "admin_archive_nav_").set_value("Days").run()
    next(widget for widget in app.date_input if widget.label == "Next competition date").set_value(date(2026, 10, 11))
    button_named(app, "Prepare next day").click().run()
    assert not app.exception and query_value(app, "event") == meet["id"]
    scheduled = [day for day in database.list_competition_days(meet["competition_id"]) if day["day_status"] == "scheduled"]
    assert len(scheduled) == 1
    assert database.get_active_competition_day(meet["competition_id"]) is None
    button_named(app, "Prepare next day").click().run()
    assert len(database.list_competition_days(meet["competition_id"])) == 2
    button_named(app, "Start this day").click().run()
    assert not app.exception
    assert query_value(app, "event") == scheduled[0]["id"]
    assert query_value(app, "who") == "Carmine"
    assert database.get_active_competition_day(meet["competition_id"])["id"] == scheduled[0]["id"]
    assert any("October NAC" in item.value for item in app.markdown)


def test_home_offers_existing_competition_between_days(database):
    meet, _children = _meet(database)
    database.finish_meet(meet["id"], "Carmine")
    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.session_state["landing_admin_unlocked"] = True
    app.session_state["home_admin_actor"] = "Carmine"
    app.query_params["home"] = "1"
    app.run()
    assert not app.exception
    assert any("competition is still open" in item.value for item in app.info)
    button_named(app, "Continue this competition").click().run()
    assert not app.exception and query_value(app, "event") == meet["id"]
    assert query_value(app, "who") == "Carmine" and query_value(app, "archive") == "1"


def test_force_pools_closes_all_waves_without_fake_results(database):
    meet, children = _meet(database)
    child = children[0]
    database.merge_import(child["id"], parse_pasted_table(
        "Name\tStrip #\tTime\tPool #\nEXAMPLE Later\tB2\t1:00 PM\t2"
    ).records, "Carmine")
    rows = database.list_athletes(child["id"])
    known = next(row for row in rows if row["name"] == "EXAMPLE Morning")
    database.set_pool_result(child["id"], known["id"], wins=4, losses=2, actor="Taylor")
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    button = button_named(app, "Close Pools regardless of missing results")
    assert button.disabled
    keyed(app.checkbox, "confirm_close_pools_").check()
    button_named(app, "Close Pools regardless of missing results").click().run()
    assert not app.exception
    assert database.get_phase_states(child["id"])["pools"]["closed"]
    rows = database.list_athletes(child["id"])
    assert (database.get_athlete(child["id"], known["id"])["pool_wins"], database.get_athlete(child["id"], known["id"])["pool_losses"]) == (4, 2)
    assert pool_results_are_unknown(next(row for row in rows if row["name"] == "EXAMPLE Later"))
    assert any("Pools finished" in item.value for item in app.markdown)
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    button_named(app, "Reopen Pools phase").click().run()
    assert not app.exception and not database.get_phase_states(child["id"])["pools"]["closed"]


def test_parent_route_bypasses_admin_pin_identity_and_staff_controls(database):
    meet, children = _meet(database)
    access = get_or_create_parent_access(database, meet["competition_id"], "Carmine")
    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.query_params["competition"] = meet["competition_id"]
    app.query_params["parent"] = access["token"]
    app.run()
    assert not app.exception and not any(widget.label == "Admin PIN" for widget in app.text_input)
    assert not any(item.value == "Who are you?" for item in app.subheader)
    selector = next(widget for widget in app.selectbox if widget.label == "Athlete")
    athlete = database.list_athletes(children[0]["id"])[0]
    selector.set_value(athlete["id"]).run()
    next(widget for widget in app.text_input if widget.label == "Strip (optional)").set_value("C3")
    button_named(app, "On deck").click().run()
    assert not app.exception
    saved = database.get_athlete(children[0]["id"], athlete["id"])
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "C3"
    assert saved["reported_by"] == "Parent"
    assert not any(button.label in {"Won", "Lost", "I'm with EXAMPLE Morning"} for button in app.button)


def test_forget_device_returns_to_pin_without_remembering_again(database, monkeypatch):
    from compcoach_live import remembered_access
    cleared, remembered = [], []
    monkeypatch.setattr(remembered_access, "clear_remembered_access", lambda: cleared.append(True))
    monkeypatch.setattr(remembered_access, "remember_staff_access", lambda *args, **kwargs: remembered.append(args))
    meet, _children = _meet(database)
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    _open_settings(app)
    remembered.clear()
    button_named(app, "Forget this device").click().run()
    assert not app.exception
    assert query_value(app, "home") == "1" and not query_value(app, "event")
    assert any(widget.label == "Admin PIN" for widget in app.text_input)
    assert cleared == [True]
    assert not remembered
