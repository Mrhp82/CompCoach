"""An admin can finish Pools atomically without inventing missing scores."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from compcoach_live.pool_results import pool_is_complete
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key, *, phase="pools", time="9:00 AM"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
            "strip": "B1", "pod": "B", "pool": "1", "time": time}


@pytest.fixture
def event(tmp_path):
    db = CompCoachDB(tmp_path / "phase.db")
    meet = db.create_meet("Tournament", ["Alex", "Jordan"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [record("known"), record("pending"),
                                 record("later", time="1:00 PM"), record("absent")], "Casey")
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
    return db, meet, event, athletes


def pending_snapshot(db, event):
    return {row["id"]: row["version"] for row in db.list_athletes(event["id"])
            if row["phase"] == "pools" and row["active_state"] == "active"
            and row["participation_status"] == "active" and not pool_is_complete(row)}


def test_closure_finishes_all_waves_preserves_known_and_absent_records(event):
    db, _, event, athletes = event
    known = db.set_pool_result(event["id"], athletes["known"]["id"], wins=4, losses=2, actor="Alex")
    absent = db.set_athlete_participation(event["id"], athletes["absent"]["id"], "absent", "Jordan")
    db.set_phase_started(event["id"], "pools", True, "Casey")
    closed = db.close_pools(event["id"], "Casey", expected_versions=pending_snapshot(db, event))
    assert closed["completed_count"] == 2
    assert closed["closed"] and not closed["started"]
    assert closed["closed_by"] == "Casey" and closed["closed_at"]
    assert db.get_athlete(event["id"], known["id"]) == known
    assert db.get_athlete(event["id"], absent["id"]) == absent
    for key in ("pending", "later"):
        row = db.get_athlete(event["id"], athletes[key]["id"])
        assert row["pool_results_unknown"] == 1
        assert row["pool_wins"] is row["pool_losses"] is None
        assert row["active_state"] == row["participation_status"] == "active"
        assert pool_is_complete(row)
    assert db.get_phase_states(event["id"])["de"]["closed"] is False
    audit = next(row for row in db.recent_actions(event["id"]) if row["action"] == "pools_closed")
    assert audit["new"]["completed_count"] == 2 and audit["previous"]["started"] is True


def test_closure_cleans_call_help_takeover_coverage_and_releases_coach(event):
    db, meet, event, athletes = event
    row = athletes["pending"]
    db.report_call(event["id"], row["id"], status="now", location="J4", actor="Casey")
    db.request_help(event["id"], row["id"], "Jordan")
    db.cover_athlete(event["id"], row["id"], "Alex", "Alex")
    db.close_pools(event["id"], "Casey")
    row = db.get_athlete(event["id"], row["id"])
    assert row["call_status"] == "waiting"
    assert row["reported_at"] is row["covered_at"] is row["help_requested_at"] is None
    assert row["covered_by"] == row["live_location"] == row["takeover_coach"] == ""
    available = next(r for r in db.list_coach_availability(meet["id"]) if r["coach_name"] == "Alex")
    assert available["is_available"]


@pytest.mark.parametrize("known", [True, False])
def test_completed_pools_late_operational_state_is_cleared_without_replacing_results(event, known):
    db, meet, event, athletes = event
    athlete = athletes["known"]
    saved = (db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Jordan")
             if known else db.set_pool_done_unknown(event["id"], athlete["id"], "Jordan"))
    result_fields = {key: saved[key] for key in
                     ("pool_wins", "pool_losses", "pool_results_unknown", "pool_result_at", "pool_result_by")}
    db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Casey")
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex")
    db.request_help(event["id"], athlete["id"], "Jordan")
    db.acknowledge_help(event["id"], athlete["id"], "Alex")
    closed = db.close_pools(event["id"], "Casey", expected_versions=pending_snapshot(db, event))
    assert closed["completed_count"] == 3
    after = db.get_athlete(event["id"], athlete["id"])
    assert {key: after[key] for key in result_fields} == result_fields
    assert after["call_status"] == "waiting"
    for field in ("covered_by", "takeover_coach", "live_location", "reported_by",
                  "help_requested_by", "help_acknowledged_by", "help_location"):
        assert after[field] == ""
    for field in ("covered_at", "takeover_at", "reported_at", "help_requested_at", "help_acknowledged_at"):
        assert after[field] is None
    availability = {row["coach_name"]: row for row in db.list_coach_availability(meet["id"])}
    assert availability["Alex"]["is_available"] and availability["Jordan"]["is_available"]
    cleanup = next(row for row in db.recent_actions(event["id"])
                   if row["athlete_id"] == athlete["id"] and row["action"] == "pool_phase_cleanup")
    assert cleanup["previous"]["covered_by"] == "Alex"
    assert cleanup["new"]["pool_wins"] == result_fields["pool_wins"]


def test_idempotent_closure_repairs_residual_transients_retaining_first_phase_audit(event):
    db, meet, event, athletes = event
    saved = db.set_pool_result(event["id"], athletes["known"]["id"], wins=5, losses=1, actor="Jordan")
    first = db.close_pools(event["id"], "Casey")
    # Simulate state written by an old deployed version: current live actions
    # reject a closed phase, and a closure retry still repairs legacy drift.
    with db._connection() as conn:
        conn.execute("UPDATE athletes SET call_status = 'now', live_location = 'D4', "
                     "covered_by = 'Alex', covered_at = 'legacy' WHERE id = ?", (saved["id"],))
        db._clear_available_coaches(conn, meet["id"], ["Alex"], "Legacy app")
    second = db.close_pools(event["id"], "Jordan", expected_versions={"stale": 0})
    assert second["completed_count"] == 0
    assert second["closed_at"] == first["closed_at"] and second["closed_by"] == "Casey"
    assert second["version"] == first["version"]
    after = db.get_athlete(event["id"], saved["id"])
    assert (after["pool_wins"], after["pool_losses"], after["pool_result_by"]) == (5, 1, "Jordan")
    assert after["covered_by"] == after["live_location"] == "" and after["call_status"] == "waiting"
    assert next(row for row in db.list_coach_availability(meet["id"])
                if row["coach_name"] == "Alex")["is_available"]
    assert len([row for row in db.recent_actions(event["id"]) if row["action"] == "pools_closed"]) == 1


def test_phase_closure_does_not_release_coach_responding_to_another_event(event):
    db, meet, event, athletes = event
    known = db.set_pool_result(event["id"], athletes["known"]["id"], wins=4, losses=2, actor="Jordan")
    db.report_call(event["id"], known["id"], status="now", location="C3", actor="Casey")
    db.cover_athlete(event["id"], known["id"], "Alex", "Alex")
    other = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(other["id"], [record("elsewhere", phase="de")], "Casey")
    elsewhere = db.list_athletes(other["id"])[0]
    db.request_help(other["id"], elsewhere["id"], "Jordan")
    db.acknowledge_help(other["id"], elsewhere["id"], "Alex")
    db.close_pools(event["id"], "Casey")
    availability = next(row for row in db.list_coach_availability(meet["id"])
                        if row["coach_name"] == "Alex")
    assert not availability["is_available"]
    assert db.get_athlete(other["id"], elsewhere["id"])["help_acknowledged_by"] == "Alex"


@pytest.mark.parametrize("known", [True, False])
@pytest.mark.parametrize("operation", ["call", "coverage", "takeover", "help"])
def test_closed_phase_rejects_fresh_and_stale_live_actions_atomically(event, known, operation):
    db, meet, event, athletes = event
    athlete = athletes["known"]
    if known:
        athlete = db.set_pool_result(event["id"], athlete["id"], wins=4, losses=2, actor="Jordan")
    db.close_pools(event["id"], "Casey")
    other = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(other["id"], [record("elsewhere", phase="de")], "Casey")
    elsewhere = db.list_athletes(other["id"])[0]
    db.report_call(other["id"], elsewhere["id"], status="now", location="D4", actor="Casey")
    db.cover_athlete(other["id"], elsewhere["id"], "Alex", "Alex")
    before = db.list_athletes(event["id"])
    other_before = db.list_athletes(other["id"])
    available_before = db.list_coach_availability(meet["id"])
    actions_before = db.recent_actions(event["id"])
    other_actions_before = db.recent_actions(other["id"])
    with pytest.raises(CompCoachError, match="Pools are finished"):
        if operation == "call":
            db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Jordan",
                           expected_version=athlete["version"])
        elif operation == "coverage":
            # Handoff releases Alex's other coverage before updating the
            # closed target. Rejection must roll that entire transaction back.
            db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
        elif operation == "takeover":
            db.take_over_athlete(event["id"], athlete["id"], "Jordan", "Jordan")
        else:
            db.request_help(event["id"], athlete["id"], "Jordan")
    assert db.list_athletes(event["id"]) == before
    assert db.list_athletes(other["id"]) == other_before
    assert db.list_coach_availability(meet["id"]) == available_before
    assert db.recent_actions(event["id"]) == actions_before
    assert db.recent_actions(other["id"]) == other_actions_before


@pytest.mark.parametrize("operation", ["claim", "acknowledge"])
def test_closed_phase_rejects_responding_to_residual_legacy_call_or_help(event, operation):
    db, meet, event, athletes = event
    db.close_pools(event["id"], "Casey")
    athlete = athletes["known"]
    with db._connection() as conn:
        conn.execute("UPDATE athletes SET call_status = 'now', live_location = 'C3', "
                     "help_requested_by = 'Jordan', help_requested_at = 'legacy' WHERE id = ?",
                     (athlete["id"],))
    before = db.get_athlete(event["id"], athlete["id"])
    availability = db.list_coach_availability(meet["id"])
    audit = db.recent_actions(event["id"])
    with pytest.raises(CompCoachError, match="Pools are finished"):
        if operation == "claim":
            db.claim(event["id"], athlete["id"], "Alex")
        else:
            db.acknowledge_help(event["id"], athlete["id"], "Alex")
    assert db.get_athlete(event["id"], athlete["id"]) == before
    assert db.list_coach_availability(meet["id"]) == availability
    assert db.recent_actions(event["id"]) == audit


def test_closed_phase_accepts_late_scores_attendance_and_explicit_reopen(event):
    db, _, event, athletes = event
    db.close_pools(event["id"], "Casey")
    known = db.set_pool_result(event["id"], athletes["known"]["id"], wins=3, losses=3, actor="Jordan")
    assert known["pool_wins"] == 3 and db.get_phase_states(event["id"])["pools"]["closed"]
    absent = db.set_athlete_participation(event["id"], athletes["absent"]["id"], "absent", "Jordan")
    assert absent["participation_status"] == "absent"
    reopened = db.reopen_pool(event["id"], athletes["pending"]["id"], "Jordan")
    db.report_call(event["id"], reopened["id"], status="on_deck", location="C3", actor="Jordan")
    assert db.get_athlete(event["id"], reopened["id"])["call_status"] == "on_deck"


def test_closure_preserves_manual_unknown_actor_and_confirmed_results_can_be_added_after(event):
    db, _, event, athletes = event
    manual = db.set_pool_done_unknown(event["id"], athletes["pending"]["id"], "Jordan")
    db.close_pools(event["id"], "Casey")
    assert db.get_athlete(event["id"], manual["id"]) == manual
    known = db.set_pool_result(event["id"], manual["id"], wins=3, losses=3, actor="Jordan")
    assert known["pool_results_unknown"] == 0 and known["pool_wins"] == 3
    assert db.get_phase_states(event["id"])["pools"]["closed"]


def test_closure_is_idempotent_and_keeps_original_audit(event):
    db, _, event, _ = event
    first = db.close_pools(event["id"], "Casey")
    before = db.list_athletes(event["id"])
    second = db.close_pools(event["id"], "Jordan", expected_versions={"stale": 0})
    assert second["completed_count"] == 0
    assert second["version"] == first["version"]
    assert second["closed_by"] == first["closed_by"] == "Casey"
    assert db.list_athletes(event["id"]) == before
    assert len([r for r in db.recent_actions(event["id"]) if r["action"] == "pools_closed"]) == 1


def test_stale_pending_snapshot_rolls_back_every_athlete(event):
    db, _, event, athletes = event
    snapshot = pending_snapshot(db, event)
    db.set_pool_result(event["id"], athletes["known"]["id"], wins=5, losses=1, actor="Alex")
    before = db.list_athletes(event["id"])
    with pytest.raises(ConcurrentUpdateError, match="pending Pools list changed"):
        db.close_pools(event["id"], "Casey", expected_versions=snapshot)
    assert db.list_athletes(event["id"]) == before
    assert not db.get_phase_states(event["id"])["pools"]["closed"]


def test_transaction_failure_does_not_partially_complete_phase(event):
    db, _, event, _ = event
    before = db.list_athletes(event["id"])
    actions_before = db.recent_actions(event["id"])
    with db._connection() as conn:
        conn.execute("CREATE TRIGGER reject_closure BEFORE INSERT ON phase_states "
                     "BEGIN SELECT RAISE(ABORT, 'phase failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="phase failure"):
        db.close_pools(event["id"], "Casey")
    assert db.list_athletes(event["id"]) == before
    assert db.recent_actions(event["id"]) == actions_before


def test_individual_reopen_resumes_phase_without_old_operational_state(event):
    db, _, event, athletes = event
    db.report_call(event["id"], athletes["pending"]["id"], status="on_deck", location="C3", actor="Casey")
    db.close_pools(event["id"], "Casey")
    row = db.get_athlete(event["id"], athletes["pending"]["id"])
    reopened = db.reopen_pool(event["id"], row["id"], "Jordan", expected_version=row["version"])
    assert not pool_is_complete(reopened)
    assert reopened["call_status"] == "waiting" and reopened["live_location"] == ""
    state = db.get_phase_states(event["id"])["pools"]
    assert not state["closed"] and state["closed_at"] is None and state["closed_by"] == ""
    assert state["started"] is False


def test_closed_phase_blocks_pools_import_and_start_but_accepts_de_import(event):
    db, _, event, athletes = event
    db.close_pools(event["id"], "Casey")
    with pytest.raises(CompCoachError, match="Reopen Pools"):
        db.merge_import(event["id"], [record("new")], "Casey")
    with pytest.raises(CompCoachError, match="Reopen Pools"):
        db.set_phase_started(event["id"], "pools", True, "Jordan")
    db.merge_import(event["id"], [record("pending", phase="de")], "Casey")
    row = db.get_athlete(event["id"], athletes["pending"]["id"])
    assert row["phase"] == "de" and row["active_state"] == "active"
    assert db.get_phase_states(event["id"])["pools"]["closed"]


def test_phase_reopen_preserves_results_and_enables_new_import(event):
    db, _, event, _ = event
    db.close_pools(event["id"], "Casey")
    before = db.list_athletes(event["id"])
    state = db.reopen_pools(event["id"], "Jordan")
    assert not state["closed"]
    assert db.list_athletes(event["id"]) == before
    db.merge_import(event["id"], [record("new")], "Casey")
    assert len(db.list_athletes(event["id"])) == 5
    db.set_phase_started(event["id"], "pools", True, "Jordan")


def test_empty_phase_closes_and_closed_day_rejects_mutations(tmp_path):
    db = CompCoachDB(tmp_path / "empty.db")
    meet = db.create_meet("Tournament", [])
    event = db.list_meet_events(meet["id"])[0]
    state = db.close_pools(event["id"], "Casey", expected_versions={})
    assert state["closed"] and state["completed_count"] == 0
    db.finish_meet(meet["id"], "Casey")
    with pytest.raises(EventLockedError):
        db.reopen_pools(event["id"], "Casey")


def test_parallel_close_has_one_completion_audit(event):
    db, _, event, _ = event
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda who: db.close_pools(event["id"], who), ["Casey", "Jordan"]))
    assert saved[0]["closed_by"] == saved[1]["closed_by"]
    assert sum(row["completed_count"] for row in saved) == 4
    assert len([row for row in db.recent_actions(event["id"]) if row["action"] == "pools_closed"]) == 1


def test_closed_phase_fields_upgrade_legacy_table_additively(event):
    db, _, event, _ = event
    db.set_phase_started(event["id"], "pools", True, "Casey")
    with db._connection() as conn:
        conn.execute("ALTER TABLE phase_states DROP COLUMN closed_at")
        conn.execute("ALTER TABLE phase_states DROP COLUMN closed_by")
    upgraded = CompCoachDB(db.path)
    state = upgraded.get_phase_states(event["id"])["pools"]
    assert state["started"] and state["changed_by"] == "Casey"
    assert not state["closed"]
    upgraded.close_pools(event["id"], "Jordan")
    assert CompCoachDB(db.path).get_phase_states(event["id"])["pools"]["closed_by"] == "Jordan"
