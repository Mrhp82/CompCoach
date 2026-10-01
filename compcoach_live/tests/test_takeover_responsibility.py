"""Temporary next-call responsibility never pretends a coach is at a strip."""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest

from compcoach_live.de_bouts import create_de_bout, resolve_de_bout, undo_de_bout_result
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


@pytest.fixture
def context(tmp_path):
    url = os.environ.get("COMPCOACH_TEST_POSTGRES_URL")
    if url:
        from compcoach_live.postgres_storage import PostgresCompCoachDB
        schema = "compcoach_takeover_" + uuid4().hex
        db = PostgresCompCoachDB(url, schema=schema, pool_max_size=1 if os.environ.get("COMPCOACH_TEST_POSTGRES_PGLITE") == "1" else 4)
    else:
        db = CompCoachDB(tmp_path / "takeover.db")
    meet = db.create_meet("NAC", ["Alex", "Jordan", "Morgan"], ["Taylor"])
    events = [db.list_meet_events(meet["id"])[0], db.add_meet_event(meet["id"], "Cadets")]
    groups = []
    for index, event in enumerate(events):
        db.merge_import(event["id"], [record(f"ath_{index}_{n}") for n in range(2)], "Taylor")
        db.assign_de_pods(event["id"], pods=["B"], coaches=["Alex"], actor="Taylor")
        groups.append(db.list_athletes(event["id"]))
    try:
        yield db, meet, events, groups
    finally:
        if url:
            with db._connection() as conn:
                conn.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
            db.close()


def record(key, *, phase="de", pod="B"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "strip": f"{pod}1", "pod": pod,
            "pool": "1" if phase == "pools" else "", "phase": phase, "time": ""}


def row(db, event, athlete):
    return db.get_athlete(event["id"], athlete["id"])


def state(db, meet, coach):
    return next(value for value in db.list_coach_availability(meet["id"]) if value["coach_name"] == coach)


def reserve(context, coach="Morgan"):
    db, meet, events, groups = context
    current = row(db, events[0], groups[0][0])
    return db.take_over_athlete(events[0]["id"], current["id"], coach, coach,
                               expected_version=current["version"],
                               expected_coach_version=state(db, meet, coach)["version"])


def test_promise_before_call_preserves_pod_strip_plan_and_consumes_availability(context):
    db, meet, events, groups = context
    db.set_coach_availability(meet["id"], "Morgan", True, "Morgan")
    original = row(db, events[0], groups[0][0])
    before = state(db, meet, "Morgan")
    saved = reserve(context)
    assert saved["takeover_coach"] == "Morgan" and saved["takeover_at"] and saved["takeover_by"] == "Morgan"
    for field in ("pod", "source_strip", "call_status", "live_location", "covered_by", "covered_at", "de_coaches"):
        assert saved[field] == original[field]
    after = state(db, meet, "Morgan")
    assert not after["is_available"] and not after["is_busy"] and after["busy_since"] is None
    assert after["available_since"] is None
    assert after["version"] == before["version"] + 1
    assert after["takeover_count"] == after["temporary_count"] == after["unfinished_count"] == 1
    action = next(value for value in db.recent_actions(events[0]["id"]) if value["action"] == "takeover")
    assert action["actor"] == "Morgan" and action["new"]["takeover_coach"] == "Morgan"
    assert not any(value["assignment_kind"] == "coverage" and value["coach_name"] == "Morgan"
                   for value in db.list_assignment_history(meet_id=meet["id"]))


def test_calls_preserve_responsibility_but_actual_arrival_clears_it(context):
    db, meet, events, groups = context
    saved = reserve(context)
    called = db.report_call(events[0]["id"], saved["id"], status="on_deck", location="J4", actor="Taylor", expected_version=saved["version"])
    assert called["takeover_coach"] == "Morgan" and called["takeover_at"] == saved["takeover_at"]
    covered = db.cover_athlete(events[0]["id"], saved["id"], "Morgan", "Morgan", expected_version=called["version"])
    assert covered["takeover_coach"] == "" and covered["takeover_at"] is None and covered["takeover_by"] == ""
    assert covered["covered_by"] == "Morgan" and state(db, meet, "Morgan")["is_busy"]


def test_other_coachs_actual_coverage_also_ends_promise(context):
    db, _, events, _ = context
    saved = reserve(context)
    covered = db.report_call(events[0]["id"], saved["id"], status="now", location="C3", actor="Taylor", covered_by="Jordan", expected_version=saved["version"])
    assert covered["covered_by"] == "Jordan" and covered["takeover_coach"] == ""


def test_same_owner_repeat_is_idempotent_and_keeps_original_timestamp(context):
    db, meet, events, groups = context
    original = row(db, events[0], groups[0][0])
    one = reserve(context)
    coach_state = state(db, meet, "Morgan")
    two = db.take_over_athlete(events[0]["id"], one["id"], "Morgan", "Morgan", expected_version=original["version"])
    assert two["version"] == one["version"] and two["takeover_at"] == one["takeover_at"]
    assert state(db, meet, "Morgan")["version"] == coach_state["version"]
    assert not state(db, meet, "Morgan")["is_available"]
    assert sum(value["action"] == "takeover" for value in db.recent_actions(events[0]["id"])) == 1


def test_pending_promise_masks_legacy_available_flag(context):
    db, meet, events, _ = context
    db.set_coach_availability(meet["id"], "Morgan", True, "Morgan")
    saved = reserve(context)
    # Older app versions could persist both a takeover and a green availability
    # flag. Projection must repair the contradiction immediately after upgrade.
    with db._connection() as conn:
        conn.execute(
            "UPDATE coach_availability SET is_available = 1, available_since = ? "
            "WHERE meet_id = ? AND coach_name = ?",
            ("2026-10-01T10:00:00Z", meet["id"], "Morgan"),
        )
        conn.commit()
    current = state(db, meet, "Morgan")
    assert not current["is_available"] and current["available_since"] is None
    assert not current["is_busy"] and current["busy_since"] is None
    assert current["takeover_count"] == 1
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    with pytest.raises(ConcurrentUpdateError):
        db.deploy_available_coach_to_pod(
            events[1]["id"], pod="B", coach="Morgan", actor="Taylor",
            expected_availability_version=current["version"],
        )
    assert all(athlete["de_coaches"] == ["Alex"] for athlete in db.list_athletes(events[1]["id"]))


@pytest.mark.parametrize("ending", ["release", "won"])
def test_last_legacy_promise_refreshes_availability_time_and_revision_once(context, ending):
    db, meet, events, _ = context
    db.set_coach_availability(meet["id"], "Morgan", True, "Morgan")
    saved = reserve(context)
    previous_time = "2026-09-01T10:00:00Z"
    with db._connection() as conn:
        conn.execute(
            "UPDATE coach_availability SET is_available = 1, available_since = ? "
            "WHERE meet_id = ? AND coach_name = ?",
            (previous_time, meet["id"], "Morgan"),
        )
        conn.commit()
    before = state(db, meet, "Morgan")
    assert not before["is_available"] and before["available_since"] is None

    if ending == "release":
        db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan")
    else:
        db.mark_result(events[0]["id"], saved["id"], outcome="won", actor="Alex")
    after = state(db, meet, "Morgan")
    assert after["is_available"] and not after["is_busy"]
    assert after["takeover_count"] == 0
    assert after["available_since"] and after["available_since"] != previous_time
    assert after["available_since"] >= saved["takeover_at"]
    assert after["version"] == before["version"] + 1


@pytest.mark.parametrize("actor", ["Morgan", "Taylor"])
def test_manual_available_cannot_bypass_pending_promise(context, actor):
    db, meet, events, _ = context
    saved = reserve(context)
    before = state(db, meet, "Morgan")
    with pytest.raises(CompCoachError):
        db.set_coach_availability(
            meet["id"], "Morgan", True, actor, expected_version=before["version"],
        )
    after = state(db, meet, "Morgan")
    assert not after["is_available"] and after["version"] == before["version"]
    assert row(db, events[0], saved)["takeover_coach"] == "Morgan"


def test_stale_available_tap_cannot_restore_green_after_takeover(context):
    db, meet, _, _ = context
    db.set_coach_availability(meet["id"], "Morgan", True, "Morgan")
    before = state(db, meet, "Morgan")
    reserve(context)
    with pytest.raises(CompCoachError):
        db.set_coach_availability(
            meet["id"], "Morgan", True, "Morgan", expected_version=before["version"],
        )
    assert not state(db, meet, "Morgan")["is_available"]


@pytest.mark.parametrize("ending", ["release", "won", "lost", "bye"])
def test_only_last_pending_promise_releases_coach_across_events(context, ending):
    db, meet, events, groups = context
    one = reserve(context)
    two = db.take_over_athlete(events[1]["id"], groups[1][0]["id"], "Morgan", "Morgan")
    assert state(db, meet, "Morgan")["takeover_count"] == 2

    def end(event, athlete):
        if ending == "release":
            return db.release_takeover(event["id"], athlete["id"], "Morgan", "Morgan")
        if ending == "bye":
            return db.mark_bye(event["id"], athlete["id"], actor="Alex")
        return db.mark_result(event["id"], athlete["id"], outcome=ending, actor="Alex")

    end(events[0], one)
    after_first = state(db, meet, "Morgan")
    assert after_first["takeover_count"] == 1 and not after_first["is_available"]
    assert after_first["available_since"] is None and not after_first["is_busy"]
    assert row(db, events[1], two)["takeover_coach"] == "Morgan"
    end(events[1], two)
    after_last = state(db, meet, "Morgan")
    assert after_last["takeover_count"] == 0 and after_last["is_available"]
    assert after_last["available_since"] and not after_last["is_busy"]


def test_releasing_last_promise_does_not_release_physical_coverage_elsewhere(context):
    db, meet, events, groups = context
    saved = reserve(context)
    other = db.cover_athlete(events[1]["id"], groups[1][0]["id"], "Morgan", "Taylor")
    db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan")
    current = state(db, meet, "Morgan")
    assert current["takeover_count"] == 0 and current["is_busy"] and not current["is_available"]
    assert row(db, events[1], other)["covered_by"] == "Morgan"


@pytest.mark.parametrize("ending", ["other_coach", "absent", "withdrawn", "phase_change"])
def test_ending_last_promise_without_result_releases_available_coach(context, ending):
    db, meet, events, _ = context
    saved = reserve(context)
    if ending == "other_coach":
        db.cover_athlete(events[0]["id"], saved["id"], "Jordan", "Taylor")
    elif ending == "phase_change":
        db.merge_import(events[0]["id"], [record(saved["athlete_key"], phase="pools")], "Taylor")
    else:
        db.set_athlete_participation(events[0]["id"], saved["id"], ending, "Taylor")
    current = state(db, meet, "Morgan")
    assert row(db, events[0], saved)["takeover_coach"] == ""
    assert current["takeover_count"] == 0 and current["is_available"] and not current["is_busy"]


def test_other_owner_and_stale_target_are_rejected(context):
    db, _, events, groups = context
    stale = row(db, events[0], groups[0][0])
    saved = reserve(context)
    with pytest.raises(ConcurrentUpdateError, match="already taken responsibility"):
        db.take_over_athlete(events[0]["id"], saved["id"], "Jordan", "Jordan", expected_version=stale["version"])
    released = db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan", expected_version=saved["version"])
    with pytest.raises(ConcurrentUpdateError):
        db.take_over_athlete(events[0]["id"], saved["id"], "Jordan", "Jordan", expected_version=stale["version"])
    assert row(db, events[0], released)["takeover_coach"] == ""


def test_concurrent_first_coach_wins_only_one_promise(context):
    db, _, events, groups = context
    original = row(db, events[0], groups[0][0])
    def take(coach):
        try:
            return db.take_over_athlete(events[0]["id"], original["id"], coach, coach, expected_version=original["version"])
        except ConcurrentUpdateError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(take, ["Morgan", "Jordan"]))
    assert sum(value is not None for value in outcomes) == 1
    assert row(db, events[0], original)["takeover_coach"] in {"Morgan", "Jordan"}


def test_busy_in_other_event_is_rejected_and_never_handed_off(context):
    db, meet, events, groups = context
    covered = db.cover_athlete(events[1]["id"], groups[1][0]["id"], "Morgan", "Taylor")
    with pytest.raises(ConcurrentUpdateError, match="already covering"):
        reserve(context)
    assert row(db, events[1], covered)["covered_by"] == "Morgan"
    assert row(db, events[0], groups[0][0])["takeover_coach"] == ""
    assert state(db, meet, "Morgan")["busy_count"] == 1


def test_stale_coach_revision_rejected_after_availability_changes(context):
    db, meet, events, groups = context
    stale = state(db, meet, "Morgan")["version"]
    db.set_coach_availability(meet["id"], "Morgan", True, "Morgan")
    current = row(db, events[0], groups[0][0])
    with pytest.raises(ConcurrentUpdateError, match="coach's situation changed"):
        db.take_over_athlete(events[0]["id"], current["id"], "Morgan", "Morgan", expected_version=current["version"], expected_coach_version=stale)
    assert row(db, events[0], current)["takeover_coach"] == ""


@pytest.mark.parametrize("coach", ["Unknown", "Taylor"])
def test_only_active_coaches_can_take_responsibility(context, coach):
    db, _, events, groups = context
    with pytest.raises(CompCoachError, match="not an active coach"):
        db.take_over_athlete(events[0]["id"], groups[0][0]["id"], coach, coach)


@pytest.mark.parametrize("change", ["covered", "absent", "lost"])
def test_ineligible_athletes_cannot_be_reserved(context, change):
    db, _, events, groups = context
    target = groups[0][0]
    if change == "covered":
        db.cover_athlete(events[0]["id"], target["id"], "Alex", "Taylor")
    elif change == "absent":
        db.mark_absent(events[0]["id"], target["id"], "Taylor")
    else:
        db.mark_result(events[0]["id"], target["id"], outcome="lost", actor="Alex")
    with pytest.raises(ConcurrentUpdateError):
        db.take_over_athlete(events[0]["id"], target["id"], "Morgan", "Morgan")


def test_release_restricts_owner_and_stale_revision(context):
    db, _, events, _ = context
    saved = reserve(context)
    with pytest.raises(ConcurrentUpdateError, match="current responsible coach"):
        db.release_takeover(events[0]["id"], saved["id"], "Jordan", "Jordan", expected_version=saved["version"])
    db.report_call(events[0]["id"], saved["id"], status="now", location="C3", actor="Taylor")
    with pytest.raises(ConcurrentUpdateError):
        db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan", expected_version=saved["version"])
    current = row(db, events[0], saved)
    released = db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan", expected_version=current["version"])
    assert released["takeover_coach"] == "" and released["live_location"] == "C3" and released["call_status"] == "now"


@pytest.mark.parametrize("outcome", ["won", "lost", "bye"])
def test_results_clear_promise_and_corrections_never_resurrect_it(context, outcome):
    db, meet, events, _ = context
    saved = reserve(context)
    if outcome == "bye":
        result = db.mark_bye(events[0]["id"], saved["id"], actor="Alex", expected_version=saved["version"])
    else:
        result = db.mark_result(events[0]["id"], saved["id"], outcome=outcome, actor="Alex", expected_version=saved["version"])
    assert result["takeover_coach"] == "" and result["takeover_at"] is None
    assert state(db, meet, "Morgan")["is_available"]
    corrected = db.correct_de_result(events[0]["id"], saved["id"], "Taylor", expected_version=result["version"])
    assert corrected["active_state"] == "active" and corrected["takeover_coach"] == ""


def test_generic_result_undo_does_not_replay_old_promise(context):
    db, _, events, _ = context
    saved = reserve(context)
    db.mark_result(events[0]["id"], saved["id"], outcome="lost", actor="Alex")
    action = next(value for value in db.recent_actions(events[0]["id"]) if value["action"] == "lost")
    result = db.undo_action(events[0]["id"], action["id"], "Taylor")
    assert result["active_state"] == "active" and result["takeover_coach"] == ""


def test_result_never_marks_promise_owner_available_if_busy_elsewhere(context):
    db, meet, events, groups = context
    saved = reserve(context)
    other = db.cover_athlete(events[1]["id"], groups[1][0]["id"], "Morgan", "Taylor")
    result = db.mark_result(events[0]["id"], saved["id"], outcome="won", actor="Alex")
    assert result["takeover_coach"] == ""
    assert row(db, events[1], other)["covered_by"] == "Morgan"
    assert state(db, meet, "Morgan")["is_busy"] and not state(db, meet, "Morgan")["is_available"]


def test_afm_results_clear_both_promises_and_joint_undo_does_not_restore(context):
    db, _, events, groups = context
    one = reserve(context)
    two = db.take_over_athlete(events[0]["id"], groups[0][1]["id"], "Jordan", "Jordan")
    bout = create_de_bout(db, events[0]["id"], one["id"], two["id"], actor="Taylor")
    result = resolve_de_bout(db, events[0]["id"], bout["id"], winner_id=one["id"], actor="Taylor")
    assert all(row(db, events[0], athlete)["takeover_coach"] == "" for athlete in [one, two])
    undo_de_bout_result(db, events[0]["id"], bout["id"], actor="Taylor", expected_version=result["version"])
    assert all(row(db, events[0], athlete)["takeover_coach"] == "" for athlete in [one, two])


def test_same_phase_import_preserves_promise_but_phase_changes_clear_it(context):
    db, _, events, _ = context
    saved = reserve(context)
    db.merge_import(events[0]["id"], [record(saved["athlete_key"], pod="C")], "Taylor")
    imported = row(db, events[0], saved)
    assert imported["takeover_coach"] == "Morgan" and imported["takeover_at"] == saved["takeover_at"]
    db.merge_import(events[0]["id"], [record(saved["athlete_key"], phase="pools")], "Taylor")
    assert row(db, events[0], saved)["takeover_coach"] == ""


@pytest.mark.parametrize("status", ["absent", "withdrawn"])
def test_attendance_changes_end_promise_and_restore_does_not_revive_it(context, status):
    db, _, events, _ = context
    saved = reserve(context)
    absent = db.set_athlete_participation(events[0]["id"], saved["id"], status, "Taylor")
    assert absent["takeover_coach"] == ""
    restored = db.restore_athlete_participation(events[0]["id"], saved["id"], "Taylor", expected_version=absent["version"])
    assert restored["takeover_coach"] == ""


def test_pool_completion_ends_temporary_responsibility(context):
    db, _, events, groups = context
    key = groups[0][0]["athlete_key"]
    db.merge_import(events[0]["id"], [record(key, phase="pools")], "Taylor")
    saved = reserve(context)
    completed = db.set_pool_result(events[0]["id"], saved["id"], wins=3, losses=3, actor="Alex", expected_version=saved["version"])
    assert completed["takeover_coach"] == "" and completed["pool_wins"] == 3


def test_pending_owner_name_tracks_rename_but_audit_keeps_original(context):
    db, meet, events, _ = context
    saved = reserve(context)
    coach = next(value for value in db.list_coaches() if value["name"] == "Morgan")
    db.update_coach(coach["id"], name="Riley")
    current = row(db, events[0], saved)
    assert current["takeover_coach"] == "Riley" and current["takeover_by"] == "Morgan"
    assert state(db, meet, "Riley")["takeover_count"] == 1


def test_removed_staff_releases_promise_with_audit(context):
    db, meet, events, _ = context
    saved = reserve(context)
    coach = next(value for value in db.list_coaches() if value["name"] == "Morgan")
    db.set_day_coach_presence(meet["id"], coach["id"], actor="Taylor", presence_status="absent")
    assert row(db, events[0], saved)["takeover_coach"] == ""
    assert any(value["action"] == "takeover_staff_removed" for value in db.recent_actions(events[0]["id"]))


def test_undo_release_checks_coach_not_busy_in_another_event(context):
    db, _, events, groups = context
    saved = reserve(context)
    db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan")
    action = next(value for value in db.recent_actions(events[0]["id"]) if value["action"] == "takeover_release")
    db.cover_athlete(events[1]["id"], groups[1][0]["id"], "Morgan", "Taylor")
    with pytest.raises(ConcurrentUpdateError, match="already covering"):
        db.undo_action(events[0]["id"], action["id"], "Taylor")
    assert row(db, events[0], saved)["takeover_coach"] == ""


def test_closed_day_blocks_takeover_release_and_same_owner_retry(context):
    db, meet, events, groups = context
    saved = reserve(context)
    db.finish_meet(meet["id"], "Taylor")
    with pytest.raises(EventLockedError):
        db.take_over_athlete(events[0]["id"], groups[0][1]["id"], "Jordan", "Jordan")
    with pytest.raises(EventLockedError):
        db.take_over_athlete(events[0]["id"], saved["id"], "Morgan", "Morgan")
    with pytest.raises(EventLockedError):
        db.release_takeover(events[0]["id"], saved["id"], "Morgan", "Morgan")


def test_additive_sqlite_upgrade_preserves_existing_rows_and_results(tmp_path):
    db = CompCoachDB(tmp_path / "legacy.db")
    event = db.create_event("Previous version", ["Alex"], ["Taylor"])
    db.merge_import(event["id"], [record("legacy")], "Taylor")
    original = db.list_athletes(event["id"])[0]
    saved = db.mark_result(event["id"], original["id"], outcome="won", actor="Alex")
    with db._connection() as conn:
        for field in ("takeover_coach", "takeover_at", "takeover_by"):
            conn.execute(f"ALTER TABLE athletes DROP COLUMN {field}")
    upgraded = CompCoachDB(tmp_path / "legacy.db")
    current = upgraded.get_athlete(event["id"], original["id"])
    assert current["takeover_coach"] == current["takeover_by"] == "" and current["takeover_at"] is None
    for field in ("name", "athlete_key", "version", "de_wins", "last_de_result_at", "pod", "source_strip"):
        assert current[field] == saved[field]
