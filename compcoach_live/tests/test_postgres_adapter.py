"""Dialect/security contracts plus optional tests against a real PostgreSQL server.

Set COMPCOACH_TEST_POSTGRES_URL to a disposable database to enable integration
tests. Each test uses its own private schema; never use production credentials.
"""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from compcoach_live import postgres_storage as pg
from compcoach_live.storage import CompCoachError, ConcurrentUpdateError, EventLockedError


pytestmark = pytest.mark.skipif(pg.psycopg is None, reason="psycopg is not installed")


@pytest.mark.parametrize("source, expected", [
    ("SELECT '?' AS literal, ? AS value", "SELECT '?' AS literal, %s AS value"),
    ("SELECT 'O''Brien?' AS literal, ?", "SELECT 'O''Brien?' AS literal, %s"),
    ("SELECT '100%' AS literal, ?", "SELECT '100%%' AS literal, %s"),
    ("SELECT * FROM coaches ORDER BY coaches.name COLLATE NOCASE, id", "SELECT * FROM coaches ORDER BY lower(coaches.name), id"),
    ("UPDATE pool_waves SET activated_at = CASE WHEN ? THEN ? ELSE activated_at END", "UPDATE pool_waves SET activated_at = CASE WHEN %s <> 0 THEN %s ELSE activated_at END"),
    ("INSERT OR IGNORE INTO coaches (id) VALUES (?)", "INSERT INTO coaches (id) VALUES (%s) ON CONFLICT DO NOTHING"),
    ("INSERT INTO actions (actor) VALUES (?)", "INSERT INTO actions (actor) VALUES (%s) RETURNING id"),
])
def test_domain_sql_is_translated_without_interpolating_data(source, expected):
    assert pg._translate_sql(source)[0] == expected


def test_directory_ordering_select_alias_is_valid_for_postgres():
    sql, _ = pg._translate_sql(
        "SELECT coaches.name, (SELECT COUNT(*) FROM athletes) AS assignment_count "
        "FROM coaches ORDER BY assignment_count > 0, coaches.name COLLATE NOCASE"
    )
    assert sql.startswith("SELECT * FROM (SELECT coaches.name,")
    assert sql.endswith("ORDER BY  assignment_count > 0, lower(name)")


@pytest.mark.parametrize("url", [
    "postgresql://user:super-secret@example.com/postgres",
    "postgresql://user:super-secret@example.com/postgres?sslmode=prefer",
    "host=127.0.0.1 hostaddr=203.0.113.1 user=user password=super-secret sslmode=disable",
    "postgresql://user:super-secret@example.com:6543/postgres?sslmode=require",
    "postgresql://user:super-secret@/postgres",
    "invalid connection with super-secret",
])
def test_cloud_configuration_fails_closed_and_hides_password(url):
    with pytest.raises(pg.PostgresConfigurationError) as exc:
        pg.PostgresCompCoachDB(url, initialize=False)
    assert "super-secret" not in str(exc.value)


def test_connection_info_keeps_secure_credentials_server_side():
    info = pg._validated_connection_info(
        "postgresql://private:secret@example.com:5432/postgres?sslmode=require"
    )
    parsed = pg.conninfo_to_dict(info)
    assert parsed["sslmode"] == "require"
    assert parsed["password"] == "secret"
    assert parsed["connect_timeout"] == "12"


def test_cloud_failure_does_not_create_a_local_database(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    def unavailable(*args, **kwargs):
        raise pg.psycopg.OperationalError("secret password in connection details")
    monkeypatch.setattr(pg.ConnectionPool, "open", unavailable)
    with pytest.raises(CompCoachError, match="unavailable") as exc:
        pg.PostgresCompCoachDB("postgresql://user:secret@example.com/db?sslmode=require")
    assert "secret" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


class FakeRaw:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=pg.TransactionStatus.IDLE)
        self.calls = []
        self.closed = False
    def execute(self, sql, parameters=None, **kwargs):
        self.calls.append((sql, parameters))
        if sql == "BEGIN":
            self.info.transaction_status = pg.TransactionStatus.INTRANS
        return SimpleNamespace(rowcount=1, fetchone=lambda: {"id": 37}, fetchall=lambda: [])
    def commit(self):
        self.info.transaction_status = pg.TransactionStatus.IDLE
    def rollback(self):
        self.info.transaction_status = pg.TransactionStatus.IDLE
    def close(self):
        self.closed = True


def test_write_transaction_acquires_advisory_lock_before_read():
    raw = FakeRaw()
    connection = pg._Connection(raw, "compcoach", 42)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("SELECT covered_by FROM athletes WHERE id = ?", ["athlete"])
    assert raw.calls[:2] == [("BEGIN", None), ("SELECT pg_advisory_xact_lock(%s)", (42,))]
    assert raw.calls[-1] == ("SELECT covered_by FROM athletes WHERE id = %s", ("athlete",))


def test_writer_advisory_lock_is_taken_once_per_transaction():
    raw = FakeRaw()
    connection = pg._Connection(raw, "compcoach", 42)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE coaches SET name = ? WHERE id = ?", ["One", "1"])
    connection.execute("UPDATE coaches SET name = ? WHERE id = ?", ["Two", "2"])
    assert len([sql for sql, _ in raw.calls if "pg_advisory_xact_lock" in sql]) == 1
    connection.commit()
    connection.execute("UPDATE coaches SET name = ? WHERE id = ?", ["Three", "3"])
    assert len([sql for sql, _ in raw.calls if "pg_advisory_xact_lock" in sql]) == 2


def test_failed_rollback_closes_session_and_retains_domain_failure():
    raw = FakeRaw()
    def disconnected():
        raise pg.psycopg.OperationalError("connection is gone")
    raw.rollback = disconnected
    pg._Connection(raw, "compcoach", 42).rollback()
    assert raw.closed


def test_audit_lastrowid_and_executemany_contract():
    raw = FakeRaw()
    connection = pg._Connection(raw, "compcoach", 42)
    inserted = connection.execute("INSERT INTO actions (actor) VALUES (?)", ("Coach",))
    assert inserted.lastrowid == 37
    batch = connection.executemany("UPDATE coach_assignment_history SET ended_by = ? WHERE id = ?", [("Admin", 1), ("Admin", 2)])
    assert batch.rowcount == 2
    writes = [call for call in raw.calls if call[0].startswith("UPDATE")]
    assert len(writes) == 2
    assert raw.info.transaction_status == pg.TransactionStatus.INTRANS


def test_constraint_error_preserves_domain_exception_contract():
    raw = FakeRaw()
    original = raw.execute
    def execute(sql, parameters=None, **kwargs):
        if sql.startswith("UPDATE"):
            raise pg.psycopg.errors.UniqueViolation("secret details")
        return original(sql, parameters, **kwargs)
    raw.execute = execute
    with pytest.raises(sqlite3.IntegrityError) as exc:
        pg._Connection(raw, "compcoach", 42).execute("UPDATE coaches SET name = ?", ["Coach"])
    assert "secret" not in str(exc.value)


def test_connection_setup_uses_only_private_schema_and_bounded_timeouts():
    raw = FakeRaw()
    db = pg.PostgresCompCoachDB("postgresql://postgres@127.0.0.1/postgres", initialize=False)
    try:
        db._configure_connection(raw)
        assert raw.calls == [
            ('SET search_path TO "compcoach", pg_catalog', None),
            ("SET lock_timeout TO '12s'", None),
            ("SET statement_timeout TO '30s'", None),
        ]
    finally:
        db.close()


@pytest.fixture
def postgres_db():
    url = os.environ.get("COMPCOACH_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set COMPCOACH_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    schema = "compcoach_test_" + uuid4().hex
    db = pg.PostgresCompCoachDB(url, schema=schema, pool_max_size=1 if os.environ.get("COMPCOACH_TEST_POSTGRES_PGLITE") == "1" else 4)
    try:
        yield db
    finally:
        with db._connection() as conn:
            conn.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
        db.close()


def _record(phase="de", time=""):
    return {"athlete_id": "athlete_1", "name": "DOE Jamie", "strip": "B1", "pod": "B", "pool": "1" if phase == "pools" else "", "phase": phase, "time": time}


def test_postgres_live_claim_is_atomic_across_connections(postgres_db):
    db = postgres_db
    event = db.create_event("Test", ["Coach A", "Coach B", "Coach C"])
    db.merge_import(event["id"], [_record()], "Admin")
    athlete = db.list_athletes(event["id"])[0]
    db.report_call(event["id"], athlete["id"], status="now", location="B1", actor="Coordinator")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda coach: db.claim(event["id"], athlete["id"], coach), ["Coach A", "Coach B", "Coach C"]))
    assert sum(success for success, _ in results) == 1
    covered = db.get_athlete(event["id"], athlete["id"])
    assert covered["covered_by"] in {"Coach A", "Coach B", "Coach C"}
    assert len([action for action in db.recent_actions(event["id"]) if action["action"] == "claim"]) == 1
    db.release(event["id"], athlete["id"], "Admin", expected_version=covered["version"])
    assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == ""


def test_postgres_assignments_results_undo_history_and_neutral_state(postgres_db):
    db = postgres_db
    event = db.create_event("Test", ["Coach A", "Coach B"], ["Coordinator"])
    db.merge_import(event["id"], [_record("pools", "9:00")], "Admin")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Coach A", actor="Admin")
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Coach B", actor="Admin")
    history = db.list_assignment_history(athlete_id=athlete["id"])
    assert history[0]["ended_at"] is not None
    assert history[-1]["coach_name"] == "Coach B"
    result = db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Coach B")
    with pytest.raises(ConcurrentUpdateError):
        db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Coach B", expected_version=result["version"] - 1)
    action = db.recent_actions(event["id"])[0]
    assert isinstance(action["id"], int)
    db.undo_action(event["id"], action["id"], "Admin")
    assert db.get_athlete(event["id"], athlete["id"])["pool_wins"] is None
    meet = db.get_meet_for_event(event["id"])
    db.finish_competition(meet["competition_id"], "Admin")
    assert db.get_active_competition_day(meet["competition_id"]) is None
    assert db.get_competition(meet["competition_id"])["status"] == "closed"
    with pytest.raises(EventLockedError):
        db.assign_athletes(event["id"], [athlete["id"]], main_coach="Coach A", actor="Admin")
    assert db.list_assignment_history(athlete_id=athlete["id"])


def test_postgres_directory_roles_rename_and_used_coach_order(postgres_db):
    db = postgres_db
    event = db.create_event("Test", ["Zebra Coach", "Alpha Coach"], [])
    meet = db.get_meet_for_event(event["id"])
    coach = next(coach for coach in db.list_coaches() if coach["name"] == "Alpha Coach")
    db.set_competition_coach_roles(meet["competition_id"], coach["id"], ["coach", "coordinator"])
    db.merge_import(event["id"], [_record()], "Admin")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Alpha Coach", actor="Admin")
    rows = db.list_day_coaches(meet["id"])
    assert [row["name"] for row in rows] == ["Zebra Coach", "Alpha Coach"]
    db.update_coach(coach["id"], name="Bravo Coach")
    assert db.get_athlete(event["id"], athlete["id"])["main_coach"] == "Bravo Coach"
    db.add_meet_event(meet["id"], "Second")
    ids = [row["id"] for row in db.list_meet_events(meet["id"])]
    db.reorder_meet_events(meet["id"], list(reversed(ids)))
    assert [row["id"] for row in db.list_meet_events(meet["id"])] == list(reversed(ids))


@pytest.mark.skipif(os.environ.get("COMPCOACH_TEST_POSTGRES_PGLITE") == "1", reason="PGlite's wire emulator returns no ROLLBACK result after a SQL constraint error")
def test_postgres_constraint_failure_rolls_back_and_preserves_domain_message(postgres_db):
    db = postgres_db
    first = db.create_coach("Coach One")
    db.create_coach("Coach Two")
    with pytest.raises(CompCoachError, match="already exists"):
        db.update_coach(first["id"], name="Coach Two")
    assert db.get_coach(first["id"])["name"] == "Coach One"


def test_postgres_wave_pod_presence_help_and_de_workflow(postgres_db):
    db = postgres_db
    event = db.create_event("Day", ["Coach A", "Coach B"], [])
    meet = db.get_meet_for_event(event["id"])
    record_one = _record("pools", "9:00")
    record_two = {**_record("pools", "10:00"), "athlete_id": "athlete_2", "name": "DOE Alex"}
    db.merge_import(event["id"], [record_one, record_two], "Admin")
    waves = db.list_pool_waves(event["id"])
    assert len(waves) == 2
    db.set_pool_wave(event["id"], waves[1]["wave_key"], "Admin", visible=True)
    assert db.list_pool_waves(event["id"])[1]["is_visible"]
    db.set_pool_wave(event["id"], waves[1]["wave_key"], "Admin", active=True)
    assert db.list_pool_waves(event["id"])[1]["is_active"]
    db.assign_pod(event["id"], phase="pools", pod="B", main_coach="Coach A", side_coach="", actor="Admin")
    db.assign_pod(event["id"], phase="pools", pod="B", main_coach="Coach B", side_coach="Coach A", actor="Admin")
    history = db.list_assignment_history(event_id=event["id"])
    assert any(row["target_type"] == "pod" and row["ended_at"] for row in history)
    athlete = db.list_athletes(event["id"])[0]
    help_state = db.request_help(event["id"], athlete["id"], "Coach B", location="B1")
    assert help_state["help_requested_at"]
    db.acknowledge_help(event["id"], athlete["id"], "Coach A")
    assert db.get_athlete(event["id"], athlete["id"])["help_acknowledged_by"] == "Coach A"
    db.clear_help(event["id"], athlete["id"], "Coach B")
    assert db.get_athlete(event["id"], athlete["id"])["help_requested_at"] is None
    coach = next(coach for coach in db.list_coaches() if coach["name"] == "Coach A")
    db.set_day_coach_presence(meet["id"], coach["id"], actor="Admin", presence_status="absent")
    assert not next(row for row in db.list_day_coaches(meet["id"]) if row["name"] == "Coach A")["is_present"]
    db.set_day_coach_presence(meet["id"], coach["id"], actor="Admin", presence_status="present")
    available = db.set_coach_availability(meet["id"], "Coach A", True, "Coach A")
    assert available["is_available"]
    db.merge_import(event["id"], [{**record_one, "phase": "de", "pool": ""}], "Admin")
    de_athlete = next(row for row in db.list_athletes(event["id"]) if row["athlete_key"] == "athlete_1")
    db.mark_result(event["id"], de_athlete["id"], outcome="lost", actor="Coach B")
    assert db.get_athlete(event["id"], de_athlete["id"])["active_state"] == "eliminated"
    db.restore_athlete(event["id"], de_athlete["id"], "Admin")
    assert db.get_athlete(event["id"], de_athlete["id"])["active_state"] == "active"


def test_postgres_private_schema_denies_public_and_preserves_restart(postgres_db):
    db = postgres_db
    event = db.create_event("Durable", ["Coach"])
    restarted = pg.PostgresCompCoachDB(db._conninfo, schema=db.schema)
    assert restarted.get_event(event["id"])["name"] == "Durable"
    restarted.close()
    with db._connection() as conn:
        public = conn.raw.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace AS namespace "
            "CROSS JOIN LATERAL aclexplode(COALESCE(namespace.nspacl, "
            "acldefault('n', namespace.nspowner))) AS privilege "
            "WHERE namespace.nspname = %s AND privilege.grantee = 0 "
            "AND privilege.privilege_type = 'USAGE') AS allowed", (db.schema,),
        ).fetchone()
        assert not public["allowed"]
        path = conn.raw.execute("SHOW search_path").fetchone()["search_path"]
        assert "public" not in path


def test_postgres_pool_reuses_connections_and_recovers_after_broken_session(postgres_db):
    db = postgres_db
    with db._connection() as conn:
        first = conn.raw
        assert conn.raw.execute("SELECT 1 AS value").fetchone()["value"] == 1
    with db._connection() as conn:
        assert conn.raw is first
    first.close()
    with pytest.raises(CompCoachError):
        with db._connection() as conn:
            conn.raw.execute("SELECT 1")
    with db._connection() as conn:
        assert conn.raw is not first
        assert conn.raw.execute("SELECT 1 AS value").fetchone()["value"] == 1


def test_postgres_de_peer_groups_cover_four_pods_preserve_exceptions_and_clear_availability(postgres_db):
    """Exercise new DE history CHECK values and TEXT JSON against PostgreSQL."""
    from compcoach_live.storage import assigned_coaches
    db = postgres_db
    names = ["Coach A", "Coach B", "Coach C", "Coach D"]
    event = db.create_event("DE peer groups", names)
    records = [
        {**_record(), "athlete_id": f"athlete_{pod}", "name": f"TEST {pod}", "strip": f"{pod}1", "pod": pod}
        for pod in ("B", "C", "D", "E")
    ]
    db.merge_import(event["id"], records, "Admin")
    meet = db.get_meet_for_event(event["id"])
    db.set_coach_availability(meet["id"], "Coach C", True, "Coach C")

    assert db.assign_de_pods(event["id"], pods=["B", "C", "D", "E"], coaches=names[:3], actor="Admin") == 4
    athletes = db.list_athletes(event["id"])
    assert all(assigned_coaches(athlete) == names[:3] for athlete in athletes)
    assert all(assignment["coaches"] == names[:3] for assignment in db.list_pod_assignments(event["id"], "de"))
    third = next(row for row in db.list_coach_availability(meet["id"]) if row["coach_name"] == "Coach C")
    assert third["assigned_count"] == third["unfinished_count"] == 4
    assert not third["is_available"]
    history = db.list_assignment_history(event_id=event["id"], include_closed=False)
    assert len(history) == 24  # Four pod and four athlete groups, three peers each.
    assert {row["assignment_kind"] for row in history} == {"de_coach"}

    exception = athletes[0]
    db.assign_de_athletes(event["id"], [exception["id"]], coaches=["Coach D"], actor="Admin")
    db.assign_de_pods(event["id"], pods=["B", "C", "D", "E"], coaches=["Coach D"], actor="Admin", mode="add")
    assert assigned_coaches(db.get_athlete(event["id"], exception["id"])) == ["Coach D"]
    inherited = [row for row in db.list_athletes(event["id"]) if row["id"] != exception["id"]]
    assert all(assigned_coaches(row) == names for row in inherited)
    before = db.list_pod_assignments(event["id"], "de")
    with pytest.raises(ValueError, match="one and four"):
        db.assign_de_pods(event["id"], pods=["A", "B", "C", "D", "E"], coaches=["Coach A"], actor="Admin")
    assert db.list_pod_assignments(event["id"], "de") == before


def test_postgres_same_club_de_result_updates_both_athletes_and_undo_preserves_peers(postgres_db):
    from compcoach_live.de_bouts import create_de_bout, list_de_bouts, resolve_de_bout, undo_de_bout_result
    from compcoach_live.storage import assigned_coaches
    db = postgres_db
    coaches = ["Coach A", "Coach B", "Coach C"]
    event = db.create_event("AFM paired DE", coaches)
    db.merge_import(event["id"], [
        _record(), {**_record(), "athlete_id": "athlete_2", "name": "DOE Avery"},
    ], "Admin")
    db.assign_de_pods(event["id"], pods=["B"], coaches=coaches, actor="Admin")
    first, second = db.list_athletes(event["id"])
    bout = create_de_bout(
        db, event["id"], first["id"], second["id"], actor="Coach C", round_label="T64",
        expected_a_version=first["version"], expected_b_version=second["version"],
    )
    resolved = resolve_de_bout(
        db, event["id"], bout["id"], winner_id=first["id"], actor="Coach B",
        expected_version=bout["version"], expected_a_version=first["version"], expected_b_version=second["version"],
    )
    assert db.get_athlete(event["id"], first["id"])["last_de_result"] == "won"
    assert db.get_athlete(event["id"], second["id"])["active_state"] == "eliminated"
    with pytest.raises(ConcurrentUpdateError):
        resolve_de_bout(db, event["id"], bout["id"], winner_id=first["id"], actor="Coach C", expected_version=bout["version"])
    assert db.get_athlete(event["id"], first["id"])["de_wins"] == 1
    undo_de_bout_result(db, event["id"], bout["id"], actor="Admin", expected_version=resolved["version"])
    restored = list_de_bouts(db, event["id"])[0]
    assert restored["status"] == "pending"
    assert restored["round_label"] == "T64"
    for athlete in (restored["athlete_a"], restored["athlete_b"]):
        assert athlete["active_state"] == "active"
        assert athlete["de_wins"] == 0
        assert assigned_coaches(athlete) == coaches


def test_postgres_restarts_legacy_de_schema_and_upgrades_history_constraint(postgres_db):
    from compcoach_live.storage import assigned_coaches
    db = postgres_db
    names = ["Coach A", "Coach B", "Coach C"]
    event = db.create_event("Legacy DE upgrade", names)
    db.merge_import(event["id"], [_record()], "Admin")
    db.assign_pod(event["id"], phase="de", pod="B", main_coach=names[0], side_coach=names[1], actor="Admin")
    athlete_id = db.list_athletes(event["id"])[0]["id"]
    with db._connection() as connection:
        connection.raw.execute("ALTER TABLE athletes DROP COLUMN de_coaches_json")
        connection.raw.execute("ALTER TABLE pod_assignments DROP COLUMN coaches_json")
        connection.raw.execute("UPDATE coach_assignment_history SET assignment_kind = CASE WHEN coach_name='Coach A' THEN 'main' ELSE 'side' END WHERE phase='de'")
        connection.raw.execute("ALTER TABLE coach_assignment_history DROP CONSTRAINT coach_assignment_history_assignment_kind_check")
        connection.raw.execute("ALTER TABLE coach_assignment_history ADD CONSTRAINT coach_assignment_history_assignment_kind_check CHECK(assignment_kind IN ('main','side','coverage'))")
    db._initialize()
    assert assigned_coaches(db.get_athlete(event["id"], athlete_id)) == names[:2]
    assert db.list_pod_assignments(event["id"], "de")[0]["coaches"] == names[:2]
    assert {row["assignment_kind"] for row in db.list_assignment_history(event_id=event["id"])} == {"de_coach"}
    db.assign_de_pods(event["id"], pods=["B"], coaches=names, actor="Admin")
    assert assigned_coaches(db.get_athlete(event["id"], athlete_id)) == names
    assert {row["coach_name"] for row in db.list_assignment_history(athlete_id=athlete_id, include_closed=False)} == set(names)


def test_postgres_de_bye_and_two_wins_count_three_rounds_with_reversible_bye(postgres_db):
    db = postgres_db
    event = db.create_event("BYE progression", ["Coach A", "Coach B", "Coach C"])
    db.merge_import(event["id"], [_record()], "Admin")
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Coach A", "Coach B", "Coach C"], actor="Admin")
    athlete = db.list_athletes(event["id"])[0]
    bye = db.mark_bye(event["id"], athlete["id"], actor="Coach C", expected_version=athlete["version"])
    assert (bye["de_byes"], bye["de_wins"], bye["de_rounds_passed"]) == (1, 0, 1)
    bye_action = db.recent_actions(event["id"])[0]
    undone = db.undo_action(event["id"], bye_action["id"], "Admin")
    assert (undone["de_byes"], undone["de_wins"], undone["de_rounds_passed"]) == (0, 0, 0)
    bye = db.mark_bye(event["id"], athlete["id"], actor="Coach C", expected_version=undone["version"])
    bye_action = db.recent_actions(event["id"])[0]
    first_win = db.mark_result(event["id"], athlete["id"], outcome="won", actor="Coach B", expected_version=bye["version"])
    second_win = db.mark_result(event["id"], athlete["id"], outcome="won", actor="Coach A", expected_version=first_win["version"])
    assert (second_win["de_byes"], second_win["de_wins"], second_win["de_rounds_passed"]) == (1, 2, 3)
    with pytest.raises(ConcurrentUpdateError):
        db.mark_bye(event["id"], athlete["id"], actor="Coach C", expected_version=bye["version"])
    with pytest.raises(ConcurrentUpdateError):
        db.undo_action(event["id"], bye_action["id"], "Admin")
    assert db.get_athlete(event["id"], athlete["id"])["de_rounds_passed"] == 3


def test_postgres_legacy_schema_adds_zero_byes_without_changing_fenced_wins(postgres_db):
    db = postgres_db
    event = db.create_event("Existing win totals", ["Coach A"])
    db.merge_import(event["id"], [_record()], "Admin")
    athlete = db.list_athletes(event["id"])[0]
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Coach A")
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Coach A")
    with db._connection() as connection:
        connection.raw.execute("ALTER TABLE athletes DROP COLUMN de_byes")
    db._initialize()
    restored = db.get_athlete(event["id"], athlete["id"])
    assert (restored["de_byes"], restored["de_wins"], restored["de_rounds_passed"]) == (0, 2, 2)


def test_postgres_de_progression_resumes_and_correction_preserves_new_assignment(postgres_db):
    from compcoach_live.storage import assigned_coaches
    db = postgres_db
    event = db.create_event("DE current round", ["Coach A", "Coach B", "Coach C"])
    db.merge_import(event["id"], [_record()], "Admin")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Coach A", "Coach B"], actor="Admin")
    athlete = db.get_athlete(event["id"], athlete["id"])
    won = db.mark_result(event["id"], athlete["id"], outcome="won", actor="Coach A", expected_version=athlete["version"])
    assert won["de_awaiting_next"] == 1
    ready = db.resume_de_athlete(event["id"], athlete["id"], "Coach A", expected_version=won["version"])
    assert (ready["de_awaiting_next"], ready["de_wins"]) == (0, 1)
    lost = db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Coach A", expected_version=ready["version"])
    assert (lost["de_awaiting_next"], lost["active_state"]) == (0, "eliminated")
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=["Coach B", "Coach C"], actor="Admin")
    current = db.get_athlete(event["id"], athlete["id"])
    with pytest.raises(ConcurrentUpdateError):
        db.correct_de_result(event["id"], athlete["id"], "Coach A", expected_version=lost["version"])
    restored = db.correct_de_result(event["id"], athlete["id"], "Coach A", expected_version=current["version"])
    assert (restored["de_awaiting_next"], restored["active_state"], restored["de_wins"]) == (0, "active", 1)
    assert assigned_coaches(restored) == ["Coach B", "Coach C"]
    fresh = db.report_call(event["id"], athlete["id"], status="on_deck", location="C4", actor="Coach B", expected_version=restored["version"])
    help_state = db.request_help(event["id"], athlete["id"], "Coach B", location="C4", expected_version=fresh["version"])
    corrected_win = db.correct_de_result(event["id"], athlete["id"], "Coach A", expected_version=help_state["version"])
    assert (corrected_win["de_awaiting_next"], corrected_win["de_wins"], corrected_win["last_de_result"]) == (0, 0, "")
    for field in ("call_status", "live_location", "reported_at", "reported_by", "help_requested_at", "help_requested_by", "help_location"):
        assert corrected_win[field] == help_state[field]
    assert assigned_coaches(corrected_win) == ["Coach B", "Coach C"]


def test_postgres_legacy_upgrade_backfills_only_idle_advanced_de_athletes(postgres_db):
    db = postgres_db
    event = db.create_event("Legacy current rounds", ["Coach A"])
    records = [
        {**_record(), "athlete_id": key, "name": f"TEST {key}"}
        for key in ("won_idle", "bye_idle", "won_called", "won_help", "lost_idle", "pool_idle")
    ]
    db.merge_import(event["id"], records, "Admin")
    with db._connection() as conn:
        conn.raw.execute("UPDATE athletes SET last_de_result='won', de_wins=1 WHERE event_id=%s", (event["id"],))
        conn.raw.execute("UPDATE athletes SET last_de_result='bye', de_wins=0, de_byes=1 WHERE athlete_key='bye_idle'")
        conn.raw.execute("UPDATE athletes SET call_status='on_deck', live_location='B3' WHERE athlete_key='won_called'")
        conn.raw.execute("UPDATE athletes SET help_requested_at='2026-10-01T10:00:00+00:00', help_requested_by='Coach A' WHERE athlete_key='won_help'")
        conn.raw.execute("UPDATE athletes SET last_de_result='lost', active_state='eliminated' WHERE athlete_key='lost_idle'")
        conn.raw.execute("UPDATE athletes SET phase='pools' WHERE athlete_key='pool_idle'")
        conn.raw.execute("ALTER TABLE athletes DROP COLUMN de_awaiting_next")
    db._initialize()
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
    assert {key: row["de_awaiting_next"] for key, row in athletes.items()} == {
        "won_idle": 1, "bye_idle": 1, "won_called": 0,
        "won_help": 0, "lost_idle": 0, "pool_idle": 0,
    }
    db.resume_de_athlete(event["id"], athletes["won_idle"]["id"], "Coach A")
    db._initialize()
    assert db.get_athlete(event["id"], athletes["won_idle"]["id"])["de_awaiting_next"] == 0


def test_postgres_pair_correction_keeps_new_help_call_and_peer_group(postgres_db):
    from compcoach_live.de_bouts import create_de_bout, resolve_de_bout, undo_de_bout_result
    from compcoach_live.storage import assigned_coaches
    db = postgres_db
    event = db.create_event("Paired correction after dispatch", ["Coach A", "Coach B", "Coach C"])
    db.merge_import(event["id"], [_record(), {**_record(), "athlete_id": "athlete_2", "name": "DOE Avery"}], "Admin")
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Coach A", "Coach B"], actor="Admin")
    first, second = db.list_athletes(event["id"])
    bout = create_de_bout(db, event["id"], first["id"], second["id"], actor="Coach A", round_label="T64")
    resolved = resolve_de_bout(db, event["id"], bout["id"], winner_id=first["id"], actor="Coach A", expected_version=bout["version"])
    assert db.get_athlete(event["id"], first["id"])["de_awaiting_next"] == 1
    db.report_call(event["id"], first["id"], status="now", location="C2", actor="Coach B", covered_by="Coach C")
    db.request_help(event["id"], first["id"], "Coach C", location="C2")
    db.assign_de_athletes(event["id"], [second["id"]], coaches=["Coach B", "Coach C"], actor="Admin")
    fresh = db.get_athlete(event["id"], first["id"])
    undo_de_bout_result(db, event["id"], bout["id"], actor="Admin", expected_version=resolved["version"])
    winner = db.get_athlete(event["id"], first["id"])
    loser = db.get_athlete(event["id"], second["id"])
    assert (winner["de_wins"], winner["de_awaiting_next"], loser["active_state"]) == (0, 0, "active")
    for field in ("call_status", "live_location", "reported_at", "covered_by", "covered_at", "help_requested_at", "help_requested_by", "help_location"):
        assert winner[field] == fresh[field]
    assert assigned_coaches(winner) == ["Coach A", "Coach B"]
    assert assigned_coaches(loser) == ["Coach B", "Coach C"]
