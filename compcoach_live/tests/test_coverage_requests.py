"""Single-bout offers reserve only the first available recipient accepting."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key="one", phase="de", pod="B"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase, "pod": pod, "strip": f"{pod}1", "pool": "2" if phase == "pools" else ""}


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "offers.sqlite")
    meet = db.create_meet("NAC", ["Alex", "Jordan", "Taylor"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [record(), record("other", pod="F")], "Casey")
    athlete = next(a for a in db.list_athletes(event["id"]) if a["athlete_key"] == "one")
    db.assign_de_pods(event["id"], pods=["B"], coaches=["Taylor"], actor="Casey")
    for name in ("Alex", "Jordan"):
        db.set_coach_availability(meet["id"], name, True, name)
    return db, meet, event, db.get_athlete(event["id"], athlete["id"])


def create(day, names=("Alex", "Jordan"), **kwargs):
    db, meet, event, athlete = day
    return db.create_coverage_request(meet["id"], event["id"], athlete["id"], names, "Casey", **kwargs)


def available(db, meet, name):
    return next(row for row in db.list_coach_availability(meet["id"]) if row["coach_name"] == name)


def test_sending_keeps_plan_and_all_recipients_available_and_filters_identity(day):
    db, meet, event, athlete = day
    before = db.get_athlete(event["id"], athlete["id"])
    offer = create(day)
    assert {r["coach_name"] for r in offer["recipients"]} == {"Alex", "Jordan"}
    assert db.get_athlete(event["id"], athlete["id"]) == before
    assert all(available(db, meet, n)["is_available"] for n in ("Alex", "Jordan"))
    assert len(db.list_coverage_requests(meet["id"], "Alex")) == len(db.list_coverage_requests(meet["id"], "Jordan")) == 1
    assert db.list_coverage_requests(meet["id"], "Taylor") == []
    assert db.list_assignment_notices(meet["id"]) == []


def test_acceptance_is_temporary_responsibility_without_timer_and_closes_for_everyone(day):
    db, meet, event, athlete = day
    offer = create(day)
    accepted = db.accept_coverage_request(meet["id"], offer["id"], "Alex", expected_request_version=offer["version"])
    current = db.get_athlete(event["id"], athlete["id"])
    assert accepted["accepted_by"] == current["takeover_coach"] == "Alex"
    assert current["takeover_at"] and current["covered_by"] == "" and current["covered_at"] is None
    assert current["de_coaches"] == ["Taylor"] and current["pod"] == "B"
    assert not available(db, meet, "Alex")["is_available"] and available(db, meet, "Jordan")["is_available"]
    assert db.list_coverage_requests(meet["id"]) == []
    assert CompCoachDB(db.path).list_coverage_requests(meet["id"], pending_only=False)[0]["status"] == "accepted"
    with pytest.raises(ConcurrentUpdateError, match="accepted by Alex"):
        db.accept_coverage_request(meet["id"], offer["id"], "Jordan")


def test_concurrent_recipients_have_one_winner_and_one_reserved_coach(day):
    db, meet, event, athlete = day
    offer = create(day)
    def accept(name):
        try:
            return name, db.accept_coverage_request(meet["id"], offer["id"], name)["status"]
        except ConcurrentUpdateError:
            return name, "lost-race"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(accept, ("Alex", "Jordan")))
    winner = [name for name, status in results if status == "accepted"]
    assert len(winner) == 1 and sum(status == "lost-race" for _, status in results) == 1
    assert db.get_athlete(event["id"], athlete["id"])["takeover_coach"] == winner[0]
    assert sum(not available(db, meet, n)["is_available"] for n in ("Alex", "Jordan")) == 1


def test_old_double_tap_after_result_does_not_resurrect_coverage(day):
    db, meet, event, athlete = day
    offer = create(day)
    first = db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    assert db.accept_coverage_request(meet["id"], offer["id"], "Alex", expected_request_version=0) == first
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="C3")
    db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex")
    snapshot = db.get_athlete(event["id"], athlete["id"])
    db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    assert db.get_athlete(event["id"], athlete["id"]) == snapshot
    assert snapshot["takeover_coach"] == snapshot["covered_by"] == ""


def test_uninvited_actor_cross_day_and_duplicate_pending_are_rejected(day):
    db, meet, _, _ = day
    offer = create(day)
    with pytest.raises(CompCoachError, match="not invited"):
        db.accept_coverage_request(meet["id"], offer["id"], "Taylor")
    with pytest.raises(CompCoachError, match="invited coach"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex", actor="Casey")
    other = db.create_meet("Other", ["Alex"])
    with pytest.raises(ConcurrentUpdateError, match="no longer available"):
        db.accept_coverage_request(other["id"], offer["id"], "Alex")
    with pytest.raises(ConcurrentUpdateError, match="already pending"):
        create(day)
    db.cancel_coverage_request(meet["id"], offer["id"], "Casey")
    assert create(day)["id"] != offer["id"]
    with pytest.raises(ConcurrentUpdateError, match="ended"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex")


def test_unavailable_recipient_and_any_stale_snapshot_roll_back_whole_offer(day):
    db, meet, event, athlete = day
    with pytest.raises(ConcurrentUpdateError, match="marked available"):
        create(day, ("Alex", "Taylor"))
    versions = {n: available(db, meet, n)["version"] for n in ("Alex", "Jordan")}
    db.report_call(event["id"], athlete["id"], status="on_deck", location="C3", actor="Casey")
    with pytest.raises(ConcurrentUpdateError, match="athlete changed"):
        create(day, expected_version=athlete["version"], expected_coach_versions=versions)
    db.set_coach_availability(meet["id"], "Jordan", False, "Jordan")
    db.set_coach_availability(meet["id"], "Jordan", True, "Jordan")
    with pytest.raises(ConcurrentUpdateError, match="availability changed"):
        create(day, expected_coach_versions=versions)
    assert db.list_coverage_requests(meet["id"]) == []


def test_call_changes_keep_offer_and_returning_available_coach_can_accept_fresh_view(day):
    db, meet, event, athlete = day
    offer = create(day)
    old_coach_version = available(db, meet, "Alex")["version"]
    db.report_call(event["id"], athlete["id"], status="now", location="J4", actor="Casey")
    current = db.list_coverage_requests(meet["id"], "Alex")[0]
    assert current["live_location"] == "J4" and current["id"] == offer["id"]
    with pytest.raises(ConcurrentUpdateError, match="athlete changed"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex", expected_version=athlete["version"])
    db.set_coach_availability(meet["id"], "Alex", False, "Alex")
    with pytest.raises(ConcurrentUpdateError, match="marked available"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    db.set_coach_availability(meet["id"], "Alex", True, "Alex")
    with pytest.raises(ConcurrentUpdateError, match="availability changed"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex", expected_coach_version=old_coach_version)
    db.accept_coverage_request(meet["id"], offer["id"], "Alex", expected_version=current["athlete_version"], expected_coach_version=available(db, meet, "Alex")["version"])


@pytest.mark.parametrize("operation", ["won", "lost", "bye", "absent", "withdrawn", "phase", "physical", "takeover"])
def test_bout_changes_supersede_pending_offer_without_reimport_resurrection(day, operation):
    db, meet, event, athlete = day
    offer = create(day)
    if operation in {"won", "lost"}:
        db.mark_result(event["id"], athlete["id"], outcome=operation, actor="Casey")
    elif operation == "bye":
        db.mark_bye(event["id"], athlete["id"], actor="Casey")
    elif operation == "absent":
        db.mark_absent(event["id"], athlete["id"], actor="Casey")
    elif operation == "withdrawn":
        db.mark_withdrawn(event["id"], athlete["id"], actor="Casey")
    elif operation == "phase":
        db.merge_import(event["id"], [record(phase="pools")], "Casey")
    elif operation == "physical":
        db.cover_athlete(event["id"], athlete["id"], "Taylor", "Taylor", location="C3")
    else:
        db.take_over_athlete(event["id"], athlete["id"], "Taylor", "Taylor")
    assert db.list_coverage_requests(meet["id"]) == []
    assert db.list_coverage_requests(meet["id"], pending_only=False)[0]["status"] == "superseded"
    with pytest.raises(ConcurrentUpdateError, match="ended"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    current = db.get_athlete(event["id"], athlete["id"])
    db.merge_import(event["id"], [record(phase=current["phase"])], "Casey")
    assert db.list_coverage_requests(meet["id"]) == []


def test_expiry_changes_revision_and_pending_views_without_a_write(day, monkeypatch):
    db, meet, _, _ = day
    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr("compcoach_live.storage.utc_now", lambda: clock[0].isoformat())
    offer = create(day, expires_in_seconds=30)
    before = db.get_meet_revision(meet["id"])
    original = db._connection
    statements = []
    @contextmanager
    def trace():
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn
    db._connection = trace
    assert db.get_meet_revision(meet["id"]) == before
    assert len(statements) == 1 and statements[0].startswith("WITH target AS")
    db._connection = original
    clock[0] += timedelta(seconds=31)
    assert db.get_meet_revision(meet["id"]) != before
    assert db.list_coverage_requests(meet["id"], "Alex") == []
    assert db.list_coverage_requests(meet["id"], pending_only=False)[0]["status"] == "expired"
    with pytest.raises(ConcurrentUpdateError, match="expired"):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    assert create(day)["id"] != offer["id"]


def test_cancel_accept_race_preserves_exactly_one_outcome(day):
    db, meet, event, athlete = day
    offer = create(day)
    def action(kind):
        try:
            if kind == "accept":
                return db.accept_coverage_request(meet["id"], offer["id"], "Alex")["status"]
            return db.cancel_coverage_request(meet["id"], offer["id"], "Casey")["status"]
        except ConcurrentUpdateError:
            return "lost-race"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(action, ("accept", "cancel")))
    assert "lost-race" in results
    audit = db.list_coverage_requests(meet["id"], pending_only=False)[0]
    assert (audit["status"] == "accepted") == bool(db.get_athlete(event["id"], athlete["id"])["takeover_coach"])


def test_closed_event_rejects_send_and_accept_and_hides_pending(day):
    db, meet, event, _ = day
    offer = create(day)
    db.set_event_locked(event["id"], True)
    assert db.list_coverage_requests(meet["id"]) == []
    with pytest.raises(EventLockedError):
        db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    with pytest.raises(EventLockedError):
        create(day)


def test_pending_recipient_follows_rename_and_accepted_label_stays_historical(day):
    db, meet, event, athlete = day
    offer = create(day)
    coach = next(c for c in db.list_coaches() if c["name"] == "Alex")
    db.update_coach(coach["id"], name="Alec")
    assert db.list_coverage_requests(meet["id"], "Alex") == []
    pending = db.list_coverage_requests(meet["id"], "Alec")[0]
    assert pending["id"] == offer["id"] and pending["version"] > offer["version"]
    assert "Alec" in {r["coach_name"] for r in pending["recipients"]}
    accepted = db.accept_coverage_request(meet["id"], offer["id"], "Alec")
    assert accepted["accepted_by"] == db.get_athlete(event["id"], athlete["id"])["takeover_coach"] == "Alec"
    db.update_coach(coach["id"], name="Alexandra")
    assert db.list_coverage_requests(meet["id"], pending_only=False)[0]["accepted_by"] == "Alec"


def test_pool_emergency_accept_arrive_finish_releases_physical_and_temporary_owner(day):
    db, meet, event, _ = day
    db.merge_import(event["id"], [record("pool", phase="pools")], "Casey")
    athlete = next(a for a in db.list_athletes(event["id"]) if a["athlete_key"] == "pool")
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Taylor", actor="Casey")
    offer = db.create_coverage_request(meet["id"], event["id"], athlete["id"], ["Alex"], "Casey")
    db.accept_coverage_request(meet["id"], offer["id"], "Alex")
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="J4")
    db.report_call(event["id"], athlete["id"], status="now", location="J4", actor="Alex")
    db.request_help(event["id"], athlete["id"], "Alex", location="J4")
    result = db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert result["covered_by"] == result["takeover_coach"] == "" and result["covered_at"] is None
    assert result["main_coach"] == "Taylor"
    assert result["source_strip"] == "B1" and result["pool_no"] == "2"
    assert (result["pool_wins"], result["pool_losses"]) == (3, 3)
    assert result["call_status"] == "waiting" and result["live_location"] == ""
    assert result["reported_at"] is None and result["help_requested_at"] is None
    assert result["help_acknowledged_at"] is None and result["help_location"] == ""
    assert available(db, meet, "Alex")["is_available"]


def test_finishing_old_pool_does_not_release_coach_covering_another_event(day):
    db, meet, event, _ = day
    db.merge_import(event["id"], [record("pool", phase="pools")], "Casey")
    athlete = next(a for a in db.list_athletes(event["id"]) if a["athlete_key"] == "pool")
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="J4")
    other_event = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(other_event["id"], [record("cross", phase="pools", pod="F")], "Casey")
    other = db.list_athletes(other_event["id"])[0]
    db.cover_athlete(other_event["id"], other["id"], "Alex", "Alex", location="F2")
    db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Casey")
    assert not available(db, meet, "Alex")["is_available"]
    assert db.get_athlete(other_event["id"], other["id"])["covered_by"] == "Alex"
