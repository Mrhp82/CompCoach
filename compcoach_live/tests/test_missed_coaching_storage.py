"""Missed strip coaching is immutable evidence, never a sporting outcome."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from compcoach_live import training
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key, phase="de", pod="B"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
            "pod": pod, "strip": f"{pod}1", "pool": "2" if phase == "pools" else ""}


@pytest.fixture
def day(tmp_path):
    db = CompCoachDB(tmp_path / "missed.sqlite")
    meet = db.create_meet("NAC", ["Alex", "Jordan", "Taylor", "Morgan"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    other = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(event["id"], [record("target"), record("next")], "Casey")
    db.merge_import(other["id"], [record("busy", pod="C"), record("reserved", pod="F")], "Casey")
    athletes = {a["athlete_key"]: a for e in (event, other) for a in db.list_athletes(e["id"])}
    db.cover_athlete(other["id"], athletes["busy"]["id"], "Alex", "Alex", location="J4")
    db.take_over_athlete(other["id"], athletes["reserved"]["id"], "Jordan", "Jordan")
    db.set_coach_availability(meet["id"], "Taylor", True, "Taylor")
    db.report_call(event["id"], athletes["target"]["id"], status="now", location="D3", actor="Casey")
    return db, meet, event, other, athletes


def report(day, **kwargs):
    db, _, event, _, athletes = day
    return db.report_missed_coaching(event["id"], athletes["target"]["id"], "Casey", **kwargs)


def test_recording_changes_no_results_calls_coverage_or_availability(day):
    db, meet, event, _, athletes = day
    before = db.get_athlete(event["id"], athletes["target"]["id"])
    availability = db.list_coach_availability(meet["id"])
    incident = report(day, expected_version=before["version"], note="Parent reported nobody at the strip.")
    assert db.get_athlete(event["id"], athletes["target"]["id"]) == before
    assert db.list_coach_availability(meet["id"]) == availability
    assert incident["athlete_snapshot"]["version"] == before["version"]
    assert incident["athlete_snapshot"]["de_wins"] == before["de_wins"]
    assert incident["live_location"] == "D3" and incident["call_status"] == "now"
    assert incident["recorded_by"] == "Casey" and incident["status"] == "reported"
    assert not incident["summary"]["target_coverage_was_recorded"]
    logged = db.recent_actions(event["id"])[0]
    assert logged["action"] == "missed_coaching_reported" and logged["previous"] is None
    with pytest.raises(CompCoachError):
        db.undo_action(event["id"], logged["id"], "Casey")


def test_atomic_snapshot_distinguishes_busy_reserved_available_and_unconfirmed_across_events(day):
    db, meet, _, other, _ = day
    original = db._connection
    calls = []
    @contextmanager
    def counted():
        calls.append(1)
        with original() as conn:
            yield conn
    db._connection = counted
    incident = report(day)
    db._connection = original
    assert len(calls) == 1
    snapshot = incident["coaches_snapshot"]
    rows = {row["coach_name"]: row for row in snapshot["coaches"]}
    assert {name: row["status"] for name, row in rows.items()} == {
        "Alex": "busy", "Jordan": "reserved", "Taylor": "available", "Morgan": "unconfirmed",
    }
    busy = rows["Alex"]["busy_with"][0]
    assert busy["athlete_name"] == "ATHLETE busy" and busy["event_id"] == other["id"]
    assert busy["actual_strip"] == "J4" and busy["covered_since"]
    assert rows["Jordan"]["reserved_for"][0]["reserved_since"]
    assert snapshot["unconfirmed_count"] == snapshot["available_count"] == 1
    assert snapshot["status_note"] == "Status at reporting time"
    assert snapshot["snapshot_at"] == incident["recorded_at"] and not snapshot["all_committed"]


def test_snapshot_is_immutable_after_coach_moves_renames_and_event_renames(day):
    db, meet, event, other, athletes = day
    incident = report(day)
    db.release(other["id"], athletes["busy"]["id"], "Alex")
    coach = next(c for c in db.list_coaches() if c["name"] == "Taylor")
    db.update_coach(coach["id"], name="Terry")
    with db._connection() as conn:
        conn.execute("UPDATE events SET name = 'Renamed event' WHERE id = ?", (event["id"],))
    stored = db.list_missed_coaching(meet["id"])[0]
    assert stored["coaches_snapshot"] == incident["coaches_snapshot"]
    assert stored["event_name"] == incident["event_name"]
    assert "Taylor" in {c["coach_name"] for c in stored["coaches_snapshot"]["coaches"]}


def test_all_committed_is_factual_and_target_recorded_coverage_is_not_hidden(day):
    db, meet, event, _, athletes = day
    db.cover_athlete(event["id"], athletes["target"]["id"], "Taylor", "Taylor", location="D3")
    db.take_over_athlete(event["id"], athletes["next"]["id"], "Morgan", "Morgan")
    incident = report(day)
    assert incident["summary"]["all_committed"]
    assert incident["summary"]["available_count"] == incident["summary"]["unconfirmed_count"] == 0
    assert incident["summary"]["target_coverage_was_recorded"]
    assert incident["summary"]["target_recorded_coach"] == "Taylor"


def test_de_phone_reports_coalesce_same_stage_but_new_won_stage_is_distinct(day):
    db, meet, event, _, athletes = day
    first = report(day, idempotency_key="phone-one", bout_context="current")
    def again(actor):
        return db.report_missed_coaching(event["id"], athletes["target"]["id"], actor, bout_context="current")
    with ThreadPoolExecutor(max_workers=2) as workers:
        duplicate = list(workers.map(again, ("Alex", "Jordan")))
    assert {i["id"] for i in duplicate} == {first["id"]}
    assert all(i["was_already_recorded"] for i in duplicate)
    db.mark_result(event["id"], athletes["target"]["id"], outcome="won", actor="Casey")
    after_result = report(day)
    assert after_result["id"] == first["id"]
    db.report_call(event["id"], athletes["target"]["id"], status="on_deck", location="F2", actor="Casey")
    next_stage = report(day, bout_context="current")
    assert next_stage["id"] != first["id"] and next_stage["bout_number"] == 2
    assert len(db.list_missed_coaching(meet["id"])) == 2


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_late_result_report_retains_bout_strip_and_current_coach_snapshot(day, outcome):
    db, meet, event, _, athletes = day
    target = athletes["target"]
    db.cover_athlete(event["id"], target["id"], "Taylor", "Taylor", location="D3")
    db.mark_result(event["id"], target["id"], outcome=outcome, actor="Taylor")
    current = db.get_athlete(event["id"], target["id"])
    assert current["live_location"] == current["covered_by"] == ""
    incident = report(day, expected_version=current["version"])
    assert incident["bout_kind"] == "last_completed" and incident["bout_outcome"] == outcome
    assert incident["source_action_id"] and incident["source_result_at"]
    assert incident["location_snapshot_source"] == "last_completed_result" and incident["live_location"] == "D3"
    assert incident["athlete_snapshot"]["covered_by"] == "" and incident["bout_snapshot"]["covered_by"] == "Taylor"
    assert not incident["summary"]["target_coverage_was_recorded"] and incident["summary"]["bout_coverage_was_recorded"]
    taylor = next(c for c in incident["coaches_snapshot"]["coaches"] if c["coach_name"] == "Taylor")
    assert taylor["status"] == "available"


def test_bye_is_never_a_completed_fenced_bout(day):
    db, _, event, _, athletes = day
    db.mark_bye(event["id"], athletes["target"]["id"], actor="Casey")
    with pytest.raises(CompCoachError, match="bye is not a fenced bout"):
        report(day, bout_context="last_completed")
    incident = report(day)
    assert incident["bout_kind"] == "current" and incident["bout_number"] == 2
    assert incident["bout_outcome"] == "" and incident["source_action_id"] is None


def test_pools_allow_distinct_repeated_incidents_but_retry_key_coalesces(day):
    db, meet, event, _, athletes = day
    db.merge_import(event["id"], [record("target", phase="pools")], "Casey")
    first = report(day, idempotency_key="pool-one", note="First bout")
    retry = report(day, idempotency_key="pool-one", note="Retry does not rewrite original note")
    second = report(day, idempotency_key="pool-two", note="Another bout")
    assert first["id"] == retry["id"] != second["id"] and retry["note"] == "First bout"
    db.set_pool_result(event["id"], athletes["target"]["id"], wins=3, losses=3, actor="Casey")
    third = report(day, idempotency_key="after-pools", note="Late parent report")
    assert third["phase"] == "pools" and third["bout_number"] is None
    assert len(db.list_missed_coaching(meet["id"])) == 3


def test_phase_reset_does_not_merge_a_genuinely_new_de_run(day):
    db, _, event, _, _ = day
    first = report(day)
    db.merge_import(event["id"], [record("target", phase="pools")], "Casey")
    db.merge_import(event["id"], [record("target", phase="de")], "Casey")
    second = report(day)
    assert first["bout_number"] == second["bout_number"] == 1
    assert first["bout_key"] != second["bout_key"] and first["id"] != second["id"]


def test_stale_report_rejected_but_same_operation_retry_preserves_original_snapshot(day):
    db, meet, event, _, athletes = day
    before = db.get_athlete(event["id"], athletes["target"]["id"])
    first = report(day, expected_version=before["version"], idempotency_key="stable")
    db.report_call(event["id"], athletes["target"]["id"], status="on_deck", location="J2", actor="Casey")
    with pytest.raises(ConcurrentUpdateError, match="athlete changed"):
        report(day, expected_version=before["version"], idempotency_key="another")
    retry = report(day, expected_version=before["version"], idempotency_key="stable")
    assert retry["id"] == first["id"] and retry["live_location"] == "D3"
    assert len(db.list_missed_coaching(meet["id"])) == 1


def test_correction_preserves_evidence_is_visible_in_audit_and_cannot_generic_undo(day):
    db, meet, event, _, _ = day
    incident = report(day)
    before = db.get_meet_revision(meet["id"])
    with pytest.raises(ConcurrentUpdateError, match="report changed"):
        db.correct_missed_coaching(meet["id"], incident["id"], "Alex", expected_version=7)
    corrected = db.correct_missed_coaching(meet["id"], incident["id"], "Alex", expected_version=0, note="Coach had arrived.")
    assert corrected["status"] == "corrected" and corrected["version"] == 1
    assert corrected["corrected_by"] == "Alex" and corrected["correction_note"] == "Coach had arrived."
    for key in ("recorded_at", "recorded_by", "athlete_snapshot", "bout_snapshot", "coaches_snapshot", "summary"):
        assert corrected[key] == incident[key]
    assert db.list_missed_coaching(meet["id"]) == []
    assert db.list_missed_coaching(meet["id"], include_corrected=True)[0]["id"] == incident["id"]
    assert db.get_meet_revision(meet["id"]) != before
    action = db.recent_actions(event["id"])[0]
    assert action["previous"] is None and action["action"] == "missed_coaching_corrected"
    with pytest.raises(CompCoachError):
        db.undo_action(event["id"], action["id"], "Casey")
    replacement = report(day)
    assert replacement["id"] != incident["id"]


def test_archived_review_is_readonly_and_practice_data_does_not_leak_into_real_history(day):
    db, meet, event, _, _ = day
    real = report(day)
    hub = training.start_training(db, meet["id"], actor="Casey")
    run = training.join_training(db, hub["id"], "Learner", participant_key="a" * 32)
    exercise_event = db.list_meet_events(run["id"])[0]
    exercise = db.list_athletes(exercise_event["id"])[0]
    practice = db.report_missed_coaching(exercise_event["id"], exercise["id"], "Learner")
    assert practice["is_training"] and db.list_missed_coaching(run["id"]) == []
    assert db.list_missed_coaching(run["id"], include_training=True)[0]["id"] == practice["id"]
    assert db.list_missed_coaching(meet["id"])[0]["id"] == real["id"]
    db.set_meet_locked(meet["id"], True)
    assert db.list_missed_coaching(meet["id"])[0]["id"] == real["id"]
    with pytest.raises(EventLockedError):
        report(day)
    with pytest.raises(EventLockedError):
        db.correct_missed_coaching(meet["id"], real["id"], "Casey")


def test_migration_discovers_incident_table_and_preserves_immutable_evidence(day):
    from compcoach_live import migrate_to_supabase as migration
    db, meet, _, _, _ = day
    incident = report(day)
    with migration.source_snapshot(db.path) as snapshot:
        tables = migration.ordered_tables(snapshot)
        assert tables.index("athletes") < tables.index("missed_coaching_incidents")
        rows = migration.table_rows(snapshot, "missed_coaching_incidents")
        assert len(rows) == 1 and rows[0]["id"] == incident["id"]
        saved = db._missed_coaching_dict(rows[0])
        assert saved["coaches_snapshot"] == incident["coaches_snapshot"]
        assert saved["summary"] == incident["summary"]
    assert db.list_missed_coaching(meet["id"])[0]["id"] == incident["id"]


def test_training_athlete_reset_cascades_incidents_without_touching_real_reports(day):
    db, meet, _, _, _ = day
    real = report(day)
    hub = training.start_training(db, meet["id"], actor="Casey")
    run = training.join_training(db, hub["id"], "Learner", participant_key="b" * 32)
    event = db.list_meet_events(run["id"])[0]
    athlete = db.list_athletes(event["id"])[0]
    db.report_missed_coaching(event["id"], athlete["id"], "Learner")
    with db._connection() as conn:
        conn.execute("DELETE FROM athletes WHERE id = ?", (athlete["id"],))
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    assert db.list_missed_coaching(run["id"], include_training=True) == []
    assert db.list_missed_coaching(meet["id"])[0]["id"] == real["id"]
