"""Unknown pool totals must not strand shared coverage or autonomous practice."""

from contextlib import contextmanager
import shutil
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from compcoach_live import migrate_to_supabase as migration
from compcoach_live import postgres_storage as pg
from compcoach_live import training
from compcoach_live.assignment_notices import assignment_is_actionable
from compcoach_live.busy_board import build_busy_coach_board
from compcoach_live.pool_results import pool_has_results, pool_is_complete
from compcoach_live.storage import CompCoachDB, ConcurrentUpdateError


def _record(key, phase="pools", pod="B"):
    return {
        "athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
        "strip": f"{pod}1", "pod": pod,
        "pool": "1" if phase == "pools" else "", "time": "",
    }


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "pool-unknown.sqlite")
    meet = db.create_meet("October NAC", ["Alex", "Jordan"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [_record("first"), _record("second")], "Casey")
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
    db.assign_athletes(event["id"], [row["id"] for row in athletes.values()], main_coach="Alex", actor="Casey")
    return db, meet, event, athletes


def _availability(db, meet, coach="Alex"):
    return next(row for row in db.list_coach_availability(meet["id"]) if row["coach_name"] == coach)


def _board(db, event):
    return build_busy_coach_board(db.list_athletes(event["id"]), {event["id"]: event})


def test_unknown_completed_pool_is_removed_from_shared_alternate_coverage(day):
    db, meet, event, athletes = day
    db.cover_athlete(event["id"], athletes["first"]["id"], "Alex", "Alex", location="C3")
    assert [row["athlete_id"] for row in _board(db, event)["needs_cover"]] == [athletes["second"]["id"]]
    done = db.set_pool_done_unknown(event["id"], athletes["second"]["id"], "Alex")
    assert pool_is_complete(done) and not pool_has_results(done)
    assert not assignment_is_actionable(done)
    projected = _board(db, event)
    assert [row["athlete_id"] for row in projected["busy"]] == [athletes["first"]["id"]]
    assert projected["needs_cover"] == []
    assert _availability(db, meet)["unfinished_count"] == 1


def test_unknown_completion_releases_own_coverage_and_allows_available(day):
    db, meet, event, athletes = day
    db.set_pool_result(event["id"], athletes["second"]["id"], wins=3, losses=3, actor="Alex")
    db.cover_athlete(event["id"], athletes["first"]["id"], "Alex", "Alex", location="C3")
    db.request_help(event["id"], athletes["first"]["id"], "Alex", location="C3")
    assert _availability(db, meet)["is_busy"]
    done = db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    state = _availability(db, meet)
    assert state["unfinished_count"] == 0 and state["suggested_available"]
    assert state["is_available"] and not state["is_busy"] and not state["is_committed"]
    assert not done["help_requested_at"] and not done["covered_by"] and not done["live_location"]
    assert _board(db, event) == {"busy": [], "needs_cover": [], "accepted_cover": []}
    assert db.set_coach_availability(meet["id"], "Alex", True, "Alex")["is_available"]


def test_reopening_unknown_pool_restores_shared_workload_without_old_call(day):
    db, meet, event, athletes = day
    done = db.set_pool_done_unknown(event["id"], athletes["second"]["id"], "Alex")
    db.cover_athlete(event["id"], athletes["first"]["id"], "Alex", "Alex", location="C3")
    assert _board(db, event)["needs_cover"] == []
    reopened = db.reopen_pool(event["id"], done["id"], "Casey", expected_version=done["version"])
    assert not pool_is_complete(reopened) and assignment_is_actionable(reopened)
    assert reopened["call_status"] == "waiting" and not reopened["live_location"]
    assert [row["athlete_id"] for row in _board(db, event)["needs_cover"]] == [done["id"]]
    assert _availability(db, meet)["unfinished_count"] == 2


def test_de_import_preserves_unknown_pool_history_but_enables_new_bout_offer(day):
    db, meet, event, athletes = day
    done = db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    db.set_coach_availability(meet["id"], "Jordan", True, "Jordan")
    with pytest.raises(ConcurrentUpdateError, match="finished"):
        db.create_coverage_request(meet["id"], event["id"], done["id"], ["Jordan"], "Casey")
    db.merge_import(event["id"], [_record("first", phase="de", pod="F")], "Casey")
    advanced = db.get_athlete(event["id"], done["id"])
    assert advanced["phase"] == "de" and advanced["pod"] == "F"
    assert advanced["pool_results_unknown"] == 1
    assert advanced["pool_wins"] is advanced["pool_losses"] is None
    assert advanced["pool_result_at"] == done["pool_result_at"]
    assert advanced["pool_result_by"] == "Alex" and assignment_is_actionable(advanced)
    offer = db.create_coverage_request(meet["id"], event["id"], done["id"], ["Jordan"], "Casey")
    db.accept_coverage_request(meet["id"], offer["id"], "Jordan")
    covered = db.cover_athlete(event["id"], done["id"], "Jordan", "Jordan", location="J4")
    assert covered["covered_by"] == "Jordan" and covered["pool_results_unknown"] == 1
    won = db.mark_result(event["id"], done["id"], outcome="won", actor="Jordan")
    assert won["de_wins"] == 1 and won["pool_results_unknown"] == 1
    assert _availability(db, meet, "Jordan")["is_available"]


def test_same_phase_reimport_keeps_override_new_pool_phase_clears_it(day):
    db, _meet, event, athletes = day
    done = db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    record = _record("first")
    record["strip"] = "C3"
    db.merge_import(event["id"], [record], "Casey")
    refreshed = db.get_athlete(event["id"], done["id"])
    assert refreshed["pool_results_unknown"] == 1 and refreshed["source_strip"] == "C3"
    db.merge_import(event["id"], [_record("first", phase="de")], "Casey")
    db.merge_import(event["id"], [_record("first", phase="pools", pod="D")], "Casey")
    next_pool = db.get_athlete(event["id"], done["id"])
    assert next_pool["pool_results_unknown"] == 0 and next_pool["pool_result_at"] is None
    assert next_pool["pool_wins"] is next_pool["pool_losses"] is None
    assert not pool_is_complete(next_pool)


@pytest.mark.parametrize("view", ["My Group", "Live"])
def test_training_accepts_unknown_result_from_either_view_without_fabricating_tally(tmp_path, view):
    db = CompCoachDB(tmp_path / "practice.sqlite")
    real = db.create_meet("Actual competition", ["Learner"], [])
    real_before = db.get_meet(real["id"])
    hub = training.start_training(db, real["id"], actor="Learner", open_entry=True)
    run = training.join_training(db, hub["id"], "Learner", participant_key=uuid4().hex)
    assert training.tick_training(db, run["id"], "Learner", "My Group")["stage"] == 1
    lesson = training.tick_training(db, run["id"], "Learner", "Live")
    primary = next(row for event in db.list_meet_events(run["id"])
                   for row in db.list_athletes(event["id"])
                   if row["id"] == lesson["state"]["primary_id"])
    db.acknowledge_help(primary["event_id"], primary["id"], "Learner")
    assert training.tick_training(db, run["id"], "Learner", view)["stage"] == 3
    done = db.set_pool_done_unknown(primary["event_id"], primary["id"], "Learner")
    assert training.tick_training(db, run["id"], "Learner", view)["stage"] == 4
    refreshed = db.get_athlete(primary["event_id"], primary["id"])
    assert refreshed["pool_results_unknown"] == 1
    assert refreshed["pool_wins"] is refreshed["pool_losses"] is None
    assert refreshed["pool_result_at"] == done["pool_result_at"] and refreshed["pool_result_by"] == "Learner"
    db.set_coach_availability(run["id"], "Learner", True, "Learner")
    assert training.tick_training(db, run["id"], "Learner", view)["stage"] == 5
    de = db.get_athlete(primary["event_id"], primary["id"])
    assert de["phase"] == "de" and de["pool_results_unknown"] == 1
    assert de["pool_wins"] is de["pool_losses"] is None and assignment_is_actionable(de)
    assert db.get_meet(real["id"]) == real_before


def test_next_day_can_be_neutral_and_new_import_starts_with_no_completion(day):
    db, meet, event, athletes = day
    done = db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    next_day = db.prepare_next_day(meet["id"], "Day 2", "2026-10-10", [], "Casey")
    assert db.list_meet_events(next_day["id"]) == []
    assert db.get_athlete(event["id"], done["id"]) == done
    new_event = db.add_meet_event(next_day["id"], "Junior Epee")
    db.merge_import(new_event["id"], [_record("first")], "Casey")
    new_athlete = db.list_athletes(new_event["id"])[0]
    assert new_athlete["id"] != done["id"]
    assert new_athlete["pool_results_unknown"] == 0 and not pool_is_complete(new_athlete)
    assert new_athlete["pool_result_at"] is None and new_athlete["pool_wins"] is None


def test_supabase_migration_snapshot_preserves_unknown_and_numeric_results(day):
    db, _meet, event, athletes = day
    done = db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    numeric = db.set_pool_result(event["id"], athletes["second"]["id"], wins=2, losses=4, actor="Alex")
    with migration.source_snapshot(db.path) as snapshot:
        rows = {row["id"]: row for row in migration.table_rows(snapshot, "athletes")}
    assert rows[done["id"]]["pool_results_unknown"] == 1
    assert rows[done["id"]]["pool_wins"] is rows[done["id"]]["pool_losses"] is None
    assert rows[numeric["id"]]["pool_results_unknown"] == 0
    assert (rows[numeric["id"]]["pool_wins"], rows[numeric["id"]]["pool_losses"]) == (2, 4)


def test_legacy_additive_migration_and_domain_roundtrip_through_postgres_sql_facade(day, tmp_path, monkeypatch):
    """Exercise production SQL translation with a local transport, not a PG server."""
    db, _meet, event, athletes = day
    with sqlite3.connect(db.path) as connection:
        connection.execute("ALTER TABLE athletes DROP COLUMN pool_results_unknown")
    connection.close()
    legacy = tmp_path / "facade.sqlite"
    shutil.copyfile(db.path, legacy)
    statements = []

    class TracedDB(CompCoachDB):
        @contextmanager
        def _connection(self):
            with super()._connection() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

    migrated = TracedDB(db.path)
    assert migrated.get_athlete(event["id"], athletes["first"]["id"])["pool_results_unknown"] == 0
    ddl = [statement for statement in statements if statement.startswith("ALTER TABLE athletes ADD COLUMN pool_results_unknown")]
    assert len(ddl) == 1
    statuses = SimpleNamespace(IDLE="idle", INTRANS="intrans")
    monkeypatch.setattr(pg, "TransactionStatus", statuses, raising=False)
    monkeypatch.setattr(pg, "psycopg", SimpleNamespace(IntegrityError=type("PGConstraintError", (Exception,), {})))
    translated = []

    class Raw:
        def __init__(self, connection):
            self.connection = connection
            self.info = SimpleNamespace(transaction_status=statuses.IDLE)
            self.closed = False

        def execute(self, sql, parameters=None, **kwargs):
            translated.append((sql, parameters))
            if "pg_advisory_xact_lock" in sql:
                return SimpleNamespace(rowcount=0, fetchone=lambda: None, fetchall=lambda: [])
            if sql == "BEGIN":
                self.info.transaction_status = statuses.INTRANS
            if "information_schema.columns" in sql:
                return self.connection.execute(f"PRAGMA table_info({parameters[1]})")
            return self.connection.execute(sql.replace("%s", "?").replace("%%", "%"), parameters or ())

        def commit(self):
            self.connection.commit()
            self.info.transaction_status = statuses.IDLE

        def rollback(self):
            self.connection.rollback()
            self.info.transaction_status = statuses.IDLE

    class FacadeDB(CompCoachDB):
        @contextmanager
        def _connection(self):
            with sqlite3.connect(self.path) as connection:
                connection.row_factory = lambda cursor, values: {column[0]: value for column, value in zip(cursor.description, values)}
                yield pg._Connection(Raw(connection), "compcoach", 1)

    # Apply the actual SQLite additive migration through the PostgreSQL facade.
    facade_db = object.__new__(FacadeDB)
    facade_db.path = legacy
    with facade_db._connection() as connection:
        connection.execute(ddl[0])
        connection.commit()
    unknown = facade_db.set_pool_done_unknown(event["id"], athletes["first"]["id"], "Alex")
    assert unknown["pool_results_unknown"] == 1 and pool_is_complete(unknown)
    assert facade_db.get_athlete(event["id"], unknown["id"])["pool_wins"] is None
    numeric = facade_db.set_pool_result(event["id"], unknown["id"], wins=4, losses=2, actor="Alex")
    assert numeric["pool_results_unknown"] == 0 and pool_has_results(numeric)
    assert any("pg_advisory_xact_lock" in sql for sql, _parameters in translated)
    assert any(sql.startswith("UPDATE athletes") and "%s" in sql for sql, _parameters in translated)
    assert [row["action"] for row in facade_db.recent_actions(event["id"])][:2] == ["pool_result", "pool_done_unknown"]
