"""Unknown pool totals finish duty without fabricating a result or losing corrections."""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_group_named, button_named, create_multi_event, keyed,
    markdown_values, nav_named, open_board,
)


@pytest.fixture
def pool_board(tmp_path, monkeypatch):
    path = tmp_path / "pool-unknown-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\tPool #\nRIVERA Rowan\tB1\t1").records,
        "Carmine",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Sam", side_coach="Carmine", actor="Carmine",
    )
    yield database, meet, event, database.get_athlete(event["id"], athlete["id"])
    st.cache_resource.clear()


def _open(meet, role="coach", actor="Sam", *, team=False):
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    if team and role != "coordinator":
        nav_named(app, f"{role}_nav_").set_value("Live").run()
    return app


def _cards(app):
    return "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)


def test_coach_one_tap_finishes_unknown_pool_and_moves_to_collapsed_completed_list(pool_board):
    database, meet, event, athlete = pool_board
    app = _open(meet)
    keyed(app.button, f"pool_done_unknown_{athlete['id']}").click().run()
    assert not app.exception
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["pool_results_unknown"] == 1
    assert saved["pool_wins"] is saved["pool_losses"] is None
    assert saved["active_state"] == "active"
    assert saved["main_coach"] == "Sam" and saved["side_coach"] == "Carmine"
    fresh = _open(meet)
    completed = next(section for section in fresh.expander if section.label == "Completed pool results · 1")
    assert not completed.proto.expanded
    assert "RIVERA Rowan" in "\n".join(markdown_values(completed))
    assert "Pool done · Not sure about results" in "\n".join(markdown_values(completed))
    assert any("0 active work · 1 completed pool" in item.value for item in fresh.caption)
    assert not any(button.label == "🚨 Need help now" for button in fresh.button)
    assert not any("0 W" in value or "0 L" in value for value in markdown_values(fresh))
    assert keyed(fresh.button, f"pool_reopen_{athlete['id']}").label == "Reopen pool"
    assert not any(button.label.startswith("Confirm pool") for button in fresh.button)


@pytest.mark.parametrize("role,actor", [("coach", "Sam"), ("admin", "Carmine"), ("coordinator", "Irina")])
def test_all_staff_roles_finish_from_team_and_result_stays_out_of_active_filters(pool_board, role, actor):
    database, meet, event, athlete = pool_board
    app = _open(meet, role, actor, team=True)
    keyed(app.button, f"pool_done_unknown_{athlete['id']}").click().run()
    assert not app.exception
    assert database.get_athlete(event["id"], athlete["id"])["pool_result_by"] == actor
    app = _open(meet, role, actor, team=True)
    assert athlete["name"] not in _cards(app)
    summary = next(section for section in app.expander if section.label == "Pool results")
    summary_text = "\n".join(markdown_values(summary))
    assert "Pool done · Results unknown · 1" in summary_text
    assert "Not sure about results" in summary_text
    assert not any("Awaiting result:" in item.value for item in summary.caption)
    assert any("0 results entered" in section.label and "results unknown" in section.label for section in summary.expander)
    assert not any("cc-pool-score" in value for value in markdown_values(summary))
    assert keyed(summary.button, f"pool_reopen_{athlete['id']}")


def test_unknown_can_replace_a_confirmed_tally_then_later_accept_correct_numbers(pool_board):
    database, meet, event, athlete = pool_board
    database.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Sam")
    app = _open(meet)
    keyed(app.button, f"pool_done_unknown_{athlete['id']}").click().run()
    assert not app.exception
    app = _open(meet)
    assert not any("4 W" in value or "2 L" in value for value in markdown_values(app))
    assert button_group_named(app, "Wins").value == 0
    button_group_named(app, "Wins").set_value(3)
    button_group_named(app, "Losses").set_value(3)
    button_named(app, "Save pool result").click().run()
    saved = database.get_athlete(event["id"], athlete["id"])
    assert (saved["pool_wins"], saved["pool_losses"], saved["pool_results_unknown"]) == (3, 3, 0)
    fresh = _open(meet)
    assert not any("Pool done · Not sure about results</div>" in value for value in markdown_values(fresh))
    assert any("Pools: 3 W · 3 L" in value for value in markdown_values(fresh))
    assert not any(button.label == "Reopen pool" for button in fresh.button)
    assert not fresh.exception


def test_reopen_returns_pool_to_working_list_and_availability_requires_open_duty_override(pool_board):
    database, meet, event, athlete = pool_board
    database.set_pool_done_unknown(event["id"], athlete["id"], "Sam")
    app = _open(meet)
    assert not keyed(app.button, "availability_on_").disabled
    keyed(app.button, f"pool_reopen_{athlete['id']}").click().run()
    assert not app.exception
    fresh = _open(meet)
    assert not any(section.label.startswith("Completed pool results") for section in fresh.expander)
    assert any("1 active work · 0 completed pools" in item.value for item in fresh.caption)
    assert keyed(fresh.button, "availability_on_").disabled
    assert keyed(fresh.button, f"pool_done_unknown_{athlete['id']}")
    assert any(button.label == "🚨 Need help now" for button in fresh.button)
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["pool_results_unknown"] == 0 and saved["pool_result_at"] is None


def test_coordinator_location_picker_excludes_finished_pool_until_reopened(pool_board):
    database, meet, event, athlete = pool_board
    database.set_pool_done_unknown(event["id"], athlete["id"], "Sam")
    app = _open(meet, "coordinator", "Irina", team=True)
    report = next(section for section in app.expander if section.label == "➕ Report athlete location")
    assert not any(selector.label == "Athlete" for selector in report.selectbox)
    assert any("No active athletes are available." in item.value for item in report.info)
    keyed(app.button, f"pool_reopen_{athlete['id']}").click().run()
    app = _open(meet, "coordinator", "Irina", team=True)
    report = next(section for section in app.expander if section.label == "➕ Report athlete location")
    selector = next(selector for selector in report.selectbox if selector.label == "Athlete")
    assert selector.value == athlete["id"]
    assert not app.exception


def test_stale_unknown_action_does_not_replace_newer_confirmed_result(pool_board):
    database, meet, event, athlete = pool_board
    stale = _open(meet)
    database.set_pool_result(event["id"], athlete["id"], wins=5, losses=1, actor="Carmine")
    keyed(stale.button, f"pool_done_unknown_{athlete['id']}").click().run()
    assert not stale.exception and stale.error
    saved = database.get_athlete(event["id"], athlete["id"])
    assert (saved["pool_wins"], saved["pool_losses"], saved["pool_results_unknown"]) == (5, 1, 0)


def test_completed_unknown_history_does_not_impede_direct_elimination(pool_board):
    database, meet, event, athlete = pool_board
    database.set_pool_done_unknown(event["id"], athlete["id"], "Sam")
    database.merge_import(
        event["id"], parse_pasted_table("Name\tStrip #\nRIVERA Rowan\tC1").records,
        "Carmine",
    )
    database.assign_de_pods(event["id"], pods=["C"], coaches=["Sam"], actor="Carmine")
    app = _open(meet)
    assert keyed(app.button, f"won_{athlete['id']}")
    assert "RIVERA Rowan" in _cards(app)
    assert any("Pool done · Not sure about results</div>" in value for value in markdown_values(app))
    assert not any(button.label in {"Reopen pool", "Pool done · Not sure about results"} for button in app.button)
    team = _open(meet, team=True)
    summary = next(section for section in team.expander if section.label == "Pool results")
    assert any("Not sure about results" in value for value in markdown_values(summary))
    assert not summary.button
    assert not app.exception and not team.exception


def test_coordinator_can_override_existing_results_in_shared_completed_section(pool_board):
    database, meet, event, athlete = pool_board
    database.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Sam")
    app = _open(meet, "coordinator", "Irina", team=True)
    summary = next(section for section in app.expander if section.label == "Pool results")
    assert keyed(summary.button, f"pool_done_unknown_{athlete['id']}")
    keyed(summary.button, f"pool_done_unknown_{athlete['id']}").click().run()
    assert not app.exception
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["pool_results_unknown"] == 1 and saved["pool_result_by"] == "Irina"
