"""Regression tests for the v0.6 storage model.

These tests deliberately exercise the storage contract without depending on
Streamlit.  The older ``test_storage`` module continues to cover the legacy
single-event and multi-event behavior.
"""

from __future__ import annotations

import sqlite3

import pytest

from compcoach_live.storage import (
    CompCoachDB,
    CompCoachError,
    ConcurrentUpdateError,
)
from compcoach_live.tests.test_storage import (
    add_pre_end_day_meet_schema,
    make_legacy_v021_database,
)


def pool_record(
    athlete_id: str,
    name: str,
    *,
    time: str = "",
    pool: str = "1",
    strip: str = "B1",
    pod: str = "B",
) -> dict[str, str]:
    return {
        "athlete_id": athlete_id,
        "name": name,
        "strip": strip,
        "time": time,
        "pool": pool,
        "pod": pod,
        "phase": "pools",
    }


def make_two_event_day(tmp_path):
    db = CompCoachDB(tmp_path / "v06.db")
    competition = db.create_competition(
        "October NAC",
        location="Salt Palace, Salt Lake City",
        start_date="2026-10-09",
        end_date="2026-10-12",
        logo_path="assets/october-nac.png",
        strip_map_path="assets/october-nac-map.png",
    )
    meet = db.create_competition_day(
        competition["id"],
        "October NAC — Friday",
        "2026-10-09",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Men's Epee",
        day_status="active",
    )
    first = db.list_meet_events(meet["id"])[0]
    second = db.add_meet_event(meet["id"], "Junior Women's Epee")
    return db, competition, meet, first, second


def test_parent_competition_metadata_days_and_single_active_day(tmp_path):
    db = CompCoachDB(tmp_path / "competition.db")
    competition = db.create_competition(
        "October NAC",
        location="Salt Palace",
        start_date="2026-10-09",
        end_date="2026-10-12",
        timezone_name="America/Denver",
        logo_path="assets/logo-v1.png",
        strip_map_path="assets/map-v1.png",
    )

    assert {
        "name": competition["name"],
        "location": competition["location"],
        "start_date": competition["start_date"],
        "end_date": competition["end_date"],
        "timezone": competition["timezone"],
        "logo_path": competition["logo_path"],
        "strip_map_path": competition["strip_map_path"],
        "day_count": competition["day_count"],
    } == {
        "name": "October NAC",
        "location": "Salt Palace",
        "start_date": "2026-10-09",
        "end_date": "2026-10-12",
        "timezone": "America/Denver",
        "logo_path": "assets/logo-v1.png",
        "strip_map_path": "assets/map-v1.png",
        "day_count": 0,
    }

    day_one = db.create_competition_day(
        competition["id"],
        "Friday",
        "2026-10-09",
        ["Carmine"],
        first_event_name="Cadet Men's Epee",
        day_status="active",
    )
    day_two = db.create_competition_day(
        competition["id"],
        "Saturday",
        "2026-10-10",
        ["Carmine", "Sam"],
        first_event_name=None,
        day_status="scheduled",
    )

    with pytest.raises(CompCoachError, match="already has an active day"):
        db.create_competition_day(
            competition["id"],
            "Conflicting active day",
            "2026-10-11",
            ["Sam"],
            day_status="active",
        )

    activated = db.set_day_status(day_two["id"], "active", "Irina")
    assert activated["day_status"] == "active"
    assert db.get_meet(day_one["id"])["day_status"] == "scheduled"
    assert db.get_active_competition_day(competition["id"])["id"] == day_two["id"]
    assert [day["competition_date"] for day in db.list_competition_days(competition["id"])] == [
        "2026-10-09",
        "2026-10-10",
    ]
    assert sum(
        day["day_status"] == "active"
        for day in db.list_competition_days(competition["id"])
    ) == 1

    updated = db.update_competition(
        competition["id"],
        name="October NAC 2026",
        location="Salt Palace Convention Center",
        start_date="2026-10-09",
        end_date="2026-10-12",
        timezone_name="America/Denver",
        logo_path="assets/logo-final.png",
        strip_map_path="assets/map-final.png",
    )
    assert updated["location"] == "Salt Palace Convention Center"
    assert updated["start_date"] == "2026-10-09"
    assert updated["end_date"] == "2026-10-12"
    assert updated["logo_path"] == "assets/logo-final.png"
    assert updated["strip_map_path"] == "assets/map-final.png"
    assert updated["day_count"] == 2
    assert all(
        day["timezone"] == "America/Denver"
        for day in db.list_competition_days(competition["id"])
    )

    closed = db.set_day_status(day_two["id"], "closed", "Irina")
    assert closed["day_status"] == "closed"
    assert closed["status"] == "locked"
    assert db.get_active_competition_day(competition["id"]) is None


def test_coach_directory_roles_presence_and_home_event_is_not_a_gate(tmp_path):
    db, competition, meet, first, second = make_two_event_day(tmp_path)
    coaches = {row["name"]: row for row in db.list_coaches()}
    assert set(coaches) == {"Carmine", "Irina", "Sam"}
    assert db.create_coach("  CARMINE  ")["id"] == coaches["Carmine"]["id"]

    sam_roles = db.set_competition_coach_roles(
        competition["id"], coaches["Sam"]["id"], ["coach", "admin"]
    )
    assert set(sam_roles["roles"]) == {"coach", "admin"}
    irina = next(
        row
        for row in db.list_competition_coaches(competition["id"])
        if row["name"] == "Irina"
    )
    assert set(irina["roles"]) == {"coach", "coordinator"}

    carmine_day = db.set_day_coach_presence(
        meet["id"],
        coaches["Carmine"]["id"],
        actor="Irina",
        presence_status="present",
        home_event_id=first["id"],
    )
    sam_day = db.set_day_coach_presence(
        meet["id"],
        coaches["Sam"]["id"],
        actor="Irina",
        presence_status="scheduled",
        home_event_id=second["id"],
    )
    assert carmine_day["home_event_name"] == "Cadet Men's Epee"
    assert carmine_day["is_present"] is True
    assert sam_day["presence_status"] == "scheduled"
    assert sam_day["is_present"] is False
    assert "Sam" not in {row["name"] for row in db.list_day_coaches(meet["id"], present_only=True)}

    # A home event is a planning hint, never a hard assignment boundary.
    assert db.assign_pod(
        second["id"],
        phase="pools",
        pod="B",
        main_coach="Carmine",
        side_coach="",
        actor="Irina",
    ) == 0

    history = db.list_assignment_history(event_id=second["id"])
    assert len(history) == 1
    assert history[0]["target_type"] == "pod"
    assert history[0]["home_event_id"] == first["id"]
    assert history[0]["is_cross_event"] is True

    day_coaches = db.list_day_coaches(meet["id"])
    carmine_after = next(row for row in day_coaches if row["name"] == "Carmine")
    assert carmine_after["is_used"] is True
    assert carmine_after["assignment_count"] == 1
    assert day_coaches[-1]["name"] == "Carmine"
    assert all(row["is_used"] is False for row in day_coaches[:-1])


@pytest.mark.parametrize("status", ["absent", "withdrawn"])
def test_participation_clears_live_state_and_immediate_restore_is_reversible(
    tmp_path, status
):
    db = CompCoachDB(tmp_path / f"participation-{status}.db")
    event = db.create_event("Cadet Men's Epee", ["Carmine", "Sam"], ["Irina"])
    db.merge_import(
        event["id"],
        [pool_record("ath_max", "DING Max", time="9:00 AM")],
        "Import",
    )
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Carmine", actor="Irina"
    )
    athlete = db.set_pool_result(
        event["id"], athlete["id"], wins=4, losses=2, actor="Carmine"
    )
    athlete = db.report_call(
        event["id"],
        athlete["id"],
        status="now",
        location="B7",
        actor="Irina",
        covered_by="Sam",
    )
    athlete = db.request_help(
        event["id"], athlete["id"], "Carmine", location="B7"
    )
    before = db.acknowledge_help(event["id"], athlete["id"], "Irina")

    if status == "absent":
        changed = db.mark_absent(
            event["id"], athlete["id"], "Irina", expected_version=before["version"]
        )
    else:
        changed = db.mark_withdrawn(
            event["id"], athlete["id"], "Irina", expected_version=before["version"]
        )

    assert changed["participation_status"] == status
    assert changed["active_state"] == "active"
    assert changed["call_status"] == "waiting"
    for field in (
        "live_location",
        "reported_by",
        "covered_by",
        "help_requested_by",
        "help_location",
        "help_acknowledged_by",
    ):
        assert changed[field] == ""
    for field in (
        "reported_at",
        "covered_at",
        "help_requested_at",
        "help_acknowledged_at",
    ):
        assert changed[field] is None
    assert changed["main_coach"] == "Carmine"
    assert (changed["pool_wins"], changed["pool_losses"]) == (4, 2)

    restored = db.restore_athlete_participation(
        event["id"],
        athlete["id"],
        "Irina",
        expected_version=changed["version"],
    )
    assert restored["participation_status"] == "active"
    assert restored["call_status"] == before["call_status"]
    assert restored["live_location"] == before["live_location"]
    assert restored["covered_by"] == before["covered_by"]
    assert restored["help_requested_by"] == before["help_requested_by"]
    assert restored["help_acknowledged_by"] == before["help_acknowledged_by"]


def test_reimport_preserves_absent_or_withdrawn_status(tmp_path):
    db = CompCoachDB(tmp_path / "reimport-status.db")
    event = db.create_event("Cadet Men's Epee", ["Carmine"], ["Irina"])
    original = pool_record("ath_max", "DING Max", time="9:00 AM", strip="B1")
    db.merge_import(event["id"], [original], "Import")
    athlete = db.list_athletes(event["id"])[0]

    absent = db.mark_absent(event["id"], athlete["id"], "Irina")
    changed_import = {**original, "strip": "B2", "time": "9:15 AM"}
    assert db.merge_import(event["id"], [changed_import], "Reimport")["updated"] == 1
    refreshed = db.get_athlete(event["id"], athlete["id"])
    assert refreshed["participation_status"] == "absent"
    assert refreshed["source_strip"] == "B2"
    assert refreshed["time_text"] == "9:15 AM"
    assert refreshed["version"] > absent["version"]

    # Reactivate before checking the second non-active state.  A newer import
    # correctly makes the old one-click restore snapshot stale.
    with pytest.raises(ConcurrentUpdateError, match="newer update exists"):
        db.restore_athlete_participation(event["id"], athlete["id"], "Irina")


def test_assignment_history_uses_intervals_and_records_cross_event_work(tmp_path):
    db, _competition, meet, first, second = make_two_event_day(tmp_path)
    coaches = {row["name"]: row for row in db.list_coaches()}
    db.set_day_coach_presence(
        meet["id"],
        coaches["Carmine"]["id"],
        actor="Irina",
        presence_status="present",
        home_event_id=first["id"],
    )
    db.set_day_coach_presence(
        meet["id"],
        coaches["Sam"]["id"],
        actor="Irina",
        presence_status="present",
        home_event_id=second["id"],
    )
    db.merge_import(
        second["id"],
        [pool_record("ath_zoe", "DOE Zoe", time="11:00 AM")],
        "Import",
    )
    athlete = db.list_athletes(second["id"])[0]

    db.assign_athletes(
        second["id"], [athlete["id"]], main_coach="Carmine", actor="Irina"
    )
    db.assign_athletes(
        second["id"], [athlete["id"]], main_coach="Sam", actor="Irina"
    )
    main_intervals = [
        row
        for row in db.list_assignment_history(athlete_id=athlete["id"])
        if row["assignment_kind"] == "main"
    ]
    assert [row["coach_name"] for row in main_intervals] == ["Carmine", "Sam"]
    assert main_intervals[0]["ended_at"] is not None
    assert main_intervals[0]["ended_by"] == "Irina"
    assert main_intervals[0]["home_event_id"] == first["id"]
    assert main_intervals[0]["is_cross_event"] is True
    assert main_intervals[1]["ended_at"] is None
    assert main_intervals[1]["home_event_id"] == second["id"]
    assert main_intervals[1]["is_cross_event"] is False

    db.assign_athletes(
        second["id"], [athlete["id"]], main_coach="", actor="Irina"
    )
    assert db.list_assignment_history(
        athlete_id=athlete["id"], include_closed=False
    ) == []
    assert all(
        row["ended_at"] is not None
        for row in db.list_assignment_history(athlete_id=athlete["id"])
        if row["assignment_kind"] == "main"
    )

    called = db.report_call(
        second["id"],
        athlete["id"],
        status="on_deck",
        location="C4",
        actor="Irina",
        covered_by="Carmine",
    )
    db.release(
        second["id"], athlete["id"], "Carmine", expected_version=called["version"]
    )
    coverage = [
        row
        for row in db.list_assignment_history(athlete_id=athlete["id"])
        if row["assignment_kind"] == "coverage"
    ]
    assert len(coverage) == 1
    assert coverage[0]["is_cross_event"] is True
    assert coverage[0]["ended_at"] is not None

    db.assign_athletes(
        second["id"], [athlete["id"]], side_coach="Sam", actor="Irina"
    )
    assert len(db.list_assignment_history(meet_id=meet["id"], include_closed=False)) == 1
    db.finish_meet(meet["id"], "Irina")
    assert db.list_assignment_history(meet_id=meet["id"], include_closed=False) == []
    latest_side = [
        row
        for row in db.list_assignment_history(athlete_id=athlete["id"])
        if row["assignment_kind"] == "side"
    ][-1]
    assert latest_side["ended_by"] == "Irina"


def test_pool_waves_sort_by_time_hide_later_waves_and_use_cas(tmp_path):
    db = CompCoachDB(tmp_path / "waves.db")
    event = db.create_event("Cadet Men's Epee", ["Carmine"], ["Irina"])
    records = [
        pool_record("ath_late", "LATE Lee", time="1:00 PM", pool="3", strip="B10"),
        pool_record("ath_early", "EARLY Erin", time="9:00 AM", pool="1", strip="B1"),
        pool_record("ath_mid", "MID Max", time="11:15 AM", pool="2", strip="B2"),
    ]
    assert db.merge_import(event["id"], records, "Import")["added"] == 3

    waves = db.list_pool_waves(event["id"])
    assert [wave["label"] for wave in waves] == ["9:00 AM", "11:15 AM", "1:00 PM"]
    assert [wave["sort_order"] for wave in waves] == [0, 1, 2]
    assert [wave["is_active"] for wave in waves] == [True, False, False]
    assert [wave["is_visible"] for wave in waves] == [True, False, False]
    assert waves[0]["activated_by"] == "Import"

    stale_versions = {wave["wave_key"]: wave["version"] for wave in waves}
    second = db.activate_pool_wave(
        event["id"],
        waves[1]["wave_key"],
        "Irina",
        expected_version=stale_versions[waves[1]["wave_key"]],
    )
    assert second["is_active"] is True
    assert second["is_visible"] is True
    switched = db.list_pool_waves(event["id"])
    assert [wave["is_active"] for wave in switched] == [False, True, False]
    assert [wave["is_visible"] for wave in switched] == [False, True, False]

    with pytest.raises(ConcurrentUpdateError, match="changed on another phone"):
        db.activate_pool_wave(
            event["id"],
            waves[2]["wave_key"],
            "Carmine",
            expected_version=stale_versions[waves[2]["wave_key"]],
        )

    current_third = db.list_pool_waves(event["id"])[2]
    db.activate_pool_wave(
        event["id"],
        current_third["wave_key"],
        "Carmine",
        expected_version=current_third["version"],
    )
    final = db.list_pool_waves(event["id"])
    assert [wave["is_active"] for wave in final] == [False, False, True]
    assert [wave["is_visible"] for wave in final] == [False, False, True]


def test_pool_import_without_times_remains_backward_compatible(tmp_path):
    db = CompCoachDB(tmp_path / "no-times.db")
    event = db.create_event("Y12 Men's Epee", ["Carmine"], ["Irina"])
    records = [
        pool_record("ath_one", "ONE Alex", time="", pool="1", strip="A1"),
        pool_record("ath_two", "TWO Bea", time="   ", pool="2", strip="A2"),
    ]

    assert db.merge_import(event["id"], records, "Legacy import") == {
        "added": 2,
        "updated": 0,
        "unchanged": 0,
        "moved_out": 0,
    }
    assert db.list_pool_waves(event["id"]) == []
    assert {row["athlete_key"] for row in db.list_athletes(event["id"])} == {
        "ath_one",
        "ath_two",
    }
    assert all(row["time_text"] == "" for row in db.list_athletes(event["id"]))


def test_v06_migration_and_reopen_are_idempotent_and_keep_foreign_keys_valid(
    tmp_path,
):
    path = tmp_path / "legacy-v021.db"
    make_legacy_v021_database(path)
    add_pre_end_day_meet_schema(path)

    first_open = CompCoachDB(path)
    meet = first_open.get_meet("legacy_meet")
    assert meet["competition_id"]
    assert meet["day_status"] == "active"
    assert first_open.get_competition(meet["competition_id"])["day_count"] == 1
    assert first_open.list_athletes("legacy_event")[0]["participation_status"] == "active"
    assert {row["name"] for row in first_open.list_coaches()} == {
        "Carmine",
        "Irina",
        "Sam",
    }
    assert len(first_open.list_day_coaches("legacy_meet")) == 3
    assert len(first_open.list_assignment_history(meet_id="legacy_meet")) == 4

    with sqlite3.connect(path) as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "competitions",
                "coaches",
                "competition_coaches",
                "day_coach_presence",
                "coach_assignment_history",
                "pool_waves",
            )
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    second_open = CompCoachDB(path)
    third_open = CompCoachDB(path)
    assert second_open.get_meet_for_event("legacy_event")["id"] == "legacy_meet"
    assert third_open.get_meet_for_event("legacy_event")["id"] == "legacy_meet"

    with sqlite3.connect(path) as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert after == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {
            row[1] for row in conn.execute("PRAGMA table_info(meets)").fetchall()
        } >= {"competition_id", "competition_date", "day_status"}
        assert {
            row[1] for row in conn.execute("PRAGMA table_info(athletes)").fetchall()
        } >= {"participation_status"}

