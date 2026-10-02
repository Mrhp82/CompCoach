"""Phone workflows preserve a rotating DE queue and live physical coverage."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named, create_multi_event, keyed, markdown_values, nav_named, open_board,
)


@pytest.fixture
def wheel_scenario(tmp_path, monkeypatch):
    path = tmp_path / "wheel-live.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database, ["Junior Epee", "Cadet Epee"], coaches=("Carmine", "Sam", "Vivien")
    )
    for child, table in zip(events, (
        "Name\tStrip #\nTEST Alex\tB1\nTEST Robin\tB2\nTEST Taylor\tB3",
        "Name\tStrip #\nTEST Casey\tP1",
    )):
        database.merge_import(child["id"], parse_pasted_table(table).records, "Carmine")
        database.assign_de_pods(
            child["id"], pods=[database.list_athletes(child["id"])[0]["pod"]],
            coaches=["Carmine"], actor="Carmine",
        )
    athletes = {row["name"]: row for child in events for row in database.list_athletes(child["id"])}
    yield database, meet, events, athletes
    st.cache_resource.clear()


def _cards(app):
    return [value for value in markdown_values(app) if "class='cc-athlete'" in value]


def _order(app, names):
    return [next(name for name in names if name in value) for value in _cards(app)
            if any(name in value for name in names)]


def _text(app):
    return "\n".join([*markdown_values(app), *(item.value for item in app.caption),
                      *(item.value for item in app.info), *(item.value for item in app.warning)])


def _live_key(athlete, suffix, prefix="personal"):
    return f"live_{prefix}_{athlete['id']}_{suffix}"


def _strip(app, athlete, value, prefix="personal"):
    keyed(app.text_input, _live_key(athlete, "strip_v", prefix)).set_value(value)


def test_results_rotate_the_wheel_continuously_with_no_ready_gate(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    names = ["TEST Alex", "TEST Robin", "TEST Taylor"]
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert _order(app, names) == names
    for selected, expected in (
        (names[0], [names[1], names[2], names[0]]),
        (names[1], [names[2], names[0], names[1]]),
        (names[2], names),
        (names[0], [names[1], names[2], names[0]]),
    ):
        athlete = athletes[selected]
        keyed(app.button, f"won_{athlete['id']}").click().run()
        assert _order(app, names) == expected
        assert not any("Ready for next bout" in button.label for button in app.button)
        for name in names:
            assert keyed(app.button, f"won_{athletes[name]['id']}")
            assert keyed(app.button, f"lost_{athletes[name]['id']}")
            assert keyed(app.button, _live_key(athletes[name], "call_on_deck"))
        assert not app.exception
    assert database.get_athlete(events[0]["id"], athletes[names[0]]["id"])["de_wins"] == 2
    assert "Next bout · 2 rounds passed" in _text(app)


def test_personal_queue_prioritizes_new_calls_without_changing_waiting_rotation(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    names = ["TEST Alex", "TEST Robin", "TEST Taylor"]
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    for selected in (names[0], names[0], names[1]):
        keyed(app.button, f"won_{athletes[selected]['id']}").click().run()
    assert _order(app, names) == [names[2], names[0], names[1]]
    assert database.get_athlete(events[0]["id"], athletes[names[0]]["id"])["de_wins"] == 2
    assert database.get_athlete(events[0]["id"], athletes[names[1]]["id"])["de_wins"] == 1
    _strip(app, athletes[names[0]], "C3")
    keyed(app.button, _live_key(athletes[names[0]], "call_now")).click().run()
    assert _order(app, names) == [names[0], names[2], names[1]]
    assert "C3" in _text(app)
    assert keyed(app.button, f"won_{athletes[names[0]]['id']}")
    assert not app.exception


def test_actual_strip_is_separate_from_pod_and_resets_after_a_bout(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert keyed(app.text_input, _live_key(alex, "strip_v")).value == ""
    assert "Pod B" in _text(app)
    _strip(app, alex, "c3")
    keyed(app.button, _live_key(alex, "call_on_deck")).click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert (saved["pod"], saved["source_strip"], saved["live_location"], saved["call_status"]) == ("B", "B1", "C3", "on_deck")
    assert saved["reported_at"] and saved["reported_by"] == "Carmine"
    keyed(app.button, _live_key(alex, "busy")).click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["covered_by"] == "Carmine" and saved["covered_at"]
    keyed(app.button, f"won_{alex['id']}").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["live_location"] == "" and saved["call_status"] == "waiting"
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["pod"] == "B" and saved["source_strip"] == "B1"
    assert keyed(app.text_input, _live_key(alex, "strip_v")).value == ""
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Carmine")
    assert status["is_available"] is True
    assert "Carmine" in "\n".join(value for value in markdown_values(app) if "cc-available-coaches-banner" in value)
    _strip(app, alex, "D4")
    keyed(app.button, _live_key(alex, "call_in_hole")).click().run()
    assert database.get_athlete(events[0]["id"], alex["id"])["live_location"] == "D4"
    # The normal mobile card offers forward call states only. Cancelling a
    # call is a deliberate correction in the compact Modify call controls.
    keyed(app.toggle, _live_key(alex, "modify_v")).set_value(True).run()
    keyed(app.button, _live_key(alex, "call_waiting")).click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == "waiting" and saved["live_location"] == ""
    assert not app.exception


@pytest.mark.parametrize(("role", "actor"), [("coach", "Sam"), ("admin", "Carmine"), ("coordinator", "Irina")])
def test_every_home_shows_busy_timer_and_uncovered_siblings_across_events(wheel_scenario, role, actor):
    database, meet, events, athletes = wheel_scenario
    alex, robin = athletes["TEST Alex"], athletes["TEST Robin"]
    database.cover_athlete(events[0]["id"], alex["id"], "Carmine", "Irina", location="J4")
    database.report_call(events[0]["id"], robin["id"], status="now", location="C3", actor="Irina")
    four_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)).isoformat()
    with sqlite3.connect(database.path) as conn:
        conn.execute("UPDATE athletes SET covered_at = ? WHERE id = ?", (four_minutes_ago, alex["id"]))
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    text = _text(app)
    assert "Coaches busy now" in text and "4 min" in text
    assert "with **TEST Alex**" in text and "Actual strip J4" in text
    assert "Other athletes needing cover · 3" in text
    assert any("TEST Robin" in warning.value and "Assigned coach busy: Carmine with TEST Alex" in warning.value for warning in app.warning)
    assert any("TEST Casey" in info.value and "Cadet Epee" in info.value and "Alternate coverage needed if called" in info.value for info in app.info)
    assert "Actual strip B1" not in text
    assert not app.exception


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_coordinator_marks_another_coach_busy_and_result_releases_him(wheel_scenario, outcome):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    _strip(coordinator, alex, "D4", prefix="live")
    keyed(coordinator.selectbox, _live_key(alex, "cover_coach_v", "live")).set_value("Sam")
    keyed(coordinator.button, _live_key(alex, "busy", "live")).click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["covered_by"] == "Sam" and saved["covered_at"] and saved["live_location"] == "D4"
    assert saved["de_coaches"] == ["Carmine"]
    coach = open_board(meet["id"], meet["coach_token"], "Sam")
    assert alex["name"] in "\n".join(_cards(coach))
    keyed(coach.button, f"{outcome}_{alex['id']}").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["last_de_result"] == outcome
    assert next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")["is_available"]
    coordinator.run()
    assert "Coaches busy now" not in _text(coordinator)
    assert not coach.exception and not coordinator.exception


def test_stale_inline_call_cannot_overwrite_a_newer_phone_update(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    stale = open_board(meet["id"], meet["coach_token"], "Carmine")
    _strip(stale, alex, "D4")
    database.report_call(events[0]["id"], alex["id"], status="now", location="C3", actor="Irina")
    keyed(stale.button, _live_key(alex, "call_on_deck")).click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == "now" and saved["live_location"] == "C3"
    assert stale.error and not stale.exception


def test_stale_other_phone_busy_tap_cannot_silently_move_a_coach_to_another_event(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex, casey = athletes["TEST Alex"], athletes["TEST Casey"]
    first = open_board(meet["id"], meet["coordinator_token"], "Irina")
    stale = open_board(meet["id"], meet["coordinator_token"], "Irina")
    _strip(first, alex, "D4", prefix="live")
    keyed(first.selectbox, _live_key(alex, "cover_coach_v", "live")).set_value("Sam")
    keyed(first.button, _live_key(alex, "busy", "live")).click().run()
    _strip(stale, casey, "P3", prefix="live")
    keyed(stale.selectbox, _live_key(casey, "cover_coach_v", "live")).set_value("Sam")
    keyed(stale.button, _live_key(casey, "busy", "live")).click().run()
    assert database.get_athlete(events[0]["id"], alex["id"])["covered_by"] == "Sam"
    assert database.get_athlete(events[1]["id"], casey["id"])["covered_by"] == ""
    assert stale.error
    assert not first.exception and not stale.exception


def test_stale_global_publish_cannot_overwrite_a_newer_call(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    stale = open_board(meet["id"], meet["coordinator_token"], "Irina")
    nav_named(stale, "coordinator_nav_").set_value("Live").run()
    keyed(stale.selectbox, "call_athlete_").set_value(alex["id"]).run()
    keyed(stale.get("button_group"), "call_status_").set_value("On Deck")
    keyed(stale.text_input, "call_location_").set_value("D4")
    database.report_call(events[0]["id"], alex["id"], status="now", location="C3", actor="Sam")
    button_named(stale, "Publish update").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == "now" and saved["live_location"] == "C3"
    assert saved["reported_by"] == "Sam"
    assert stale.error and not stale.exception


@pytest.mark.parametrize("label,status", [("Now", "now"), ("On Deck", "on_deck"),
                                          ("In the Hole", "in_hole")])
def test_coordinator_can_publish_unknown_strip_call_visible_to_assigned_coach(wheel_scenario, label, status):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    nav_named(coordinator, "coordinator_nav_").set_value("Live").run()
    keyed(coordinator.selectbox, "call_athlete_").set_value(alex["id"]).run()
    location = keyed(coordinator.text_input, "call_location_")
    assert location.label == "📍 **Actual bout strip** (optional)" and location.value == ""
    keyed(coordinator.get("button_group"), "call_status_").set_value(label)
    button_named(coordinator, "Publish update").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == status and saved["live_location"] == ""
    assert saved["reported_at"] and saved["reported_by"] == "Irina"
    assert saved["pod"] == "B" and saved["source_strip"] == "B1"
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert any("Actual strip to confirm" in message.value for message in coordinator.success)
    assert "Actual strip to confirm" in _text(coordinator)
    assert not coordinator.error and not coordinator.exception
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert alex["name"] in "\n".join(_cards(observer))
    assert "Actual strip to confirm" in _text(observer)
    assert "Reported by Irina" in _text(observer)
    assert not observer.exception


def test_coordinator_can_complete_unknown_strip_call_through_publish_form(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    nav_named(coordinator, "coordinator_nav_").set_value("Live").run()
    keyed(coordinator.selectbox, "call_athlete_").set_value(alex["id"]).run()
    keyed(coordinator.get("button_group"), "call_status_").set_value("On Deck")
    button_named(coordinator, "Publish update").click().run()
    first = database.get_athlete(events[0]["id"], alex["id"])
    assert first["call_status"] == "on_deck" and first["live_location"] == ""
    keyed(coordinator.selectbox, "call_athlete_").set_value(alex["id"]).run()
    keyed(coordinator.get("button_group"), "call_status_").set_value("On Deck")
    keyed(coordinator.text_input, "call_location_").set_value("c3")
    button_named(coordinator, "Publish update").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "C3"
    assert saved["version"] > first["version"] and saved["reported_by"] == "Irina"
    assert saved["pod"] == "B" and saved["source_strip"] == "B1"
    assert "<b>C3</b>" in _text(coordinator)
    assert "Actual strip to confirm" not in _text(coordinator)
    assert not coordinator.error and not coordinator.exception


def test_stale_unknown_strip_publish_cannot_erase_another_coachs_known_location(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex = athletes["TEST Alex"]
    stale = open_board(meet["id"], meet["coordinator_token"], "Irina")
    nav_named(stale, "coordinator_nav_").set_value("Live").run()
    keyed(stale.selectbox, "call_athlete_").set_value(alex["id"]).run()
    keyed(stale.get("button_group"), "call_status_").set_value("On Deck")
    assert keyed(stale.text_input, "call_location_").value == ""
    database.report_call(events[0]["id"], alex["id"], status="now", location="C3", actor="Sam")
    before = database.get_athlete(events[0]["id"], alex["id"])
    button_named(stale, "Publish update").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["call_status"] == "now" and saved["live_location"] == "C3"
    assert saved["version"] == before["version"]
    assert saved["reported_at"] == before["reported_at"] and saved["reported_by"] == "Sam"
    assert stale.error and not stale.exception


def test_stale_global_publish_cannot_move_a_busy_coach_to_another_event(wheel_scenario):
    database, meet, events, athletes = wheel_scenario
    alex, casey = athletes["TEST Alex"], athletes["TEST Casey"]
    stale = open_board(meet["id"], meet["coordinator_token"], "Irina")
    nav_named(stale, "coordinator_nav_").set_value("Live").run()
    keyed(stale.selectbox, "call_athlete_").set_value(casey["id"]).run()
    keyed(stale.get("button_group"), "call_status_").set_value("Now")
    keyed(stale.text_input, "call_location_").set_value("P3")
    keyed(stale.selectbox, "call_coverage_").set_value("Sam")
    database.cover_athlete(events[0]["id"], alex["id"], "Sam", "Carmine", location="D4")
    button_named(stale, "Publish update").click().run()
    assert database.get_athlete(events[0]["id"], alex["id"])["covered_by"] == "Sam"
    saved = database.get_athlete(events[1]["id"], casey["id"])
    assert saved["covered_by"] == "" and saved["call_status"] == "waiting"
    assert saved["live_location"] == ""
    assert stale.error and not stale.exception
