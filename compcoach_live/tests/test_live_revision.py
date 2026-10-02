"""Operational polling detects actual changes with one read-only statement."""

from contextlib import contextmanager

import pytest

from compcoach_live.storage import CompCoachDB
from compcoach_live import training


@pytest.fixture
def context(tmp_path):
    db = CompCoachDB(tmp_path / "revision.sqlite")
    meet = db.create_meet(
        "Competition", ["Coach Alex", "Coach Jordan"],
        competition_date="2026-10-02",
    )
    event = db.list_meet_events(meet["id"])[0]
    other_event = db.add_meet_event(meet["id"], "Other event")
    db.merge_import(event["id"], [
        {"athlete_id": "one", "name": "ONE Athlete", "phase": "de", "strip": "B1", "pod": "B"},
        {"athlete_id": "two", "name": "TWO Athlete", "phase": "de", "strip": "B1", "pod": "B"},
    ], "Coach Alex")
    athletes = db.list_athletes(event["id"])
    coach = db.create_coach("Coach Extra")
    with db._connection() as conn:
        conn.execute(
            "INSERT INTO phase_states (event_id, phase, started) VALUES (?, 'de', 0)",
            (event["id"],),
        )
        conn.execute(
            "INSERT INTO pool_waves (event_id, wave_key, label, is_active, is_visible, created_at, updated_at) "
            "VALUES (?, 'early', '9 AM', 1, 1, 'same-time', 'same-time')", (event["id"],),
        )
        conn.execute(
            "INSERT INTO pod_assignments (event_id, phase, pod, coaches_json, updated_at) "
            "VALUES (?, 'de', 'B', '[\"Coach Alex\"]', 'same-time')", (event["id"],),
        )
        conn.execute(
            "INSERT INTO de_bouts (id, event_id, athlete_a_id, athlete_b_id, athlete_a_version, "
            "athlete_b_version, created_at, updated_at) VALUES ('pair', ?, ?, ?, 0, 0, 'same-time', 'same-time')",
            (event["id"], athletes[0]["id"], athletes[1]["id"]),
        )
        conn.execute(
            "INSERT INTO coach_availability (meet_id, coach_name, is_available, updated_at) "
            "VALUES (?, 'Coach Alex', 0, 'same-time')", (meet["id"],),
        )
        conn.execute(
            "INSERT INTO day_coach_presence (meet_id, coach_id, presence_status, updated_at) "
            "VALUES (?, ?, 'present', 'same-time')", (meet["id"], coach["id"]),
        )
        conn.execute(
            "INSERT INTO competition_coaches (competition_id, coach_id, role, added_at) "
            "VALUES (?, ?, 'coach', 'same-time')", (meet["competition_id"], coach["id"]),
        )
    return db, meet, event, other_event, athletes, coach


@pytest.mark.parametrize("statement", [
    "UPDATE meets SET active_coaches_json = '[\"Coach Jordan\"]' WHERE id = ?",
    "UPDATE meets SET day_status = 'closed' WHERE id = ?",
    "UPDATE competitions SET logo_path = 'cloud/new-logo' WHERE id = (SELECT competition_id FROM meets WHERE id = ?)",
    "UPDATE events SET name = 'Renamed event' WHERE id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE meet_events SET sort_order = 3 WHERE meet_id = ? AND sort_order = 0",
    "UPDATE athletes SET version = version + 1 WHERE event_id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE phase_states SET started = 1 WHERE event_id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE pool_waves SET is_visible = 0 WHERE event_id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE pod_assignments SET coaches_json = '[\"Coach Jordan\"]' WHERE event_id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE de_bouts SET version = version + 1 WHERE event_id IN (SELECT event_id FROM meet_events WHERE meet_id = ?)",
    "UPDATE coach_availability SET is_available = 1 WHERE meet_id = ?",
    "UPDATE day_coach_presence SET presence_status = 'absent' WHERE meet_id = ?",
    "UPDATE competition_coaches SET role = 'admin' WHERE competition_id = (SELECT competition_id FROM meets WHERE id = ?) AND coach_id IN (SELECT id FROM coaches WHERE name = 'Coach Extra')",
    "UPDATE coaches SET is_active = 0 WHERE id IN (SELECT coach_id FROM day_coach_presence WHERE meet_id = ?)",
])
def test_revision_covers_every_operational_surface_without_timestamp_changes(context, statement):
    db, meet, *_ = context
    before = db.get_meet_revision(meet["id"])
    with db._connection() as conn:
        conn.execute(statement, (meet["id"],))
    assert db.get_meet_revision(meet["id"]) != before


def test_idle_polling_is_one_read_only_statement_and_missing_day_is_none(context):
    db, meet, *_ = context
    before = db.get_meet_revision(meet["id"])
    original = db._connection
    statements = []

    @contextmanager
    def trace_connection():
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    db._connection = trace_connection
    assert db.get_meet_revision(meet["id"]) == before
    assert len(statements) == 1
    assert statements[0].lstrip().startswith("WITH target AS")
    assert db.get_meet_revision("missing") is None


def test_removal_and_replacement_with_same_version_are_detected(context):
    db, meet, event, _, athletes, _ = context
    before = db.get_meet_revision(meet["id"])
    with db._connection() as conn:
        conn.execute("DELETE FROM de_bouts WHERE event_id = ?", (event["id"],))
        conn.execute("DELETE FROM athletes WHERE id = ?", (athletes[0]["id"],))
    deleted = db.get_meet_revision(meet["id"])
    assert deleted != before
    db.merge_import(event["id"], [
        {"athlete_id": "replacement", "name": "NEW Athlete", "phase": "de", "strip": "B1", "pod": "B"},
    ], "Coach Alex")
    assert db.get_meet_revision(meet["id"]) != deleted


def test_sibling_day_activation_and_competition_closure_are_detected(context):
    db, meet, *_ = context
    next_day = db.create_meet(
        "Tomorrow", ["Coach Alex"], competition_id=meet["competition_id"],
        competition_date="2026-10-03", day_status="scheduled",
    )
    with db._connection() as conn:
        conn.execute("UPDATE meets SET day_status = 'closed' WHERE id = ?", (meet["id"],))
    before = db.get_meet_revision(meet["id"])
    with db._connection() as conn:
        conn.execute("UPDATE meets SET day_status = 'active' WHERE id = ?", (next_day["id"],))
    activated = db.get_meet_revision(meet["id"])
    assert activated != before
    with db._connection() as conn:
        conn.execute("UPDATE competitions SET status = 'closed' WHERE id = ?", (meet["competition_id"],))
    assert db.get_meet_revision(meet["id"]) != activated


def test_training_observation_alone_does_not_refresh_operational_board(context):
    db, meet, *_ = context
    hub = training.start_training(db, meet["id"], coach_names=["Coach Alex"], actor="Coach Alex")
    run = training.join_training(db, hub["id"], "Coach Alex")
    before = db.get_meet_revision(run["id"])
    with db._connection() as conn:
        row = training._row(conn, run["id"])
        state = training._load(row["state_json"], {})
        state.setdefault("participants", {})["Coach Alex"] = {"views": ["My Group"]}
        training._persist(conn, row, state)
    assert db.get_meet_revision(run["id"]) == before


def test_polling_retains_cross_event_help_and_availability_detection(context):
    db, meet, _, other_event, *_ = context
    db.merge_import(other_event["id"], [
        {"athlete_id": "cross-event", "name": "CROSS Athlete", "phase": "pools", "strip": "J4", "pool": "1"},
    ], "Coach Alex")
    athlete = db.list_athletes(other_event["id"])[0]
    before = db.get_meet_revision(meet["id"])
    db.request_help(other_event["id"], athlete["id"], "Coach Jordan", location="J4")
    help_revision = db.get_meet_revision(meet["id"])
    assert help_revision != before
    db.set_coach_availability(meet["id"], "Coach Alex", True, "Coach Alex")
    assert db.get_meet_revision(meet["id"]) != help_revision
