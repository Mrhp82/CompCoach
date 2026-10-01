"""Atomicity, revision guards and reversibility of manually reported AFM bouts."""

import pytest

from compcoach_live.de_bouts import (
    cancel_de_bout, create_de_bout, list_de_bouts, resolve_de_bout,
    restore_de_bout_pairing, undo_de_bout_result,
)
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


@pytest.fixture
def bout_context(tmp_path):
    db = CompCoachDB(tmp_path / "bouts.db")
    meet = db.create_meet("October Test", ["Alex", "Taylor", "Morgan"], ["Jordan"], first_event_name="Junior Epee")
    event = db.list_meet_events(meet["id"])[0]
    records = [{"athlete_id": f"ath_{name.lower()}","name": name,"phase":"de","pod":"M","strip":"M","pool":"","time":""} for name in ("CASEY Athlete", "RILEY Athlete", "JAMIE Athlete")]
    db.merge_import(event["id"], records, "Jordan")
    a,b,c = sorted(db.list_athletes(event["id"]),key=lambda row:row["name"])
    return db,meet,event,a,b,c


def _create(ctx):
    db,_,event,a,b,_ = ctx
    return create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan",round_label="T64")


def test_pair_result_and_undo_preserve_assignments_results_and_audit_without_old_calls(bout_context):
    db,_,event,a,b,_ = bout_context
    db.assign_de_athletes(event["id"],[a["id"],b["id"]],coaches=["Alex","Taylor","Morgan"],actor="Jordan")
    db.report_call(event["id"],a["id"],status="now",location="M3",actor="Jordan")
    db.request_help(event["id"],b["id"],"Taylor",location="M3")
    before_a,before_b = [db.get_athlete(event["id"],row["id"]) for row in (a,b)]
    bout = _create(bout_context)
    resolved = resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex",expected_version=bout["version"],expected_a_version=before_a["version"],expected_b_version=before_b["version"])
    winner,loser = [db.get_athlete(event["id"],row["id"]) for row in (a,b)]
    assert (winner["active_state"],winner["de_wins"],winner["last_de_result"]) == ("active",1,"won")
    assert (loser["active_state"],loser["de_wins"],loser["last_de_result"]) == ("eliminated",0,"lost")
    assert winner["call_status"] == "waiting" and loser["help_requested_at"] is None
    assert winner["de_coaches"] == loser["de_coaches"] == ["Alex","Taylor","Morgan"]
    restored = undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan",expected_version=resolved["version"])
    assert restored["status"] == "pending"
    after_a,after_b = [db.get_athlete(event["id"],row["id"]) for row in (a,b)]
    assert after_a["de_wins"] == after_b["de_wins"] == 0
    assert after_a["live_location"] == ""
    assert after_b["help_requested_at"] is None
    assert after_b["active_state"] == "active"
    assert {"de_bout_create","de_bout_resolve","de_bout_undo_result"} <= {action["action"] for action in db.recent_actions(event["id"],200)}


@pytest.mark.parametrize("outcome",["won","lost"])
def test_individual_result_resolves_both_participants(bout_context,outcome):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    result = db.mark_result(event["id"],a["id"],outcome=outcome,actor="Alex",expected_version=a["version"])
    assert result["last_de_result"] == outcome
    other = db.get_athlete(event["id"],b["id"])
    assert other["last_de_result"] == ("lost" if outcome == "won" else "won")
    assert list_de_bouts(db,event["id"])[0]["status"] == "resolved"
    with pytest.raises(ConcurrentUpdateError):
        resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Taylor")
    assert sum(row["de_wins"] for row in db.list_athletes(event["id"])) == 1


def test_repeated_resolve_and_stale_result_never_increment_twice(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    with pytest.raises(ConcurrentUpdateError):
        resolve_de_bout(db,event["id"],bout["id"],winner_id=b["id"],actor="Taylor")
    with pytest.raises(ConcurrentUpdateError):
        db.mark_result(event["id"],a["id"],outcome="won",actor="Alex",expected_version=a["version"])
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 1


def test_stale_other_participant_blocked_but_undo_preserves_newer_live_call(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    newer_b = db.report_call(event["id"],b["id"],status="on_deck",location="M2",actor="Jordan")
    with pytest.raises(ConcurrentUpdateError,match="changed on another phone"):
        resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex",expected_a_version=a["version"],expected_b_version=b["version"])
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 0
    assert list_de_bouts(db,event["id"])[0]["status"] == "pending"
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex",expected_a_version=a["version"],expected_b_version=newer_b["version"])
    db.report_call(event["id"],a["id"],status="now",location="M4",actor="Jordan")
    undo_de_bout_result(db,event["id"],bout["id"],actor="Taylor")
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "active"
    assert db.get_athlete(event["id"],a["id"])["live_location"] == "M4"


def test_mid_transaction_failure_rolls_back_both_results_and_audit(bout_context,monkeypatch):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    original = db._update_athlete
    calls = []
    def fail_second(*args,**kwargs):
        calls.append(kwargs["athlete_id"])
        if len(calls) == 2:
            raise CompCoachError("Injected second-athlete failure")
        return original(*args,**kwargs)
    monkeypatch.setattr(db,"_update_athlete",fail_second)
    with pytest.raises(CompCoachError,match="second-athlete"):
        resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    assert db.get_athlete(event["id"],a["id"])["version"] == a["version"]
    assert db.get_athlete(event["id"],b["id"])["version"] == b["version"]
    assert list_de_bouts(db,event["id"])[0]["status"] == "pending"
    assert not any(row["action"] == "de_bout_result" for row in db.recent_actions(event["id"]))


def test_remove_restore_pair_is_reversible_without_touching_athletes(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    cancelled = cancel_de_bout(db,event["id"],bout["id"],actor="Alex",expected_version=bout["version"])
    assert not list_de_bouts(db,event["id"])
    assert list_de_bouts(db,event["id"],include_cancelled=True)[0]["status"] == "cancelled"
    assert db.get_athlete(event["id"],a["id"])["version"] == a["version"]
    restored = restore_de_bout_pairing(db,event["id"],bout["id"],actor="Taylor",expected_version=cancelled["version"])
    assert restored["status"] == "pending"
    assert db.get_athlete(event["id"],b["id"])["last_de_result"] == ""


def test_invalid_duplicate_cross_event_pool_and_out_pairings_are_blocked(bout_context):
    db,_,event,a,b,c = bout_context
    with pytest.raises(CompCoachError,match="different"):
        create_de_bout(db,event["id"],a["id"],a["id"],actor="Alex")
    with pytest.raises(CompCoachError,match="who you are"):
        create_de_bout(db,event["id"],a["id"],b["id"],actor="")
    _create(bout_context)
    with pytest.raises(ConcurrentUpdateError,match="already has"):
        create_de_bout(db,event["id"],a["id"],c["id"],actor="Taylor")
    other = db.create_event("Cadet Epee",["Alex"],["Jordan"])
    with pytest.raises(CompCoachError,match="no longer in this event"):
        create_de_bout(db,other["id"],a["id"],b["id"],actor="Taylor")
    db.mark_result(event["id"],c["id"],outcome="lost",actor="Alex")
    pending = list_de_bouts(db,event["id"])[0]
    cancel_de_bout(db,event["id"],pending["id"],actor="Alex")
    with pytest.raises(ConcurrentUpdateError,match="active"):
        create_de_bout(db,event["id"],a["id"],c["id"],actor="Taylor")


def test_new_pending_bout_blocks_undo_of_previous_round(bout_context):
    db,_,event,a,b,c = bout_context
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    create_de_bout(db,event["id"],a["id"],c["id"],actor="Taylor",round_label="T32")
    with pytest.raises(ConcurrentUpdateError,match="pending AFM"):
        undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan")
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "eliminated"


def test_individual_undo_and_restore_cannot_split_paired_result(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    paired_actions = [row for row in db.recent_actions(event["id"]) if row["action"] == "de_bout_result"]
    for action in paired_actions:
        with pytest.raises(CompCoachError,match="both athletes"):
            db.undo_action(event["id"],action["id"],"Jordan")
    with pytest.raises(CompCoachError,match="both athletes"):
        db.restore_athlete(event["id"],b["id"],"Jordan")


def test_reimport_preserves_pairing_and_closed_event_is_read_only(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    records = [{"athlete_id":row["athlete_key"],"name":row["name"],"phase":"de","pod":"M","strip":"M2","time":"","pool":""} for row in db.list_athletes(event["id"])]
    db.merge_import(event["id"],records,"Jordan")
    assert list_de_bouts(db,event["id"])[0]["id"] == bout["id"]
    db.set_event_locked(event["id"],True)
    for action in (
        lambda:cancel_de_bout(db,event["id"],bout["id"],actor="Alex"),
        lambda:resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex"),
    ):
        with pytest.raises(EventLockedError):
            action()


@pytest.mark.parametrize("change",["created","replaced","restored","opponent_updated"])
def test_individual_result_checks_pair_and_opponent_snapshot(bout_context,change):
    db,_,event,a,b,_ = bout_context
    expected_id = ""
    expected_version = None
    if change != "created":
        bout = _create(bout_context)
        expected_id,expected_version = bout["id"],bout["version"]
        if change in {"replaced","restored"}:
            cancelled = cancel_de_bout(db,event["id"],bout["id"],actor="Jordan")
            if change == "replaced":
                _create(bout_context)
            else:
                restore_de_bout_pairing(db,event["id"],bout["id"],actor="Jordan",expected_version=cancelled["version"])
        else:
            db.report_call(event["id"],b["id"],status="on_deck",location="M2",actor="Jordan")
    else:
        _create(bout_context)
    with pytest.raises(ConcurrentUpdateError):
        db.mark_result(event["id"],a["id"],outcome="won",actor="Alex",
            expected_version=a["version"],expected_bout_id=expected_id,
            expected_bout_version=expected_version,expected_opponent_version=b["version"])
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 0
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "active"


def test_pair_undo_removes_all_equal_coaches_from_available_pool(bout_context):
    db,meet,event,a,b,_ = bout_context
    coaches = ["Alex","Taylor","Morgan"]
    db.assign_de_athletes(event["id"],[a["id"],b["id"]],coaches=coaches,actor="Jordan")
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    for coach in coaches:
        db.set_coach_availability(meet["id"],coach,True,"Jordan")
    assert all(row["is_available"] for row in db.list_coach_availability(meet["id"]))
    undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan")
    assert not any(row["is_available"] for row in db.list_coach_availability(meet["id"]))


@pytest.mark.parametrize("participation",["pools","absent","withdrawn"])
def test_pairing_rejects_athletes_outside_active_de(bout_context,participation):
    db,_,event,a,b,_ = bout_context
    if participation == "pools":
        db.merge_import(event["id"],[{"athlete_id":a["athlete_key"],"name":a["name"],"phase":"pools","strip":"M1","pool":"4","pod":"M","time":""}],"Jordan")
    else:
        db.set_athlete_participation(event["id"],a["id"],participation,"Jordan")
    with pytest.raises(ConcurrentUpdateError,match="active in direct elimination"):
        create_de_bout(db,event["id"],a["id"],b["id"],actor="Alex")


def test_paired_bout_win_and_undo_preserve_prior_byes(bout_context):
    db,_,event,a,b,_ = bout_context
    for athlete in (a,b):
        db.mark_bye(event["id"],athlete["id"],actor="Jordan",expected_version=athlete["version"])
    bout = _create(bout_context)
    for athlete in (a,b):
        with pytest.raises(CompCoachError):
            db.mark_bye(event["id"],athlete["id"],actor="Alex")
    resolved = resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    winner,loser = [db.get_athlete(event["id"],athlete["id"]) for athlete in (a,b)]
    assert (winner["de_byes"],winner["de_wins"]) == (1,1)
    assert winner["de_rounds_passed"] == winner["de_byes"] + winner["de_wins"] == 2
    assert (loser["de_byes"],loser["de_wins"],loser["active_state"]) == (1,0,"eliminated")
    undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan",expected_version=resolved["version"])
    restored = [db.get_athlete(event["id"],athlete["id"]) for athlete in (a,b)]
    assert all((athlete["de_byes"],athlete["de_wins"],athlete["active_state"]) == (1,0,"active") for athlete in restored)
    assert all(athlete["de_rounds_passed"] == 1 for athlete in restored)
    assert all(athlete["last_de_result"] == "bye" for athlete in restored)


def test_new_pair_reactivates_waiting_winners_and_invalidates_old_cards(bout_context):
    db,meet,event,a,b,_ = bout_context
    db.assign_de_athletes(event["id"],[a["id"],b["id"]],coaches=["Alex"],actor="Jordan")
    for athlete in (a,b):
        current = db.get_athlete(event["id"],athlete["id"])
        db.mark_result(event["id"],athlete["id"],outcome="won",actor="Alex",expected_version=current["version"])
    before = [db.get_athlete(event["id"],athlete["id"]) for athlete in (a,b)]
    assert all(athlete["de_awaiting_next"] for athlete in before)
    db.set_coach_availability(meet["id"],"Alex",True,"Jordan")
    bout = create_de_bout(db,event["id"],a["id"],b["id"],actor="Jordan",round_label="T32",expected_a_version=before[0]["version"],expected_b_version=before[1]["version"])
    current = [db.get_athlete(event["id"],athlete["id"]) for athlete in (a,b)]
    assert all(not athlete["de_awaiting_next"] for athlete in current)
    assert [athlete["version"] for athlete in current] == [athlete["version"]+1 for athlete in before]
    assert [bout["athlete_a_version"],bout["athlete_b_version"]] == [athlete["version"] for athlete in current]
    assert not next(row for row in db.list_coach_availability(meet["id"]) if row["coach_name"] == "Alex")["is_available"]
    with pytest.raises(ConcurrentUpdateError):
        db.mark_result(event["id"],a["id"],outcome="won",actor="Alex",expected_version=before[0]["version"])
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 1


def test_pair_undo_keeps_new_assignments_and_live_information(bout_context):
    db,meet,event,a,b,_ = bout_context
    db.assign_de_athletes(event["id"],[a["id"],b["id"]],coaches=["Alex"],actor="Jordan")
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    db.assign_de_athletes(event["id"],[a["id"],b["id"]],coaches=["Taylor","Morgan"],actor="Jordan")
    db.report_call(event["id"],a["id"],status="on_deck",location="M5",actor="Jordan")
    db.request_help(event["id"],a["id"],"Morgan",location="M5")
    before = db.get_athlete(event["id"],a["id"])
    for coach in ("Taylor","Morgan"):
        db.set_coach_availability(meet["id"],coach,True,"Jordan")
    undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan")
    after = db.get_athlete(event["id"],a["id"])
    assert after["de_coaches"] == ["Taylor","Morgan"]
    assert after["live_location"] == before["live_location"] == "M5"
    assert after["help_requested_at"] == before["help_requested_at"]
    assert after["de_wins"] == 0 and after["de_awaiting_next"] == 0
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "active"
    assert not any(row["is_available"] for row in db.list_coach_availability(meet["id"]) if row["coach_name"] in {"Taylor","Morgan"})


def test_pair_undo_cannot_erase_a_newer_round_result(bout_context):
    db,_,event,a,b,_ = bout_context
    bout = _create(bout_context)
    resolve_de_bout(db,event["id"],bout["id"],winner_id=a["id"],actor="Alex")
    db.report_call(event["id"],a["id"],status="now",location="M3",actor="Jordan")
    current = db.get_athlete(event["id"],a["id"])
    db.mark_result(event["id"],a["id"],outcome="won",actor="Alex",expected_version=current["version"])
    with pytest.raises(ConcurrentUpdateError,match="newer DE result"):
        undo_de_bout_result(db,event["id"],bout["id"],actor="Jordan")
    assert db.get_athlete(event["id"],a["id"])["de_wins"] == 2
    assert db.get_athlete(event["id"],b["id"])["active_state"] == "eliminated"


def test_older_pair_can_be_corrected_after_newest_round_undone_and_pair_removed(bout_context):
    db,_,event,a,b,c = bout_context
    first = _create(bout_context)
    resolve_de_bout(db,event["id"],first["id"],winner_id=a["id"],actor="Alex")
    second = create_de_bout(db,event["id"],a["id"],c["id"],actor="Jordan",round_label="T32")
    resolve_de_bout(db,event["id"],second["id"],winner_id=a["id"],actor="Alex")
    undo_de_bout_result(db,event["id"],second["id"],actor="Jordan")
    cancel_de_bout(db,event["id"],second["id"],actor="Jordan")
    undo_de_bout_result(db,event["id"],first["id"],actor="Jordan")
    assert all(athlete["active_state"] == "active" and athlete["de_wins"] == 0 for athlete in db.list_athletes(event["id"]))
