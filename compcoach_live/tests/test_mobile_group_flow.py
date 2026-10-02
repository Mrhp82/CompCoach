"""The current bout closes immediately and the personal queue stays actionable."""

import pytest
import streamlit as st

from compcoach_live.de_bouts import create_de_bout
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import create_multi_event, keyed, markdown_values, open_board


@pytest.fixture
def group(tmp_path, monkeypatch):
    path = tmp_path / "mobile-group.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"])
    for event, table in zip(events, (
        "Name\tStrip #\nEXAMPLE Current\tP1\nEXAMPLE Waiting\tP1\nEXAMPLE Hole\tP1",
        "Name\tStrip #\nEXAMPLE Deck\tR1\nEXAMPLE Now\tR1",
    )):
        database.merge_import(event["id"], parse_pasted_table(table).records, "Carmine")
        pods = sorted({row["pod"] for row in database.list_athletes(event["id"])})
        database.assign_de_pods(event["id"], pods=pods, coaches=["Carmine", "Sam"], actor="Carmine")
    athletes = {row["name"]: row for event in events for row in database.list_athletes(event["id"])}
    for label, status, strip in (("Hole", "in_hole", "C2"), ("Deck", "on_deck", "D3"), ("Now", "now", "G4")):
        row = athletes[f"EXAMPLE {label}"]
        database.report_call(row["event_id"], row["id"], status=status, location=strip, actor="Irina")
    current = athletes["EXAMPLE Current"]
    database.cover_athlete(current["event_id"], current["id"], "Sam", "Sam", location="J4")
    yield database, meet, events, athletes
    st.cache_resource.clear()


def _coach(meet, actor="Sam"):
    return open_board(meet["id"], meet["coach_token"], actor)


def _cards(app):
    return "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_current_bout_has_first_visible_result_actions_and_releases_coach(group, outcome):
    database, meet, _events, athletes = group
    current = athletes["EXAMPLE Current"]
    app = _coach(meet)
    quick = keyed(app.button, f"current_{outcome}_{current['id']}")
    assert quick.label == outcome.title()
    order = [button.key for button in app.button]
    assert order.index(quick.key) < order.index(f"won_{athletes['EXAMPLE Now']['id']}")
    assert any(section.label == "Current bout details" for section in app.expander)
    quick.click().run()

    assert not app.exception
    saved = database.get_athlete(current["event_id"], current["id"])
    assert saved["last_de_result"] == outcome
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["call_status"] == "waiting" and saved["live_location"] == ""
    assert saved["active_state"] == ("active" if outcome == "won" else "eliminated")
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert status["is_available"] and not status["is_busy"]
    assert not any((button.key or "").startswith(f"current_{outcome}_{current['id']}") for button in app.button)
    assert not any(button.label == f"Confirm {outcome.title()}" for button in app.button)


def test_current_bout_is_kept_out_of_work_queue_and_calls_rank_across_events(group):
    _database, meet, _events, _athletes = group
    app = _coach(meet)
    cards = _cards(app)
    assert cards.index("EXAMPLE Now") < cards.index("EXAMPLE Deck") < cards.index("EXAMPLE Hole") < cards.index("EXAMPLE Waiting")
    # The current athlete remains editable directly below the busy hero, in
    # its own collapsed details, and does not reappear in the work queue.
    assert cards.count("EXAMPLE Current") == 1
    assert cards.index("EXAMPLE Current") < cards.index("EXAMPLE Now")
    assert not any(value.startswith("#### ⭐") for value in markdown_values(app))
    assert not app.exception


def test_current_bout_stale_result_on_second_phone_preserves_first_outcome(group):
    database, meet, _events, athletes = group
    current = athletes["EXAMPLE Current"]
    first, second = _coach(meet), _coach(meet)
    keyed(first.button, f"current_won_{current['id']}").click().run()
    keyed(second.button, f"current_lost_{current['id']}").click().run()
    saved = database.get_athlete(current["event_id"], current["id"])
    assert saved["de_wins"] == 1 and saved["last_de_result"] == "won"
    assert saved["active_state"] == "active"
    assert second.error
    assert not first.exception and not second.exception


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_current_afm_bout_fast_result_updates_both_fencers_and_releases_coverage(group, outcome):
    database, meet, _events, athletes = group
    current, opponent = athletes["EXAMPLE Current"], athletes["EXAMPLE Waiting"]
    create_de_bout(database, current["event_id"], current["id"], opponent["id"], actor="Irina", round_label="T32")
    app = _coach(meet)
    keyed(app.button, f"current_{outcome}_{current['id']}").click().run()
    winner = current if outcome == "won" else opponent
    loser = opponent if outcome == "won" else current
    saved_winner = database.get_athlete(winner["event_id"], winner["id"])
    saved_loser = database.get_athlete(loser["event_id"], loser["id"])
    assert saved_winner["de_wins"] == 1 and saved_winner["active_state"] == "active"
    assert saved_loser["active_state"] == "eliminated"
    assert saved_winner["covered_by"] == saved_loser["covered_by"] == ""
    assert saved_winner["covered_at"] is saved_loser["covered_at"] is None
    assert not app.exception


def test_accepted_takeover_has_priority_over_unclaimed_called_athletes(group):
    database, meet, _events, athletes = group
    current, waiting = athletes["EXAMPLE Current"], athletes["EXAMPLE Waiting"]
    database.mark_result(current["event_id"], current["id"], outcome="won", actor="Sam")
    database.take_over_athlete(waiting["event_id"], waiting["id"], "Sam", "Sam")
    app = _coach(meet)
    cards = _cards(app)
    assert cards.index("EXAMPLE Waiting") < cards.index("EXAMPLE Now")
    assert cards.index("EXAMPLE Now") < cards.index("EXAMPLE Deck") < cards.index("EXAMPLE Hole")
    assert cards.index("EXAMPLE Hole") < cards.index("EXAMPLE Current")
    assert not app.exception


def test_current_afm_result_rejects_newer_opponent_state_on_another_phone(group):
    database, meet, _events, athletes = group
    current, opponent = athletes["EXAMPLE Current"], athletes["EXAMPLE Waiting"]
    create_de_bout(database, current["event_id"], current["id"], opponent["id"], actor="Irina", round_label="T32")
    app = _coach(meet)
    database.report_call(opponent["event_id"], opponent["id"], status="on_deck", location="R7", actor="Irina")
    keyed(app.button, f"current_won_{current['id']}").click().run()
    saved_current = database.get_athlete(current["event_id"], current["id"])
    saved_opponent = database.get_athlete(opponent["event_id"], opponent["id"])
    assert saved_current["de_wins"] == 0 and saved_current["covered_by"] == "Sam"
    assert saved_opponent["active_state"] == "active" and saved_opponent["live_location"] == "R7"
    assert app.error and not app.exception


def test_pool_help_control_explains_emergency_use_while_remaining_available(group):
    database, meet, _events, _athletes = group
    event = database.add_meet_event(meet["id"], "Cadet Women")
    database.merge_import(
        event["id"], parse_pasted_table("Name\tStrip #\tPool #\nEXAMPLE Pool\tF3\t7").records, "Carmine",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(event["id"], [athlete["id"]], main_coach="Sam", actor="Carmine")
    app = _coach(meet)
    caption = next(item.value for item in app.caption if "Pools: use Need help" in item.value)
    assert "real emergency" in caption and "All coaches are busy" in caption
    assert not keyed(app.button, f"help_request_{athlete['id']}").disabled
    assert not app.exception
