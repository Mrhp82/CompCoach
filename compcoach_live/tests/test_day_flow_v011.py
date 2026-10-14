"""Preparation stays inside a competition and waits for an explicit start."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError


def days(tmp_path):
    db = CompCoachDB(tmp_path / "days.db")
    parent = db.create_competition("Tournament", start_date="2026-10-09", end_date="2026-10-11")
    today = db.create_competition_day(
        parent["id"], "Friday", "2026-10-09", ["Alex"], ["Casey"],
        first_event_name="Cadet Epee", day_status="active",
    )
    return db, parent, today


def prepare(db, today, **kwargs):
    return db.prepare_next_day(
        today["id"], "Saturday", "2026-10-10", ["Junior Epee"], "Casey",
        activate=False, **kwargs,
    )


def test_prepare_closes_only_today_and_keeps_same_competition_neutral(tmp_path):
    db, parent, today = days(tmp_path)
    tomorrow = prepare(db, today)
    assert db.get_meet(today["id"])["day_status"] == "closed"
    assert tomorrow["competition_id"] == parent["id"]
    assert tomorrow["day_status"] == "scheduled"
    assert tomorrow["ended_at"] is None
    assert db.get_competition(parent["id"])["status"] == "open"
    assert db.get_active_competition_day(parent["id"]) is None
    assert [e["name"] for e in db.list_meet_events(tomorrow["id"])] == ["Junior Epee"]
    started = db.set_day_status(tomorrow["id"], "active", "Casey", require_closed_previous=True)
    assert started["id"] == tomorrow["id"]
    assert db.get_active_competition_day(parent["id"])["id"] == tomorrow["id"]


def test_preparation_reuses_existing_date_preserving_staff_imports_and_assignments(tmp_path):
    db, parent, today = days(tmp_path)
    planned = db.create_competition_day(
        parent["id"], "Already planned Saturday", "2026-10-10", ["Jordan"], ["Taylor"],
        first_event_name="Women's Epee", day_status="scheduled",
    )
    event = db.list_meet_events(planned["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "one", "name": "STONE Avery",
                                  "phase": "pools", "strip": "C3", "pool": "2"}], "Taylor")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Jordan", actor="Taylor")
    before = db.get_athlete(event["id"], athlete["id"])
    tokens = {role: planned[f"{role}_token"] for role in ("admin", "coordinator", "coach")}
    tomorrow = prepare(db, today)
    assert tomorrow["id"] == planned["id"]
    assert tomorrow["name"] == "Already planned Saturday"
    assert tomorrow["active_coaches"] == ["Jordan"]
    assert tomorrow["coordinators"] == ["Taylor"]
    assert tomorrow["prepared_from_meet_id"] == today["id"]
    assert db.get_athlete(event["id"], athlete["id"]) == before
    assert {role: tomorrow[f"{role}_token"] for role in tokens} == tokens
    assert len(db.list_competition_days(parent["id"])) == 2
    assert db.get_prepared_successor(today["id"])["id"] == planned["id"]


def test_repeated_and_parallel_prepare_create_one_scheduled_day(tmp_path):
    db, parent, today = days(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = list(pool.map(lambda _: prepare(db, today), range(2)))
    assert prepared[0]["id"] == prepared[1]["id"]
    assert prepare(db, today)["id"] == prepared[0]["id"]
    assert len(db.list_competition_days(parent["id"])) == 2
    assert db.get_active_competition_day(parent["id"]) is None


def test_strict_start_rejects_another_active_day_without_demoting_it(tmp_path):
    db, parent, today = days(tmp_path)
    tomorrow = db.create_competition_day(parent["id"], "Saturday", "2026-10-10", [], day_status="scheduled")
    with pytest.raises(CompCoachError, match="Close the current"):
        db.set_day_status(tomorrow["id"], "active", "Casey", require_closed_previous=True)
    assert db.get_meet(today["id"])["day_status"] == "active"
    assert db.get_meet(tomorrow["id"])["day_status"] == "scheduled"


def test_prepare_cannot_accidentally_close_a_separately_active_day(tmp_path):
    db, parent, today = days(tmp_path)
    db.finish_meet(today["id"], "Casey")
    active = db.create_competition_day(parent["id"], "Sunday", "2026-10-11", [], day_status="active")
    with pytest.raises(CompCoachError, match="Close the current"):
        db.prepare_next_day(today["id"], "Saturday", "2026-10-10", [], "Casey")
    tomorrow = prepare(db, today)
    assert tomorrow["day_status"] == "scheduled"
    assert db.get_meet(active["id"])["day_status"] == "active"
    assert db.get_meet(active["id"])["ended_at"] is None


@pytest.mark.parametrize("day", ["2026-10-08", "2026-10-09"])
def test_prepare_requires_a_later_date_and_does_not_close_source_on_error(tmp_path, day):
    db, _, today = days(tmp_path)
    with pytest.raises(ValueError, match="after the current"):
        db.prepare_next_day(today["id"], "Earlier", day, [], "Casey", activate=False)
    assert db.get_meet(today["id"])["day_status"] == "active"


def test_closed_scheduled_date_is_not_recreated_or_overwritten(tmp_path):
    db, parent, today = days(tmp_path)
    tomorrow = db.create_competition_day(parent["id"], "Saturday", "2026-10-10", [], day_status="scheduled")
    db.finish_meet(tomorrow["id"], "Taylor")
    with pytest.raises(CompCoachError, match="already closed"):
        prepare(db, today)
    assert db.get_meet(today["id"])["day_status"] == "active"
    assert db.get_meet(tomorrow["id"])["ended_by"] == "Taylor"


def test_existing_earlier_scheduled_day_is_not_mistaken_for_next_day(tmp_path):
    db, parent, today = days(tmp_path)
    previous = db.create_competition_day(parent["id"], "Earlier", "2026-10-08", [], day_status="scheduled")
    with pytest.raises(ValueError, match="after the current"):
        db.prepare_next_day(today["id"], "Earlier", "2026-10-08", [], "Casey", activate=False)
    assert db.get_meet(today["id"])["day_status"] == "active"
    assert db.get_meet(previous["id"])["day_status"] == "scheduled"


def test_legacy_prepare_default_still_activates_successor(tmp_path):
    db, parent, today = days(tmp_path)
    tomorrow = db.prepare_next_day(today["id"], "Saturday", "2026-10-10", [], "Casey")
    assert tomorrow["day_status"] == "active"
    assert db.get_active_competition_day(parent["id"])["id"] == tomorrow["id"]
