"""A shared warning can become a real, reversible coverage commitment.

Taking over before an athlete is called must not invent a bout strip or a
physical busy timer. Render-time snapshots protect competing phone taps.
"""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    create_multi_event, keyed, markdown_values, open_board,
)


@pytest.fixture
def takeover_scenario(tmp_path, monkeypatch):
    path = tmp_path / "takeover-phones.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database, ["Junior Epee", "Cadet Epee"],
        coaches=("Carmine", "Sam", "Vivien", "Jordan"),
    )
    for child, table in zip(events, (
        "Name\tStrip #\nTEST Busy\tB1\nTEST Waiting\tF1",
        "Name\tStrip #\nTEST Cross Event\tP1",
    )):
        database.merge_import(child["id"], parse_pasted_table(table).records, "Carmine")
        pods = sorted({row["pod"] for row in database.list_athletes(child["id"])})
        database.assign_de_pods(
            child["id"], pods=pods, coaches=["Vivien"], actor="Carmine",
        )
    rows = {row["name"]: row for child in events for row in database.list_athletes(child["id"])}
    busy = rows["TEST Busy"]
    database.cover_athlete(busy["event_id"], busy["id"], "Vivien", "Irina", location="B1")
    yield database, meet, events, rows
    st.cache_resource.clear()


def _text(app):
    return "\n".join([
        *markdown_values(app), *(item.value for item in app.caption),
        *(item.value for item in app.info), *(item.value for item in app.warning),
        *(item.value for item in app.success),
    ])


def _cards(app):
    return "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)


def _button_present(app, key):
    return any(button.key == key for button in app.button)


def _saved(database, athlete):
    return database.get_athlete(athlete["event_id"], athlete["id"])


def _available_banner(app):
    return "\n".join(value for value in markdown_values(app)
                     if "<div class='cc-available-coaches-banner'>" in value)


def test_available_coach_takeover_removes_green_for_everyone_until_release(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    watcher = open_board(meet["id"], meet["coordinator_token"], "Irina")
    assert "Sam" in _available_banner(app) and "Sam" in _available_banner(watcher)

    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    watcher.run()
    assert "Sam" not in _available_banner(app) and "Sam" not in _available_banner(watcher)
    assert "Takeover accepted" in _text(app)
    available_button = keyed(app.button, f"availability_on_{meet['id']}_")
    assert available_button.disabled
    assert not any((widget.key or "").startswith("availability_override_") for widget in app.checkbox)
    sam = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert not sam["is_available"] and not sam["is_busy"] and sam["busy_since"] is None

    keyed(app.button, f"global_release_takeover_{athlete['id']}").click().run()
    watcher.run()
    assert "Sam" in _available_banner(app) and "Sam" in _available_banner(watcher)
    assert not app.exception and not watcher.exception


@pytest.mark.parametrize(("role", "actor"), [("coach", "Sam"), ("admin", "Carmine")])
def test_free_coach_can_take_over_not_called_warning_in_one_tap(takeover_scenario, role, actor):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    assert any("TEST Waiting" in item.value and "Not called yet" in item.value
               and "Actual strip TBD" in item.value for item in app.info)
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()

    saved = _saved(database, athlete)
    assert saved["takeover_coach"] == actor
    assert saved["takeover_at"] and saved["takeover_by"] == actor
    assert saved["de_coaches"] == ["Vivien"]
    assert (saved["pod"], saved["source_strip"], saved["live_location"], saved["call_status"]) == (
        "F", "F1", "", "waiting",
    )
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert athlete["name"] in _cards(app)
    assert f"Taken by {actor}" in _text(app)
    assert _button_present(app, f"global_release_takeover_{athlete['id']}")
    assert not _button_present(app, f"global_takeover_{athlete['id']}")
    assert not app.exception


def test_non_coach_coordinator_sees_warning_without_self_takeover_button(takeover_scenario):
    _, meet, _, rows = takeover_scenario
    app = open_board(meet["id"], meet["coordinator_token"], "Irina")
    assert "TEST Waiting" in _text(app)
    for name in ("TEST Waiting", "TEST Cross Event"):
        assert not _button_present(app, f"global_takeover_{rows[name]['id']}")
    assert not app.exception


def test_currently_busy_coach_cannot_accept_another_warning(takeover_scenario):
    _, meet, _, rows = takeover_scenario
    app = open_board(meet["id"], meet["coach_token"], "Vivien")
    assert keyed(app.button, f"global_takeover_{rows['TEST Waiting']['id']}").disabled
    assert "You are busy with another athlete." in _text(app)
    assert not app.exception


def test_accepting_current_call_preserves_actual_strip_and_timestamp(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    before = database.report_call(
        athlete["event_id"], athlete["id"], status="on_deck", location="C3", actor="Irina",
    )
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    saved = _saved(database, athlete)
    for field in ("call_status", "live_location", "reported_at", "reported_by", "pod", "source_strip"):
        assert saved[field] == before[field]
    assert saved["takeover_coach"] == "Sam"
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert "On deck · Actual strip C3 · Pod F" in _text(app)
    assert not app.exception


@pytest.mark.parametrize(("role", "actor"), [
    ("coach", "Jordan"), ("admin", "Carmine"), ("coordinator", "Irina"),
])
def test_takeover_is_shared_across_roles_and_events_after_refresh(takeover_scenario, role, actor):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Cross Event"]
    watcher = open_board(meet["id"], meet[f"{role}_token"], actor)
    taker = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(taker.button, f"global_takeover_{athlete['id']}").click().run()
    watcher.run()
    assert _saved(database, athlete)["takeover_coach"] == "Sam"
    assert any(athlete["name"] in item.value and "Taken by Sam" in item.value
               for item in [*watcher.info, *watcher.warning, *watcher.success])
    assert not _button_present(watcher, f"global_takeover_{athlete['id']}")
    assert athlete["name"] in _cards(taker)
    assert "Cadet Epee" in _text(taker)
    assert not taker.exception and not watcher.exception


def test_stale_second_phone_cannot_replace_first_takeover(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    first = open_board(meet["id"], meet["coach_token"], "Sam")
    stale = open_board(meet["id"], meet["coach_token"], "Jordan")
    keyed(first.button, f"global_takeover_{athlete['id']}").click().run()
    keyed(stale.button, f"global_takeover_{athlete['id']}").click().run()
    assert _saved(database, athlete)["takeover_coach"] == "Sam"
    assert stale.error
    assert not stale.exception and not first.exception


def test_stale_free_coach_button_cannot_takeover_after_becoming_busy_elsewhere(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete, elsewhere = rows["TEST Waiting"], rows["TEST Cross Event"]
    stale = open_board(meet["id"], meet["coach_token"], "Sam")
    database.cover_athlete(elsewhere["event_id"], elsewhere["id"], "Sam", "Irina", location="P3")
    keyed(stale.button, f"global_takeover_{athlete['id']}").click().run()
    assert _saved(database, athlete)["takeover_coach"] == ""
    assert _saved(database, elsewhere)["covered_by"] == "Sam"
    assert stale.error and not stale.exception


def test_takeover_owner_can_cancel_without_touching_calls_or_assignments(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    keyed(app.button, f"global_release_takeover_{athlete['id']}").click().run()
    saved = _saved(database, athlete)
    assert saved["takeover_coach"] == "" and saved["takeover_at"] is None
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["de_coaches"] == ["Vivien"]
    assert saved["pod"] == "F" and saved["live_location"] == ""
    assert saved["call_status"] == "waiting" and saved["last_de_result"] == ""
    assert athlete["name"] not in _cards(app)
    assert _button_present(app, f"global_takeover_{athlete['id']}")
    assert not app.exception


def test_physical_arrival_converts_commitment_to_real_busy_timer(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    strip_prefix = f"live_personal_{athlete['id']}_strip_v"
    keyed(app.text_input, strip_prefix).set_value("D4")
    keyed(app.button, f"live_personal_{athlete['id']}_busy").click().run()
    saved = _saved(database, athlete)
    assert saved["takeover_coach"] == "" and saved["takeover_at"] is None
    assert saved["covered_by"] == "Sam" and saved["covered_at"]
    assert saved["live_location"] == "D4" and saved["pod"] == "F"
    assert "with **TEST Waiting**" in _text(app)
    assert "Actual strip D4" in _text(app)
    assert not app.exception


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_result_clears_commitment_and_returns_taker_to_available(takeover_scenario, outcome):
    database, meet, _, rows = takeover_scenario
    athlete = rows["TEST Waiting"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    keyed(app.button, f"{outcome}_{athlete['id']}").click().run()
    saved = _saved(database, athlete)
    assert saved["takeover_coach"] == "" and saved["takeover_at"] is None
    assert saved["last_de_result"] == outcome
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    sam = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert sam["is_available"]
    assert not app.exception


def test_result_on_promised_athlete_does_not_free_coach_physically_busy_elsewhere(takeover_scenario):
    database, meet, _, rows = takeover_scenario
    athlete, elsewhere = rows["TEST Waiting"], rows["TEST Cross Event"]
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"global_takeover_{athlete['id']}").click().run()
    database.cover_athlete(elsewhere["event_id"], elsewhere["id"], "Sam", "Irina", location="P3")
    app.run()
    assert any("TEST Waiting" in item.value and "Taken by Sam, who is now busy elsewhere" in item.value
               for item in [*app.info, *app.warning])
    keyed(app.button, f"won_{athlete['id']}").click().run()
    assert _saved(database, athlete)["takeover_coach"] == ""
    saved_elsewhere = _saved(database, elsewhere)
    assert saved_elsewhere["covered_by"] == "Sam" and saved_elsewhere["live_location"] == "P3"
    sam = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert sam["is_busy"] and not sam["is_available"]
    assert not app.exception
