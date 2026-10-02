"""Emergency help stays visible on DE cards and the current-bout shortcut."""

from __future__ import annotations

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import create_multi_event, keyed, markdown_values, open_board
from compcoach_live.training import get_training, join_training, start_training, tick_training


@pytest.fixture(params=["real", "practice"])
def board(tmp_path, monkeypatch, request):
    path = tmp_path / "help-access.sqlite"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database, ["Junior Epee", "Cadet Epee"],
        coaches=("Carmine", "Sam"), coordinators=("Irina",),
    )
    actor = "Sam"
    if request.param == "practice":
        hub = start_training(database, meet["id"], actor="Carmine")
        meet = join_training(database, hub["id"], "Sam")
        # Reach the DE-call lesson before adding our separate test athletes.
        # Its unrelated waiting target keeps the live simulator active without
        # introducing the earlier Pool emergency into this coverage scenario.
        assert tick_training(database, meet["id"], actor, "My Group")["stage"] == 1
        assert tick_training(database, meet["id"], actor, "Live")["stage"] == 2
        target_id = get_training(database, meet["id"])["state"]["primary_id"]
        primary = next(
            row for child in database.list_meet_events(meet["id"])
            for row in database.list_athletes(child["id"]) if row["id"] == target_id
        )
        database.acknowledge_help(primary["event_id"], primary["id"], actor)
        assert tick_training(database, meet["id"], actor, "My Group")["stage"] == 3
        database.set_pool_result(primary["event_id"], primary["id"], wins=3, losses=3, actor=actor)
        assert tick_training(database, meet["id"], actor, "My Group")["stage"] == 4
        database.set_coach_availability(meet["id"], actor, True, actor)
        assert tick_training(database, meet["id"], actor, "My Group")["stage"] == 5
        assert tick_training(database, meet["id"], actor, "Live")["stage"] == 6
        events = database.list_meet_events(meet["id"])
    database.merge_import(
        events[0]["id"],
        parse_pasted_table("Name\tStrip #\nEXAMPLE Current\tP1").records, actor,
    )
    database.merge_import(
        events[1]["id"],
        parse_pasted_table("Name\tStrip #\nEXAMPLE Help\tQ1").records, actor,
    )
    rows = {row["name"]: row for event in events for row in database.list_athletes(event["id"])}
    current, target = rows["EXAMPLE Current"], rows["EXAMPLE Help"]
    database.assign_de_pods(current["event_id"], pods=["P"], coaches=[actor], actor=actor)
    database.assign_de_pods(target["event_id"], pods=["Q"], coaches=[actor], actor=actor)
    database.report_call(current["event_id"], current["id"], status="now", location="J2", actor=actor)
    database.report_call(target["event_id"], target["id"], status="now", location="K4", actor=actor)
    yield database, database.get_meet(meet["id"]), actor, current, target
    st.cache_resource.clear()


def _open(board, role, view):
    _, meet, actor, _, _ = board
    who = meet["coordinators"][0] if role == "coordinator" else actor
    app = open_board(meet["id"], meet[f"{role}_token"], who)
    if view == "Team situation":
        keyed(app.get("button_group"), f"{role}_nav_").set_value("Team situation").run()
    assert not app.exception
    return app, who


def _expander_ancestors(app, widget_key):
    """AppTest includes closed contents; assert their actual position instead."""
    matches = []

    def visit(node, ancestors):
        if getattr(node, "type", "") == "button" and getattr(node, "key", None) == widget_key:
            matches.append(ancestors)
        for child in getattr(node, "children", {}).values():
            next_ancestors = [*ancestors, child] if getattr(child, "type", "") == "expander" else ancestors
            visit(child, next_ancestors)

    visit(app._tree, [])
    assert len(matches) == 1, f"Expected one help button {widget_key!r}, found {len(matches)}"
    return matches[0]


@pytest.mark.parametrize("role,view", [
    ("coach", "My Group"), ("coach", "Team situation"),
    ("admin", "My Group"), ("admin", "Team situation"),
    ("coordinator", "Team situation"),
])
def test_need_help_is_directly_visible_for_each_operational_role_and_view(board, role, view):
    database, _, _, _, target = board
    app, who = _open(board, role, view)
    key = f"help_request_{target['id']}"
    help_button = keyed(app.button, key)
    assert help_button.label == "🚨 Need help now" and not help_button.disabled
    assert not _expander_ancestors(app, key)
    assert not any("Confirm" in button.label for button in app.button)
    help_button.click().run()
    saved = database.get_athlete(target["event_id"], target["id"])
    assert saved["help_requested_by"] == who and saved["help_requested_at"]
    assert saved["help_location"] == "K4" and saved["active_state"] == "active"
    assert saved["de_wins"] == 0 and saved["de_byes"] == 0
    assert not app.exception


@pytest.mark.parametrize("role,view", [
    ("coach", "My Group"), ("coach", "Team situation"),
    ("admin", "My Group"), ("admin", "Team situation"),
])
def test_busy_hero_keeps_help_next_to_results_without_opening_details(board, role, view):
    database, _, actor, current, _ = board
    database.cover_athlete(current["event_id"], current["id"], actor, actor, location="J2")
    app, _ = _open(board, role, view)
    key = f"current_help_request_{current['id']}"
    help_button = keyed(app.button, key)
    assert not _expander_ancestors(app, key)
    assert not help_button.disabled
    assert keyed(app.button, f"current_won_{current['id']}")
    assert keyed(app.button, f"current_lost_{current['id']}")
    help_button.click().run()
    saved = database.get_athlete(current["event_id"], current["id"])
    assert saved["help_requested_by"] == actor and saved["help_location"] == "J2"
    assert saved["covered_by"] == actor and saved["covered_at"]
    assert saved["last_de_result"] == "" and not app.exception


def test_busy_coach_can_request_help_for_separate_uncovered_athlete(board):
    database, meet, actor, current, target = board
    database.cover_athlete(current["event_id"], current["id"], actor, actor, location="J2")
    app, _ = _open(board, "coach", "Team situation")
    keyed(app.button, f"help_request_{target['id']}").click().run()
    saved_target = database.get_athlete(target["event_id"], target["id"])
    saved_current = database.get_athlete(current["event_id"], current["id"])
    assert saved_target["help_requested_by"] == actor and saved_target["help_location"] == "K4"
    assert not saved_target["covered_by"]
    assert saved_current["covered_by"] == actor and saved_current["live_location"] == "J2"
    assert not saved_current["help_requested_at"]
    observer = open_board(meet["id"], meet["coordinator_token"], meet["coordinators"][0])
    assert not observer.exception
    alerts = "\n".join(markdown_values(observer))
    assert "EXAMPLE Help" in alerts and "K4" in alerts
    assert keyed(observer.button, f"help_ack_{target['id']}")


def test_help_snapshot_from_stale_phone_preserves_newer_field_update(board):
    database, _, _, _, target = board
    app, _ = _open(board, "coach", "My Group")
    database.report_call(target["event_id"], target["id"], status="on_deck", location="L3", actor="Sam")
    before = database.get_athlete(target["event_id"], target["id"])
    keyed(app.button, f"help_request_{target['id']}").click().run()
    saved = database.get_athlete(target["event_id"], target["id"])
    assert not saved["help_requested_at"] and saved["version"] == before["version"]
    assert saved["call_status"] == "on_deck" and saved["live_location"] == "L3"
    assert app.error and not app.exception
