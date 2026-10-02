"""Background checks stay quiet while real actions and timed scenarios progress."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from compcoach_live import training
from compcoach_live.storage import CompCoachDB


@pytest.fixture
def course(tmp_path, monkeypatch):
    from compcoach_live import refresh

    now = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training, "utc_now", lambda: now[0].isoformat())

    class CourseClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz) if tz else now[0]

    monkeypatch.setattr(refresh, "datetime", CourseClock)
    database = CompCoachDB(tmp_path / "refresh.sqlite")
    source = database.create_meet("Real competition", ["Alex", "Robin"], [])
    hub = training.start_training(database, source["id"], actor="Alex")
    run = training.join_training(database, hub["id"], "Alex")

    def advance(seconds):
        now[0] += timedelta(seconds=seconds)

    return database, source, hub, run, advance


def _tick(database, run, view="My Group"):
    return training.tick_training(database, run["id"], "Alex", view)


def _target(database, run, name):
    metadata = training.get_training(database, run["id"])
    for event in database.list_meet_events(run["id"]):
        athlete = database.get_athlete(event["id"], metadata["state"][name])
        if athlete:
            return athlete
    raise AssertionError(name)


def _snapshot(database):
    with database._connection() as connection:
        return {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
            for table in ("training_sessions", "meets", "events", "athletes", "actions", "coach_availability", "phase_states")
        }


def _record_sql(database, monkeypatch):
    statements = []
    original = database._connection

    @contextmanager
    def traced():
        with original() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, "_connection", traced)
    return statements


def _enter_busy_scenario(database, run):
    assert _tick(database, run)["stage"] == 1
    assert _tick(database, run, "Live")["stage"] == 2
    athlete = _target(database, run, "primary_id")
    database.acknowledge_help(athlete["event_id"], athlete["id"], "Alex")
    assert _tick(database, run)["stage"] == 3
    database.set_pool_result(athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert _tick(database, run)["stage"] == 4
    database.set_coach_availability(run["id"], "Alex", True, "Alex")
    assert _tick(database, run)["stage"] == 5
    assert _tick(database, run, "Live")["stage"] == 6
    athlete = _target(database, run, "primary_id")
    database.report_call(athlete["event_id"], athlete["id"], status="on_deck", location="", actor="Alex")
    assert _tick(database, run)["stage"] == 7
    database.cover_athlete(athlete["event_id"], athlete["id"], "Alex", "Alex", location="C3")
    assert _tick(database, run)["stage"] == 8
    return athlete


def test_repeated_idle_checks_do_not_write_or_change_practice_state(course, monkeypatch):
    database, _source, _hub, run, advance = course
    # Reach a step which waits for navigation, then record this step's current
    # view once. Clock changes alone must not invent progress or attendance.
    assert _tick(database, run)["stage"] == 1
    before_metadata = _tick(database, run)
    before = _snapshot(database)
    statements = _record_sql(database, monkeypatch)

    for _ in range(8):
        advance(5)
        assert _tick(database, run) == before_metadata

    writes = [query for query in statements if query.lstrip().split(" ", 1)[0].upper() in {"UPDATE", "INSERT", "DELETE", "REPLACE"}]
    assert not writes
    assert _snapshot(database) == before


def test_pending_virtual_coverage_still_arrives_once_without_instructor(course):
    database, source, _hub, run, advance = course
    source_before = database.get_meet(source["id"])
    _enter_busy_scenario(database, run)
    _tick(database, run)  # Observe the new stage once before idle checks.
    secondary = _target(database, run, "secondary_id")
    assert not secondary["covered_by"]

    advance(14)
    assert _tick(database, run)["stage"] == 8
    assert not _target(database, run, "secondary_id")["covered_by"]

    advance(2)
    result = _tick(database, run)
    assert result["stage"] == 8
    assert result["state"]["response_processed"] is True
    assert not result["state"]["pending_events"]
    covered = _target(database, run, "secondary_id")
    assert covered["covered_by"] == result["state"]["coaches"][1]
    assert covered["live_location"] == "J2"
    after = _snapshot(database)
    for _ in range(4):
        advance(5)
        assert _tick(database, run) == result
    assert _snapshot(database) == after
    assert database.get_meet(source["id"]) == source_before


def test_learner_result_advances_and_delayed_call_is_not_lost(course):
    database, _source, _hub, run, advance = course
    primary = _enter_busy_scenario(database, run)
    advance(16)
    _tick(database, run)
    database.mark_result(primary["event_id"], primary["id"], outcome="won", actor="Alex")
    result = _tick(database, run)
    assert result["stage"] == 9
    secondary = _target(database, run, "secondary_id")
    assert secondary["call_status"] == "waiting"
    assert not secondary["covered_by"]

    advance(9)
    called = _tick(database, run)
    secondary = _target(database, run, "secondary_id")
    assert called["stage"] == 9
    assert secondary["call_status"] == "now"
    assert secondary["live_location"] == "J2"
    database.take_over_athlete(secondary["event_id"], secondary["id"], "Alex", "Alex")
    assert _tick(database, run)["stage"] == 10


def test_virtual_coordinator_requests_temporary_coverage_without_changing_plan(course):
    database, _source, _hub, run, advance = course
    primary = _enter_busy_scenario(database, run)
    advance(16)
    _tick(database, run)
    database.mark_result(primary["event_id"], primary["id"], outcome="won", actor="Alex")
    assert _tick(database, run)["stage"] == 9
    advance(9)
    _tick(database, run)
    secondary = _target(database, run, "secondary_id")
    database.take_over_athlete(secondary["event_id"], secondary["id"], "Alex", "Alex")
    assert _tick(database, run)["stage"] == 10
    database.cover_athlete(secondary["event_id"], secondary["id"], "Alex", "Alex", location="J2")
    database.request_help(secondary["event_id"], secondary["id"], "Alex", location="J2")
    assert _tick(database, run)["stage"] == 11
    previous_plans = {
        athlete["id"]: athlete.get("de_coaches", [])
        for event in database.list_meet_events(run["id"])
        for athlete in database.list_athletes(event["id"])
    }
    database.mark_result(secondary["event_id"], secondary["id"], outcome="lost", actor="Alex")
    assert _tick(database, run)["stage"] == 12
    assigned = _target(database, run, "reassigned_id")
    assert "Alex" not in previous_plans[assigned["id"]]
    assert assigned["de_coaches"] == previous_plans[assigned["id"]]
    request = next(
        row for row in database.list_coverage_requests(run["id"], "Alex")
        if row["athlete_id"] == assigned["id"]
    )
    assert not request.get("accepted_by")
    assert "Alex" in {recipient["coach_name"] for recipient in request["recipients"]}
    assert assigned["call_status"] == "on_deck" and assigned["live_location"] == "E2"
    database.accept_coverage_request(run["id"], request["id"], "Alex", actor="Alex")
    assert _tick(database, run)["stage"] == 12
    accepted = database.get_athlete(assigned["event_id"], assigned["id"])
    assert accepted["covered_by"] == "" and accepted["takeover_coach"] == "Alex"
    assert accepted["de_coaches"] == previous_plans[assigned["id"]]


def _open_coach_app(database, run, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("COMPCOACH_DB_PATH", str(database.path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=25)
    app.query_params.update(event=run["id"], token=run["coach_token"], who="Alex")
    return app.run()


def test_unsaved_pool_taps_survive_idle_and_unrelated_availability_change(course, monkeypatch):
    database, _source, _hub, run, advance = course
    app = _open_coach_app(database, run, monkeypatch)
    assert not app.exception
    wins = next(item for item in app.get("button_group") if (item.key or "").startswith("pool_wins_"))
    losses = next(item for item in app.get("button_group") if (item.key or "").startswith("pool_losses_"))
    wins_key, losses_key = wins.key, losses.key
    wins.set_value(4)
    losses.set_value(2)
    app.run()
    advance(10)
    app.run()
    virtual = training.get_training(database, run["id"])["state"]["coaches"][1]
    database.set_coach_availability(run["id"], virtual, True, virtual)
    app.run()

    assert not app.exception
    assert next(item for item in app.get("button_group") if item.key == wins_key).value == 4
    assert next(item for item in app.get("button_group") if item.key == losses_key).value == 2
    assert _target(database, run, "primary_id")["pool_wins"] is None


def test_strip_draft_survives_timed_simulation_update_for_another_athlete(course, monkeypatch):
    database, _source, _hub, run, advance = course
    _enter_busy_scenario(database, run)
    primary = _target(database, run, "primary_id")
    app = _open_coach_app(database, run, monkeypatch)
    assert not app.exception
    selection = next(item for item in app.selectbox if (item.key or "").startswith("call_athlete_"))
    selection.set_value(primary["id"]).run()
    field = next(item for item in app.text_input if (item.key or "").startswith("call_location_"))
    field_key = field.key
    field.set_value("D7").run()
    advance(16)
    app.run()

    assert not app.exception
    assert _target(database, run, "secondary_id")["covered_by"]
    assert next(item for item in app.text_input if item.key == field_key).value == "D7"
    assert _target(database, run, "primary_id")["live_location"] == "C3"
