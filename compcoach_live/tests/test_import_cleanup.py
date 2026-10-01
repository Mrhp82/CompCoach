"""Older screenshot residue disappears without deleting tournament records."""

from __future__ import annotations

import sqlite3

import pytest

from compcoach_live.import_cleanup import quarantine_import_noise
from compcoach_live.storage import CompCoachDB, ConcurrentUpdateError, EventLockedError


def setup_legacy_row(tmp_path):
    path = tmp_path / "cleanup.db"
    db = CompCoachDB(path)
    event = db.create_event("October NAC", ["Coach A", "Coach B"])
    event_id = event["id"]
    db.merge_import(event_id, [
        {"athlete_id": "legacy_noise", "name": "Legacy row", "strip": "VE",
         "pod": "VE", "phase": "de"},
        {"athlete_id": "real_fencer", "name": "Max VE", "strip": "VE",
         "pod": "VE", "phase": "de"},
    ], "Old import")
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event_id)}
    noise = athletes["legacy_noise"]
    # Simulate an existing database predating the parser/merge noise filter.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE athletes SET name = ? WHERE id = ?",
                     ("fe melive.com", noise["id"]))
    return db, event, db.get_athlete(event_id, noise["id"]), athletes["real_fencer"]


def test_cleanup_quarantines_legacy_residue_preserving_results_and_assignments(tmp_path):
    db, event, noise, real = setup_legacy_row(tmp_path)
    event_id = event["id"]
    db.assign_de_athletes(event_id, [noise["id"]], coaches=["Coach A", "Coach B"], actor="Setup")
    db.mark_result(event_id, noise["id"], outcome="won", actor="Coach A")
    db.report_call(event_id, noise["id"], status="on_deck", location="VE1", actor="Coach A")
    db.request_help(event_id, noise["id"], "Coach A")
    before = db.get_athlete(event_id, noise["id"])
    before_history = db.list_assignment_history(event_id=event_id)
    before_real = db.get_athlete(event_id, real["id"])

    assert quarantine_import_noise(db, event_id) == 1

    after = db.get_athlete(event_id, noise["id"])
    for field in ("id", "name", "pod", "source_strip", "de_coaches_json", "de_wins",
                  "de_byes", "last_de_result", "active_state"):
        assert after[field] == before[field]
    assert after["participation_status"] == "withdrawn"
    assert after["call_status"] == "waiting"
    assert after["reported_at"] is None
    assert after["help_requested_at"] is None
    assert after["version"] == before["version"] + 1
    assert db.get_athlete(event_id, real["id"]) == before_real
    assert db.list_assignment_history(event_id=event_id) == before_history
    assert len(db.list_athletes(event_id)) == 2
    action = db.recent_actions(event_id)[0]
    assert action["action"] == "participation_withdrawn"
    assert action["actor"] == "Import cleanup"
    assert action["previous"]["participation_status"] == "active"


def test_cleanup_is_idempotent_and_restore_retains_row(tmp_path):
    db, event, noise, _ = setup_legacy_row(tmp_path)
    assert quarantine_import_noise(db, event["id"]) == 1
    after = db.get_athlete(event["id"], noise["id"])
    audit_before = db.recent_actions(event["id"])
    assert quarantine_import_noise(db, event["id"]) == 0
    assert db.get_athlete(event["id"], noise["id"]) == after
    assert db.recent_actions(event["id"]) == audit_before
    restored = db.restore_athlete_participation(
        event["id"], noise["id"], "Coordinator", expected_version=after["version"]
    )
    assert restored["id"] == noise["id"]
    assert restored["participation_status"] == "active"


@pytest.mark.parametrize("closed_scope", ["event", "meet", "competition"])
def test_cleanup_does_not_write_to_locked_or_archived_records(tmp_path, closed_scope):
    db, event, noise, _ = setup_legacy_row(tmp_path)
    if closed_scope == "event":
        db.set_event_locked(event["id"], True)
    elif closed_scope == "meet":
        db.set_meet_locked(event["meet_id"], True)
    else:
        meet = db.get_meet(event["meet_id"])
        db.finish_competition(meet["competition_id"], "Coordinator")
    before = db.get_athlete(event["id"], noise["id"])
    audits = db.recent_actions(event["id"])
    assert quarantine_import_noise(db, event["id"]) == 0
    assert db.get_athlete(event["id"], noise["id"]) == before
    assert db.recent_actions(event["id"]) == audits


@pytest.mark.parametrize("participation", ["absent", "withdrawn"])
def test_cleanup_skips_attendance_rows_already_excluded(tmp_path, participation):
    db, event, noise, _ = setup_legacy_row(tmp_path)
    db.set_athlete_participation(event["id"], noise["id"], participation, "Coordinator")
    before = db.get_athlete(event["id"], noise["id"])
    assert quarantine_import_noise(db, event["id"]) == 0
    assert db.get_athlete(event["id"], noise["id"]) == before


@pytest.mark.parametrize("failure", [ConcurrentUpdateError, EventLockedError])
def test_cleanup_leaves_rows_changed_during_scan_untouched(tmp_path, monkeypatch, failure):
    db, event, noise, _ = setup_legacy_row(tmp_path)
    def reject(*args, **kwargs):
        assert kwargs["expected_version"] == noise["version"]
        raise failure("Changed on another phone")
    monkeypatch.setattr(db, "set_athlete_participation", reject)
    assert quarantine_import_noise(db, event["id"]) == 0
    assert db.get_athlete(event["id"], noise["id"])["participation_status"] == "active"


def test_cleanup_does_not_silently_hide_unexpected_database_failure(tmp_path, monkeypatch):
    db, event, _, _ = setup_legacy_row(tmp_path)
    def reject(*args, **kwargs):
        raise RuntimeError("Database unavailable")
    monkeypatch.setattr(db, "set_athlete_participation", reject)
    with pytest.raises(RuntimeError, match="Database unavailable"):
        quarantine_import_noise(db, event["id"])


def test_cleanup_ignores_missing_event(tmp_path):
    db = CompCoachDB(tmp_path / "empty.db")
    assert quarantine_import_noise(db, "missing") == 0
