import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from compcoach_live.storage import (
    CompCoachDB,
    CompCoachError,
    ConcurrentUpdateError,
    EventLockedError,
)


def sample_record(phase="de", strip="M1"):
    return {
        "athlete_id": "ath_max",
        "name": "DING Max",
        "strip": strip,
        "time": "",
        "pool": "4" if phase == "pools" else "",
        "pod": "M",
        "phase": phase,
    }


def make_db():
    temp = TemporaryDirectory()
    db = CompCoachDB(Path(temp.name) / "test.db")
    event = db.create_event("Test", ["Carmine", "Sam"], ["Irina"])
    return temp, db, event


def make_legacy_v021_database(path):
    """Build the relevant v0.2.1 schema without the additive meet tables."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
            status TEXT NOT NULL DEFAULT 'open',
            active_coaches_json TEXT NOT NULL DEFAULT '[]',
            coordinators_json TEXT NOT NULL DEFAULT '[]',
            admin_token TEXT NOT NULL UNIQUE,
            coordinator_token TEXT NOT NULL UNIQUE,
            coach_token TEXT NOT NULL UNIQUE,
            source_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE athletes (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            athlete_key TEXT NOT NULL,
            name TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'pools',
            pool_no TEXT NOT NULL DEFAULT '',
            pod TEXT NOT NULL DEFAULT '',
            source_strip TEXT NOT NULL DEFAULT '',
            time_text TEXT NOT NULL DEFAULT '',
            main_coach TEXT NOT NULL DEFAULT '',
            side_coach TEXT NOT NULL DEFAULT '',
            assignment_override INTEGER NOT NULL DEFAULT 0,
            active_state TEXT NOT NULL DEFAULT 'active',
            call_status TEXT NOT NULL DEFAULT 'waiting',
            live_location TEXT NOT NULL DEFAULT '',
            reported_at TEXT,
            reported_by TEXT NOT NULL DEFAULT '',
            covered_by TEXT NOT NULL DEFAULT '',
            covered_at TEXT,
            pool_wins INTEGER,
            pool_losses INTEGER,
            pool_result_at TEXT,
            pool_result_by TEXT NOT NULL DEFAULT '',
            de_wins INTEGER NOT NULL DEFAULT 0,
            last_de_result TEXT NOT NULL DEFAULT '',
            last_de_result_at TEXT,
            last_de_result_by TEXT NOT NULL DEFAULT '',
            help_requested_by TEXT NOT NULL DEFAULT '',
            help_requested_at TEXT,
            help_location TEXT NOT NULL DEFAULT '',
            help_acknowledged_by TEXT NOT NULL DEFAULT '',
            help_acknowledged_at TEXT,
            version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(event_id, athlete_key)
        );
        CREATE TABLE pod_assignments (
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            pod TEXT NOT NULL,
            main_coach TEXT NOT NULL DEFAULT '',
            side_coach TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(event_id, phase, pod)
        );
        CREATE TABLE phase_states (
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            started INTEGER NOT NULL DEFAULT 0,
            changed_at TEXT,
            changed_by TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(event_id, phase)
        );
        CREATE TABLE actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            athlete_id TEXT REFERENCES athletes(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            previous_json TEXT,
            new_json TEXT,
            version_after INTEGER,
            created_at TEXT NOT NULL,
            undone_at TEXT,
            undone_by TEXT
        );
        INSERT INTO events VALUES (
            'legacy_event', 'October NAC', 'America/Los_Angeles', 'open',
            '["Carmine","Sam"]', '["Irina"]', 'legacy_admin',
            'legacy_coord', 'legacy_coach', 'https://example.test/legacy',
            '2026-09-01T12:00:00+00:00', '2026-09-02T12:00:00+00:00'
        );
        INSERT INTO athletes (
            id, event_id, athlete_key, name, phase, pool_no, pod,
            source_strip, time_text, main_coach, side_coach,
            assignment_override, active_state, call_status, live_location,
            reported_at, reported_by, covered_by, covered_at, pool_wins,
            pool_losses, pool_result_at, pool_result_by, de_wins,
            last_de_result, last_de_result_at, last_de_result_by,
            help_requested_by, help_requested_at, help_location,
            help_acknowledged_by, help_acknowledged_at, version,
            created_at, updated_at
        ) VALUES (
            'legacy_athlete', 'legacy_event', 'ath_max', 'DING Max',
            'pools', '4', 'M', 'M1', '9:00 AM', 'Carmine', 'Sam',
            1, 'active', 'on_deck', 'M1', '2026-09-02T12:00:00+00:00',
            'Irina', '', NULL, 3, 3, '2026-09-02T12:01:00+00:00',
            'Carmine', 0, '', NULL, '', '', NULL, '', '', NULL, 7,
            '2026-09-01T12:00:00+00:00', '2026-09-02T12:01:00+00:00'
        );
        INSERT INTO pod_assignments VALUES (
            'legacy_event', 'pools', 'M', 'Carmine', 'Sam',
            '2026-09-02T12:00:00+00:00'
        );
        INSERT INTO phase_states VALUES (
            'legacy_event', 'pools', 1, '2026-09-02T12:00:00+00:00',
            'Irina', 2
        );
        INSERT INTO actions (
            event_id, athlete_id, action, actor, previous_json, new_json,
            version_after, created_at
        ) VALUES (
            'legacy_event', 'legacy_athlete', 'assignment', 'Carmine',
            '{}', '{}', 7, '2026-09-02T12:01:00+00:00'
        );
        """
    )
    conn.close()


def add_pre_end_day_meet_schema(path):
    """Add the first multi-event schema, before End Day fields existed."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
            status TEXT NOT NULL DEFAULT 'open',
            active_coaches_json TEXT NOT NULL DEFAULT '[]',
            coordinators_json TEXT NOT NULL DEFAULT '[]',
            admin_token TEXT NOT NULL UNIQUE,
            coordinator_token TEXT NOT NULL UNIQUE,
            coach_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE meet_events (
            meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL CHECK (sort_order BETWEEN 0 AND 3),
            PRIMARY KEY(meet_id, event_id),
            UNIQUE(meet_id, sort_order)
        );
        INSERT INTO meets VALUES (
            'legacy_meet', 'October NAC', 'America/Los_Angeles', 'open',
            '["Carmine","Sam"]', '["Irina"]', 'legacy_admin',
            'legacy_coord', 'legacy_coach', '2026-09-01T12:00:00+00:00',
            '2026-09-02T12:00:00+00:00'
        );
        INSERT INTO meet_events VALUES ('legacy_meet', 'legacy_event', 0);
        """
    )
    conn.close()


def test_event_tokens_resolve_roles():
    temp, db, event = make_db()
    try:
        assert db.authorize(event["id"], event["admin_token"]) == "admin"
        assert db.authorize(event["id"], event["coordinator_token"]) == "coordinator"
        assert db.authorize(event["id"], event["coach_token"]) == "coach"
        assert db.authorize(event["id"], "wrong") is None
    finally:
        temp.cleanup()


def test_phase_states_default_to_not_started_and_update_independently():
    temp, db, event = make_db()
    try:
        initial = db.get_phase_states(event["id"])
        assert initial == {
            "pools": {
                "phase": "pools",
                "started": False,
                "changed_at": None,
                "changed_by": "",
                "version": 0,
            },
            "de": {
                "phase": "de",
                "started": False,
                "changed_at": None,
                "changed_by": "",
                "version": 0,
            },
        }

        pools = db.set_phase_started(
            event["id"],
            "pools",
            True,
            "Irina",
            expected_started=False,
            expected_version=0,
        )
        assert pools["started"] is True
        assert pools["changed_by"] == "Irina"
        assert pools["changed_at"]
        assert pools["version"] == 1

        states = db.get_phase_states(event["id"])
        assert states["pools"] == pools
        assert states["de"]["started"] is False

        action = db.recent_actions(event["id"])[0]
        assert action["action"] == "phase_started"
        assert action["athlete_id"] is None
        assert action["actor"] == "Irina"
        assert action["previous"]["started"] is False
        assert action["new"]["started"] is True

        reset = db.set_phase_started(
            event["id"],
            "pools",
            False,
            "Carmine",
            expected_started=True,
            expected_version=1,
        )
        assert reset["started"] is False
        assert reset["changed_by"] == "Carmine"
        assert reset["version"] == 2
        assert db.recent_actions(event["id"])[0]["action"] == "phase_reset"
    finally:
        temp.cleanup()


def test_phase_state_rejects_stale_phone_after_red_green_red_and_locked_event():
    temp, db, event = make_db()
    try:
        db.set_phase_started(
            event["id"],
            "de",
            True,
            "Irina",
            expected_started=False,
            expected_version=0,
        )
        db.set_phase_started(
            event["id"],
            "de",
            False,
            "Irina",
            expected_started=True,
            expected_version=1,
        )
        with pytest.raises(ConcurrentUpdateError):
            db.set_phase_started(
                event["id"],
                "de",
                True,
                "Carmine",
                expected_started=False,
                expected_version=0,
            )

        db.set_event_locked(event["id"], True)
        with pytest.raises(EventLockedError):
            db.set_phase_started(
                event["id"],
                "de",
                True,
                "Irina",
                expected_started=False,
                expected_version=2,
            )
    finally:
        temp.cleanup()


def test_phase_state_validates_phase_and_actor():
    temp, db, event = make_db()
    try:
        with pytest.raises(ValueError, match="Phase"):
            db.set_phase_started(event["id"], "final", True, "Irina")
        with pytest.raises(ValueError, match="Actor"):
            db.set_phase_started(event["id"], "pools", True, "  ")
    finally:
        temp.cleanup()


def test_import_is_merge_only_and_preserves_state():
    temp, db, event = make_db()
    try:
        stats = db.merge_import(event["id"], [sample_record()], "Carmine")
        assert stats == {
            "added": 1,
            "updated": 0,
            "unchanged": 0,
            "moved_out": 0,
        }
        athlete = db.list_athletes(event["id"])[0]
        db.report_call(
            event["id"], athlete["id"], status="now", location="M1", actor="Irina"
        )
        db.claim(event["id"], athlete["id"], "Sam")
        db.merge_import(event["id"], [sample_record(strip="M2")], "Carmine")
        updated = db.get_athlete(event["id"], athlete["id"])
        assert updated["source_strip"] == "M2"
        assert updated["live_location"] == "M1"
        assert updated["covered_by"] == "Sam"
        assert db.merge_import(event["id"], [], "Carmine") == {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "moved_out": 0,
        }
        assert len(db.list_athletes(event["id"])) == 1
    finally:
        temp.cleanup()


def test_de_import_marks_non_advancers_out_only_after_exact_confirmation():
    temp, db, event = make_db()
    try:
        pool_records = [
            sample_record(phase="pools", strip="G1"),
            {
                "athlete_id": "ath_evan",
                "name": "OR Evan",
                "strip": "G2",
                "time": "",
                "pool": "5",
                "pod": "G",
                "phase": "pools",
            },
            {
                "athlete_id": "ath_alex",
                "name": "NG Alexander",
                "strip": "G3",
                "time": "",
                "pool": "6",
                "pod": "G",
                "phase": "pools",
            },
        ]
        db.merge_import(event["id"], pool_records, "Carmine")
        athletes = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
        evan = athletes["ath_evan"]
        alex = athletes["ath_alex"]
        db.set_pool_result(
            event["id"],
            evan["id"],
            wins=3,
            losses=3,
            actor="Carmine",
            expected_version=evan["version"],
        )
        alex = db.get_athlete(event["id"], alex["id"])
        db.request_help(
            event["id"],
            alex["id"],
            "Carmine",
            expected_version=alex["version"],
        )

        de_records = [sample_record(phase="de", strip="M1")]

        # A row import may be partial. Without the explicit whole-list
        # confirmation it can advance Max, but it must not eliminate anyone.
        partial_stats = db.merge_import(event["id"], de_records, "Irina")
        assert partial_stats["moved_out"] == 0
        after_partial = {
            row["athlete_key"]: row for row in db.list_athletes(event["id"])
        }
        assert after_partial["ath_max"]["phase"] == "de"
        assert after_partial["ath_max"]["active_state"] == "active"
        assert after_partial["ath_evan"]["phase"] == "pools"
        assert after_partial["ath_evan"]["active_state"] == "active"
        assert after_partial["ath_alex"]["active_state"] == "active"

        non_advancers = db.preview_de_non_advancers(event["id"], de_records)
        assert {row["athlete_key"] for row in non_advancers} == {
            "ath_evan",
            "ath_alex",
        }
        snapshot = {row["id"]: row["version"] for row in non_advancers}
        complete_stats = db.merge_import(
            event["id"],
            de_records,
            "Irina",
            confirmed_non_advancers=snapshot,
        )
        assert complete_stats["moved_out"] == 2

        final = {row["athlete_key"]: row for row in db.list_athletes(event["id"])}
        assert final["ath_max"]["phase"] == "de"
        assert final["ath_max"]["active_state"] == "active"
        assert final["ath_evan"]["phase"] == "pools"
        assert final["ath_evan"]["active_state"] == "eliminated"
        assert final["ath_evan"]["pool_wins"] == 3
        assert final["ath_evan"]["pool_losses"] == 3
        assert final["ath_alex"]["active_state"] == "eliminated"
        assert final["ath_alex"]["help_requested_at"] is None

        actions = [
            action
            for action in db.recent_actions(event["id"], limit=100)
            if action["action"] == "de_import_not_advanced"
        ]
        assert {action["athlete_id"] for action in actions} == {
            final["ath_evan"]["id"],
            final["ath_alex"]["id"],
        }

        # Both existing recovery paths remain available: the athlete-card
        # Restore action and the coordinator/admin activity-log Undo action.
        restored_evan = db.restore_athlete(
            event["id"],
            final["ath_evan"]["id"],
            "Irina",
            expected_version=final["ath_evan"]["version"],
        )
        assert restored_evan["active_state"] == "active"
        assert restored_evan["phase"] == "pools"
        alex_action = next(
            action
            for action in actions
            if action["athlete_id"] == final["ath_alex"]["id"]
        )
        restored_alex = db.undo_action(event["id"], alex_action["id"], "Irina")
        assert restored_alex["active_state"] == "active"
        assert restored_alex["phase"] == "pools"
        assert restored_alex["help_requested_by"] == "Carmine"
        assert restored_alex["help_requested_at"]
    finally:
        temp.cleanup()


def test_complete_de_confirmation_rejects_a_stale_non_advancer_snapshot_atomically():
    temp, db, event = make_db()
    try:
        pool_records = [
            sample_record(phase="pools", strip="G1"),
            {
                "athlete_id": "ath_evan",
                "name": "OR Evan",
                "strip": "G2",
                "time": "",
                "pool": "5",
                "pod": "G",
                "phase": "pools",
            },
        ]
        db.merge_import(event["id"], pool_records, "Carmine")
        de_records = [sample_record(phase="de", strip="M1")]
        preview = db.preview_de_non_advancers(event["id"], de_records)
        assert len(preview) == 1
        stale_snapshot = {preview[0]["id"]: preview[0]["version"]}

        db.set_pool_result(
            event["id"],
            preview[0]["id"],
            wins=4,
            losses=2,
            actor="Carmine",
            expected_version=preview[0]["version"],
        )
        with pytest.raises(ConcurrentUpdateError, match="changed on another phone"):
            db.merge_import(
                event["id"],
                de_records,
                "Irina",
                confirmed_non_advancers=stale_snapshot,
            )

        # Validation occurs before the merge, so the failed confirmation does
        # not even advance a listed athlete partway through the operation.
        unchanged = {
            row["athlete_key"]: row for row in db.list_athletes(event["id"])
        }
        assert unchanged["ath_max"]["phase"] == "pools"
        assert unchanged["ath_max"]["active_state"] == "active"
        assert unchanged["ath_evan"]["phase"] == "pools"
        assert unchanged["ath_evan"]["active_state"] == "active"
    finally:
        temp.cleanup()


def test_explicit_complete_empty_de_list_marks_all_pool_athletes_out():
    temp, db, event = make_db()
    try:
        pool_records = [
            sample_record(phase="pools", strip="G1"),
            {
                "athlete_id": "ath_evan",
                "name": "OR Evan",
                "strip": "G2",
                "time": "",
                "pool": "5",
                "pod": "G",
                "phase": "pools",
            },
        ]
        db.merge_import(event["id"], pool_records, "Carmine")

        with pytest.raises(ValueError, match="requires at least one"):
            db.preview_de_non_advancers(event["id"], [])

        candidates = db.preview_de_non_advancers(
            event["id"], [], allow_empty=True
        )
        snapshot = {row["id"]: row["version"] for row in candidates}
        stats = db.merge_import(
            event["id"],
            [],
            "Carmine",
            confirmed_non_advancers=snapshot,
        )

        assert stats == {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "moved_out": 2,
        }
        athletes = db.list_athletes(event["id"])
        assert {row["active_state"] for row in athletes} == {"eliminated"}
        assert {
            action["athlete_id"]
            for action in db.recent_actions(event["id"], limit=20)
            if action["action"] == "de_import_not_advanced"
        } == {row["id"] for row in athletes}
    finally:
        temp.cleanup()


def test_pod_assignment_applies_now_and_to_later_imports():
    temp, db, event = make_db()
    try:
        db.assign_pod(
            event["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="Sam",
            actor="Carmine",
        )
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        assert athlete["main_coach"] == "Carmine"
        assert athlete["side_coach"] == "Sam"
    finally:
        temp.cleanup()


def test_moving_to_unassigned_pod_does_not_keep_old_pod_coaches():
    temp, db, event = make_db()
    try:
        db.assign_pod(
            event["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="Sam",
            actor="Carmine",
        )
        db.merge_import(event["id"], [sample_record(strip="M1")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        assert athlete["main_coach"] == "Carmine"

        moved = sample_record(strip="P1")
        moved["pod"] = "P"
        db.merge_import(event["id"], [moved], "Irina")
        updated = db.get_athlete(event["id"], athlete["id"])
        assert updated["pod"] == "P"
        assert updated["main_coach"] == ""
        assert updated["side_coach"] == ""
    finally:
        temp.cleanup()


def test_phase_change_clears_pool_call_and_pool_assignment():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(phase="pools", strip="C3")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.assign_athletes(
            event["id"], [athlete["id"]], main_coach="Carmine", actor="Carmine"
        )
        db.report_call(
            event["id"], athlete["id"], status="on_deck", location="C3", actor="Irina"
        )
        db.merge_import(event["id"], [sample_record(phase="de", strip="M1")], "Carmine")
        changed = db.get_athlete(event["id"], athlete["id"])
        assert changed["phase"] == "de"
        assert changed["pool_no"] == ""
        assert changed["time_text"] == ""
        assert changed["main_coach"] == ""
        assert changed["call_status"] == "waiting"
        assert changed["live_location"] == ""
    finally:
        temp.cleanup()


def test_phase_change_replaces_pool_override_with_de_pod_assignment():
    temp, db, event = make_db()
    try:
        db.assign_pod(
            event["id"],
            phase="de",
            pod="M",
            main_coach="Sam",
            side_coach="",
            actor="Carmine",
        )
        db.merge_import(
            event["id"], [sample_record(phase="pools", strip="C3")], "Carmine"
        )
        athlete = db.list_athletes(event["id"])[0]
        db.assign_athletes(
            event["id"],
            [athlete["id"]],
            main_coach="Carmine",
            actor="Carmine",
        )

        db.merge_import(event["id"], [sample_record(phase="de", strip="M1")], "Irina")
        advanced = db.get_athlete(event["id"], athlete["id"])
        assert advanced["phase"] == "de"
        assert advanced["assignment_override"] == 0
        assert advanced["main_coach"] == "Sam"
        assert advanced["side_coach"] == ""
    finally:
        temp.cleanup()


def test_claim_first_coach_wins():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.report_call(
            event["id"], athlete["id"], status="on_deck", location="M1", actor="Irina"
        )
        first = db.claim(event["id"], athlete["id"], "Sam")
        second = db.claim(event["id"], athlete["id"], "Carmine")
        assert first[0] is True
        assert second[0] is False
        assert "Sam" in second[1]
    finally:
        temp.cleanup()


def test_simultaneous_claims_have_exactly_one_winner():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.report_call(
            event["id"], athlete["id"], status="now", location="M1", actor="Irina"
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda coach: db.claim(event["id"], athlete["id"], coach),
                    ["Sam", "Carmine"],
                )
            )
        assert sum(1 for ok, _ in results if ok) == 1
        assert db.get_athlete(event["id"], athlete["id"])["covered_by"] in {
            "Sam",
            "Carmine",
        }
    finally:
        temp.cleanup()


def test_won_and_lost_are_de_only_and_reversible():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        lost = db.mark_result(event["id"], athlete["id"], outcome="lost", actor="Sam")
        assert lost["active_state"] == "eliminated"
        action = db.recent_actions(event["id"])[0]
        restored = db.undo_action(event["id"], action["id"], "Irina")
        assert restored["active_state"] == "active"
    finally:
        temp.cleanup()


def test_undo_is_blocked_after_newer_action():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.report_call(
            event["id"], athlete["id"], status="now", location="M1", actor="Irina"
        )
        action = db.recent_actions(event["id"])[0]
        db.claim(event["id"], athlete["id"], "Sam")
        try:
            db.undo_action(event["id"], action["id"], "Irina")
        except ConcurrentUpdateError:
            pass
        else:
            raise AssertionError("Expected guarded Undo to be blocked")
    finally:
        temp.cleanup()


def test_live_update_preserves_existing_coverage_unless_explicitly_changed():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.report_call(
            event["id"], athlete["id"], status="on_deck", location="M1", actor="Irina"
        )
        db.claim(event["id"], athlete["id"], "Sam")
        covered = db.get_athlete(event["id"], athlete["id"])

        db.report_call(
            event["id"],
            athlete["id"],
            status="now",
            location="M2",
            actor="Irina",
            expected_version=covered["version"],
        )
        updated = db.get_athlete(event["id"], athlete["id"])
        assert updated["covered_by"] == "Sam"

        db.report_call(
            event["id"],
            athlete["id"],
            status="now",
            location="M2",
            actor="Irina",
            covered_by="",
            expected_version=updated["version"],
        )
        assert db.get_athlete(event["id"], athlete["id"])["covered_by"] == ""
    finally:
        temp.cleanup()


def test_athlete_assignment_override_survives_pod_change_and_reimport():
    temp, db, event = make_db()
    try:
        db.assign_pod(
            event["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="",
            actor="Carmine",
        )
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        db.assign_athletes(
            event["id"], [athlete["id"]], main_coach="Sam", actor="Carmine"
        )

        db.assign_pod(
            event["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="Sam",
            actor="Carmine",
        )
        db.merge_import(event["id"], [sample_record(strip="M2")], "Carmine")

        updated = db.get_athlete(event["id"], athlete["id"])
        assert updated["main_coach"] == "Sam"
        assert updated["side_coach"] == ""
        assert updated["assignment_override"] == 1
    finally:
        temp.cleanup()


def test_clear_call_and_pool_out_are_reversible():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(phase="pools")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        called = db.report_call(
            event["id"], athlete["id"], status="now", location="C3", actor="Irina"
        )
        cleared = db.clear_call(
            event["id"], athlete["id"], "Irina", expected_version=called["version"]
        )
        assert cleared["call_status"] == "waiting"
        assert cleared["live_location"] == ""

        out = db.mark_out(
            event["id"], athlete["id"], "Irina", expected_version=cleared["version"]
        )
        assert out["active_state"] == "eliminated"
        restored = db.restore_athlete(
            event["id"], athlete["id"], "Irina", expected_version=out["version"]
        )
        assert restored["active_state"] == "active"
    finally:
        temp.cleanup()


def test_stale_phone_cannot_overwrite_newer_live_state():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record()], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        initial_version = athlete["version"]
        db.report_call(
            event["id"],
            athlete["id"],
            status="on_deck",
            location="M1",
            actor="Irina",
            expected_version=initial_version,
        )
        with pytest.raises(ConcurrentUpdateError):
            db.report_call(
                event["id"],
                athlete["id"],
                status="now",
                location="M2",
                actor="Carmine",
                expected_version=initial_version,
            )
    finally:
        temp.cleanup()


def test_pool_result_is_editable_undoable_and_preserved_in_de():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(phase="pools", strip="G1")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]

        saved = db.set_pool_result(
            event["id"],
            athlete["id"],
            wins=3,
            losses=3,
            actor="Carmine",
            expected_version=athlete["version"],
        )
        assert saved["pool_wins"] == 3
        assert saved["pool_losses"] == 3
        assert saved["pool_result_by"] == "Carmine"
        assert saved["pool_result_at"]

        action = db.recent_actions(event["id"])[0]
        restored = db.undo_action(event["id"], action["id"], "Irina")
        assert restored["pool_wins"] is None
        assert restored["pool_losses"] is None

        saved_again = db.set_pool_result(
            event["id"],
            athlete["id"],
            wins=4,
            losses=2,
            actor="Carmine",
            expected_version=restored["version"],
        )
        db.merge_import(event["id"], [sample_record(phase="de", strip="G2")], "Irina")
        advanced = db.get_athlete(event["id"], athlete["id"])
        assert advanced["phase"] == "de"
        assert advanced["pool_wins"] == saved_again["pool_wins"] == 4
        assert advanced["pool_losses"] == saved_again["pool_losses"] == 2
    finally:
        temp.cleanup()


def test_pool_result_rejects_values_outside_zero_to_six():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(phase="pools")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]

        with pytest.raises(ValueError, match="between 0 and 6"):
            db.set_pool_result(
                event["id"], athlete["id"], wins=7, losses=0, actor="Carmine"
            )
        with pytest.raises(ValueError, match="between 0 and 6"):
            db.set_pool_result(
                event["id"], athlete["id"], wins=0, losses=-1, actor="Carmine"
            )
    finally:
        temp.cleanup()


def test_help_request_can_be_acknowledged_and_resolved_without_resetting_age():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(strip="G1")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        requested = db.request_help(
            event["id"],
            athlete["id"],
            "Carmine",
            expected_version=athlete["version"],
        )
        assert requested["help_requested_by"] == "Carmine"
        assert requested["help_location"] == "G1"
        assert requested["help_requested_at"]

        duplicate = db.request_help(
            event["id"],
            athlete["id"],
            "Carmine",
            expected_version=athlete["version"],
        )
        assert duplicate["help_requested_at"] == requested["help_requested_at"]

        moved = db.report_call(
            event["id"],
            athlete["id"],
            status="on_deck",
            location="G2",
            actor="Irina",
            expected_version=requested["version"],
        )
        assert moved["help_location"] == "G2"
        assert moved["help_requested_at"] == requested["help_requested_at"]

        acknowledged = db.acknowledge_help(
            event["id"],
            athlete["id"],
            "Sam",
            expected_version=moved["version"],
        )
        assert acknowledged["help_acknowledged_by"] == "Sam"
        assert acknowledged["help_acknowledged_at"]

        resolved = db.clear_help(
            event["id"],
            athlete["id"],
            "Sam",
            expected_version=acknowledged["version"],
        )
        assert resolved["help_requested_at"] is None
        assert resolved["help_requested_by"] == ""
        assert resolved["help_acknowledged_by"] == ""

        resolve_action = db.recent_actions(event["id"])[0]
        reopened = db.undo_action(event["id"], resolve_action["id"], "Irina")
        assert reopened["help_requested_by"] == "Carmine"
        assert reopened["help_acknowledged_by"] == "Sam"
    finally:
        temp.cleanup()


def test_only_one_coach_can_acknowledge_the_same_help_request():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(strip="G1")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        requested = db.request_help(
            event["id"],
            athlete["id"],
            "Carmine",
            expected_version=athlete["version"],
        )

        def acknowledge(coach):
            try:
                db.acknowledge_help(
                    event["id"],
                    athlete["id"],
                    coach,
                    expected_version=requested["version"],
                )
                return True
            except CompCoachError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acknowledge, ["Sam", "Irina"]))
        assert sum(results) == 1
        assert db.get_athlete(event["id"], athlete["id"])[
            "help_acknowledged_by"
        ] in {"Sam", "Irina"}
    finally:
        temp.cleanup()


def test_de_results_remain_scoreless_but_track_wins_and_clear_help():
    temp, db, event = make_db()
    try:
        db.merge_import(event["id"], [sample_record(strip="M1")], "Carmine")
        athlete = db.list_athletes(event["id"])[0]
        requested = db.request_help(
            event["id"],
            athlete["id"],
            "Carmine",
            expected_version=athlete["version"],
        )
        won = db.mark_result(
            event["id"],
            athlete["id"],
            outcome="won",
            actor="Carmine",
            expected_version=requested["version"],
        )
        assert won["active_state"] == "active"
        assert won["de_wins"] == 1
        assert won["last_de_result"] == "won"
        assert won["help_requested_at"] is None

        called = db.report_call(
            event["id"],
            athlete["id"],
            status="now",
            location="M2",
            actor="Irina",
            expected_version=won["version"],
        )
        claimed, _ = db.claim(event["id"], athlete["id"], "Sam")
        assert claimed
        covered = db.get_athlete(event["id"], athlete["id"])
        lost = db.mark_result(
            event["id"],
            athlete["id"],
            outcome="lost",
            actor="Carmine",
            expected_version=covered["version"],
        )
        assert lost["active_state"] == "eliminated"
        assert lost["de_wins"] == 1
        assert lost["last_de_result"] == "lost"

        restored = db.restore_athlete(
            event["id"],
            athlete["id"],
            "Irina",
            expected_version=lost["version"],
        )
        assert restored["active_state"] == "active"
        assert restored["de_wins"] == 1
        assert restored["last_de_result"] == "won"
        assert restored["call_status"] == called["call_status"] == "now"
        assert restored["live_location"] == "M2"
        assert restored["covered_by"] == "Sam"
    finally:
        temp.cleanup()


def test_v021_migration_creates_one_meet_losslessly_and_is_idempotent():
    temp = TemporaryDirectory()
    try:
        path = Path(temp.name) / "legacy.db"
        make_legacy_v021_database(path)

        db = CompCoachDB(path)
        event = db.get_event("legacy_event")
        meet = db.get_meet_for_event("legacy_event")
        assert event["name"] == "October NAC"
        assert event["source_url"] == "https://example.test/legacy"
        assert event["meet_id"] == meet["id"]
        assert meet["name"] == "October NAC"
        assert meet["active_coaches"] == ["Carmine", "Sam"]
        assert meet["coordinators"] == ["Irina"]
        assert meet["event_count"] == 1
        assert db.list_athletes("legacy_event")[0]["id"] == "legacy_athlete"
        assert db.list_athletes("legacy_event")[0]["version"] == 7
        assert db.get_phase_states("legacy_event")["pools"]["version"] == 2
        assert db.recent_actions("legacy_event")[0]["action"] == "assignment"
        assert db.authorize("legacy_event", "legacy_admin") == "admin"
        assert db.authorize_meet(meet["id"], "legacy_admin") == "admin"
        assert db.authorize_meet(meet["id"], "legacy_coord") == "coordinator"
        assert db.authorize_meet(meet["id"], "legacy_coach") == "coach"

        reopened = CompCoachDB(path)
        assert len(reopened.list_meets()) == 1
        assert len(reopened.list_meet_events(meet["id"])) == 1
        assert reopened.list_athletes("legacy_event")[0]["pool_wins"] == 3
        check = sqlite3.connect(path)
        try:
            assert check.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            check.close()
    finally:
        temp.cleanup()


def test_legacy_create_event_also_creates_resolvable_meet():
    temp, db, event = make_db()
    try:
        meet = db.get_meet_for_event(event["id"])
        assert meet is not None
        assert event["meet_id"] == meet["id"]
        assert db.list_meet_events(meet["id"])[0]["id"] == event["id"]
        assert db.authorize_meet(meet["id"], event["coach_token"]) == "coach"
    finally:
        temp.cleanup()


def test_create_meet_can_start_empty_while_default_keeps_main_event():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")

        empty = db.create_meet(
            "October NAC — Day 1",
            ["Carmine"],
            ["Irina"],
            first_event_name=None,
        )
        assert empty["event_count"] == 0
        assert db.list_meet_events(empty["id"]) == []

        first = db.add_meet_event(empty["id"], "Cadet Epee")
        assert first["name"] == "Cadet Epee"
        assert first["sort_order"] == 0
        assert db.get_meet(empty["id"])["event_count"] == 1

        defaulted = db.create_meet("October NAC — Day 2", ["Sam"], ["Irina"])
        default_children = db.list_meet_events(defaulted["id"])
        assert defaulted["event_count"] == 1
        assert [child["name"] for child in default_children] == ["Main Event"]
    finally:
        temp.cleanup()


def test_meet_events_keep_athletes_phases_and_pods_isolated():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet(
            "October NAC",
            ["Carmine", "Sam"],
            ["Irina"],
            first_event_name="Y12 Men's Epee",
        )
        first = db.list_meet_events(meet["id"])[0]
        second = db.add_meet_event(meet["id"], "Y14 Men's Epee")

        db.assign_pod(
            first["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="",
            actor="Carmine",
        )
        assert db.merge_import(first["id"], [sample_record()], "Carmine")["added"] == 1
        assert db.merge_import(second["id"], [sample_record()], "Carmine")["added"] == 1
        first_athlete = db.list_athletes(first["id"])[0]
        second_athlete = db.list_athletes(second["id"])[0]
        assert first_athlete["athlete_key"] == second_athlete["athlete_key"] == "ath_max"
        assert first_athlete["id"] != second_athlete["id"]
        assert first_athlete["main_coach"] == "Carmine"
        assert second_athlete["main_coach"] == ""

        db.set_phase_started(first["id"], "de", True, "Irina")
        assert db.get_phase_states(first["id"])["de"]["started"] is True
        assert db.get_phase_states(second["id"])["de"]["started"] is False
        assert len(db.recent_actions(first["id"])) == 2
        assert len(db.recent_actions(second["id"])) == 1
    finally:
        temp.cleanup()


def test_meet_enforces_four_event_limit_under_concurrent_adds():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet("October NAC", ["Carmine"], ["Irina"])
        db.add_meet_event(meet["id"], "Event 2")
        db.add_meet_event(meet["id"], "Event 3")

        def add(name):
            try:
                return db.add_meet_event(meet["id"], name)["id"]
            except CompCoachError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, ["Event 4A", "Event 4B"]))
        assert sum(result is not None for result in results) == 1
        assert len(db.list_meet_events(meet["id"])) == 4
        with pytest.raises(CompCoachError, match="at most 4"):
            db.add_meet_event(meet["id"], "Event 5")
    finally:
        temp.cleanup()


def test_meet_child_rename_reorder_and_safe_delete():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet("October NAC", ["Carmine"], ["Irina"])
        first = db.list_meet_events(meet["id"])[0]
        second = db.add_meet_event(
            meet["id"], "Temporary", source_url="https://example.test/event2"
        )
        renamed = db.rename_meet_event(meet["id"], second["id"], "Cadet Women's Epee")
        assert renamed["name"] == "Cadet Women's Epee"
        assert renamed["source_url"] == "https://example.test/event2"

        db.reorder_meet_events(meet["id"], [second["id"], first["id"]])
        ordered = db.list_meet_events(meet["id"])
        assert [event["id"] for event in ordered] == [second["id"], first["id"]]
        assert [event["sort_order"] for event in ordered] == [0, 1]

        db.delete_meet_event(meet["id"], second["id"])
        assert [event["id"] for event in db.list_meet_events(meet["id"])] == [first["id"]]

        # Phase-light history is operational metadata, not athlete data. It
        # must not make an otherwise unused event impossible to remove.
        started = db.set_phase_started(first["id"], "pools", True, "Irina")
        db.set_phase_started(
            first["id"],
            "pools",
            False,
            "Irina",
            expected_started=True,
            expected_version=started["version"],
        )
        assert len(db.recent_actions(first["id"])) == 2

        # The final child may be deleted, leaving a valid day with zero events.
        db.delete_meet_event(meet["id"], first["id"])
        assert db.list_meet_events(meet["id"]) == []
        assert db.get_meet(meet["id"])["event_count"] == 0

        populated = db.add_meet_event(meet["id"], "Populated")
        assert populated["sort_order"] == 0
        db.merge_import(populated["id"], [sample_record()], "Carmine")
        with pytest.raises(CompCoachError, match="empty"):
            db.delete_meet_event(meet["id"], populated["id"])
    finally:
        temp.cleanup()


def test_child_from_another_meet_is_rejected():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        first_meet = db.create_meet("Meet A", ["Carmine"], ["Irina"])
        second_meet = db.create_meet("Meet B", ["Sam"], ["Irina"])
        foreign_event = db.list_meet_events(second_meet["id"])[0]

        with pytest.raises(CompCoachError, match="does not belong"):
            db.rename_meet_event(first_meet["id"], foreign_event["id"], "Wrong")
        with pytest.raises(CompCoachError, match="does not belong"):
            db.delete_meet_event(first_meet["id"], foreign_event["id"])
    finally:
        temp.cleanup()


def test_meet_staff_and_lock_state_sync_to_every_child():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet("October NAC", ["Carmine"], ["Irina"])
        second = db.add_meet_event(meet["id"], "Junior Men's Epee")
        updated = db.update_meet_staff(
            meet["id"],
            name="October NAC 2026",
            active_coaches=["Carmine", "Sam"],
            coordinators=["Irina", "Igor"],
            timezone_name="America/New_York",
        )
        assert updated["name"] == "October NAC 2026"
        for child in db.list_meet_events(meet["id"]):
            assert child["active_coaches"] == ["Carmine", "Sam"]
            assert child["coordinators"] == ["Irina", "Igor"]
            assert child["timezone"] == "America/New_York"

        db.set_meet_locked(meet["id"], True)
        assert db.get_meet(meet["id"])["status"] == "locked"
        assert {child["status"] for child in db.list_meet_events(meet["id"])} == {"locked"}
        with pytest.raises(EventLockedError):
            db.add_meet_event(meet["id"], "Blocked")
        with pytest.raises(EventLockedError):
            db.rename_meet_event(meet["id"], second["id"], "Blocked")
        with pytest.raises(EventLockedError):
            db.update_meet_staff(
                meet["id"],
                name="Blocked",
                active_coaches=["Carmine"],
                coordinators=["Irina"],
                timezone_name="America/Los_Angeles",
            )

        # Defense in depth: even a child status that drifted to open cannot
        # bypass a locked parent meet.
        drift = sqlite3.connect(db.path)
        try:
            drift.execute("UPDATE events SET status = 'open' WHERE id = ?", (second["id"],))
            drift.commit()
        finally:
            drift.close()
        with pytest.raises(EventLockedError):
            db.merge_import(second["id"], [sample_record()], "Carmine")

        db.set_meet_locked(meet["id"], False)
        assert {child["status"] for child in db.list_meet_events(meet["id"])} == {"open"}
    finally:
        temp.cleanup()


def test_meet_and_legacy_tokens_authorize_only_their_own_group():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        first = db.create_meet("Meet A", ["Carmine"], ["Irina"])
        first_child = db.list_meet_events(first["id"])[0]
        second = db.create_meet("Meet B", ["Sam"], ["Irina"])

        assert db.authorize_meet(first["id"], first["admin_token"]) == "admin"
        assert db.authorize_meet(first["id"], first_child["coach_token"]) == "coach"
        assert db.authorize_meet(second["id"], first["admin_token"]) is None
        assert db.authorize_meet(second["id"], first_child["coach_token"]) is None
        assert db.authorize(first_child["id"], first_child["coach_token"]) == "coach"

        rotated = db.rotate_meet_token(first["id"], "coach")
        assert db.authorize_meet(first["id"], rotated) == "coach"
        assert db.authorize_meet(first["id"], first["coach_token"]) is None
        assert db.authorize_meet(first["id"], first_child["coach_token"]) is None
        refreshed_child = db.get_event(first_child["id"])
        assert db.authorize_meet(first["id"], refreshed_child["coach_token"]) == "coach"
    finally:
        temp.cleanup()


def test_legacy_event_admin_updates_shared_staff_and_lock():
    temp, db, first = make_db()
    try:
        meet = db.get_meet_for_event(first["id"])
        second = db.add_meet_event(meet["id"], "Second Event")
        db.update_event_staff(
            first["id"],
            name="First Renamed",
            active_coaches=(name for name in ["Carmine", "Sam", "JM"]),
            coordinators=(name for name in ["Irina", "Igor"]),
            timezone_name="America/New_York",
        )
        assert db.get_event(first["id"])["name"] == "First Renamed"
        assert db.get_event(second["id"])["active_coaches"] == ["Carmine", "Sam", "JM"]
        assert db.get_meet(meet["id"])["coordinators"] == ["Irina", "Igor"]

        db.set_event_locked(second["id"], True)
        assert db.get_meet(meet["id"])["status"] == "locked"
        assert {child["status"] for child in db.list_meet_events(meet["id"])} == {"locked"}
    finally:
        temp.cleanup()


def test_rotating_legacy_event_token_invalidates_shared_and_sibling_old_links():
    temp, db, first = make_db()
    try:
        meet = db.get_meet_for_event(first["id"])
        second = db.add_meet_event(meet["id"], "Second Event")
        old_meet_token = meet["coach_token"]
        old_first_token = first["coach_token"]
        old_second_token = second["coach_token"]

        new_first_token = db.rotate_token(first["id"], "coach")
        assert db.authorize(first["id"], new_first_token) == "coach"
        for old_token in (old_meet_token, old_first_token, old_second_token):
            assert db.authorize_meet(meet["id"], old_token) is None
        refreshed_second = db.get_event(second["id"])
        assert refreshed_second["coach_token"] != old_second_token
    finally:
        temp.cleanup()


def test_end_day_schema_migrates_existing_meets_without_data_loss():
    temp = TemporaryDirectory()
    try:
        path = Path(temp.name) / "pre_end_day.db"
        make_legacy_v021_database(path)
        add_pre_end_day_meet_schema(path)

        db = CompCoachDB(path)
        meet = db.get_meet("legacy_meet")
        assert meet["ended_at"] is None
        assert meet["ended_by"] == ""
        assert meet["prepared_from_meet_id"] is None
        assert meet["competition_date"] == ""
        assert meet["event_count"] == 1
        assert db.list_athletes("legacy_event")[0]["version"] == 7
        assert db.get_phase_states("legacy_event")["pools"]["started"] is True

        reopened = CompCoachDB(path)
        assert len(reopened.list_meets()) == 1
        assert reopened.get_meet_for_event("legacy_event")["id"] == "legacy_meet"
        check = sqlite3.connect(path)
        try:
            columns = {
                row[1] for row in check.execute("PRAGMA table_info(meets)").fetchall()
            }
            assert {
                "ended_at",
                "ended_by",
                "prepared_from_meet_id",
                "competition_date",
            } <= columns
            assert check.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            check.close()
    finally:
        temp.cleanup()


def test_finish_meet_is_idempotent_locks_children_and_retains_data():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet(
            "October NAC — Day 1",
            ["Carmine", "Sam"],
            ["Irina"],
            competition_date="2026-10-09",
        )
        first = db.list_meet_events(meet["id"])[0]
        second = db.add_meet_event(meet["id"], "Cadet Women's Epee")
        db.merge_import(first["id"], [sample_record()], "Carmine")
        db.set_phase_started(second["id"], "pools", True, "Irina")

        finished = db.finish_meet(meet["id"], "Irina")
        assert finished["status"] == "locked"
        assert finished["ended_at"]
        assert finished["ended_by"] == "Irina"
        assert finished["competition_date"] == "2026-10-09"
        assert {child["status"] for child in db.list_meet_events(meet["id"])} == {"locked"}
        assert len(db.list_athletes(first["id"])) == 1
        assert db.get_phase_states(second["id"])["pools"]["started"] is True

        retried = db.finish_meet(meet["id"], "Sam")
        assert retried["ended_at"] == finished["ended_at"]
        assert retried["ended_by"] == "Irina"
        with pytest.raises(CompCoachError, match="cannot be reopened"):
            db.set_meet_locked(meet["id"], False)
        with pytest.raises(CompCoachError, match="cannot be reopened"):
            db.set_event_locked(first["id"], False)
        with pytest.raises(EventLockedError):
            db.merge_import(first["id"], [sample_record(strip="M2")], "Carmine")
    finally:
        temp.cleanup()


def test_prepare_next_day_copies_only_configuration_and_keeps_old_day_intact():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        source = db.create_meet(
            "October NAC — Day 1",
            ["Carmine", "Sam", "JM"],
            ["Irina", "Igor"],
            "America/New_York",
            first_event_name="Y12 Men's Epee",
            competition_date="2026-10-09",
        )
        source_events = db.list_meet_events(source["id"])
        second = db.add_meet_event(
            source["id"],
            "Cadet Women's Epee",
            source_url="https://example.test/day1-event2",
        )
        first = source_events[0]
        db.update_event_source_url(first["id"], "https://example.test/day1-event1")
        db.assign_pod(
            first["id"],
            phase="de",
            pod="M",
            main_coach="Carmine",
            side_coach="Sam",
            actor="Carmine",
        )
        db.merge_import(first["id"], [sample_record()], "Carmine")
        db.merge_import(second["id"], [sample_record(phase="pools")], "Carmine")
        db.set_phase_started(first["id"], "de", True, "Irina")
        athlete = db.list_athletes(first["id"])[0]
        db.request_help(first["id"], athlete["id"], "Carmine")

        old_parent_tokens = {
            source[f"{role}_token"] for role in ("admin", "coordinator", "coach")
        }
        old_child_tokens = {
            event[f"{role}_token"]
            for event in db.list_meet_events(source["id"])
            for role in ("admin", "coordinator", "coach")
        }
        successor = db.prepare_next_day(
            source["id"],
            "October NAC — Day 2",
            "2026-10-10",
            ["Y14 Men's Epee", "Junior Women's Epee", "Division I Men's Epee"],
            "Irina",
        )

        closed = db.get_meet(source["id"])
        assert closed["status"] == "locked"
        assert closed["ended_at"]
        assert closed["ended_by"] == "Irina"
        assert closed["competition_date"] == "2026-10-09"
        assert len(db.list_athletes(first["id"])) == 1
        assert db.list_athletes(first["id"])[0]["help_requested_by"] == "Carmine"
        assert db.get_phase_states(first["id"])["de"]["started"] is True
        assert db.get_event(first["id"])["source_url"] == "https://example.test/day1-event1"

        assert successor["status"] == "open"
        assert successor["ended_at"] is None
        assert successor["ended_by"] == ""
        assert successor["prepared_from_meet_id"] == source["id"]
        assert successor["competition_date"] == "2026-10-10"
        assert successor["active_coaches"] == source["active_coaches"]
        assert successor["coordinators"] == source["coordinators"]
        assert successor["timezone"] == source["timezone"]
        new_events = db.list_meet_events(successor["id"])
        assert [event["name"] for event in new_events] == [
            "Y14 Men's Epee",
            "Junior Women's Epee",
            "Division I Men's Epee",
        ]
        assert all(event["source_url"] == "" for event in new_events)
        assert all(event["status"] == "open" for event in new_events)
        assert all(db.list_athletes(event["id"]) == [] for event in new_events)
        assert all(db.recent_actions(event["id"]) == [] for event in new_events)
        assert all(
            state["started"] is False
            for event in new_events
            for state in db.get_phase_states(event["id"]).values()
        )
        new_tokens = {
            successor[f"{role}_token"]
            for role in ("admin", "coordinator", "coach")
        } | {
            event[f"{role}_token"]
            for event in new_events
            for role in ("admin", "coordinator", "coach")
        }
        assert new_tokens.isdisjoint(old_parent_tokens | old_child_tokens)

        raw = sqlite3.connect(db.path)
        try:
            for event in new_events:
                assert raw.execute(
                    "SELECT COUNT(*) FROM pod_assignments WHERE event_id = ?",
                    (event["id"],),
                ).fetchone()[0] == 0
        finally:
            raw.close()
    finally:
        temp.cleanup()


def test_prepare_next_day_is_idempotent_and_accepts_already_ended_source():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        source = db.create_meet("Day 1", ["Carmine"], ["Irina"])
        finished = db.finish_meet(source["id"], "Irina")
        first = db.prepare_next_day(
            source["id"], "Day 2", "2026-10-10", ["Cadet Epee"], "Carmine"
        )
        retry = db.prepare_next_day(
            source["id"],
            "A different retry label",
            "2026-10-11",
            ["A different valid event"],
            "Sam",
        )
        assert retry["id"] == first["id"]
        assert retry["name"] == "Day 2"
        assert retry["competition_date"] == "2026-10-10"
        assert [event["name"] for event in db.list_meet_events(retry["id"])] == [
            "Cadet Epee"
        ]
        assert db.get_meet(source["id"])["ended_at"] == finished["ended_at"]
        assert db.get_meet(source["id"])["ended_by"] == "Irina"
        assert len(db.list_meets()) == 2
    finally:
        temp.cleanup()


def test_prepare_next_day_allows_zero_events():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        source = db.create_meet("Day 1", ["Carmine"], ["Irina"])

        successor = db.prepare_next_day(
            source["id"], "Day 2", "2026-10-10", [], "Irina"
        )

        assert successor["event_count"] == 0
        assert db.list_meet_events(successor["id"]) == []
        assert db.get_meet(source["id"])["status"] == "locked"
    finally:
        temp.cleanup()


def test_prepared_successor_lookup_distinguishes_direct_and_latest_day():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        day_one = db.create_meet(
            "Day 1", ["Carmine"], ["Irina"], first_event_name=None
        )
        assert db.get_prepared_successor(day_one["id"]) is None
        assert db.get_latest_prepared_successor(day_one["id"]) is None

        day_two = db.prepare_next_day(
            day_one["id"], "Day 2", "2026-10-10", [], "Irina"
        )
        day_three = db.prepare_next_day(
            day_two["id"], "Day 3", "2026-10-11", ["Junior Epee"], "Carmine"
        )

        direct = db.get_prepared_successor(day_one["id"])
        latest = db.get_latest_prepared_successor(day_one["id"])
        assert direct is not None and direct["id"] == day_two["id"]
        assert direct["event_count"] == 0
        assert latest is not None and latest["id"] == day_three["id"]
        assert latest["event_count"] == 1
        assert db.get_latest_prepared_successor(day_two["id"])["id"] == day_three["id"]
        assert db.get_prepared_successor(day_three["id"]) is None
        assert db.get_latest_prepared_successor(day_three["id"]) is None
    finally:
        temp.cleanup()


def test_prepare_next_day_allows_four_events_and_rejects_invalid_inputs():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        source = db.create_meet("Day 1", ["Carmine"], ["Irina"])
        invalid_calls = [
            (
                "Day 2",
                "2026-10-10",
                ["1", "2", "3", "4", "5"],
                "Irina",
                "at most 4",
            ),
            ("Day 2", "2026-10-10", ["Cadet", "cadet"], "Irina", "unique"),
            ("Day 2", "2026-10-10", ["Cadet", "  "], "Irina", "empty"),
            ("Day 2", "10/10/2026", ["Cadet"], "Irina", "YYYY-MM-DD"),
            (" ", "2026-10-10", ["Cadet"], "Irina", "name"),
            ("Day 2", "2026-10-10", ["Cadet"], " ", "Actor"),
        ]
        for next_name, day, names, actor, message in invalid_calls:
            with pytest.raises(ValueError, match=message):
                db.prepare_next_day(source["id"], next_name, day, names, actor)
        assert db.get_meet(source["id"])["ended_at"] is None
        assert len(db.list_meets()) == 1

        successor = db.prepare_next_day(
            source["id"],
            "Day 2",
            "2026-10-10",
            ["Event 1", "Event 2", "Event 3", "Event 4"],
            "Irina",
        )
        assert successor["event_count"] == 4
        with pytest.raises(CompCoachError, match="not found"):
            db.prepare_next_day(
                "missing", "Day X", "2026-10-11", ["Event"], "Irina"
            )
    finally:
        temp.cleanup()


def test_event_names_are_unique_within_one_competition_day():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        meet = db.create_meet(
            "October NAC", ["Carmine"], ["Irina"], first_event_name="Cadet Epee"
        )
        second = db.add_meet_event(meet["id"], "Junior Epee")

        with pytest.raises(CompCoachError, match="unique"):
            db.add_meet_event(meet["id"], "  cadet epee  ")
        with pytest.raises(CompCoachError, match="unique"):
            db.rename_meet_event(meet["id"], second["id"], "CADET EPEE")

        assert [row["name"] for row in db.list_meet_events(meet["id"])] == [
            "Cadet Epee",
            "Junior Epee",
        ]
    finally:
        temp.cleanup()


def test_concurrent_prepare_next_day_creates_exactly_one_successor():
    temp = TemporaryDirectory()
    try:
        db = CompCoachDB(Path(temp.name) / "test.db")
        source = db.create_meet("Day 1", ["Carmine", "Sam"], ["Irina"])

        def prepare(actor):
            return db.prepare_next_day(
                source["id"],
                "Day 2",
                "2026-10-10",
                ["Cadet Epee", "Junior Epee"],
                actor,
            )["id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(prepare, ["Irina", "Carmine"]))
        assert ids[0] == ids[1]
        assert len(db.list_meets()) == 2
        successors = [
            meet
            for meet in db.list_meets()
            if meet["prepared_from_meet_id"] == source["id"]
        ]
        assert len(successors) == 1
        assert successors[0]["event_count"] == 2
        assert db.get_meet(source["id"])["status"] == "locked"
    finally:
        temp.cleanup()


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("storage tests passed")
