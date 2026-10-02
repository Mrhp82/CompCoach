"""Exercise data stays identifiable and out of season coaching history."""

import json

import pytest

from compcoach_live import storage
from compcoach_live.storage import CompCoachDB, EventLockedError, utc_now


def _assigned_athlete(db, meet, name):
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(
        event["id"],
        [{"athlete_id": name.lower().replace(" ", "_"), "name": name, "phase": "pools", "strip": "B1", "pod": "B", "pool": "1"}],
        "Import",
    )
    athlete = db.list_athletes(event["id"])[0]
    db.assign_athletes(event["id"], [athlete["id"]], main_coach="Alex", actor="Admin")
    return event, db.get_athlete(event["id"], athlete["id"])


def _mark_training(db, meet, source=None):
    now = utc_now()
    with db._connection() as conn:
        conn.execute(
            "INSERT INTO training_sessions (meet_id, source_meet_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (meet["id"], source and source["id"], now, now),
        )


def test_training_schema_survives_restart_without_changing_real_day(tmp_path):
    path = tmp_path / "training.db"
    db = CompCoachDB(path)
    real = db.create_meet("October NAC", ["Alex"], [])
    training = db.create_meet("Training", ["Alex"], [])
    _mark_training(db, training, real)
    restarted = CompCoachDB(path)
    with restarted._connection() as conn:
        session = dict(conn.execute("SELECT * FROM training_sessions").fetchone())
    assert session["meet_id"] == training["id"]
    assert session["source_meet_id"] == real["id"]
    assert session["status"] == "running"
    assert session["stage"] == session["version"] == 0
    assert session["state_json"] == "{}"
    assert restarted.get_meet(real["id"]) == real


@pytest.mark.parametrize("ended", [False, True])
def test_season_history_excludes_training_including_finished_exercises(tmp_path, ended):
    db = CompCoachDB(tmp_path / "training.db")
    real = db.create_meet("October NAC", ["Alex"], [])
    training = db.create_meet("Training", ["Alex"], [])
    _mark_training(db, training, real)
    _, real_athlete = _assigned_athlete(db, real, "STONE Riley")
    training_event, training_athlete = _assigned_athlete(db, training, "RIVER Casey")
    if ended:
        db.finish_meet(training["id"], "Admin")
        with db._connection() as conn:
            conn.execute("UPDATE training_sessions SET status = 'finished' WHERE meet_id = ?", (training["id"],))

    ordinary = db.list_assignment_history()
    assert {row["athlete_id"] for row in ordinary} == {real_athlete["id"]}
    assert db.list_assignment_history(meet_id=training["id"]) == []
    assert db.list_assignment_history(competition_id=training["competition_id"]) == []
    assert db.list_assignment_history(event_id=training_event["id"]) == []
    assert db.list_assignment_history(athlete_id=training_athlete["id"]) == []
    assert len(db.list_assignment_history(coach_id=db.list_coaches()[0]["id"])) == 1

    all_history = db.list_assignment_history(include_training=True)
    assert len(all_history) == 2
    assert len(db.list_assignment_history(meet_id=training["id"], include_training=True)) == 1
    assert len(db.list_assignment_history(meet_id=training["id"], include_training=True, include_closed=False)) == (0 if ended else 1)


def test_training_marker_fk_does_not_prevent_source_day_removal(tmp_path):
    db = CompCoachDB(tmp_path / "training.db")
    source = db.create_meet("Real day", ["Alex"], [], first_event_name=None)
    training = db.create_meet("Training", ["Alex"], [], first_event_name=None)
    _mark_training(db, training, source)
    with db._connection() as conn:
        conn.execute("DELETE FROM meets WHERE id = ?", (source["id"],))
        assert conn.execute("SELECT source_meet_id FROM training_sessions").fetchone()[0] is None
        conn.execute("DELETE FROM meets WHERE id = ?", (training["id"],))
        assert conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0] == 0


def test_virtual_staff_assignments_and_coverage_do_not_enter_real_directory(tmp_path):
    path = tmp_path / "training.db"
    db = CompCoachDB(path)
    source = db.create_meet("Real day", ["Alex"], ["Coordinator"])
    before_directory = db.list_coaches()
    training = db.create_meet("Training", [], [])
    _mark_training(db, training, source)
    training = db.update_meet_staff(
        training["id"], name="Training",
        active_coaches=["Alex", "Virtual Jordan", "Virtual Taylor"],
        coordinators=["Virtual Morgan"], timezone_name="America/Los_Angeles",
    )
    event, athlete = _assigned_athlete(db, training, "RIVER Casey")
    db.assign_pod(event["id"], phase="pools", pod="B", main_coach="Virtual Jordan", side_coach="Virtual Taylor", actor="Virtual Morgan")
    db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Virtual Morgan", covered_by="Virtual Taylor")
    assert db.list_coaches() == before_directory
    virtual_history = [row for row in db.list_assignment_history(meet_id=training["id"], include_training=True) if row["coach_name"].startswith("Virtual")]
    assert virtual_history
    assert all(row["coach_id"] is None for row in virtual_history)
    real_history = [row for row in db.list_assignment_history(meet_id=training["id"], include_training=True) if row["coach_name"] == "Alex"]
    assert real_history[0]["coach_id"] == next(row["id"] for row in before_directory if row["name"] == "Alex")
    with db._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM day_coach_presence WHERE meet_id = ?", (training["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM competition_coaches WHERE competition_id = ?", (training["competition_id"],)).fetchone()[0] == 0

    restarted = CompCoachDB(path)
    assert restarted.list_coaches() == before_directory
    assert restarted.get_meet(source["id"]) == source
    assert len(restarted.list_assignment_history(meet_id=training["id"], include_training=True)) == len(db.list_assignment_history(meet_id=training["id"], include_training=True))


def _autonomous_course(db):
    source = db.create_meet("Real day", ["Alex"], [])
    hub = db.create_meet("Practice access", [], [], first_event_name=None)
    _mark_training(db, hub, source)
    run = db.create_meet("Alex's practice", ["Alex"], [])
    _mark_training(db, run, source)
    event, athlete = _assigned_athlete(db, run, "RIVER Casey")
    expires = "2026-10-08T12:00:00+00:00"
    with db._connection() as conn:
        conn.execute("UPDATE training_sessions SET state_json = ? WHERE meet_id = ?", (json.dumps({"kind": "hub", "expires_at": expires}), hub["id"]))
        conn.execute("UPDATE training_sessions SET state_json = ? WHERE meet_id = ?", (json.dumps({"kind": "run", "hub_meet_id": hub["id"], "expires_at": expires}), run["id"]))
    return source, hub, run, event, athlete


@pytest.mark.parametrize("now,allowed", [
    ("2026-10-08T11:59:59+00:00", True),
    ("2026-10-08T12:00:00+00:00", False),
    ("2026-10-08T12:00:01+00:00", False),
])
def test_expiration_boundary_rejects_stale_normal_controls_before_engine_tick(tmp_path, monkeypatch, now, allowed):
    db = CompCoachDB(tmp_path / "training.db")
    source, _, run, event, athlete = _autonomous_course(db)
    monkeypatch.setattr(storage, "utc_now", lambda: now)
    call = lambda: db.report_call(event["id"], athlete["id"], status="on_deck", location="", actor="Alex")
    availability = lambda: db.set_coach_availability(run["id"], "Alex", False, "Alex")
    if allowed:
        assert call()["call_status"] == "on_deck"
        assert availability()["is_available"] is False
    else:
        with pytest.raises(EventLockedError, match="expired"):
            call()
        with pytest.raises(EventLockedError, match="expired"):
            availability()
        assert db.get_athlete(event["id"], athlete["id"]) == athlete
    assert db.get_meet(source["id"]) == source


@pytest.mark.parametrize("parent_status", ["stopped", "completed"])
def test_stopped_parent_blocks_stale_personal_course_writes(tmp_path, monkeypatch, parent_status):
    db = CompCoachDB(tmp_path / "training.db")
    _, hub, run, event, athlete = _autonomous_course(db)
    monkeypatch.setattr(storage, "utc_now", lambda: "2026-10-02T12:00:00+00:00")
    with db._connection() as conn:
        conn.execute("UPDATE training_sessions SET status = ? WHERE meet_id = ?", (parent_status, hub["id"]))
    with pytest.raises(EventLockedError, match="ended"):
        db.report_call(event["id"], athlete["id"], status="now", location="C3", actor="Alex")
    with pytest.raises(EventLockedError, match="ended"):
        db.set_coach_availability(run["id"], "Alex", False, "Alex")
    assert db.get_athlete(event["id"], athlete["id"]) == athlete


def test_parent_expiration_blocks_course_even_with_later_copied_deadline(tmp_path, monkeypatch):
    db = CompCoachDB(tmp_path / "training.db")
    _, _, run, event, athlete = _autonomous_course(db)
    monkeypatch.setattr(storage, "utc_now", lambda: "2026-10-08T12:00:00+00:00")
    with db._connection() as conn:
        state = json.loads(conn.execute("SELECT state_json FROM training_sessions WHERE meet_id = ?", (run["id"],)).fetchone()[0])
        state["expires_at"] = "2026-10-15T12:00:00+00:00"
        conn.execute("UPDATE training_sessions SET state_json = ? WHERE meet_id = ?", (json.dumps(state), run["id"]))
    with pytest.raises(EventLockedError, match="expired"):
        db.report_call(event["id"], athlete["id"], status="on_deck", location="", actor="Alex")
