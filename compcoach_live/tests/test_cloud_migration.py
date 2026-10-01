"""Migration preserves tournament data and refuses unsafe partial imports.

The PostgreSQL cases use an isolated schema and run when
``COMPCOACH_TEST_POSTGRES_URL`` points at a disposable PostgreSQL server.
No Supabase project or Storage credentials are required.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from compcoach_live import migrate_to_supabase as migration
from compcoach_live.asset_store import InvalidImageError
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_asset_store import image_bytes


def populated_source(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Build real assignments, results, waves and a previous-day relationship."""
    path = tmp_path / "source.db"
    db = CompCoachDB(path)
    competition = db.create_competition("Migration test tournament")
    first = db.create_competition_day(
        competition["id"], "Friday", "2026-10-09", ["Coach Alpha", "Coach Beta"],
        ["Coordinator Gamma"], first_event_name="Cadet Epee", day_status="active",
    )
    event = db.list_meet_events(first["id"])[0]
    db.merge_import(event["id"], [{
        "athlete_id": "athlete_alpha", "name": "TEST Alpha", "strip": "B2",
        "time": "9:00 AM", "pool": "1", "pod": "B", "phase": "pools",
    }, {
        "athlete_id": "athlete_beta", "name": "TEST Beta", "strip": "B10",
        "time": "11:00 AM", "pool": "2", "pod": "B", "phase": "pools",
    }], "Coordinator Gamma")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Coach Alpha",
        side_coach="Coach Beta", actor="Coordinator Gamma",
    )
    db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Coach Alpha")
    second = db.prepare_next_day(
        first["id"], "Saturday", "2026-10-10", ["Junior Epee"], "Coordinator Gamma",
    )
    next_event = db.list_meet_events(second["id"])[0]
    db.merge_import(next_event["id"], [{
        "athlete_id": "athlete_de", "name": "TEST Direct", "strip": "P",
        "time": "", "pool": "", "pod": "P", "phase": "de",
    }], "Coordinator Gamma")
    next_athlete = db.list_athletes(next_event["id"])[0]
    db.assign_pod(
        next_event["id"], phase="de", pod="P", main_coach="Coach Beta",
        side_coach="Coach Alpha", actor="Coordinator Gamma",
    )
    db.report_call(
        next_event["id"], next_athlete["id"], status="on_deck", location="P3",
        actor="Coordinator Gamma",
    )
    db.request_help(next_event["id"], next_athlete["id"], "Coach Beta", location="P3")
    db.set_coach_availability(second["id"], "Coach Alpha", True, "Coach Alpha")
    return path, {
        "competition": competition["id"], "first_day": first["id"],
        "second_day": second["id"], "event": next_event["id"],
        "athlete": next_athlete["id"],
    }


def sqlite_rows(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    return {table: migration.table_rows(connection, table) for table in migration.ordered_tables(connection)}


def make_v06_source(path: Path) -> None:
    """Remove only the competition lifecycle fields absent from v0.6.0."""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("""
            CREATE TABLE competitions_v06 (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                logo_path TEXT NOT NULL DEFAULT '', strip_map_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        columns = "id,name,location,start_date,end_date,timezone,logo_path,strip_map_path,created_at,updated_at"
        connection.execute(f"INSERT INTO competitions_v06 ({columns}) SELECT {columns} FROM competitions")
        connection.execute("DROP TABLE competitions")
        connection.execute("ALTER TABLE competitions_v06 RENAME TO competitions")
        connection.commit()


@pytest.mark.parametrize("legacy", [False, True], ids=["v07", "v06"])
def test_snapshot_upgrades_only_temporary_copy_preserving_ids_and_links(tmp_path, legacy):
    path, ids = populated_source(tmp_path)
    if legacy:
        make_v06_source(path)
    before = path.read_bytes()
    with sqlite3.connect(path) as original:
        original.row_factory = sqlite3.Row
        original_rows = sqlite_rows(original)
        original_columns = [row[1] for row in original.execute("PRAGMA table_info(competitions)")]
    with migration.source_snapshot(path) as snapshot:
        assert snapshot.execute("PRAGMA foreign_key_check").fetchone() is None
        assert "status" in [row[1] for row in snapshot.execute("PRAGMA table_info(competitions)")]
        updated = sqlite_rows(snapshot)
        for table, rows in original_rows.items():
            if table == "competitions" and legacy:
                assert [{key: row[key] for key in original_columns} for row in updated[table]] == rows
                assert all(row["status"] == "open" for row in updated[table])
            else:
                assert updated[table] == rows
        assert updated["meets"][1]["prepared_from_meet_id"] == ids["first_day"]
    assert path.read_bytes() == before
    with sqlite3.connect(path) as original:
        assert [row[1] for row in original.execute("PRAGMA table_info(competitions)")] == original_columns


@pytest.mark.parametrize("package", [False, True], ids=["direct-subfolder", "module-from-parent"])
def test_cli_default_dry_run_has_no_cloud_side_effects_or_sensitive_output(tmp_path, package):
    path, _ = populated_source(tmp_path)
    before = path.read_bytes()
    root = Path(__file__).resolve().parents[2]
    cwd = root if package else root / "compcoach_live"
    command = [sys.executable, "-m", "compcoach_live.migrate_to_supabase"] if package else [sys.executable, "migrate_to_supabase.py"]
    environment = dict(os.environ, COMPCOACH_DATABASE_URL="postgresql://unreachable:private-test-password@example.invalid/test", SUPABASE_SECRET_KEY="private-test-storage-key")
    completed = subprocess.run(
        command + ["--sqlite", str(path)], cwd=cwd, env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "nessun dato remoto modificato" in completed.stdout
    assert "private-test" not in completed.stdout + completed.stderr
    assert "TEST Alpha" not in completed.stdout + completed.stderr
    with sqlite3.connect(path) as original:
        for token in original.execute("SELECT admin_token,coach_token,coordinator_token FROM meets").fetchone():
            assert token not in completed.stdout + completed.stderr
    assert path.read_bytes() == before


def test_explicit_apply_requires_connection_and_never_prints_private_exception(tmp_path, monkeypatch, capsys):
    path, _ = populated_source(tmp_path)
    monkeypatch.setattr(migration, "load_settings", lambda _: {})
    assert migration.main(["--sqlite", str(path), "--apply"]) == 1
    assert "Manca COMPCOACH_DATABASE_URL" in capsys.readouterr().out

    def broken_settings(_):
        raise RuntimeError("postgresql://user:do-not-print-this-password@host/database")
    monkeypatch.setattr(migration, "load_settings", broken_settings)
    assert migration.main(["--sqlite", str(path), "--apply"]) == 1
    assert "do-not-print" not in capsys.readouterr().out


def test_explicit_backup_preserves_preupgrade_schema_and_refuses_overwrite(tmp_path):
    path, _ = populated_source(tmp_path)
    make_v06_source(path)
    backup = tmp_path / "safe-copy.db"
    before = path.read_bytes()
    with migration.source_snapshot(path, backup_file=backup) as snapshot:
        assert "status" in [row[1] for row in snapshot.execute("PRAGMA table_info(competitions)")]
    with sqlite3.connect(backup) as copy, sqlite3.connect(path) as original:
        copy.row_factory = original.row_factory = sqlite3.Row
        assert "status" not in [row[1] for row in copy.execute("PRAGMA table_info(competitions)")]
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert sqlite_rows(copy) == sqlite_rows(original)
    backup_before = backup.read_bytes()
    with pytest.raises(migration.MigrationError):
        with migration.source_snapshot(path, backup_file=backup):
            pytest.fail("An existing backup must never be overwritten")
    assert backup.read_bytes() == backup_before
    assert path.read_bytes() == before


@pytest.mark.parametrize("reference", ["missing.png", "../outside.png", "/absolute.png", "https://example.test/image.png", "supabase://compcoach-assets/image.png"])
def test_asset_references_validated_before_any_target_connection(tmp_path, reference, monkeypatch):
    path, ids = populated_source(tmp_path)
    with sqlite3.connect(path) as source:
        source.execute("UPDATE competitions SET logo_path=? WHERE id=?", (reference, ids["competition"]))
    touched = []
    monkeypatch.setattr(migration, "load_settings", lambda _: touched.append("remote settings"))
    assert migration.main(["--sqlite", str(path), "--assets-dir", str(tmp_path), "--apply"]) == 1
    assert touched == []


def test_invalid_asset_image_fails_local_validation(tmp_path):
    path, ids = populated_source(tmp_path)
    image = tmp_path / "bad.png"
    image.write_bytes(b"not an image")
    with sqlite3.connect(path) as source:
        source.execute("UPDATE competitions SET logo_path=? WHERE id=?", (image.name, ids["competition"]))
    with migration.source_snapshot(path) as snapshot:
        with pytest.raises(InvalidImageError):
            migration.migration_assets(snapshot, tmp_path)


def test_self_referencing_days_are_topological_even_if_source_row_order_is_reversed():
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE days(id TEXT PRIMARY KEY, previous TEXT REFERENCES days(id))")
        connection.executemany("INSERT INTO days VALUES (?,?)", [("third", "second"), ("second", "first"), ("first", None)])
        assert [row["id"] for row in migration.table_rows(connection, "days")] == ["first", "second", "third"]
        connection.execute("UPDATE days SET previous='third' WHERE id='first'")
        with pytest.raises(migration.MigrationError, match="circolari"):
            migration.table_rows(connection, "days")


@pytest.fixture
def postgres_target():
    url = os.environ.get("COMPCOACH_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set COMPCOACH_TEST_POSTGRES_URL to run isolated real PostgreSQL migration tests")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from compcoach_live.postgres_storage import PostgresCompCoachDB
    schema = "compcoach_migration_" + uuid4().hex[:16]
    # Exercise the same noninitializing construction used by the real CLI.
    target = PostgresCompCoachDB(url, schema=schema, initialize=False)
    try:
        yield target
    finally:
        target.close()
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def postgres_rows(target) -> dict[str, list[dict]]:
    from psycopg import sql
    with target._connection() as connection:
        tables = connection.raw.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
            (target.schema,),
        ).fetchall()
        return {row["table_name"]: connection.raw.execute(
            sql.SQL("SELECT * FROM {}").format(sql.Identifier(target.schema, row["table_name"]))
        ).fetchall() for row in tables}


def normalized_rows(rows):
    return sorted(rows, key=lambda row: repr(sorted(row.items())))


def populated_peer_source(tmp_path):
    from compcoach_live.de_bouts import cancel_de_bout, create_de_bout, resolve_de_bout
    path = tmp_path / "de-peers.db"
    db = CompCoachDB(path)
    coaches = ["Coach Alpha", "Coach Beta", "Coach Gamma", "Coach Delta"]
    event = db.create_event("Peer migration", coaches)
    db.merge_import(event["id"], [
        {
            "athlete_id": f"ath_{pod}_{number}", "name": f"TEST {pod} {number}",
            "strip": pod, "time": "", "pool": "", "pod": pod, "phase": "de",
        }
        for pod in ("A", "B", "C", "D") for number in (1, 2)
    ], "Admin")
    db.assign_de_pods(event["id"], pods=["A", "B", "C", "D"], coaches=coaches[:3], actor="Admin")
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
    db.assign_de_athletes(event["id"], [athletes["ath_D_1"]["id"]], coaches=coaches, actor="Admin")
    bouts = {}
    for pod in ("A", "B", "C"):
        bouts[pod] = create_de_bout(
            db, event["id"], athletes[f"ath_{pod}_1"]["id"], athletes[f"ath_{pod}_2"]["id"],
            actor="Coach Gamma", round_label="T64",
        )
    bouts["A"] = resolve_de_bout(db, event["id"], bouts["A"]["id"], winner_id=athletes["ath_A_1"]["id"], actor="Coach Beta")
    bouts["C"] = cancel_de_bout(db, event["id"], bouts["C"]["id"], actor="Coach Alpha")
    return path, event["id"], athletes, bouts, coaches


def test_source_snapshot_preserves_multi_coach_groups_and_all_pairing_states(tmp_path):
    path, _, _, bouts, _ = populated_peer_source(tmp_path)
    before = path.read_bytes()
    with sqlite3.connect(path) as original:
        original.row_factory = sqlite3.Row
        expected = sqlite_rows(original)
    with migration.source_snapshot(path) as snapshot:
        actual = sqlite_rows(snapshot)
        assert actual == expected
        assert {row["id"]: row["status"] for row in actual["de_bouts"]} == {
            bouts["A"]["id"]: "resolved", bouts["B"]["id"]: "pending", bouts["C"]["id"]: "cancelled",
        }
    assert path.read_bytes() == before


def test_source_snapshot_upgrades_legacy_de_slots_without_touching_original(tmp_path):
    """Simulate v0.7.3 without peer JSON columns or manual DE pairings."""
    path, ids = populated_source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE de_bouts")
        connection.execute("ALTER TABLE athletes DROP COLUMN de_coaches_json")
        connection.execute("ALTER TABLE pod_assignments DROP COLUMN coaches_json")
        connection.execute("UPDATE coach_assignment_history SET assignment_kind = CASE WHEN coach_name = 'Coach Beta' THEN 'main' ELSE 'side' END WHERE phase = 'de'")
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        athlete = dict(snapshot.execute("SELECT * FROM athletes WHERE id=?", (ids["athlete"],)).fetchone())
        pod = dict(snapshot.execute("SELECT * FROM pod_assignments WHERE event_id=? AND phase='de'", (ids["event"],)).fetchone())
        from compcoach_live.storage import assigned_coaches
        assert assigned_coaches(athlete) == assigned_coaches(pod) == ["Coach Beta", "Coach Alpha"]
        assert snapshot.execute("SELECT COUNT(*) FROM de_bouts").fetchone()[0] == 0
        assert {row[0] for row in snapshot.execute("SELECT assignment_kind FROM coach_assignment_history WHERE phase='de'")} == {"de_coach"}
        assert {row[0] for row in snapshot.execute("SELECT assignment_kind FROM coach_assignment_history WHERE phase='pools'")} == {"main", "side"}
    assert path.read_bytes() == before


def test_real_postgres_migration_preserves_peer_groups_and_bout_snapshots_for_undo(tmp_path, postgres_target):
    from compcoach_live.de_bouts import list_de_bouts, resolve_de_bout, undo_de_bout_result
    from compcoach_live.storage import assigned_coaches
    path, event_id, athletes, bouts, coaches = populated_peer_source(tmp_path)
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        expected = sqlite_rows(snapshot)
        migration.migrate(snapshot, postgres_target, [])
    actual = postgres_rows(postgres_target)
    assert set(actual) == set(expected)
    for table, rows in expected.items():
        assert normalized_rows(actual[table]) == normalized_rows(rows), table
    assert path.read_bytes() == before
    assert assigned_coaches(postgres_target.get_athlete(event_id, athletes["ath_D_1"]["id"])) == coaches
    undo_de_bout_result(postgres_target, event_id, bouts["A"]["id"], actor="Admin", expected_version=bouts["A"]["version"])
    restored = postgres_target.get_athlete(event_id, athletes["ath_A_2"]["id"])
    assert restored["active_state"] == "active"
    assert assigned_coaches(restored) == coaches[:3]
    resolve_de_bout(postgres_target, event_id, bouts["B"]["id"], winner_id=athletes["ath_B_2"]["id"], actor="Coach Gamma", expected_version=bouts["B"]["version"])
    assert {row["id"]: row["status"] for row in list_de_bouts(postgres_target, event_id, include_cancelled=True)} == {
        bouts["A"]["id"]: "pending", bouts["B"]["id"]: "resolved", bouts["C"]["id"]: "cancelled",
    }


def test_source_snapshot_adds_zero_byes_to_legacy_athletes_without_changing_source(tmp_path):
    path, ids = populated_source(tmp_path)
    db = CompCoachDB(path)
    db.mark_result(ids["event"], ids["athlete"], outcome="won", actor="Coach Alpha")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE athletes DROP COLUMN de_byes")
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        athlete = dict(snapshot.execute("SELECT * FROM athletes WHERE id=?", (ids["athlete"],)).fetchone())
        assert (athlete["de_byes"], athlete["de_wins"]) == (0, 1)
    assert path.read_bytes() == before


def test_source_snapshot_upgrades_legacy_round_queue_without_mutating_source(tmp_path):
    path, event_id, athletes, _, _ = populated_peer_source(tmp_path)
    db = CompCoachDB(path)
    bye_id = athletes["ath_D_1"]["id"]
    called_id = athletes["ath_D_2"]["id"]
    db.mark_bye(event_id, bye_id, actor="Coach Gamma")
    db.mark_result(event_id, called_id, outcome="won", actor="Coach Gamma")
    db.report_call(event_id, called_id, status="on_deck", location="D4", actor="Coach Gamma")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE athletes DROP COLUMN de_awaiting_next")
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        states = {
            row["athlete_key"]: row["de_awaiting_next"]
            for row in snapshot.execute("SELECT athlete_key,de_awaiting_next FROM athletes WHERE event_id=?", (event_id,))
        }
        assert states == {
            "ath_A_1": 1, "ath_A_2": 0,
            "ath_B_1": 0, "ath_B_2": 0,
            "ath_C_1": 0, "ath_C_2": 0,
            "ath_D_1": 1, "ath_D_2": 0,
        }
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert "de_awaiting_next" not in {row[1] for row in connection.execute("PRAGMA table_info(athletes)")}


@pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
def test_real_postgres_migration_preserves_round_queue_and_selective_correction(tmp_path, postgres_target, legacy):
    from compcoach_live.storage import assigned_coaches
    path, event_id, athletes, _, coaches = populated_peer_source(tmp_path)
    db = CompCoachDB(path)
    idle_id = athletes["ath_D_1"]["id"]
    called_id = athletes["ath_D_2"]["id"]
    db.mark_bye(event_id, idle_id, actor="Coach Gamma")
    db.mark_result(event_id, called_id, outcome="won", actor="Coach Gamma")
    db.report_call(event_id, called_id, status="on_deck", location="D4", actor="Coach Gamma")
    db.request_help(event_id, called_id, "Coach Gamma", location="D4")
    db.assign_de_athletes(event_id, [called_id], coaches=coaches[1:], actor="Admin")
    if legacy:
        with sqlite3.connect(path) as connection:
            connection.execute("ALTER TABLE athletes DROP COLUMN de_awaiting_next")
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        expected = sqlite_rows(snapshot)
        migration.migrate(snapshot, postgres_target, [])
    actual = postgres_rows(postgres_target)
    for table, rows in expected.items():
        assert normalized_rows(actual[table]) == normalized_rows(rows), table
    assert path.read_bytes() == before
    idle = postgres_target.get_athlete(event_id, idle_id)
    called = postgres_target.get_athlete(event_id, called_id)
    assert (idle["de_awaiting_next"], called["de_awaiting_next"]) == (1, 0)
    ready = postgres_target.resume_de_athlete(event_id, idle_id, "Coach Gamma", expected_version=idle["version"])
    assert (ready["de_awaiting_next"], ready["de_byes"]) == (0, 1)
    corrected = postgres_target.correct_de_result(event_id, called_id, "Coach Gamma", expected_version=called["version"])
    assert (corrected["de_awaiting_next"], corrected["de_wins"]) == (0, 0)
    assert assigned_coaches(corrected) == coaches[1:]
    for field in ("call_status", "live_location", "reported_at", "help_requested_at", "help_requested_by", "help_location"):
        assert corrected[field] == called[field]


def test_real_postgres_migration_preserves_two_byes_one_win_and_can_undo_latest_bye(tmp_path, postgres_target):
    path, event_id, athletes, _, _ = populated_peer_source(tmp_path)
    db = CompCoachDB(path)
    athlete_id = athletes["ath_D_2"]["id"]
    db.mark_bye(event_id, athlete_id, actor="Coach Gamma")
    db.mark_result(event_id, athlete_id, outcome="won", actor="Coach Gamma")
    db.mark_bye(event_id, athlete_id, actor="Coach Gamma")
    latest_bye = db.recent_actions(event_id)[0]
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        expected = sqlite_rows(snapshot)
        migration.migrate(snapshot, postgres_target, [])
    actual = postgres_rows(postgres_target)
    for table, rows in expected.items():
        assert normalized_rows(actual[table]) == normalized_rows(rows), table
    assert path.read_bytes() == before
    migrated = postgres_target.get_athlete(event_id, athlete_id)
    assert (migrated["de_byes"], migrated["de_wins"], migrated["de_rounds_passed"]) == (2, 1, 3)
    undone = postgres_target.undo_action(event_id, latest_bye["id"], "Admin")
    assert (undone["de_byes"], undone["de_wins"], undone["de_rounds_passed"]) == (1, 1, 2)


@pytest.mark.parametrize("legacy", [False, True], ids=["v07", "v06"])
def test_real_postgres_import_preserves_every_column_and_future_audit_ids(tmp_path, postgres_target, legacy):
    path, ids = populated_source(tmp_path)
    if legacy:
        make_v06_source(path)
    before = path.read_bytes()
    with migration.source_snapshot(path) as snapshot:
        source_rows = sqlite_rows(snapshot)
        counts = migration.migrate(snapshot, postgres_target, [])
    actual = postgres_rows(postgres_target)
    assert set(actual) == set(source_rows)
    assert counts == {table: len(rows) for table, rows in source_rows.items()}
    for table, rows in source_rows.items():
        assert normalized_rows(actual[table]) == normalized_rows(rows), table
    assert path.read_bytes() == before

    max_action = max(row["id"] for row in source_rows["actions"])
    max_history = max(row["id"] for row in source_rows["coach_assignment_history"])
    # A retained peer keeps the same history interval; actually remove and
    # reassign the other peer to exercise the migrated serial sequence.
    postgres_target.assign_de_athletes(
        ids["event"], [ids["athlete"]], coaches=["Coach Alpha"], actor="Coordinator Gamma",
    )
    postgres_target.assign_de_athletes(
        ids["event"], [ids["athlete"]], coaches=["Coach Beta"], actor="Coordinator Gamma",
    )
    after = postgres_rows(postgres_target)
    assert max(row["id"] for row in after["actions"]) > max_action
    assert max(row["id"] for row in after["coach_assignment_history"]) > max_history


def test_real_postgres_populated_target_refuses_import_without_changes(tmp_path, postgres_target):
    path, _ = populated_source(tmp_path)
    postgres_target._initialize()
    postgres_target.create_competition("Existing target competition")
    before = postgres_rows(postgres_target)
    with migration.source_snapshot(path) as snapshot:
        with pytest.raises(migration.MigrationError, match="contiene già dati"):
            migration.migrate(snapshot, postgres_target, [])
    assert postgres_rows(postgres_target) == before


def test_cli_populated_legacy_target_remains_unbootstrapped_and_unchanged(tmp_path, postgres_target, monkeypatch):
    """Even creating a backend cannot backfill an already-used legacy target."""
    from psycopg import sql
    from compcoach_live import postgres_storage
    path, _ = populated_source(tmp_path)
    with postgres_target._connection() as connection:
        connection.raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(postgres_target.schema)))
        connection.raw.execute(sql.SQL("CREATE TABLE {} (id TEXT PRIMARY KEY, marker TEXT NOT NULL)").format(sql.Identifier(postgres_target.schema, "events")))
        connection.raw.execute(sql.SQL("INSERT INTO {} VALUES ('existing-id', 'legacy record')").format(sql.Identifier(postgres_target.schema, "events")))
    before = postgres_rows(postgres_target)
    construction = []
    real_constructor = postgres_storage.CompCoachPostgresDB

    def constructor(url, **options):
        construction.append(options)
        return real_constructor(url, schema=postgres_target.schema, **options)

    monkeypatch.setattr(postgres_storage, "CompCoachPostgresDB", constructor)
    monkeypatch.setattr(migration, "load_settings", lambda _: {"COMPCOACH_DATABASE_URL": os.environ["COMPCOACH_TEST_POSTGRES_URL"]})
    assert migration.main(["--sqlite", str(path), "--apply"]) == 1
    assert construction == [{"initialize": False}]
    assert postgres_rows(postgres_target) == before


def add_asset_refs(path, ids, tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    for name in ("logo.png", "map.png"):
        (root / name).write_bytes(image_bytes())
    with sqlite3.connect(path) as source:
        source.execute(
            "UPDATE competitions SET logo_path='logo.png',strip_map_path='map.png' WHERE id=?",
            (ids["competition"],),
        )
    return root


def test_real_postgres_asset_references_are_updated_without_changing_source(tmp_path, postgres_target):
    path, ids = populated_source(tmp_path)
    root = add_asset_refs(path, ids, tmp_path)
    before = path.read_bytes()

    class SuccessfulStorage:
        def save_logo(self, competition_id, payload, **options):
            assert options["overwrite"] is False
            return SimpleNamespace(object_key=f"competitions/{competition_id}/logo.png")
        def save_strip_map(self, competition_id, payload, **options):
            assert options["overwrite"] is False
            return SimpleNamespace(object_key=f"competitions/{competition_id}/strip-maps/main.png")

    with migration.source_snapshot(path) as snapshot:
        assets = migration.migration_assets(snapshot, root)
        migration.migrate(snapshot, postgres_target, assets, asset_store=SuccessfulStorage())
    competition = postgres_target.get_competition(ids["competition"])
    assert competition["logo_path"] == f"competitions/{ids['competition']}/logo.png"
    assert competition["strip_map_path"] == f"competitions/{ids['competition']}/strip-maps/main.png"
    assert path.read_bytes() == before


def test_real_postgres_failed_second_asset_rolls_back_data_and_only_deletes_new_object(tmp_path, postgres_target):
    path, ids = populated_source(tmp_path)
    root = add_asset_refs(path, ids, tmp_path)
    deleted = []

    class FailingStorage:
        def save_logo(self, competition_id, payload, **options):
            assert options["overwrite"] is False
            return SimpleNamespace(object_key="created-by-this-attempt/logo.png")
        def save_strip_map(self, competition_id, payload, **options):
            raise RuntimeError("Simulated upload failure")
        def delete_reference(self, reference):
            deleted.append(reference)

    with migration.source_snapshot(path) as snapshot:
        assets = migration.migration_assets(snapshot, root)
        with pytest.raises(RuntimeError, match="Simulated upload failure"):
            migration.migrate(snapshot, postgres_target, assets, asset_store=FailingStorage())
    assert all(not rows for rows in postgres_rows(postgres_target).values())
    assert deleted == ["created-by-this-attempt/logo.png"]


def test_commit_acknowledgement_loss_keeps_images_referenced_by_committed_rows(tmp_path, postgres_target):
    """A client COMMIT exception does not prove that the server rolled back."""
    path, ids = populated_source(tmp_path)
    root = add_asset_refs(path, ids, tmp_path)
    uploaded, deleted = [], []

    class Storage:
        def save_logo(self, competition_id, payload, **options):
            uploaded.append("created/logo.png")
            return SimpleNamespace(object_key=uploaded[-1])
        def save_strip_map(self, competition_id, payload, **options):
            uploaded.append("created/map.png")
            return SimpleNamespace(object_key=uploaded[-1])
        def delete_reference(self, reference):
            deleted.append(reference)

    class AmbiguousCommitTarget:
        schema = postgres_target.schema
        initialize_schema = postgres_target.initialize_schema

        @contextmanager
        def _connection(self):
            with postgres_target._connection() as connection:
                class Connection:
                    raw = connection.raw
                    execute = connection.execute

                    def commit(self):
                        connection.commit()
                        raise ConnectionError("Simulated lost COMMIT acknowledgement")
                yield Connection()

    with migration.source_snapshot(path) as snapshot:
        assets = migration.migration_assets(snapshot, root)
        with pytest.raises(migration.MigrationError, match="Non ripetere --apply"):
            migration.migrate(snapshot, AmbiguousCommitTarget(), assets, asset_store=Storage())
    assert deleted == []
    assert uploaded == ["created/logo.png", "created/map.png"]
    competition = postgres_target.get_competition(ids["competition"])
    assert competition["logo_path"] == uploaded[0]
    assert competition["strip_map_path"] == uploaded[1]
    assert postgres_target.get_meet(ids["second_day"])["prepared_from_meet_id"] == ids["first_day"]
