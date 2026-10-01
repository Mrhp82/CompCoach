"""The main-page availability banner stays shared across roles and events."""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named,
    create_multi_event,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "available-coaches.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def banner(app):
    """Select the global banner, excluding the detailed Live roster."""
    blocks = [
        value for value in markdown_values(app)
        if "<div class='cc-available-coaches-banner'>" in value
    ]
    assert len(blocks) <= 1
    return blocks[0] if blocks else ""


def import_athlete(database, event, name, strip, *, pools=False):
    table = (
        f"Name\tStrip #\tPool #\n{name}\t{strip}\t1"
        if pools else f"Name\tStrip #\n{name}\t{strip}"
    )
    database.merge_import(event["id"], parse_pasted_table(table).records, "Carmine")
    return next(row for row in database.list_athletes(event["id"]) if row["name"] == name)


@pytest.mark.parametrize(
    "role,actor,default_page",
    [
        ("admin", "Carmine", "My Group"),
        ("coordinator", "Irina", "Live"),
        ("coach", "Carmine", "My Group"),
    ],
)
def test_available_coach_is_visible_to_every_role_and_help_stays_cross_event(
    database, role, actor, default_page
):
    meet, events = create_multi_event(
        database, ["Cadet Epee", "Junior Epee"], coaches=("Carmine", "Sam", "Vivien")
    )
    first = import_athlete(database, events[0], "EXAMPLE Alex", "B2")
    second = import_athlete(database, events[1], "OTHER Robin", "P3")
    database.assign_athletes(
        events[0]["id"], [first["id"]], main_coach="Carmine", actor="Carmine"
    )
    database.assign_athletes(
        events[1]["id"], [second["id"]], main_coach="Vivien", actor="Carmine"
    )
    database.request_help(events[1]["id"], second["id"], "Vivien")
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")

    app = open_board(meet["id"], meet[f"{role}_token"], actor)

    assert not app.exception
    assert nav_named(app, f"{role}_nav_").value == default_page
    available_banner = banner(app)
    assert "🟢 Coaches available · 1" in available_banner
    assert "Sam" in available_banner
    assert "just now" in available_banner
    assert "Carmine" not in available_banner
    assert "Vivien" not in available_banner
    assert any(item.value == "Available to help across all events." for item in app.caption)
    assert "OTHER Robin · P3" in "\n".join(markdown_values(app))
    assert "Vivien needs help" in "\n".join(markdown_values(app))
    assert button_named(app, "I’m coming")

    # Navigation must not hide the global shortcut to free coaches or help.
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    assert "Sam" in banner(app)
    assert "Vivien needs help" in "\n".join(markdown_values(app))
    assert not app.exception


def test_finished_work_does_not_automatically_publish_available_coach(database):
    meet, events = create_multi_event(database, ["Cadet Epee"])
    athlete = import_athlete(database, events[0], "EXAMPLE Alex", "B2", pools=True)
    database.assign_athletes(
        events[0]["id"], [athlete["id"]], main_coach="Sam", actor="Carmine"
    )
    database.set_pool_result(events[0]["id"], athlete["id"], wins=3, losses=3, actor="Sam")
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert status["suggested_available"] is True
    assert status["is_available"] is False

    app = open_board(meet["id"], meet["coach_token"], "Carmine")

    assert not banner(app)
    assert any(item.value == "No coach currently marked available to help." for item in app.caption)
    assert not app.exception


def test_available_coach_marked_absent_is_removed_from_global_banner(database):
    meet, _events = create_multi_event(database, ["Cadet Epee"])
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert "Sam" in banner(app)
    sam = next(row for row in database.list_coaches() if row["name"] == "Sam")

    database.set_day_coach_presence(meet["id"], sam["id"], False, "Carmine")
    app.run()

    assert not banner(app)
    assert any(item.value == "No coach currently marked available to help." for item in app.caption)
    assert not app.exception


def test_other_coach_sees_availability_taps_on_next_refresh(database):
    meet, _events = create_multi_event(database, ["Cadet Epee", "Junior Epee"])
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    sam = open_board(meet["id"], meet["coach_token"], "Sam")
    assert not banner(observer)

    button_named(sam, "I’m available to help").click().run()
    observer.run()
    assert "Sam" in banner(observer)
    assert not observer.exception

    button_named(sam, "I’m no longer available").click().run()
    observer.run()
    assert not banner(observer)
    assert not observer.exception


def test_banner_lists_every_available_coach_and_updates_its_count(database):
    meet, _events = create_multi_event(
        database, ["Cadet Epee", "Junior Epee"], coaches=("Carmine", "Sam", "Vivien")
    )
    for coach in ("Sam", "Vivien"):
        database.set_coach_availability(meet["id"], coach, True, coach)
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")

    markup = banner(observer)
    assert "🟢 Coaches available · 2" in markup
    assert "Sam" in markup and "Vivien" in markup

    database.set_coach_availability(meet["id"], "Sam", False, "Sam")
    observer.run()

    markup = banner(observer)
    assert "🟢 Coaches available · 1" in markup
    assert "Vivien" in markup and "Sam" not in markup
    assert not observer.exception


@pytest.mark.parametrize("engagement", ["assignment", "claim"])
def test_new_work_clears_global_available_coach_on_next_refresh(database, engagement):
    meet, events = create_multi_event(database, ["Cadet Epee", "Junior Epee"])
    athlete = import_athlete(database, events[1], "EXAMPLE Alex", "P3")
    if engagement == "claim":
        database.report_call(
            events[1]["id"], athlete["id"], status="now", location="P3", actor="Irina"
        )
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert "Sam" in banner(observer)

    if engagement == "assignment":
        database.assign_athletes(
            events[1]["id"], [athlete["id"]], main_coach="Sam", actor="Carmine"
        )
    else:
        assert database.claim(events[1]["id"], athlete["id"], "Sam")[0]
    observer.run()

    assert not banner(observer)
    assert any(item.value == "No coach currently marked available to help." for item in observer.caption)
    assert not observer.exception


@pytest.mark.parametrize("day_state", ["scheduled", "locked", "closed"])
def test_available_banner_is_hidden_on_nonoperational_days(database, day_state):
    meet, _events = create_multi_event(database, ["Cadet Epee"])
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")
    if day_state == "locked":
        database.set_meet_locked(meet["id"], True)
    elif day_state == "closed":
        database.finish_meet(meet["id"], "Carmine")
    else:
        database.set_day_status(meet["id"], "scheduled", "Carmine")

    for role, actor in [("admin", "Carmine"), ("coach", "Carmine"), ("coordinator", "Irina")]:
        app = open_board(meet["id"], meet[f"{role}_token"], actor, archive=day_state == "closed")
        assert not banner(app)
        assert not any(item.value == "No coach currently marked available to help." for item in app.caption)
        assert not app.exception


def test_available_coach_name_is_escaped_before_html_rendering(database):
    name = "Kai <West> & Co"
    meet, _events = create_multi_event(database, ["Cadet Epee"], coaches=("Carmine", name))
    database.set_coach_availability(meet["id"], name, True, name)

    app = open_board(meet["id"], meet["coach_token"], "Carmine")

    markup = banner(app)
    assert "Kai &lt;West&gt; &amp; Co" in markup
    assert name not in markup
    assert not app.exception
