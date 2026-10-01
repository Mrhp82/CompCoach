"""Archiving returns a competition to a neutral, durable historical state."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError, EventLockedError


def competition_with_days(tmp_path):
    db = CompCoachDB(tmp_path / "lifecycle.db")
    parent = db.create_competition("October NAC")
    active = db.create_competition_day(
        parent["id"], "Friday", "2026-10-09", ["Coach A"],
        first_event_name="Cadet Epee", day_status="active",
    )
    scheduled = db.create_competition_day(
        parent["id"], "Saturday", "2026-10-10", ["Coach B"],
        first_event_name="Junior Epee", day_status="scheduled",
    )
    empty = db.create_competition_day(
        parent["id"], "Sunday", "2026-10-11", [],
        first_event_name=None, day_status="scheduled",
    )
    for day in (active, scheduled):
        event = db.list_meet_events(day["id"])[0]
        db.merge_import(event["id"], [{
            "athlete_id": f"ath_{day['id']}", "name": "DOE Alex",
            "strip": "B2", "time": "9:00 AM", "pool": "1",
            "pod": "B", "phase": "pools",
        }], "Import")
        athlete = db.list_athletes(event["id"])[0]
        db.assign_athletes(
            event["id"], [athlete["id"]], main_coach=day["active_coaches"][0],
            actor="Coordinator",
        )
    return db, parent, active, scheduled, empty


def test_competition_closure_retains_records_and_archives_every_day(tmp_path):
    db, parent, active, scheduled, empty = competition_with_days(tmp_path)
    original_athletes = {
        event["id"]: db.list_athletes(event["id"])
        for day in (active, scheduled)
        for event in db.list_meet_events(day["id"])
    }
    closed = db.finish_competition(parent["id"], "Coordinator")

    assert closed["status"] == "closed"
    assert closed["ended_at"]
    assert closed["ended_by"] == "Coordinator"
    assert closed["day_count"] == 3
    assert db.get_active_competition_day(parent["id"]) is None
    for day in (active, scheduled, empty):
        archived = db.get_meet(day["id"])
        assert archived["status"] == "locked"
        assert archived["day_status"] == "closed"
        assert archived["ended_at"] == closed["ended_at"]
        assert archived["ended_by"] == "Coordinator"
        for event in db.list_meet_events(day["id"]):
            assert event["status"] == "locked"
            assert db.list_athletes(event["id"]) == original_athletes[event["id"]]
    history = db.list_assignment_history(competition_id=parent["id"])
    assert len(history) == 2
    assert all(row["ended_at"] == closed["ended_at"] for row in history)
    assert all(row["ended_by"] == "Coordinator" for row in history)


def test_closure_retry_preserves_first_actor_and_previous_day_audit(tmp_path):
    db, parent, active, scheduled, _ = competition_with_days(tmp_path)
    previous_day = db.finish_meet(active["id"], "Previous coordinator")
    first = db.finish_competition(parent["id"], "Closing coordinator")
    retry = db.finish_competition(parent["id"], "Other coach")

    assert retry == first
    assert db.get_meet(active["id"])["ended_by"] == "Previous coordinator"
    assert db.get_meet(active["id"])["ended_at"] == previous_day["ended_at"]
    assert db.get_meet(scheduled["id"])["ended_by"] == "Closing coordinator"
    assert len(db.list_assignment_history(competition_id=parent["id"])) == 2


def test_closed_competition_cannot_create_activate_or_prepare_days(tmp_path):
    db, parent, active, scheduled, _ = competition_with_days(tmp_path)
    db.finish_competition(parent["id"], "Coordinator")

    with pytest.raises(EventLockedError, match="competition is closed"):
        db.create_competition_day(
            parent["id"], "New day", "2026-10-12", [], day_status="scheduled"
        )
    with pytest.raises(CompCoachError, match="cannot be reactivated"):
        db.set_day_status(scheduled["id"], "active", "Coordinator")
    with pytest.raises(EventLockedError, match="competition is closed"):
        db.prepare_next_day(
            active["id"], "New day", "2026-10-12", ["Epee"], "Coordinator"
        )
    with pytest.raises(CompCoachError, match="cannot be reopened"):
        db.set_meet_locked(active["id"], False)
    event = db.list_meet_events(active["id"])[0]
    with pytest.raises(CompCoachError, match="cannot be reopened"):
        db.set_event_locked(event["id"], False)
    athlete = db.list_athletes(event["id"])[0]
    with pytest.raises(EventLockedError):
        db.assign_athletes(event["id"], [athlete["id"]], main_coach="Coach B", actor="X")
    assert len(db.list_competition_days(parent["id"])) == 3


def test_parent_closure_is_an_independent_write_guard_and_active_filter(tmp_path):
    db, parent, active, _, _ = competition_with_days(tmp_path)
    # Simulate imported status drift without legitimately reopening a day.
    with db._connection() as conn:
        conn.execute("UPDATE competitions SET status = 'closed' WHERE id = ?", (parent["id"],))
    assert db.get_active_competition_day(parent["id"]) is None
    with pytest.raises(EventLockedError, match="competition is closed"):
        db.set_day_status(active["id"], "active", "Coordinator")
    with pytest.raises(EventLockedError, match="competition is closed"):
        db.add_meet_event(active["id"], "New event")
    event = db.list_meet_events(active["id"])[0]
    with pytest.raises(EventLockedError):
        db.merge_import(event["id"], [{
            "athlete_id": "new", "name": "DOE Bea", "strip": "B1",
            "time": "", "pool": "2", "pod": "B", "phase": "pools",
        }], "Import")
    with pytest.raises(EventLockedError):
        db.set_event_locked(event["id"], False)
    with pytest.raises(EventLockedError):
        db.set_meet_locked(active["id"], False)


def test_closing_day_leaves_parent_open_and_allows_later_day(tmp_path):
    db, parent, active, scheduled, _ = competition_with_days(tmp_path)
    db.finish_meet(active["id"], "Coordinator")
    assert db.get_competition(parent["id"])["status"] == "open"
    assert db.get_active_competition_day(parent["id"]) is None
    db.set_day_status(scheduled["id"], "active", "Coordinator")
    assert db.get_active_competition_day(parent["id"])["id"] == scheduled["id"]


def test_closure_is_atomic_if_history_update_fails(tmp_path):
    db, parent, active, scheduled, _ = competition_with_days(tmp_path)
    with db._connection() as conn:
        conn.execute("""
            CREATE TRIGGER reject_history_closure BEFORE UPDATE ON coach_assignment_history
            BEGIN SELECT RAISE(ABORT, 'test history failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="test history failure"):
        db.finish_competition(parent["id"], "Coordinator")
    assert db.get_competition(parent["id"])["status"] == "open"
    assert db.get_meet(active["id"])["day_status"] == "active"
    assert db.get_meet(scheduled["id"])["day_status"] == "scheduled"
    assert db.get_meet(active["id"])["ended_at"] is None
    assert len(db.list_assignment_history(competition_id=parent["id"], include_closed=False)) == 2
    assert all(event["status"] == "open" for event in db.list_meet_events(active["id"]))


def test_parallel_closure_retries_share_one_original_audit_record(tmp_path):
    db, parent, _, _, _ = competition_with_days(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(
            lambda actor: db.finish_competition(parent["id"], actor),
            ["Coach A", "Coach B"],
        ))
    assert results[0]["ended_by"] == results[1]["ended_by"]
    assert results[0]["ended_at"] == results[1]["ended_at"]
    assert db.get_active_competition_day(parent["id"]) is None


def test_competitions_status_filter_and_empty_parent_archive(tmp_path):
    db = CompCoachDB(tmp_path / "empty.db")
    first = db.create_competition("First")
    second = db.create_competition("Second")
    db.finish_competition(first["id"], "Coordinator")
    assert {row["id"] for row in db.list_competitions()} == {first["id"], second["id"]}
    assert [row["id"] for row in db.list_competitions(status="open")] == [second["id"]]
    assert [row["id"] for row in db.list_competitions(status="closed")] == [first["id"]]
    with pytest.raises(ValueError, match="status"):
        db.list_competitions(status="unknown")
    with pytest.raises(ValueError, match="Actor"):
        db.finish_competition(second["id"], " ")
    with pytest.raises(CompCoachError, match="not found"):
        db.finish_competition("missing", "Coordinator")


def test_v06_competition_schema_gets_additive_lifecycle_migration(tmp_path):
    path = tmp_path / "v06.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE competitions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '', timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                logo_path TEXT NOT NULL DEFAULT '', strip_map_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO competitions (id, name, location, logo_path, created_at, updated_at)
            VALUES ('old', 'Previous NAC', 'Salt Lake', 'logo.png', 'before', 'before')
        """)
    db = CompCoachDB(path)
    row = db.get_competition("old")
    assert row["name"] == "Previous NAC"
    assert row["location"] == "Salt Lake"
    assert row["logo_path"] == "logo.png"
    assert row["status"] == "open"
    assert row["ended_at"] is None
    assert row["ended_by"] == ""
    closed = db.finish_competition("old", "Coordinator")
    assert CompCoachDB(path).get_competition("old") == closed
