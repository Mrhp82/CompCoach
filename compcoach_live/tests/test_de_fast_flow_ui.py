"""One-tap DE results rotate an accessible live queue without hiding competitors."""

import pytest
import sqlite3
import streamlit as st

from compcoach_live.de_bouts import create_de_bout
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named, create_multi_event, keyed, markdown_values, nav_named, open_board,
)


@pytest.fixture
def fast_scenario(tmp_path, monkeypatch):
    path = tmp_path / "fast-de-flow.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"])
    for child, table in zip(events, (
        "Name\tStrip #\nTEST Alex\tP1\nTEST Robin\tQ1\nTEST Taylor\tQ2",
        "Name\tStrip #\nTEST Casey\tR1",
    )):
        database.merge_import(child["id"], parse_pasted_table(table).records, "Carmine")
        pods = sorted({row["pod"] for row in database.list_athletes(child["id"])})
        database.assign_de_pods(child["id"], pods=pods, coaches=["Carmine", "Sam"], actor="Carmine")
    athletes = {row["name"]: row for child in events for row in database.list_athletes(child["id"])}
    yield database, meet, events, athletes
    st.cache_resource.clear()


def _cards(app):
    return "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)


def _markup(app):
    return "\n".join(markdown_values(app))


def _correction(app, athlete):
    selector = next(widget for widget in app.selectbox if widget.label == "Athlete to correct")
    selector.set_value(athlete["id"]).run()
    button_named(app, "Undo last result").click().run()
    button_named(app, "Confirm undo").click().run()


def test_win_rotates_below_all_events_and_remains_ready_for_live_updates(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"won_{alex['id']}").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["de_wins"] == 1 and saved["active_state"] == "active"
    cards = _cards(app)
    assert all(athletes[name]["name"] in cards for name in ("TEST Alex", "TEST Robin", "TEST Taylor", "TEST Casey"))
    assert cards.index("TEST Casey") < cards.index("TEST Alex")
    assert "1 round passed" in _markup(app)
    assert any("4 active work" in caption.value for caption in app.caption)
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert status["unfinished_count"] == 4 and not status["suggested_available"]
    assert keyed(app.button, f"won_{alex['id']}")
    assert keyed(app.button, f"lost_{alex['id']}")
    assert not any("Ready for next bout" in button.label for button in app.button)
    keyed(app.button, f"won_{alex['id']}").click().run()
    assert database.get_athlete(events[0]["id"], alex["id"])["de_wins"] == 2
    assert "2 rounds passed" in _markup(app)
    assert not app.exception


@pytest.mark.parametrize(("role", "actor"), [("coach", "Sam"), ("admin", "Carmine"), ("coordinator", "Irina")])
def test_one_tap_loss_is_reversible_in_separate_corrections_for_all_staff(fast_scenario, role, actor):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    if role == "coordinator":
        nav_named(app, "coordinator_nav_").set_value("Live").run()
        nav_named(app, "live_view_").set_value("No Current Call").run()
    keyed(app.button, f"lost_{alex['id']}").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["active_state"] == "eliminated" and saved["last_de_result"] == "lost"
    assert alex["name"] not in _cards(app)
    assert not any(button.label in {"Confirm Lost", "Restore athlete"} for button in app.button)
    assert any(section.label == "Correct DE results" for section in app.expander)
    _correction(app, alex)
    restored = database.get_athlete(events[0]["id"], alex["id"])
    assert restored["active_state"] == "active" and restored["last_de_result"] == ""
    assert restored["de_coaches"] == ["Carmine", "Sam"]
    if role == "coordinator":
        nav_named(app, "live_view_").set_value("No Current Call").run()
    assert alex["name"] in _cards(app)
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert alex["name"] in _cards(observer)
    assert not app.exception and not observer.exception


def test_two_phones_tapping_the_same_rendered_win_cannot_add_two_rounds(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    first = open_board(meet["id"], meet["coach_token"], "Sam")
    second = open_board(meet["id"], meet["coach_token"], "Carmine")
    keyed(first.button, f"won_{alex['id']}").click().run()
    keyed(second.button, f"won_{alex['id']}").click().run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["de_wins"] == 1 and saved["active_state"] == "active"
    assert alex["name"] in _cards(second)
    assert second.error
    assert not first.exception and not second.exception


def test_accidental_win_can_be_undone_from_corrections_and_returns_to_work(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"won_{alex['id']}").click().run()
    assert alex["name"] in _cards(app)
    _correction(app, alex)
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["de_wins"] == 0 and saved["de_awaiting_next"] == 0
    assert saved["active_state"] == "active" and saved["de_coaches"] == ["Carmine", "Sam"]
    assert alex["name"] in _cards(app)
    assert "Next bout · 1 round passed" not in _markup(app)
    assert not any("Ready for next bout" in button.label for button in app.button)
    assert not app.exception


def test_fresh_call_updates_progressed_athlete_without_removing_progress(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"won_{alex['id']}").click().run()
    database.report_call(events[0]["id"], alex["id"], status="on_deck", location="S5", actor="Irina")
    app.run()
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["de_awaiting_next"] == 0 and saved["de_wins"] == 1
    assert alex["name"] in _cards(app)
    assert "S5" in "\n".join([*markdown_values(app), *(caption.value for caption in app.caption)])
    assert not any("Ready for next bout" in button.label for button in app.button)
    assert keyed(app.button, f"won_{alex['id']}")
    assert not app.exception


def test_live_keeps_progressed_athletes_in_their_assignment_sector(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    database.mark_bye(events[0]["id"], alex["id"], actor="Sam")
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Live").run()
    sector_markup = "\n".join(value for value in markdown_values(app) if "class='cc-sector" in value)
    assert "Sector P" in sector_markup and alex["name"] in sector_markup
    assert "Sector Q" in sector_markup and "Sector R" in sector_markup
    assert any("Junior Epee · 3 still in DE" in section.label for section in app.expander)
    assert alex["name"] in _markup(app)
    assert not any("Ready for next bout" in button.label for button in app.button)
    assert "1 bye" in _markup(app)
    assert not app.exception


def test_live_no_current_call_rotates_progressed_athletes_below_other_events(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex = athletes["TEST Alex"]
    database.mark_result(events[0]["id"], alex["id"], outcome="won", actor="Sam")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    nav_named(app, "coach_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("No Current Call").run()
    assert alex["name"] in _cards(app)
    assert "TEST Casey" in _cards(app)
    assert _cards(app).index("TEST Casey") < _cards(app).index("TEST Alex")
    assert keyed(app.button, f"won_{alex['id']}")
    assert keyed(app.button, f"lost_{alex['id']}")
    assert not any("Ready for next bout" in button.label for button in app.button)
    database.mark_result(events[0]["id"], athletes["TEST Robin"]["id"], outcome="lost", actor="Sam")
    app.run()
    nav_named(app, "live_view_").set_value("Out").run()
    out_cards = _cards(app)
    assert "TEST Robin" in out_cards
    assert not any(button.label.startswith("Restore") for button in app.button)
    assert any(section.label == "Correct DE results" for section in app.expander)
    assert not app.exception


def test_marking_next_afm_bout_preserves_progress_and_shared_result_controls(fast_scenario):
    database, meet, events, athletes = fast_scenario
    alex, robin = athletes["TEST Alex"], athletes["TEST Robin"]
    database.mark_result(events[0]["id"], alex["id"], outcome="won", actor="Sam")
    create_de_bout(database, events[0]["id"], alex["id"], robin["id"], actor="Irina", round_label="T16")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    saved = database.get_athlete(events[0]["id"], alex["id"])
    assert saved["de_wins"] == 1 and saved["de_awaiting_next"] == 0
    assert alex["name"] in _cards(app) and robin["name"] in _cards(app)
    assert not any("Ready for next bout" in button.label for button in app.button)
    assert "AFM vs AFM · TEST Robin · T16" in _markup(app)
    keyed(app.button, f"won_{alex['id']}").click().run()
    assert database.get_athlete(events[0]["id"], alex["id"])["de_wins"] == 2
    assert database.get_athlete(events[0]["id"], robin["id"])["active_state"] == "eliminated"
    assert not app.exception


def test_app_startup_hides_legacy_website_sector_without_excluding_real_ve_fencer(fast_scenario):
    database, meet, events, athletes = fast_scenario
    event = events[0]
    database.merge_import(event["id"], [
        {"athlete_id": "legacy_noise", "name": "Legacy Footer", "strip": "VE", "pod": "VE", "phase": "de"},
        {"athlete_id": "genuine_ve", "name": "TEST Genuine", "strip": "VE1", "pod": "VE", "phase": "de"},
    ], "Older import")
    rows = {row["athlete_key"]: row for row in database.list_athletes(event["id"])}
    noise, genuine = rows["legacy_noise"], rows["genuine_ve"]
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE athletes SET name = ? WHERE id = ?", ("fe melive.com", noise["id"]))
    database.assign_de_pods(event["id"], pods=["VE"], coaches=["Carmine", "Sam"], actor="Carmine")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    assert "fe melive.com" not in _cards(app)
    assert genuine["name"] in _cards(app)
    assert database.get_athlete(event["id"], noise["id"])["participation_status"] == "withdrawn"
    assert database.get_athlete(event["id"], genuine["id"])["participation_status"] == "active"
    nav_named(app, "coach_nav_").set_value("Live").run()
    sector_markup = "\n".join(value for value in markdown_values(app) if "class='cc-sector" in value)
    assert "Sector VE" in sector_markup and genuine["name"] in sector_markup
    assert "fe melive.com" not in sector_markup
    assert not app.exception
