"""The optional initial DE tableau is scoped, durable and safely editable."""

from contextlib import contextmanager
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from compcoach_live import migrate_to_supabase as migration
from compcoach_live import postgres_storage as pg
from compcoach_live.de_progress import describe_de_progress
from compcoach_live.storage import CompCoachDB, ConcurrentUpdateError, EventLockedError


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "start.sqlite")
    meet = db.create_meet("NAC", ["Alex"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    other = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(event["id"], [{"athlete_id": "one", "name": "ONE Athlete", "phase": "de", "pod": "B"}], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    return db, meet, event, other, athlete


def test_start_is_optional_and_configured_later_without_resetting_recorded_progress(day):
    db, meet, event, other, athlete = day
    assert event["de_start_tableau"] is None and event["de_start_tableau_version"] == 0
    db.mark_bye(event["id"], athlete["id"], actor="Alex")
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    before = db.get_athlete(event["id"], athlete["id"])
    availability = db.list_coach_availability(meet["id"])
    assert describe_de_progress(before, event)["progress_summary"] == "1 bye · 2 DE wins · Waiting for DE bout 3"
    revision = db.get_meet_revision(meet["id"])
    configured = db.set_event_de_start_tableau(event["id"], 256, "Casey", expected_version=0)
    assert configured["de_start_tableau"] == 256 and configured["de_start_tableau_version"] == 1
    assert db.get_athlete(event["id"], athlete["id"]) == before
    assert db.list_coach_availability(meet["id"]) == availability
    assert db.get_event(other["id"])["de_start_tableau"] is None
    assert db.get_meet_revision(meet["id"]) != revision
    assert describe_de_progress(before, configured)["progress_summary"] == "1 bye · 2 DE wins · Waiting for DE bout 3 · T32"
    restarted = CompCoachDB(db.path)
    assert restarted.get_event(event["id"])["de_start_tableau"] == 256
    assert restarted.get_athlete(event["id"], athlete["id"]) == before


def test_configuration_cas_idempotence_and_clear_preserve_other_events(day, monkeypatch):
    db, meet, event, other, _ = day
    monkeypatch.setattr("compcoach_live.storage.utc_now", lambda: "2026-10-02T10:00:00+00:00")
    first = db.set_event_de_start_tableau(event["id"], 256, "Casey", expected_version=0)
    revision = db.get_meet_revision(meet["id"])
    assert db.set_event_de_start_tableau(event["id"], "256", "Casey", expected_version=1) == first
    assert db.get_meet_revision(meet["id"]) == revision
    with pytest.raises(ConcurrentUpdateError, match="changed on another phone"):
        db.set_event_de_start_tableau(event["id"], 128, "Casey", expected_version=0)
    changed = db.set_event_de_start_tableau(event["id"], 128, "Casey", expected_version=1)
    assert changed["de_start_tableau_version"] == 2
    assert db.get_meet_revision(meet["id"]) != revision
    cleared = db.set_event_de_start_tableau(event["id"], None, "Casey", expected_version=2)
    assert cleared["de_start_tableau"] is None and cleared["de_start_tableau_version"] == 3
    assert db.get_event(other["id"])["de_start_tableau_version"] == 0
    actions = [a for a in db.recent_actions(event["id"]) if a["action"] == "de_start_tableau_set"]
    assert len(actions) == 3 and all(a["previous"] is None and a["athlete_id"] is None for a in actions)


@pytest.mark.parametrize("value", [True, 3, 0, -8, 8192, "T256", 256.0])
def test_invalid_or_closed_configuration_does_not_touch_event(day, value):
    db, _, event, _, _ = day
    before = db.get_event(event["id"])
    with pytest.raises(ValueError, match="power of two"):
        db.set_event_de_start_tableau(event["id"], value, "Casey")
    assert db.get_event(event["id"]) == before


def test_closed_day_cannot_change_config_and_snapshot_migration_preserves_it(day):
    db, meet, event, _, _ = day
    configured = db.set_event_de_start_tableau(event["id"], 256, "Casey")
    with migration.source_snapshot(db.path) as snapshot:
        row = next(row for row in migration.table_rows(snapshot, "events") if row["id"] == event["id"])
        assert row["de_start_tableau"] == 256 and row["de_start_tableau_version"] == 1
    db.set_meet_locked(meet["id"], True)
    with pytest.raises(EventLockedError):
        db.set_event_de_start_tableau(event["id"], 128, "Casey")
    assert db.get_event(event["id"])["de_start_tableau"] == configured["de_start_tableau"]


def test_old_sqlite_schema_upgrade_preserves_ids_progress_and_records_portable_ddl(day, tmp_path, monkeypatch):
    db, meet, event, _, athlete = day
    before = db.get_athlete(event["id"], athlete["id"])
    with sqlite3.connect(db.path) as conn:
        conn.execute("ALTER TABLE events DROP COLUMN de_start_tableau")
        conn.execute("ALTER TABLE events DROP COLUMN de_start_tableau_version")
    conn.close()  # checkpoint the WAL before copying the legacy database file
    old_copy = tmp_path / "postgres_facade.sqlite"
    shutil.copyfile(db.path, old_copy)
    statements = []
    class TracedDB(CompCoachDB):
        @contextmanager
        def _connection(self):
            with super()._connection() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
    restarted = TracedDB(db.path)
    assert restarted.get_event(event["id"])["de_start_tableau"] is None
    assert restarted.get_event(event["id"])["de_start_tableau_version"] == 0
    assert restarted.get_athlete(event["id"], athlete["id"]) == before
    ddl = [sql for sql in statements if sql.startswith("ALTER TABLE events ADD COLUMN de_start_tableau")]
    assert len(ddl) == 2
    # Execute the actual additive migration through the production PostgreSQL
    # SQL facade. The local raw double supplies only transport/lock operations;
    # a real PostgreSQL integration still needs its disposable test server.
    statuses = SimpleNamespace(IDLE="idle", INTRANS="intrans")
    monkeypatch.setattr(pg, "TransactionStatus", statuses, raising=False)
    class Noop:
        rowcount = 0
        def fetchall(self):
            return []
        def fetchone(self):
            return None
    class Raw:
        def __init__(self, connection):
            self.connection = connection
            self.info = SimpleNamespace(transaction_status=statuses.IDLE)
            self.calls = []
        def execute(self, sql, parameters=None, **kwargs):
            self.calls.append(sql)
            if sql == "BEGIN":
                self.info.transaction_status = statuses.INTRANS
            if "pg_advisory_xact_lock" in sql:
                return Noop()
            if "information_schema.columns" in sql:
                return self.connection.execute(f"PRAGMA table_info({parameters[1]})")
            return self.connection.execute(sql, parameters or ())
        def commit(self):
            self.connection.commit()
            self.info.transaction_status = statuses.IDLE
    with sqlite3.connect(old_copy) as conn:
        conn.row_factory = sqlite3.Row
        raw = Raw(conn)
        facade = pg._Connection(raw, "compcoach", 1)
        for sql in ddl:
            facade.execute(sql)
        facade.commit()
        columns = {row["name"] for row in facade.execute("PRAGMA table_info(events)").fetchall()}
        assert {"de_start_tableau", "de_start_tableau_version"} <= columns
        assert any("pg_advisory_xact_lock" in call for call in raw.calls)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET de_start_tableau = 3 WHERE id = ?", (event["id"],))
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event["id"],)).fetchone()
        assert row["de_start_tableau"] is None and row["de_start_tableau_version"] == 0
