"""A saved call is shared across views, phones and autonomous lesson progress."""

import ast
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
import streamlit as st

from compcoach_live import training
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB, CompCoachError
from compcoach_live.tests.test_app_smoke import (
    button_named,
    keyed,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "cross-view-sync.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _seed_real(database):
    meet = database.create_meet(
        "Actual competition", ["Sam", "Taylor"], ["Jordan"],
        first_event_name="Cadet Epee",
    )
    event = database.list_meet_events(meet["id"])[0]
    database.merge_import(
        event["id"], parse_pasted_table("Name\tStrip #\nEXAMPLE Riley\tB1").records, "Jordan",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_de_athletes(
        event["id"], [athlete["id"]], coaches=["Sam", "Taylor"], actor="Jordan",
    )
    return meet, event, athlete


def _visible_text(app):
    return "\n".join([
        *markdown_values(app),
        *(item.value for item in app.caption),
        *(item.value for item in app.info),
        *(item.value for item in app.success),
        *(item.value for item in app.warning),
    ])


def _native_call(app, athlete, prefix, status, strip=None):
    base = f"live_{prefix}_{athlete['id']}"
    if strip is not None:
        keyed(app.text_input, f"{base}_strip_v").set_value(strip).run()
    keyed(app.button, f"{base}_call_{status}").click().run()
    assert not app.exception


def _invoke_saved_phone_heartbeat(database, app, meet):
    """Run the real silent heartbeat once; AppTest does not run browser timers.

    The heartbeat uses this phone's rendered revision and its actual shared DB.
    If it requests an app refresh, execute that full AppTest render explicitly.
    """
    source = Path(__file__).parents[1] / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"live_refresh_fragment", "current_training_view"}
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(functions) == 2
    for function in functions:
        function.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    redraws = []
    state = dict(app.session_state.filtered_state)
    surface = SimpleNamespace(
        session_state=state, rerun=lambda **options: redraws.append(options),
        toast=lambda _message: None,
    )
    namespace = {"st": surface, "db": database, "time": time, "CompCoachError": CompCoachError}
    exec(compile(module, "actual-app-heartbeat", "exec"), namespace)
    epoch = state[f"live_refresh_epoch_{meet['id']}_coach"]
    rendered = state[f"live_revision_{meet['id']}_coach"]
    namespace["live_refresh_fragment"](
        meet["id"], "coach", "Taylor", rendered, "", False, None, epoch,
    )
    assert redraws == [{"scope": "app"}]
    app.run()
    assert not app.exception


def test_real_call_updates_both_views_and_another_phone_without_a_repeat(database):
    meet, event, athlete = _seed_real(database)
    first = open_board(meet["id"], meet["coach_token"], "Sam")
    second = open_board(meet["id"], meet["coach_token"], "Taylor")
    nav_named(first, "coach_nav_").set_value("Live").run()
    nav_named(first, "live_view_").set_value("No Current Call").run()
    _native_call(first, athlete, "live", "on_deck", "J4")
    saved = database.get_athlete(event["id"], athlete["id"])
    assert (saved["call_status"], saved["live_location"]) == ("on_deck", "J4")

    nav_named(first, "coach_nav_").set_value("My Group").run()
    assert "Actual strip <b>J4</b>" in _visible_text(first)
    assert "On deck" in _visible_text(first)
    assert not any(button.key == f"live_personal_{athlete['id']}_call_on_deck" for button in first.button)
    assert database.get_athlete(event["id"], athlete["id"]) == saved

    _invoke_saved_phone_heartbeat(database, second, meet)
    assert "Actual strip <b>J4</b>" in _visible_text(second)
    assert "On deck" in _visible_text(second)
    assert database.get_athlete(event["id"], athlete["id"]) == saved

    # A subsequent personal update is likewise current in the shared view.
    _native_call(first, athlete, "personal", "now")
    latest = database.get_athlete(event["id"], athlete["id"])
    assert latest["call_status"] == "now" and latest["live_location"] == "J4"
    nav_named(first, "coach_nav_").set_value("Live").run()
    nav_named(first, "live_view_").set_value("Uncovered").run()
    assert "Actual strip <b>J4</b>" in _visible_text(first)
    assert not any(button.key == f"live_live_{athlete['id']}_call_now" for button in first.button)
    learner_calls = [
        action for action in database.recent_actions(event["id"])
        if action["action"] == "live_update" and action["actor"] == "Sam"
    ]
    assert len(learner_calls) == 2
    assert not first.exception


def test_stale_other_phone_cannot_close_bout_after_a_new_shared_call(database):
    meet, event, athlete = _seed_real(database)
    first = open_board(meet["id"], meet["coach_token"], "Sam")
    second = open_board(meet["id"], meet["coach_token"], "Taylor")
    stale_result = keyed(second.button, f"lost_{athlete['id']}")
    nav_named(first, "coach_nav_").set_value("Live").run()
    nav_named(first, "live_view_").set_value("No Current Call").run()
    _native_call(first, athlete, "live", "on_deck", "J4")
    current = database.get_athlete(event["id"], athlete["id"])
    stale_result.click().run()
    assert not second.exception and second.error
    assert database.get_athlete(event["id"], athlete["id"]) == current
    assert current["active_state"] == "active" and not current["last_de_result"]


def _call_lesson(database):
    source = database.create_meet("Real competition", ["Alex", "Robin"], [])
    hub = training.start_training(database, source["id"], actor="Alex")
    run = training.join_training(database, hub["id"], "Alex")

    def tick(view="My Group"):
        return training.tick_training(database, run["id"], actor="Alex", view=view)

    assert tick()["stage"] == 1
    assert tick("Live")["stage"] == 2
    state = training.get_training(database, run["id"])["state"]
    athlete = next(
        row for event in database.list_meet_events(run["id"])
        for row in database.list_athletes(event["id"])
        if row["id"] == state["primary_id"]
    )
    database.acknowledge_help(athlete["event_id"], athlete["id"], "Alex")
    assert tick()["stage"] == 3
    database.set_pool_result(athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert tick()["stage"] == 4
    database.set_coach_availability(run["id"], "Alex", True, "Alex")
    assert tick()["stage"] == 5
    assert tick("Live")["stage"] == 6
    state = training.get_training(database, run["id"])["state"]
    target = next(
        row for event in database.list_meet_events(run["id"])
        for row in database.list_athletes(event["id"])
        if row["id"] == state["primary_id"]
    )
    return run, target


@pytest.mark.parametrize("method,strip", [("native", ""), ("native", "C3"), ("quick", "C3")])
def test_team_call_advances_training_immediately_and_my_group_uses_same_record(database, method, strip):
    run, athlete = _call_lesson(database)
    app = open_board(run["id"], run["coach_token"], "Alex")
    nav_named(app, "coach_nav_").set_value("Live").run()
    if method == "native":
        nav_named(app, "live_view_").set_value("No Current Call").run()
        _native_call(app, athlete, "live", "on_deck", strip)
    else:
        keyed(app.selectbox, "call_athlete_").select(athlete["id"]).run()
        nav_named(app, "call_status_").set_value("On Deck").run()
        keyed(app.text_input, "call_location_").set_value(strip).run()
        button_named(app, "Publish update").click().run()
    assert not app.exception
    metadata = training.get_training(database, run["id"])
    assert metadata["stage"] == 7
    assert nav_named(app, "coach_nav_").value == "Live"
    saved = database.get_athlete(athlete["event_id"], athlete["id"])
    assert (saved["call_status"], saved["live_location"]) == ("on_deck", strip)
    learner_calls = [
        action for action in database.recent_actions(athlete["event_id"])
        if action["action"] == "live_update" and action["actor"] == "Alex"
        and action["athlete_id"] == athlete["id"]
    ]
    assert len(learner_calls) == 1
    assert any("Step 8 of" in element.proto.body for element in app.get("html"))

    nav_named(app, "coach_nav_").set_value("My Group").run()
    assert not app.exception
    assert training.get_training(database, run["id"])["stage"] == 7
    assert database.get_athlete(athlete["event_id"], athlete["id"]) == saved
    assert f"Actual strip <b>{strip or 'TBD'}</b>" in _visible_text(app)
    assert "On deck" in _visible_text(app)
    assert not any(button.key == f"live_personal_{athlete['id']}_call_on_deck" for button in app.button)
