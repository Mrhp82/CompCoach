"""Focused regressions for durable coach identity and staff projections."""

from __future__ import annotations

import json
import sqlite3

import pytest

from compcoach_live.storage import CompCoachDB, CompCoachError


def pool_record(athlete_id: str, name: str) -> dict[str, str]:
    return {
        "athlete_id": athlete_id,
        "name": name,
        "strip": "C1",
        "time": "9:00 AM",
        "pool": "1",
        "pod": "C",
        "phase": "pools",
    }


def test_coach_rename_preserves_identity_and_updates_only_current_references(
    tmp_path,
):
    db = CompCoachDB(tmp_path / "rename.db")
    meet = db.create_meet(
        "October NAC",
        ["Alex", "Jordan"],
        ["Alex"],
        first_event_name="Cadet Epee",
    )
    event = db.list_meet_events(meet["id"])[0]
    alex = next(row for row in db.list_coaches() if row["name"] == "Alex")
    db.merge_import(
        event["id"],
        [
            pool_record("ath_main", "MAIN Mia"),
            pool_record("ath_side", "SIDE Sid"),
            pool_record("ath_pod", "POD Pia"),
            pool_record("ath_closed", "CLOSED Cole"),
        ],
        "Import",
    )
    athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}

    db.assign_athletes(
        event["id"], [athletes["ath_main"]["id"]], main_coach="Alex", actor="Irina"
    )
    db.assign_athletes(
        event["id"], [athletes["ath_side"]["id"]], side_coach="Alex", actor="Irina"
    )
    db.assign_athletes(
        event["id"],
        [athletes["ath_closed"]["id"]],
        main_coach="Alex",
        actor="Irina",
    )
    db.assign_athletes(
        event["id"],
        [athletes["ath_closed"]["id"]],
        main_coach="Jordan",
        actor="Irina",
    )
    db.assign_pod(
        event["id"],
        phase="pools",
        pod="C",
        main_coach="Alex",
        side_coach="",
        actor="Irina",
    )

    db.report_call(
        event["id"],
        athletes["ath_main"]["id"],
        status="on_deck",
        location="C1",
        actor="Alex",
        covered_by="Alex",
    )
    db.request_help(event["id"], athletes["ath_main"]["id"], "Alex")
    db.acknowledge_help(event["id"], athletes["ath_main"]["id"], "Jordan")

    db.report_call(
        event["id"],
        athletes["ath_side"]["id"],
        status="in_hole",
        location="C2",
        actor="Alex",
    )
    db.request_help(event["id"], athletes["ath_side"]["id"], "Jordan")
    db.acknowledge_help(event["id"], athletes["ath_side"]["id"], "Alex")
    db.set_pool_result(
        event["id"],
        athletes["ath_pod"]["id"],
        wins=4,
        losses=2,
        actor="Alex",
    )

    availability = {
        row["coach_name"]: row for row in db.list_coach_availability(meet["id"])
    }["Alex"]
    availability = db.set_coach_availability(
        meet["id"],
        "Alex",
        True,
        "Alex",
        expected_version=availability["version"],
    )
    actions_before = db.recent_actions(event["id"], limit=200)
    versions_before = {
        row["athlete_key"]: row["version"]
        for row in db.list_athletes(event["id"])
    }

    # Simulate harmless legacy drift: duplicate spellings in JSON and a stale
    # row already using the desired availability key.  Rename must consolidate
    # these without losing the real coach's state or CAS version.
    with sqlite3.connect(db.path) as conn:
        active_json = json.dumps(["Alex", " alex ", "Jordan", "Alexander"])
        coordinator_json = json.dumps(["Alex", " ALEX "])
        conn.execute(
            """
            UPDATE meets SET active_coaches_json = ?, coordinators_json = ?
            WHERE id = ?
            """,
            (active_json, coordinator_json, meet["id"]),
        )
        conn.execute(
            """
            UPDATE events SET active_coaches_json = ?, coordinators_json = ?
            WHERE id = ?
            """,
            (active_json, coordinator_json, event["id"]),
        )
        conn.execute(
            """
            INSERT INTO coach_availability (
                meet_id, coach_name, is_available, available_since,
                updated_at, updated_by, version
            ) VALUES (?, 'Alexander', 0, NULL, '2000-01-01T00:00:00+00:00',
                      'Legacy', 0)
            """,
            (meet["id"],),
        )

    renamed = db.update_coach(alex["id"], name="Alexander")
    assert renamed["id"] == alex["id"]
    assert renamed["name"] == "Alexander"
    assert {row["name"] for row in db.list_coaches()} == {"Alexander", "Jordan"}

    refreshed_meet = db.get_meet(meet["id"])
    refreshed_event = db.get_event(event["id"])
    assert refreshed_meet["active_coaches"] == ["Alexander", "Jordan"]
    assert refreshed_meet["coordinators"] == ["Alexander"]
    assert refreshed_event["active_coaches"] == ["Alexander", "Jordan"]
    assert refreshed_event["coordinators"] == ["Alexander"]

    current = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
    assert current["ath_main"]["main_coach"] == "Alexander"
    assert current["ath_main"]["covered_by"] == "Alexander"
    assert current["ath_main"]["help_requested_by"] == "Alexander"
    assert current["ath_main"]["help_acknowledged_by"] == "Jordan"
    assert current["ath_side"]["side_coach"] == "Alexander"
    assert current["ath_side"]["help_requested_by"] == "Jordan"
    assert current["ath_side"]["help_acknowledged_by"] == "Alexander"
    assert current["ath_pod"]["main_coach"] == "Alexander"

    # Reporter/result bylines and action snapshots are historical audit data.
    assert current["ath_main"]["reported_by"] == "Alex"
    assert current["ath_side"]["reported_by"] == "Alex"
    assert current["ath_pod"]["pool_result_by"] == "Alex"
    assert db.recent_actions(event["id"], limit=200) == actions_before
    for athlete_key in ("ath_main", "ath_side", "ath_pod"):
        assert current[athlete_key]["version"] == versions_before[athlete_key] + 1
    assert current["ath_closed"]["version"] == versions_before["ath_closed"]

    pod = db.list_pod_assignments(event["id"], "pools")[0]
    assert pod["main_coach"] == "Alexander"
    history = db.list_assignment_history(coach_id=alex["id"])
    assert any(row["ended_at"] is not None and row["coach_name"] == "Alex" for row in history)
    assert all(
        row["coach_name"] == "Alexander"
        for row in history
        if row["ended_at"] is None
    )

    after_availability = {
        row["coach_name"]: row for row in db.list_coach_availability(meet["id"])
    }["Alexander"]
    for field in (
        "is_available",
        "available_since",
        "updated_at",
        "updated_by",
        "version",
    ):
        assert after_availability[field] == availability[field]
    with sqlite3.connect(db.path) as conn:
        rows = conn.execute(
            "SELECT coach_name FROM coach_availability WHERE meet_id = ?",
            (meet["id"],),
        ).fetchall()
    availability_names = [row[0] for row in rows]
    assert availability_names.count("Alexander") == 1
    assert "Alex" not in availability_names


def test_duplicate_coach_name_rejects_whole_rename_transaction(tmp_path):
    db = CompCoachDB(tmp_path / "rename-conflict.db")
    meet = db.create_meet("NAC", ["Alex", "Jordan"], first_event_name="Epee")
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [pool_record("ath_one", "ONE Olive")], "Import")
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Alex", actor="Irina"
    )
    coaches = {row["name"]: row for row in db.list_coaches()}
    before_meet = db.get_meet(meet["id"])
    before_athlete = db.get_athlete(event["id"], athlete["id"])
    before_history = db.list_assignment_history(coach_id=coaches["Alex"]["id"])

    with pytest.raises(CompCoachError, match="already exists"):
        db.update_coach(coaches["Alex"]["id"], name="  JORDAN ")

    assert db.get_coach(coaches["Alex"]["id"])["name"] == "Alex"
    assert db.get_meet(meet["id"]) == before_meet
    assert db.get_athlete(event["id"], athlete["id"]) == before_athlete
    assert db.list_assignment_history(coach_id=coaches["Alex"]["id"]) == before_history


def test_roles_and_day_presence_keep_coordinator_projections_in_sync(tmp_path):
    db = CompCoachDB(tmp_path / "coordinator-projection.db")
    meet = db.create_meet(
        "NAC", ["Alex"], ["Casey"], first_event_name="Cadet Epee"
    )
    event = db.list_meet_events(meet["id"])[0]
    alex = next(row for row in db.list_coaches() if row["name"] == "Alex")
    competition_id = meet["competition_id"]

    db.set_day_coach_presence(
        meet["id"], alex["id"], actor="Casey", presence_status="scheduled"
    )
    db.set_competition_coach_roles(
        competition_id, alex["id"], ["coach", "coordinator"]
    )
    assert "Alex" not in db.get_meet(meet["id"])["active_coaches"]
    assert "Alex" not in db.get_meet(meet["id"])["coordinators"]
    assert "Alex" not in db.get_event(event["id"])["coordinators"]

    db.set_day_coach_presence(
        meet["id"], alex["id"], actor="Casey", presence_status="present"
    )
    assert "Alex" in db.get_meet(meet["id"])["active_coaches"]
    assert "Alex" in db.get_meet(meet["id"])["coordinators"]
    assert "Alex" in db.get_event(event["id"])["active_coaches"]
    assert "Alex" in db.get_event(event["id"])["coordinators"]

    db.set_competition_coach_roles(competition_id, alex["id"], ["coach"])
    assert "Alex" in db.get_meet(meet["id"])["active_coaches"]
    assert "Alex" not in db.get_meet(meet["id"])["coordinators"]
    assert "Alex" in db.get_event(event["id"])["active_coaches"]
    assert "Alex" not in db.get_event(event["id"])["coordinators"]

    db.set_competition_coach_roles(
        competition_id, alex["id"], ["coach", "coordinator"]
    )
    db.set_day_coach_presence(
        meet["id"], alex["id"], actor="Casey", presence_status="absent"
    )
    assert "Alex" not in db.get_meet(meet["id"])["active_coaches"]
    assert "Alex" not in db.get_meet(meet["id"])["coordinators"]
    assert "Alex" not in db.get_event(event["id"])["active_coaches"]
    assert "Alex" not in db.get_event(event["id"])["coordinators"]
