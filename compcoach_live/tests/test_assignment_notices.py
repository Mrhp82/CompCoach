"""Assignment receipts stay durable, scoped and separate from physical coverage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from compcoach_live import training
from compcoach_live.assignment_notices import assignment_notice_details
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key="one", *, phase="de", pod="B"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
            "pod": pod, "strip": f"{pod}1", "pool": "2" if phase == "pools" else ""}


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "receipts.sqlite")
    meet = db.create_meet("NAC", ["Alex", "Jordan", "Taylor"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [record()], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    return db, meet, event, athlete


def assign(day, coaches=("Alex",)):
    db, _, event, athlete = day
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=coaches, actor="Casey")


def test_new_assignment_has_names_location_and_assigner_and_persists(day):
    db, meet, event, athlete = day
    assert db.list_assignment_notices(meet["id"]) == []
    assign(day)
    db.report_call(event["id"], athlete["id"], status="on_deck", location="C3", actor="Casey")
    notice = CompCoachDB(db.path).list_assignment_notices(meet["id"], " alex ")[0]
    assert notice["athlete_name"] == "ATHLETE one"
    assert notice["event_id"] == event["id"]
    assert notice["coach_name"] == "Alex"
    assert notice["assigned_by"] == "Casey"
    assert notice["actual_strip"] == "C3"
    assert notice["call_status"] == "on_deck"
    assert notice["assignment_status"] == "pending"
    assert "Pod B · Actual strip C3" in assignment_notice_details(notice)


def test_accept_receipt_clears_available_but_never_claims_physical_bout(day):
    db, meet, event, athlete = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    db.set_coach_availability(meet["id"], "Alex", True, "Alex")
    before = db.get_meet_revision(meet["id"])
    accepted = db.accept_assignment_notice(meet["id"], notice["id"], "Alex", actor="Alex")
    assert accepted["accepted_at"] and accepted["accepted_by"] == "Alex"
    assert db.get_meet_revision(meet["id"]) != before
    assert db.list_assignment_notices(meet["id"], "Alex") == []
    shown = CompCoachDB(db.path).list_assignment_notices(meet["id"], "Alex", pending_only=False)
    assert shown[0]["assignment_status"] == "accepted"
    current = db.get_athlete(event["id"], athlete["id"])
    assert current["version"] == notice["athlete_version"]
    assert current["covered_by"] == current["takeover_coach"] == ""
    availability = next(row for row in db.list_coach_availability(meet["id"]) if row["coach_name"] == "Alex")
    assert not availability["is_available"]
    assert db.recent_actions(event["id"])[0]["action"] == "assignment_accepted"


def test_repeat_accept_is_idempotent_and_does_not_clear_later_manual_availability(day):
    db, meet, event, _ = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    first = db.accept_assignment_notice(meet["id"], notice["id"], "Alex")
    db.set_coach_availability(meet["id"], "Alex", True, "Alex")
    before = db.get_meet_revision(meet["id"])
    second = db.accept_assignment_notice(meet["id"], notice["id"], "Alex")
    assert first == second
    assert db.get_meet_revision(meet["id"]) == before
    assert len([a for a in db.recent_actions(event["id"]) if a["action"] == "assignment_accepted"]) == 1


def test_only_owner_can_accept_and_cross_day_notice_is_rejected(day):
    db, meet, _, _ = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    with pytest.raises(CompCoachError, match="assigned coach"):
        db.accept_assignment_notice(meet["id"], notice["id"], "Jordan")
    with pytest.raises(CompCoachError, match="assigned coach"):
        db.accept_assignment_notice(meet["id"], notice["id"], "Alex", actor="Casey")
    other = db.create_meet("Other", ["Alex"])
    with pytest.raises(ConcurrentUpdateError, match="no longer available"):
        db.accept_assignment_notice(other["id"], notice["id"], "Alex")
    assert not db.list_assignment_notices(meet["id"], "Alex")[0]["accepted_at"]


def test_removed_and_readded_athlete_generates_fresh_receipt_and_stale_tap_is_rejected(day):
    db, meet, _, _ = day
    assign(day)
    old = db.list_assignment_notices(meet["id"], "Alex")[0]
    assign(day, ("Jordan",))
    assert db.list_assignment_notices(meet["id"], "Alex", pending_only=False) == []
    assign(day)
    new = db.list_assignment_notices(meet["id"], "Alex")[0]
    assert new["id"] != old["id"]
    with pytest.raises(ConcurrentUpdateError, match="changed"):
        db.accept_assignment_notice(meet["id"], old["id"], "Alex")
    db.accept_assignment_notice(meet["id"], new["id"], "Alex")


def test_call_and_de_result_keep_accepted_assignment(day):
    db, meet, event, athlete = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    db.accept_assignment_notice(meet["id"], notice["id"], "Alex")
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    assert db.list_assignment_notices(meet["id"], "Alex") == []
    assert db.list_assignment_notices(meet["id"], "Alex", pending_only=False)[0]["id"] == notice["id"]
    db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex")
    assert db.list_assignment_notices(meet["id"], "Alex", pending_only=False) == []
    with pytest.raises(ConcurrentUpdateError, match="finished"):
        db.accept_assignment_notice(meet["id"], notice["id"], "Alex")


def test_absent_athlete_receipt_cannot_be_accepted(day):
    db, meet, event, athlete = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    db.mark_absent(event["id"], athlete["id"], actor="Alex")
    assert db.list_assignment_notices(meet["id"]) == []
    with pytest.raises(ConcurrentUpdateError, match="finished"):
        db.accept_assignment_notice(meet["id"], notice["id"], "Alex")


def test_pool_slot_swap_and_repeated_import_do_not_create_duplicate_receipts(day):
    db, meet, event, _ = day
    db.merge_import(event["id"], [record("pool", phase="pools")], "Casey")
    athlete = next(a for a in db.list_athletes(event["id"]) if a["athlete_key"] == "pool")
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Alex", side_coach="Jordan", actor="Casey")
    before = {n["coach_name"]: n["id"] for n in db.list_assignment_notices(meet["id"])}
    db.accept_assignment_notice(meet["id"], before["Alex"], "Alex")
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Jordan", side_coach="Alex", actor="Casey")
    db.merge_import(event["id"], [record("pool", phase="pools")], "Casey")
    after = {n["coach_name"]: n["id"] for n in db.list_assignment_notices(meet["id"], pending_only=False)}
    assert before == after
    assert db.list_assignment_notices(meet["id"], "Alex") == []
    db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert db.list_assignment_notices(meet["id"], pending_only=False) == []


def test_inherited_pod_import_and_cross_event_assignments_are_each_announced(day):
    db, meet, event, _ = day
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Alex", "Jordan", "Taylor"], actor="Casey")
    db.merge_import(event["id"], [record("new")], "Casey")
    other = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(other["id"], [record("cross", pod="F")], "Casey")
    db.assign_de_pods(other["id"], pods=["F"], coaches=["Alex"], actor="Casey")
    notices = db.list_assignment_notices(meet["id"], "Alex")
    assert {n["athlete_name"] for n in notices} == {"ATHLETE one", "ATHLETE new", "ATHLETE cross"}
    assert {n["event_id"] for n in notices} == {event["id"], other["id"]}
    assert len(db.list_assignment_notices(meet["id"], "Taylor")) == 2


def test_two_devices_accept_exactly_once(day):
    db, meet, event, _ = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    def accept(_):
        return db.accept_assignment_notice(meet["id"], notice["id"], "Alex")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(accept, range(2)))
    assert results[0] == results[1]
    assert len([a for a in db.recent_actions(event["id"]) if a["action"] == "assignment_accepted"]) == 1


def test_receipt_follows_coach_directory_rename(day):
    db, meet, _, _ = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    coach = next(c for c in db.list_coaches() if c["name"] == "Alex")
    db.update_coach(coach["id"], name="Alexandra")
    assert db.list_assignment_notices(meet["id"], "Alex") == []
    assert db.list_assignment_notices(meet["id"], "Alexandra")[0]["id"] == notice["id"]
    db.accept_assignment_notice(meet["id"], notice["id"], "Alexandra")


def test_locked_competition_rejects_acceptance(day):
    db, meet, _, _ = day
    assign(day)
    notice = db.list_assignment_notices(meet["id"], "Alex")[0]
    db.set_meet_locked(meet["id"], True)
    with pytest.raises(EventLockedError):
        db.accept_assignment_notice(meet["id"], notice["id"], "Alex")


def test_no_announcements_are_created_by_reads_or_legacy_migration(day):
    db, meet, _, _ = day
    assign(day)
    with db._connection() as conn:
        conn.execute("DELETE FROM assignment_notices")
        conn.execute("DELETE FROM coach_assignment_history")
    restarted = CompCoachDB(db.path)
    assert restarted.list_assignment_notices(meet["id"]) == []
    restarted.list_assignment_history(meet_id=meet["id"])
    assert restarted.list_assignment_notices(meet["id"]) == []


def test_expired_practice_rejects_acceptance_and_real_receipts_remain_separate(day):
    db, meet, _, _ = day
    assign(day)
    real = db.list_assignment_notices(meet["id"], "Alex")
    hub = training.start_training(db, meet["id"], actor="Alex")
    run = training.join_training(db, hub["id"], "Alex")
    practice = db.list_assignment_notices(run["id"], "Alex")
    assert practice and db.list_assignment_notices(meet["id"], "Alex") == real
    with db._connection() as conn:
        row = training._row(conn, run["id"])
        state = training._load(row["state_json"], {})
        state["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        training._persist(conn, row, state)
    with pytest.raises(EventLockedError, match="expired"):
        db.accept_assignment_notice(run["id"], practice[0]["id"], "Alex")
