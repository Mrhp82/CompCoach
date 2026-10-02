"""The unattended practice link opens a persistent course for each coach.

These tests use the actual Streamlit application and ordinary navigation, rather
than a replacement set of training-only athlete actions.
"""

import json
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


APP_PATH = str(Path(__file__).parents[1] / "app.py")


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "training-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def button(app, label):
    matches = [item for item in app.button if item.label == label]
    assert len(matches) == 1, (label, [item.label for item in app.button])
    return matches[0]


def nav(app, prefix, value=None):
    matches = [item for item in app.get("button_group") if (item.key or "").startswith(prefix)]
    assert len(matches) == 1, (prefix, [(item.key, item.options) for item in app.get("button_group")])
    if value is not None:
        matches[0].set_value(value).run()
    return matches[0]


def open_board(day, role="coach", who=None):
    app = AppTest.from_file(APP_PATH, default_timeout=25)
    app.query_params.update(event=day["id"], token=day[f"{role}_token"])
    if who:
        app.query_params["who"] = who
    return app.run()


def query(app, name):
    value = app.query_params.get(name)
    return value[0] if isinstance(value, list) and value else value


def source_day(database):
    day = database.create_meet("Real October NAC", ["Alex", "Robin"], ["Sky"], first_event_name="Real Cadet Epee")
    event = database.list_meet_events(day["id"])[0]
    database.merge_import(
        event["id"],
        [{"athlete_id": "real_riley", "name": "STONE Riley", "phase": "pools", "strip": "B1", "pod": "B", "pool": "1"}],
        "Admin",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(event["id"], [athlete["id"]], main_coach="Alex", actor="Admin")
    return day, event


def training_hub(database, day):
    from compcoach_live.training import start_training

    return start_training(
        database, source_meet_id=day["id"], coach_names=[], open_entry=True,
        actor="Admin", duration_days=14,
    )


def enter_practice(app, name):
    field = next(item for item in app.text_input if item.label == "Your name")
    field.set_value(name)
    button(app, "Start my practice").click().run()
    assert not app.exception
    return app


def legacy_training_hub(database, day):
    from compcoach_live.training import start_training

    return start_training(database, source_meet_id=day["id"], coach_names=["Alex", "Robin"], actor="Admin")


def test_admin_activates_two_week_practice_without_instructor_controls(database):
    from compcoach_live.training import get_training

    day, event = source_day(database)
    day_before = database.get_meet(day["id"])
    athletes_before = database.list_athletes(event["id"])
    app = open_board(day, role="admin", who="Alex")
    nav(app, "admin_nav_", "Setup")
    nav(app, "admin_setup_nav_", "Training")
    duration = next(item for item in app.radio if (item.key or "").startswith("training_duration_"))
    duration.set_value(14)
    button(app, "Activate autonomous practice").click().run()

    assert not app.exception
    practice_id = query(app, "event")
    assert practice_id != day["id"]
    metadata = get_training(database, practice_id)
    assert metadata
    assert metadata["source_meet_id"] == day["id"]
    assert metadata.get("duration_days", metadata.get("state", {}).get("duration_days")) == 14
    assert not any(item.label in {"Pause exercise", "Resume exercise", "Next scenario", "Advance scenario"} for item in app.button)
    assert database.get_meet(day["id"]) == day_before
    assert database.list_athletes(event["id"]) == athletes_before


def test_coach_link_accepts_names_and_starts_separate_resumable_courses(database):
    day, event = source_day(database)
    athletes_before = database.list_athletes(event["id"])
    hub = training_hub(database, day)
    alex = open_board(hub)
    assert any(item.label == "Your name" for item in alex.text_input)
    assert not alex.get("button_group")
    enter_practice(alex, "  Alex  ")

    assert not alex.exception
    alex_run = query(alex, "event")
    assert alex_run != hub["id"]
    assert query(alex, "who") == "Alex"
    assert nav(alex, "coach_nav_").options == ["My Group", "Team situation"]
    assert not any("training_end_" in str(item.key) for item in alex.button)

    robin = enter_practice(open_board(hub), "Robin")
    assert not robin.exception
    robin_run = query(robin, "event")
    assert robin_run not in {hub["id"], alex_run}
    assert query(robin, "who") == "Robin"
    alex_events = database.list_meet_events(alex_run)
    robin_events = database.list_meet_events(robin_run)
    assert alex_events and robin_events
    assert {item["id"] for item in alex_events}.isdisjoint(item["id"] for item in robin_events)
    assert database.list_athletes(event["id"]) == athletes_before

    reopened = open_board(database.get_meet(alex_run), who="Alex")
    assert not reopened.exception
    assert query(reopened, "event") == alex_run
    assert nav(reopened, "coach_nav_").options == ["My Group", "Team situation"]


def test_ordinary_coach_screen_keeps_training_management_private(database):
    day, _event = source_day(database)
    training_hub(database, day)
    app = open_board(day, who="Robin")
    assert not app.exception
    assert nav(app, "coach_nav_").options == ["My Group", "Team situation"]
    assert not any("TRAINING" in item.value for item in app.warning)
    assert not any(item.label == "Activate autonomous practice" for item in app.button)


def test_shared_practice_link_does_not_resume_from_name_in_query(database):
    day, _event = source_day(database)
    hub = training_hub(database, day)
    app = open_board(hub, who="Unknown coach")
    assert not app.exception
    assert query(app, "event") == hub["id"]
    assert any(item.label == "Your name" for item in app.text_input)
    assert not app.get("button_group")
    assert not any((item.key or "").startswith(("won_", "pool_result_", "help_request_")) for item in app.button)


def test_same_name_in_two_browser_sessions_gets_two_fresh_courses(database):
    from compcoach_live.training import get_training

    day, _event = source_day(database)
    hub = training_hub(database, day)
    first = enter_practice(open_board(hub), "Casey")
    second = enter_practice(open_board(hub), "Casey")
    first_id, second_id = query(first, "event"), query(second, "event")
    assert first_id != second_id
    assert first_id != hub["id"] and second_id != hub["id"]
    assert query(first, "who") == query(second, "who") == "Casey"
    assert get_training(database, first_id)["learner"] == "Casey"
    assert get_training(database, second_id)["learner"] == "Casey"
    first_events = {item["id"] for item in database.list_meet_events(first_id)}
    second_events = {item["id"] for item in database.list_meet_events(second_id)}
    assert first_events.isdisjoint(second_events)
    assert not first.exception and not second.exception


def test_blank_name_stays_on_shared_practice_link_with_clear_error(database):
    day, _event = source_day(database)
    hub = training_hub(database, day)
    app = open_board(hub)
    button(app, "Start my practice").click().run()
    assert query(app, "event") == hub["id"]
    assert app.error and not app.exception
    assert any(item.label == "Your name" for item in app.text_input)


def test_direct_run_link_does_not_offer_virtual_coach_identity(database):
    from compcoach_live.training import get_training, join_training

    day, _event = source_day(database)
    hub = legacy_training_hub(database, day)
    run = join_training(database, hub["id"], "Alex")
    metadata = get_training(database, run["id"])
    assert metadata["learner"] == "Alex"
    virtual_names = set(run["active_coaches"]) - {"Alex"}
    assert virtual_names
    virtual = sorted(virtual_names)[0]
    app = open_board(run, who=virtual)

    assert not app.exception
    assert not app.get("button_group")
    assert not any(item.label in virtual_names for item in app.button)
    assert not any((item.key or "").startswith(("won_", "pool_result_", "help_request_")) for item in app.button)


def test_home_keeps_personal_runs_out_of_real_competition_list(database):
    from compcoach_live.training import join_training

    day, _event = source_day(database)
    hub = legacy_training_hub(database, day)
    alex = join_training(database, hub["id"], "Alex")
    robin = join_training(database, hub["id"], "Robin")
    home = AppTest.from_file(APP_PATH, default_timeout=25)
    home.session_state["landing_admin_unlocked"] = True
    home.session_state["home_admin_actor"] = "Alex"
    home.run()

    assert not home.exception
    assert any(item.value == "🟢 Active now" for item in home.subheader)
    admin_open = [item for item in home.button if item.label == "Open Admin"]
    assert len(admin_open) == 1
    assert not any(item.label == "Open read-only archive" for item in home.button)
    assert len([item for item in home.button if item.label == "Open practice"]) == 1
    for run in (alex, robin):
        assert not any(item.key == f"open_practice_{run['id']}" for item in home.button)


def test_expired_hub_and_its_direct_run_close_without_affecting_real_day(database):
    from compcoach_live.training import get_training, join_training

    day, event = source_day(database)
    source_before = database.get_meet(day["id"])
    athletes_before = database.list_athletes(event["id"])
    hub = legacy_training_hub(database, day)
    run = join_training(database, hub["id"], "Alex")
    with database._connection() as conn:
        for board in (hub, run):
            row = conn.execute("SELECT state_json FROM training_sessions WHERE meet_id = ?", (board["id"],)).fetchone()
            state = json.loads(row["state_json"])
            state["expires_at"] = "2020-01-01T00:00:00+00:00"
            conn.execute("UPDATE training_sessions SET state_json = ? WHERE meet_id = ?", (json.dumps(state), board["id"]))

    for board in (hub, run):
        app = open_board(board, who="Alex")
        assert not app.exception
        assert not app.get("button_group")
        assert not any((item.key or "").startswith(("won_", "pool_result_", "help_request_")) for item in app.button)
        assert not any(item.label == "Practice again" for item in app.button)
    assert get_training(database, hub["id"])["expired"]
    assert database.get_meet(day["id"]) == source_before
    assert database.list_athletes(event["id"]) == athletes_before


def test_practice_uses_ordinary_pool_result_controls_and_persists_taps(database):
    day, source_event = source_day(database)
    source_before = database.list_athletes(source_event["id"])
    hub = training_hub(database, day)
    app = enter_practice(open_board(hub), "Alex")
    assert not app.exception
    run_id = query(app, "event")
    learner_athletes = [
        athlete
        for child in database.list_meet_events(run_id)
        for athlete in database.list_athletes(child["id"])
        if athlete["main_coach"] == "Alex" and athlete["phase"] == "pools"
    ]
    assert learner_athletes
    athlete = learner_athletes[0]
    wins = next(item for item in app.get("button_group") if (item.key or "").startswith(f"pool_wins_{athlete['id']}_"))
    losses = next(item for item in app.get("button_group") if (item.key or "").startswith(f"pool_losses_{athlete['id']}_"))
    assert wins.options == losses.options == [str(number) for number in range(7)]
    wins.set_value(4)
    losses.set_value(2)
    save = next(item for item in app.button if f"pool_result_{athlete['id']}_" in str(item.key) and item.label == "Save pool result")
    save.click().run()

    assert not app.exception
    stored = database.get_athlete(athlete["event_id"], athlete["id"])
    assert (stored["pool_wins"], stored["pool_losses"], stored["pool_result_by"]) == (4, 2, "Alex")
    resumed = open_board(database.get_meet(run_id), who="Alex")
    assert not resumed.exception
    assert query(resumed, "event") == run_id
    assert database.list_athletes(source_event["id"]) == source_before


def test_admin_can_end_practice_access_without_finishing_real_competition(database):
    from compcoach_live.training import get_training, join_training

    day, event = source_day(database)
    day_before = database.get_meet(day["id"])
    athletes_before = database.list_athletes(event["id"])
    hub = legacy_training_hub(database, day)
    run = join_training(database, hub["id"], "Alex")
    admin = open_board(hub, role="admin", who="Alex")
    assert not admin.exception
    button(admin, "End practice access").click().run()

    assert not admin.exception
    assert not query(admin, "event")
    assert get_training(database, hub["id"])["status"] == "stopped"
    coach = open_board(run, who="Alex")
    assert not coach.exception
    assert not coach.get("button_group")
    assert not any(item.label == "Practice again" for item in coach.button)
    assert database.get_meet(day["id"]) == day_before
    assert database.list_athletes(event["id"]) == athletes_before


def test_completed_coach_can_repeat_without_resetting_other_coach(database):
    from compcoach_live.training import TRAINING_STEPS, get_training, join_training

    day, _event = source_day(database)
    hub = legacy_training_hub(database, day)
    alex = join_training(database, hub["id"], "Alex")
    robin = join_training(database, hub["id"], "Robin")
    robin_before = get_training(database, robin["id"])
    with database._connection() as conn:
        conn.execute(
            "UPDATE training_sessions SET status = 'completed', stage = ? WHERE meet_id = ?",
            (len(TRAINING_STEPS) - 1, alex["id"]),
        )
    app = open_board(alex, who="Alex")
    assert not app.exception
    assert not app.get("button_group")
    button(app, "Practice again").click().run()

    assert not app.exception
    own = get_training(database, query(app, "event"))
    assert own["status"] == "running"
    assert own["learner"] == "Alex"
    assert own["stage_index"] < 2
    assert nav(app, "coach_nav_").options == ["My Group", "Team situation"]
    assert get_training(database, robin["id"]) == robin_before
