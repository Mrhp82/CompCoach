"""The pool-result lesson names the return route from the shared team view."""

import pytest
import streamlit as st

from compcoach_live import training
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import button_named, keyed, nav_named, open_board, query_value


@pytest.fixture
def database(tmp_path,monkeypatch):
    path = tmp_path / "pool-return.sqlite"
    monkeypatch.setenv("COMPCOACH_DB_PATH",str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN","4321")
    for variable in ("COMPCOACH_DATABASE_URL","COMPCOACH_REQUIRE_CLOUD","COMPCOACH_PUBLIC_URL"):
        monkeypatch.delenv(variable,raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _lesson(database,app):
    return training.get_training(database,query_value(app,"event"))


def _primary(database,lesson):
    return next(athlete for event in database.list_meet_events(lesson["meet_id"])
                for athlete in database.list_athletes(event["id"])
                if athlete["id"] == lesson["state"]["primary_id"])


def test_coach_follows_return_to_my_group_shortcut_then_records_pool_result(database):
    source = database.create_meet("Actual competition",["Jordan"],[])
    hub = training.start_training(database,source["id"],actor="Jordan",open_entry=True)
    app = open_board(hub["id"],hub["coach_token"])
    next(item for item in app.text_input if item.label == "Your name").set_value("Casey")
    button_named(app,"Start my practice").click().run()
    assert not app.exception and _lesson(database,app)["stage"] == 1
    nav_named(app,"coach_nav_").set_value("Live").run()
    assert _lesson(database,app)["stage"] == 2
    primary = _primary(database,_lesson(database,app))
    keyed(app.button,f"help_ack_{primary['id']}").click().run()
    lesson = _lesson(database,app)
    assert lesson["stage"] == 3 and nav_named(app,"coach_nav_").value == "Live"
    prefix = f"Return to My Group. For {primary['name']}, open Add pool result"
    assert lesson["instruction"].startswith(prefix)
    assert lesson["guide_instruction"].startswith(prefix)
    assert lesson["guide_view"] == "My Group"
    assert any(prefix in element.proto.body for element in app.get("html"))

    # The instruction's literal shortcut changes only navigation, retaining
    # this same pending lesson and the athlete's shared data.
    button_named(app,"Open My Group").click().run()
    assert not app.exception and _lesson(database,app)["stage"] == 3
    assert nav_named(app,"coach_nav_").value == "My Group"
    editor = next(section for section in app.expander if section.label == "Add pool result"
                  and any(primary["id"] in (group.key or "") for group in section.get("button_group")))
    keyed(editor.get("button_group"),"pool_wins_").set_value(3)
    keyed(editor.get("button_group"),"pool_losses_").set_value(3)
    button_named(editor,"Save pool result").click().run()
    assert not app.exception and _lesson(database,app)["stage"] == 4
    stored = database.get_athlete(primary["event_id"],primary["id"])
    assert (stored["pool_wins"],stored["pool_losses"],stored["pool_result_by"]) == (3,3,"Casey")


def test_navigation_advice_does_not_gate_a_saved_pool_result_from_team_situation(database):
    source = database.create_meet("Actual competition",["Learner"],[])
    hub = training.start_training(database,source["id"],actor="Learner")
    run = training.join_training(database,hub["id"],"Learner")
    assert training.tick_training(database,run["id"],"Learner","My Group")["stage"] == 1
    lesson = training.tick_training(database,run["id"],"Learner","Live")
    primary = _primary(database,lesson)
    database.acknowledge_help(primary["event_id"],primary["id"],"Learner")
    lesson = training.tick_training(database,run["id"],"Learner","Live")
    assert lesson["stage"] == 3 and lesson["guide_view"] == "My Group"
    database.set_pool_result(primary["event_id"],primary["id"],wins=3,losses=3,actor="Learner")
    assert training.tick_training(database,run["id"],"Learner","Live")["stage"] == 4
