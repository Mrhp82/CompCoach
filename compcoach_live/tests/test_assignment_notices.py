"""Planned assignments are implicit; obsolete receipts remain audit-only."""

import pytest

from compcoach_live.assignment_notices import assignment_notice_details
from compcoach_live.storage import CompCoachDB, CompCoachError


def test_ordinary_import_and_pod_assignments_never_alert(tmp_path):
    db = CompCoachDB(tmp_path / "implicit.sqlite")
    meet = db.create_meet("NAC", ["Alex", "Jordan"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "de", "name": "DE Athlete", "phase": "de", "pod": "C"}], "Casey")
    db.assign_de_pods(event["id"], pods=["C"], coaches=["Alex", "Jordan"], actor="Casey")
    db.merge_import(event["id"], [{"athlete_id": "new", "name": "NEW Athlete", "phase": "de", "pod": "C"}], "Casey")
    assert db.list_assignment_notices(meet["id"]) == []
    assert db.list_coverage_requests(meet["id"]) == []
    with db._connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS total FROM assignment_notices").fetchone()["total"] == 0


def test_old_receipts_survive_restart_but_cannot_be_accepted(tmp_path):
    db = CompCoachDB(tmp_path / "audit.sqlite")
    meet = db.create_meet("NAC", ["Alex"], ["Casey"])
    event = db.list_meet_events(meet["id"])[0]
    db.merge_import(event["id"], [{"athlete_id": "one", "name": "ONE Athlete", "phase": "de", "pod": "B"}], "Casey")
    athlete = db.list_athletes(event["id"])[0]
    with db._connection() as conn:
        conn.execute("INSERT INTO assignment_notices (id, meet_id, event_id, athlete_id, coach_name, coach_key, phase, created_at, created_by, accepted_at, accepted_by) VALUES ('old', ?, ?, ?, 'Alex', 'alex', 'de', '2026-10-02T10:00:00+00:00', 'Casey', '2026-10-02T10:01:00+00:00', 'Alex')", (meet["id"], event["id"], athlete["id"]))
    restarted = CompCoachDB(db.path)
    assert restarted.list_assignment_notices(meet["id"], "Alex", pending_only=False) == []
    with pytest.raises(CompCoachError, match="implicit"):
        restarted.accept_assignment_notice(meet["id"], "old", "Alex")
    with restarted._connection() as conn:
        row = conn.execute("SELECT * FROM assignment_notices WHERE id = 'old'").fetchone()
        assert row["accepted_by"] == "Alex" and row["accepted_at"]


def test_details_distinguish_actual_strip_from_planned_pod():
    assert assignment_notice_details({"event_name": "Junior ME", "phase": "de", "pod": "B", "source_strip": "B1", "live_location": "C3"}) == "Junior ME · Pod B · Actual strip C3"
