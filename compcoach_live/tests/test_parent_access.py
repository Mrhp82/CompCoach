"""Shared parent capability is scoped to field updates on today's athletes."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from compcoach_live.parent_access import (
    ensure_parent_schema, get_or_create_parent_access, get_parent_context,
    parent_context_revision, report_parent_call,
)
from compcoach_live.storage import CompCoachDB, CompCoachError, ConcurrentUpdateError, EventLockedError


def record(key="one", phase="de", name="STONE Avery"):
    return {"athlete_id": key, "name": name, "phase": phase, "pod": "B", "strip": "B1", "pool": "1" if phase == "pools" else ""}


@pytest.fixture
def family(tmp_path):
    db = CompCoachDB(tmp_path / "parents.db")
    competition = db.create_competition("Example NAC", start_date="2026-10-10", end_date="2026-10-11")
    day = db.create_competition_day(competition["id"], "Saturday", "2026-10-10", ["Alex", "Jordan"], ["Casey"], first_event_name="Cadet ME", day_status="active")
    event = db.list_meet_events(day["id"])[0]
    db.merge_import(event["id"], [record()], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    access = get_or_create_parent_access(db, competition["id"], "Casey")
    return db, competition, day, event, athlete, access


def send(family, *, athlete=None, token=None, **kwargs):
    db, competition, day, event, original, access = family
    athlete = athlete or original
    arguments = {"status": "on_deck", "location": "C3", "expected_version": athlete["version"], "expected_day_id": day["id"], "expected_phase": athlete["phase"]}
    arguments.update(kwargs)
    return report_parent_call(db, competition["id"], access["token"] if token is None else token, event["id"], athlete["id"], **arguments)


def context(family, token=None):
    db, competition, _, _, _, access = family
    return get_parent_context(db, competition["id"], access["token"] if token is None else token)


def test_parent_schema_and_common_link_are_idempotent_and_persist(family):
    db, competition, _, _, _, access = family
    ensure_parent_schema(db)
    assert get_or_create_parent_access(db, competition["id"], "Alex") == access
    assert get_or_create_parent_access(CompCoachDB(db.path), competition["id"], "Alex") == access
    assert len(access["token"]) >= 32


def test_concurrent_share_panel_creates_only_one_link(tmp_path):
    db = CompCoachDB(tmp_path / "creation.db")
    competition = db.create_competition("NAC")
    ensure_parent_schema(db)
    with ThreadPoolExecutor(max_workers=5) as workers:
        tokens = list(workers.map(lambda _: get_or_create_parent_access(db, competition["id"], "Casey")["token"], range(5)))
    assert len(set(tokens)) == 1


@pytest.mark.parametrize("token", ["", "wrong", "☃", "x" * 300])
def test_invalid_capability_has_no_roster_or_competition_details(family, token):
    result = context(family, token)
    assert not result["authorized"]
    assert result["competition"] is None and result["day"] is None
    assert result["athletes"] == result["events"] == []
    with pytest.raises(CompCoachError, match="link"):
        send(family, token=token)


def test_parent_token_never_grants_a_coach_or_admin_role(family):
    db, _, day, event, _, access = family
    assert db.authorize(event["id"], access["token"]) is None
    assert db.authorize_meet(day["id"], access["token"]) is None
    assert not context(family, day["admin_token"])["authorized"]
    assert not context(family, day["coach_token"])["authorized"]


def test_parent_context_redacts_staff_credentials(family):
    result = context(family)
    assert result["authorized"] and result["reason"] == "ready"
    assert result["athletes"][0]["event_name"] == "Cadet ME"
    for section in [result["competition"], result["day"], *result["events"], *result["athletes"]]:
        assert not any("token" in key for key in section)
    assert parent_context_revision(result) == parent_context_revision(context(family))


def test_selected_parent_revision_ignores_other_families_call_changes(family):
    db, competition, day, event, athlete, access = family
    db.merge_import(event["id"], [record(), record("two", name="REED Nora")], "Casey")
    before = context(family)
    other = next(row for row in before["athletes"] if row["id"] != athlete["id"])
    report_parent_call(db, competition["id"], access["token"], event["id"], other["id"],
                       status="now", location="F4", expected_version=other["version"],
                       expected_day_id=day["id"], expected_phase=other["phase"])
    after = context(family)
    assert parent_context_revision(before, athlete["id"]) == parent_context_revision(after, athlete["id"])
    assert parent_context_revision(before, "") == parent_context_revision(after, "")
    assert parent_context_revision(before) != parent_context_revision(after)
    current = db.get_athlete(event["id"], athlete["id"])
    send(family, athlete=current)
    assert parent_context_revision(after, athlete["id"]) != parent_context_revision(context(family), athlete["id"])


@pytest.mark.parametrize("change", ["day_closed", "other_out", "other_absent", "selected_out"])
def test_parent_selected_revision_refreshes_day_and_roster_eligibility(family, change):
    db, _, day, event, athlete, _ = family
    db.merge_import(event["id"], [record(), record("two", name="REED Nora")], "Casey")
    before = context(family)
    other = next(row for row in before["athletes"] if row["id"] != athlete["id"])
    if change == "day_closed":
        db.finish_meet(day["id"], "Casey")
    elif change == "other_absent":
        db.set_athlete_participation(event["id"], other["id"], "absent", "Casey")
    else:
        target = athlete if change == "selected_out" else other
        db.mark_result(event["id"], target["id"], outcome="lost", actor="Alex")
    assert parent_context_revision(before, athlete["id"]) != parent_context_revision(context(family), athlete["id"])


@pytest.mark.parametrize("status", ["on_deck", "in_hole", "now"])
def test_parent_call_persists_timestamp_and_actual_strip(family, status):
    original = context(family)
    saved = send(family, status=status, location=" c3 ")
    assert saved["call_status"] == status and saved["live_location"] == "C3"
    assert saved["reported_by"] == "Parent" and saved["reported_at"]
    assert saved["source_strip"] == "B1" and saved["pod"] == "B"
    assert parent_context_revision(context(family)) != parent_context_revision(original)
    db, _, _, event, _, _ = family
    action = db.recent_actions(event["id"])[0]
    assert action["action"] == "parent_call" and action["actor"] == "Parent"


def test_unknown_de_strip_is_not_replaced_by_imported_calling_strip(family):
    saved = send(family, location="")
    assert saved["live_location"] == "" and saved["source_strip"] == "B1"
    assert saved["call_status"] == "on_deck"


def test_parent_update_preserves_staff_state_exactly(family):
    db, _, day, event, athlete, _ = family
    db.assign_de_athletes(event["id"], [athlete["id"]], coaches=["Alex"], actor="Casey")
    db.set_coach_availability(day["id"], "Alex", True, "Alex")
    current = db.get_athlete(event["id"], athlete["id"])
    before = db.list_coach_availability(day["id"])
    saved = send(family, athlete=current)
    assert db.list_coach_availability(day["id"]) == before
    protected = ("main_coach", "side_coach", "de_coaches", "active_state", "covered_by", "covered_at", "takeover_coach", "takeover_at", "de_wins", "de_byes", "help_requested_at", "pool_result_at")
    assert {key: saved.get(key) for key in protected} == {key: current.get(key) for key in protected}


def test_parent_update_preserves_physical_coverage_and_help(family):
    db, _, day, event, athlete, _ = family
    db.request_help(event["id"], athlete["id"], "Alex")
    db.cover_athlete(event["id"], athlete["id"], "Alex", "Alex", location="D4")
    current = db.get_athlete(event["id"], athlete["id"])
    before = db.list_coach_availability(day["id"])
    saved = send(family, athlete=current)
    assert saved["covered_by"] == current["covered_by"] == "Alex"
    assert saved["covered_at"] == current["covered_at"]
    assert saved["help_requested_at"] == current["help_requested_at"]
    assert saved["help_location"] == current["help_location"]
    # The shared busy strip is derived from the newly reported actual strip;
    # all coach availability/reservation state itself remains unchanged.
    after = db.list_coach_availability(day["id"])
    assert [{k: v for k, v in row.items() if k != "busy_location"} for row in after] == [
        {k: v for k, v in row.items() if k != "busy_location"} for row in before
    ]


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_old_parent_page_cannot_restore_an_old_call_after_result(family, outcome):
    db, _, _, event, athlete, _ = family
    result = db.mark_result(event["id"], athlete["id"], outcome=outcome, actor="Alex")
    with pytest.raises(ConcurrentUpdateError, match="changed"):
        send(family)
    assert db.get_athlete(event["id"], athlete["id"]) == result


def test_two_parent_taps_from_same_revision_have_one_winner(family):
    def write(status):
        try:
            send(family, status=status)
            return "saved"
        except ConcurrentUpdateError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(write, ["now", "on_deck"])) == ["saved", "stale"]


def test_completed_pool_and_nonparticipating_athletes_are_hidden_and_blocked(family):
    db, _, _, event, athlete, _ = family
    db.merge_import(event["id"], [record(phase="pools")], "Casey")
    current = db.get_athlete(event["id"], athlete["id"])
    completed = db.set_pool_done_unknown(event["id"], athlete["id"], "Alex")
    assert context(family)["athletes"] == []
    with pytest.raises(ConcurrentUpdateError):
        send(family, athlete=current)
    with pytest.raises(ConcurrentUpdateError, match="no longer"):
        send(family, athlete=completed)


@pytest.mark.parametrize("status", ["absent", "withdrawn"])
def test_attendance_exclusions_are_not_writable(family, status):
    db, _, _, event, athlete, _ = family
    current = db.set_athlete_participation(event["id"], athlete["id"], status, "Alex")
    assert context(family)["athletes"] == []
    with pytest.raises(ConcurrentUpdateError, match="no longer"):
        send(family, athlete=current)


def test_parent_link_follows_new_active_day_and_old_actions_are_blocked(family):
    db, competition, day, event, athlete, access = family
    db.finish_meet(day["id"], "Casey")
    assert context(family)["reason"] == "no_active_day"
    with pytest.raises(ConcurrentUpdateError, match="day"):
        send(family)
    next_day = db.create_competition_day(competition["id"], "Sunday", "2026-10-11", ["Alex"], ["Casey"], day_status="active", first_event_name="Junior ME")
    next_event = db.list_meet_events(next_day["id"])[0]
    db.merge_import(next_event["id"], [record("new", name="PARK Eli")], "Casey")
    current = context(family)
    assert current["day"]["id"] == next_day["id"]
    assert [row["name"] for row in current["athletes"]] == ["PARK Eli"]
    assert get_or_create_parent_access(db, competition["id"], "Casey") == access
    with pytest.raises(ConcurrentUpdateError, match="day"):
        send(family)
    assert db.get_athlete(event["id"], athlete["id"])["version"] == athlete["version"]


def test_scheduled_day_does_not_appear_and_different_competition_is_denied(family):
    db, competition, _, _, athlete, access = family
    scheduled = db.create_competition_day(competition["id"], "Sunday", "2026-10-11", ["Alex"], ["Casey"], first_event_name="Junior ME")
    scheduled_event = db.list_meet_events(scheduled["id"])[0]
    db.merge_import(scheduled_event["id"], [record("scheduled", name="NEXT Athlete")], "Casey")
    assert [row["id"] for row in context(family)["athletes"]] == [athlete["id"]]
    other = db.create_competition("Other event")
    get_or_create_parent_access(db, other["id"], "Casey")
    assert not get_parent_context(db, other["id"], access["token"])["authorized"]


def test_closed_competition_keeps_readonly_parent_link(family):
    db, competition, _, _, _, _ = family
    db.finish_competition(competition["id"], "Casey")
    assert context(family)["reason"] == "competition_closed"
    with pytest.raises(EventLockedError):
        send(family)


def test_closed_pool_phase_blocks_even_a_legacy_uncompleted_record(family):
    db, _, _, event, athlete, _ = family
    db.merge_import(event["id"], [record(phase="pools")], "Casey")
    db.set_phase_started(event["id"], "pools", True, "Casey")
    current = db.get_athlete(event["id"], athlete["id"])
    # Simulate an old import that retained incomplete totals under a closed
    # phase: the phase guard must not rely solely on per-athlete completion.
    with db._connection() as conn:
        conn.execute("UPDATE phase_states SET closed_at = ? WHERE event_id = ? AND phase = 'pools'", ("2026-10-10T20:00:00+00:00", event["id"]))
    assert context(family)["athletes"] == []
    with pytest.raises(ConcurrentUpdateError, match="phase"):
        send(family, athlete=current)


@pytest.mark.parametrize("field,value", [("status", "won"), ("status", "waiting"), ("expected_version", None), ("expected_phase", "invalid"), ("location", "X" * 17)])
def test_parent_writer_rejects_invalid_actions(family, field, value):
    with pytest.raises(ValueError):
        send(family, **{field: value})
