"""Completing Pools never requires inventing an uncertain win/loss tally."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from compcoach_live.pool_results import pool_has_results, pool_is_complete
from compcoach_live.storage import (
    CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError,
)


def record(key="one", phase="pools"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
            "pod": "B", "strip": "B1", "pool": "1" if phase == "pools" else ""}


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "unknown.db")
    meet = db.create_meet("NAC", ["Alex", "Jordan", "Taylor"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [record(), record("two")], "Casey")
    rows = db.list_athletes(event["id"])
    db.assign_athletes(event["id"], [a["id"] for a in rows], main_coach="Alex", actor="Casey")
    return db, meet, event, rows


def availability(db, meet, name="Alex"):
    return next(a for a in db.list_coach_availability(meet["id"]) if a["coach_name"] == name)


def complete(day, index=0, **kwargs):
    db, _, event, rows = day
    return db.set_pool_done_unknown(event["id"], rows[index]["id"], "Alex", **kwargs)


def latest(db, event, action):
    return next(a for a in db.recent_actions(event["id"]) if a["action"] == action)


def test_completion_has_no_fake_zero_score_and_persists_per_athlete(day):
    db, _, event, rows = day
    original = db.get_athlete(event["id"], rows[0]["id"])
    saved = complete(day, expected_version=original["version"])
    assert saved["pool_wins"] is saved["pool_losses"] is None
    assert saved["pool_results_unknown"] == 1
    assert saved["pool_result_at"] and saved["pool_result_by"] == "Alex"
    assert pool_is_complete(saved) and not pool_has_results(saved)
    assert saved["version"] == original["version"] + 1
    assert saved["main_coach"] == "Alex" and saved["pool_no"] == "1"
    assert saved["participation_status"] == saved["active_state"] == "active"
    assert CompCoachDB(db.path).get_athlete(event["id"], saved["id"]) == saved
    assert not pool_is_complete(db.get_athlete(event["id"], rows[1]["id"]))


def test_override_known_results_retains_previous_tally_in_audit(day):
    db, _, event, rows = day
    known = db.set_pool_result(event["id"], rows[0]["id"], wins=4, losses=2, actor="Taylor")
    saved = complete(day, expected_version=known["version"])
    audit = latest(db, event, "pool_done_unknown")
    assert audit["actor"] == "Alex"
    assert audit["previous"]["pool_wins"] == 4 and audit["previous"]["pool_losses"] == 2
    assert audit["previous"]["pool_result_by"] == "Taylor"
    assert audit["new"]["pool_results_unknown"] == 1
    assert audit["new"]["pool_wins"] is audit["new"]["pool_losses"] is None
    assert audit["version_after"] == saved["version"]


def test_confirmed_results_replace_unknown_completion(day):
    db, _, event, _ = day
    unknown = complete(day)
    known = db.set_pool_result(event["id"], unknown["id"], wins=3, losses=3,
                               actor="Alex", expected_version=unknown["version"])
    assert known["pool_results_unknown"] == 0
    assert (known["pool_wins"], known["pool_losses"]) == (3, 3)
    assert pool_has_results(known) and pool_is_complete(known)
    assert latest(db, event, "pool_result")["previous"]["pool_results_unknown"] == 1


def test_reopen_corrects_completion_without_resurrecting_live_data(day):
    db, _, event, rows = day
    db.report_call(event["id"], rows[0]["id"], status="on_deck", location="C3", actor="Casey")
    db.request_help(event["id"], rows[0]["id"], "Alex")
    db.cover_athlete(event["id"], rows[0]["id"], "Alex", "Alex")
    unknown = complete(day)
    reopened = db.reopen_pool(event["id"], unknown["id"], "Alex", expected_version=unknown["version"])
    assert reopened["pool_results_unknown"] == 0
    assert reopened["pool_wins"] is reopened["pool_losses"] is reopened["pool_result_at"] is None
    assert reopened["pool_result_by"] == ""
    assert not pool_is_complete(reopened)
    for field in ("covered_by", "takeover_coach", "live_location", "reported_by", "help_requested_by"):
        assert reopened[field] == ""
    assert reopened["call_status"] == "waiting"
    assert latest(db, event, "pool_reopened")["previous"]["pool_results_unknown"] == 1


@pytest.mark.parametrize("known", [False, True])
def test_reopen_requires_unknown_completion(day, known):
    db, _, event, rows = day
    if known:
        db.set_pool_result(event["id"], rows[0]["id"], wins=2, losses=4, actor="Alex")
    before = db.get_athlete(event["id"], rows[0]["id"])
    with pytest.raises(CompCoachError, match="unknown"):
        db.reopen_pool(event["id"], before["id"], "Alex")
    assert db.get_athlete(event["id"], before["id"]) == before


@pytest.mark.parametrize("operation", ["unknown", "known", "reopen"])
def test_stale_snapshot_cannot_replace_newer_completion(day, operation):
    db, _, event, rows = day
    old = db.get_athlete(event["id"], rows[0]["id"])
    saved = complete(day)
    with pytest.raises(ConcurrentUpdateError):
        if operation == "unknown":
            complete(day, expected_version=old["version"])
        elif operation == "known":
            db.set_pool_result(event["id"], saved["id"], wins=2, losses=4,
                               actor="Alex", expected_version=old["version"])
        else:
            db.reopen_pool(event["id"], saved["id"], "Alex", expected_version=old["version"])
    assert db.get_athlete(event["id"], saved["id"]) == saved


def test_concurrent_known_and_unknown_have_one_winning_result(day):
    db, _, event, rows = day
    original = db.get_athlete(event["id"], rows[0]["id"])
    def write(kind):
        try:
            if kind == "unknown":
                complete(day, expected_version=original["version"])
            else:
                db.set_pool_result(event["id"], original["id"], wins=1, losses=5,
                                   actor="Alex", expected_version=original["version"])
            return "saved"
        except ConcurrentUpdateError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(write, ["unknown", "known"])) == ["saved", "stale"]
    saved = db.get_athlete(event["id"], original["id"])
    assert saved["version"] == original["version"] + 1
    assert (saved["pool_wins"] is None) == bool(saved["pool_results_unknown"])
    assert sum(a["action"] in {"pool_result", "pool_done_unknown"}
               for a in db.recent_actions(event["id"])) == 1


@pytest.mark.parametrize("operation", ["unknown", "reopen"])
def test_closed_event_rejects_completion_and_reopen(day, operation):
    db, _, event, rows = day
    if operation == "reopen":
        complete(day)
    db.set_event_locked(event["id"], True)
    with pytest.raises(EventLockedError):
        if operation == "unknown":
            complete(day)
        else:
            db.reopen_pool(event["id"], rows[0]["id"], "Alex")


@pytest.mark.parametrize("participation", ["absent", "withdrawn"])
def test_nonparticipating_athlete_cannot_be_completed(day, participation):
    db, _, event, rows = day
    db.set_athlete_participation(event["id"], rows[0]["id"], participation, "Alex")
    before = db.get_athlete(event["id"], rows[0]["id"])
    with pytest.raises(CompCoachError):
        complete(day)
    assert db.get_athlete(event["id"], rows[0]["id"]) == before


def test_de_phase_rejects_pool_actions_but_keeps_completion_history(day):
    db, _, event, rows = day
    unknown = complete(day)
    db.merge_import(event["id"], [record(phase="de"), record("two", phase="de")], "Casey")
    after = db.get_athlete(event["id"], unknown["id"])
    assert after["pool_results_unknown"] == 1
    assert after["pool_result_at"] == unknown["pool_result_at"]
    with pytest.raises(CompCoachError, match="Pools"):
        complete(day)
    with pytest.raises(CompCoachError, match="Pools"):
        db.reopen_pool(event["id"], rows[0]["id"], "Alex")
    db.merge_import(event["id"], [record(), record("two")], "Casey")
    next_pools = db.get_athlete(event["id"], unknown["id"])
    assert next_pools["pool_results_unknown"] == 0 and next_pools["pool_result_at"] is None


def test_unknown_completion_excluded_from_coach_unfinished_work(day):
    db, meet, _, _ = day
    assert availability(db, meet)["unfinished_count"] == 2
    complete(day)
    assert availability(db, meet)["unfinished_count"] == 1
    complete(day, index=1)
    assert availability(db, meet)["unfinished_count"] == 0


def test_finishing_last_physical_coverage_releases_coach_and_cleans_help(day):
    db, meet, event, rows = day
    db.report_call(event["id"], rows[0]["id"], status="now", location="J4", actor="Casey")
    db.cover_athlete(event["id"], rows[0]["id"], "Alex", "Alex")
    db.request_help(event["id"], rows[0]["id"], "Alex")
    assert availability(db, meet)["is_busy"]
    result = complete(day)
    status = availability(db, meet)
    assert not status["is_busy"] and status["is_available"]
    assert result["covered_by"] == "" and result["covered_at"] is None
    assert result["help_requested_at"] is result["help_acknowledged_at"] is None
    assert result["call_status"] == "waiting" and result["live_location"] == ""


def test_takeover_completion_releases_only_when_no_other_live_duty(day):
    db, meet, event, rows = day
    db.take_over_athlete(event["id"], rows[0]["id"], "Jordan", "Jordan")
    db.take_over_athlete(event["id"], rows[1]["id"], "Jordan", "Jordan")
    saved = complete(day)
    assert saved["takeover_coach"] == "" and saved["takeover_at"] is None
    assert not availability(db, meet, "Jordan")["is_available"]
    complete(day, index=1)
    assert availability(db, meet, "Jordan")["is_available"]


def test_pending_coverage_offer_is_atomically_superseded(day):
    db, meet, event, rows = day
    db.set_coach_availability(meet["id"], "Jordan", True, "Jordan")
    request = db.create_coverage_request(meet["id"], event["id"], rows[0]["id"], ["Jordan"], "Casey")
    complete(day)
    assert db.list_coverage_requests(meet["id"]) == []
    archived = db.list_coverage_requests(meet["id"], pending_only=False)[0]
    assert archived["id"] == request["id"] and archived["status"] == "superseded"
    assert archived["close_reason"] == "pool_done_unknown"
    with pytest.raises(ConcurrentUpdateError):
        db.accept_coverage_request(meet["id"], request["id"], "Jordan")


def test_additive_migration_keeps_existing_known_results(tmp_path):
    path = tmp_path / "legacy.db"
    db = CompCoachDB(path)
    event = db.create_event("Existing", ["Alex"])
    db.merge_import(event["id"], [record()], "Alex")
    athlete = db.list_athletes(event["id"])[0]
    saved = db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Alex")
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE athletes DROP COLUMN pool_results_unknown")
    upgraded = CompCoachDB(path)
    current = upgraded.get_athlete(event["id"], athlete["id"])
    assert current["pool_results_unknown"] == 0
    assert all(current[key] == value for key, value in saved.items() if key != "pool_results_unknown")
    upgraded.set_pool_done_unknown(event["id"], current["id"], "Alex")
    assert CompCoachDB(path).get_athlete(event["id"], current["id"])["pool_results_unknown"] == 1


def test_backup_and_next_day_keep_historical_unknown_completion(day, tmp_path):
    db, meet, event, rows = day
    complete(day)
    backup_path = tmp_path / "backup.db"
    with sqlite3.connect(db.path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
    copy = CompCoachDB(backup_path)
    assert copy.get_athlete(event["id"], rows[0]["id"])["pool_results_unknown"] == 1
    next_day = db.prepare_next_day(meet["id"], "Next day", "2026-10-10", ["Juniors"], "Casey")
    assert db.get_athlete(event["id"], rows[0]["id"])["pool_results_unknown"] == 1
    next_event = db.list_meet_events(next_day["id"])[0]
    db.merge_import(next_event["id"], [record()], "Casey")
    fresh = db.list_athletes(next_event["id"])[0]
    assert fresh["pool_results_unknown"] == 0 and not pool_is_complete(fresh)


def test_manual_postgres_schema_upgrades_existing_athletes():
    schema = (Path(__file__).resolve().parents[1] / "postgres_schema.sql").read_text()
    assert "ALTER TABLE athletes ADD COLUMN IF NOT EXISTS pool_results_unknown" in schema
