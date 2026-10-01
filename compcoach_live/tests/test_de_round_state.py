"""Advancement queues and safe correction of rushed direct-elimination taps."""

import json
import sqlite3

import pytest

from compcoach_live.de_bouts import create_de_bout, resolve_de_bout
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key="a", *, phase="de", pod="B"):
    return {"athlete_id": key, "name": f"ATHLETE {key}", "phase": phase,
            "pod": pod, "strip": f"{pod}1", "pool": "1" if phase == "pools" else "", "time": ""}


@pytest.fixture
def context(tmp_path):
    db = CompCoachDB(tmp_path / "rounds.db")
    event = db.create_event("DE Test", ["Alex", "Taylor", "Morgan"], ["Jordan"])
    db.merge_import(event["id"], [record()], "Jordan")
    athlete = db.list_athletes(event["id"])[0]
    return db, event, athlete


def current(ctx):
    db, event, athlete = ctx
    return db.get_athlete(event["id"], athlete["id"])


def win(ctx):
    db, event, athlete = ctx
    return db.mark_result(event["id"], athlete["id"], outcome="won", actor="Alex", expected_version=current(ctx)["version"])


@pytest.mark.parametrize("outcome, awaiting, state", [("won", 1, "active"), ("lost", 0, "eliminated")])
def test_result_sets_canonical_round_queue_state(context, outcome, awaiting, state):
    db, event, athlete = context
    db.report_call(event["id"], athlete["id"], status="now", location="B2", actor="Jordan")
    result = db.mark_result(event["id"], athlete["id"], outcome=outcome, actor="Alex", expected_version=current(context)["version"])
    assert result["de_awaiting_next"] == awaiting
    assert result["active_state"] == state
    assert result["call_status"] == "waiting" and not result["live_location"]
    assert current(context)["de_awaiting_next"] == awaiting


@pytest.mark.parametrize("readiness", ["call", "resume", "help"])
def test_new_bout_readiness_returns_advanced_athlete_and_clears_every_peer_availability(context, readiness):
    db, event, athlete = context
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=["Alex", "Taylor", "Morgan"], actor="Jordan")
    advanced = win(context)
    meet_id = db.get_meet_for_event(event["id"])["id"]
    for coach in ["Alex", "Taylor", "Morgan"]:
        db.set_coach_availability(meet_id, coach, True, coach)
    if readiness == "call":
        result = db.report_call(event["id"], athlete["id"], status="on_deck", location="B3", actor="Jordan", expected_version=advanced["version"])
    elif readiness == "resume":
        result = db.resume_de_athlete(event["id"], athlete["id"], "Jordan", expected_version=advanced["version"])
    else:
        result = db.request_help(event["id"], athlete["id"], "Alex", expected_version=advanced["version"])
    assert result["de_awaiting_next"] == 0
    assert result["de_wins"] == 1 and result["last_de_result"] == "won"
    assert not any(row["is_available"] for row in db.list_coach_availability(meet_id))


def test_bye_advances_without_inventing_a_win(context):
    db, event, athlete = context
    result = db.mark_bye(event["id"], athlete["id"], actor="Alex", expected_version=athlete["version"])
    assert (result["de_byes"], result["de_wins"], result["de_rounds_passed"], result["de_awaiting_next"]) == (1, 0, 1, 1)


def test_duplicate_tap_using_old_revision_cannot_increment_twice(context):
    db, event, athlete = context
    win(context)
    with pytest.raises(ConcurrentUpdateError):
        db.mark_result(event["id"], athlete["id"], outcome="won", actor="Taylor", expected_version=athlete["version"])
    assert current(context)["de_wins"] == 1


def test_reimports_preserve_advanced_state_and_phase_transition_resets_it(context):
    db, event, athlete = context
    win(context)
    db.merge_import(event["id"], [record(pod="C")], "Jordan")
    assert current(context)["pod"] == "C" and current(context)["de_awaiting_next"] == 1
    db.merge_import(event["id"], [record(phase="pools")], "Jordan")
    assert current(context)["de_awaiting_next"] == 0 and current(context)["de_wins"] == 0
    db.merge_import(event["id"], [record()], "Jordan")
    assert current(context)["de_awaiting_next"] == 0


def test_selective_correction_after_assignment_keeps_all_equal_coaches(context):
    db, event, athlete = context
    win(context)
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=["Morgan", "Taylor", "Alex"], actor="Jordan")
    updated = current(context)
    result = db.correct_de_result(event["id"], athlete["id"], "Taylor", expected_version=updated["version"])
    assert result["de_coaches"] == ["Morgan", "Taylor", "Alex"]
    assert result["assignment_override"] == 1
    assert (result["de_wins"], result["last_de_result"], result["de_awaiting_next"]) == (0, "", 0)
    actions = db.recent_actions(event["id"])
    assert next(action for action in actions if action["action"] == "won")["undone_by"] == "Taylor"
    assert actions[0]["action"] == "de_result_correction"


def test_selective_correction_preserves_fresh_call_coverage_and_help(context):
    db, event, athlete = context
    win(context)
    db.report_call(event["id"], athlete["id"], status="on_deck", location="D4", actor="Jordan", covered_by="Morgan")
    db.request_help(event["id"], athlete["id"], "Morgan", location="D4")
    before = current(context)
    result = db.correct_de_result(event["id"], athlete["id"], "Alex", expected_version=before["version"])
    for field in ("call_status", "live_location", "reported_at", "reported_by", "covered_by", "covered_at", "help_requested_by", "help_requested_at", "help_location"):
        assert result[field] == before[field]
    assert result["de_awaiting_next"] == 0 and result["de_wins"] == 0


def test_correct_lost_restores_only_latest_result_and_does_not_replay_old_calls(context):
    db, event, athlete = context
    win(context)
    db.resume_de_athlete(event["id"], athlete["id"], "Alex")
    db.report_call(event["id"], athlete["id"], status="now", location="B4", actor="Jordan")
    lost = db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex", expected_version=current(context)["version"])
    restored = db.correct_de_result(event["id"], athlete["id"], "Taylor", expected_version=lost["version"])
    assert (restored["active_state"], restored["de_wins"], restored["last_de_result"], restored["de_awaiting_next"]) == ("active", 1, "won", 0)
    assert restored["call_status"] == "waiting" and restored["live_location"] == ""
    assert restored["reported_at"] is None


def test_multiple_results_can_be_corrected_last_first(context):
    db, event, athlete = context
    db.mark_bye(event["id"], athlete["id"], actor="Alex")
    db.resume_de_athlete(event["id"], athlete["id"], "Alex")
    win(context)
    db.resume_de_athlete(event["id"], athlete["id"], "Alex")
    win(context)
    result = db.correct_de_result(event["id"], athlete["id"], "Jordan", expected_version=current(context)["version"])
    assert (result["de_byes"], result["de_wins"], result["last_de_result"]) == (1, 1, "won")
    result = db.correct_de_result(event["id"], athlete["id"], "Jordan", expected_version=result["version"])
    assert (result["de_byes"], result["de_wins"], result["last_de_result"]) == (1, 0, "bye")
    result = db.correct_de_result(event["id"], athlete["id"], "Jordan", expected_version=result["version"])
    assert (result["de_byes"], result["de_wins"], result["last_de_result"]) == (0, 0, "")
    with pytest.raises(CompCoachError, match="No individual DE result"):
        db.correct_de_result(event["id"], athlete["id"], "Jordan", expected_version=result["version"])


def test_correction_keeps_absence_and_never_returns_it_to_visible_lists(context):
    db, event, athlete = context
    win(context)
    absent = db.set_athlete_participation(event["id"], athlete["id"], "absent", "Alex", expected_version=current(context)["version"])
    result = db.correct_de_result(event["id"], athlete["id"], "Jordan", expected_version=absent["version"])
    assert result["participation_status"] == "absent" and result["de_wins"] == 0
    with pytest.raises(ConcurrentUpdateError):
        db.resume_de_athlete(event["id"], athlete["id"], "Alex", expected_version=result["version"])


def test_stale_correction_and_readiness_preserve_newer_updates(context):
    db, event, athlete = context
    advanced = win(context)
    db.report_call(event["id"], athlete["id"], status="on_deck", location="M1", actor="Jordan")
    for action in (db.correct_de_result, db.resume_de_athlete):
        with pytest.raises(ConcurrentUpdateError):
            action(event["id"], athlete["id"], "Taylor", expected_version=advanced["version"])
    assert current(context)["live_location"] == "M1" and current(context)["de_wins"] == 1


def test_competition_state_barrier_blocks_older_correction(context):
    db, event, athlete = context
    win(context)
    db.mark_out(event["id"], athlete["id"], "Jordan", expected_version=current(context)["version"])
    with pytest.raises(ConcurrentUpdateError, match="newer competition update"):
        db.correct_de_result(event["id"], athlete["id"], "Alex", expected_version=current(context)["version"])
    assert current(context)["active_state"] == "eliminated" and current(context)["de_wins"] == 1


def test_individual_correction_rejects_paired_outcomes(context):
    db, event, athlete = context
    db.merge_import(event["id"], [record("b")], "Jordan")
    opponent = next(row for row in db.list_athletes(event["id"]) if row["athlete_key"] == "b")
    bout = create_de_bout(db, event["id"], athlete["id"], opponent["id"], actor="Jordan")
    with pytest.raises(CompCoachError, match="both athletes"):
        db.correct_de_result(event["id"], athlete["id"], "Alex", expected_version=current(context)["version"])
    resolve_de_bout(db, event["id"], bout["id"], winner_id=athlete["id"], actor="Alex")
    for participant in (athlete, opponent):
        with pytest.raises(CompCoachError, match="both athletes"):
            db.correct_de_result(event["id"], participant["id"], "Jordan", expected_version=db.get_athlete(event["id"], participant["id"])["version"])


def test_closed_event_blocks_correction_and_readiness(context):
    db, event, athlete = context
    win(context)
    db.set_event_locked(event["id"], True)
    for action in (db.correct_de_result, db.resume_de_athlete):
        with pytest.raises(EventLockedError):
            action(event["id"], athlete["id"], "Alex", expected_version=current(context)["version"])


def test_legacy_upgrade_backfills_once_but_resumed_state_survives_restart(context):
    db, event, athlete = context
    win(context)
    with sqlite3.connect(db.path) as conn:
        conn.execute("ALTER TABLE athletes DROP COLUMN de_awaiting_next")
    migrated = CompCoachDB(db.path)
    assert migrated.get_athlete(event["id"], athlete["id"])["de_awaiting_next"] == 1
    migrated.resume_de_athlete(event["id"], athlete["id"], "Alex")
    restarted = CompCoachDB(db.path)
    assert restarted.get_athlete(event["id"], athlete["id"])["de_awaiting_next"] == 0


def test_legacy_generic_undo_derives_missing_round_queue_field(context):
    db, event, athlete = context
    win(context)
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=["Taylor"], actor="Jordan")
    action = db.recent_actions(event["id"])[0]
    previous = dict(action["previous"])
    previous.pop("de_awaiting_next")
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE actions SET previous_json = ? WHERE id = ?", (json.dumps(previous), action["id"]))
    restored = db.undo_action(event["id"], action["id"], "Jordan")
    assert restored["de_awaiting_next"] == 1 and restored["de_wins"] == 1
