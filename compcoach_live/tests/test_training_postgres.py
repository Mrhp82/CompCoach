"""Autonomous practice uses the same isolated lifecycle on the cloud adapter."""

import pytest

from compcoach_live.storage import EventLockedError
from compcoach_live.training import (
    get_training, join_training, start_training, stop_training, tick_training,
)
from compcoach_live.tests.test_postgres_adapter import postgres_db
from compcoach_live.tests.test_training import (
    clock,
    test_full_course_without_admin_or_other_real_coach as _complete_course,
)


def test_cloud_practice_hub_and_personal_runs_preserve_real_competition(postgres_db):
    db = postgres_db
    source = db.create_meet("Real competition", ["Alex", "Robin"], first_event_name="Cadet Epee")
    original = db.get_meet(source["id"])
    directory = db.list_coaches()
    hub = start_training(db, source["id"], ["Alex", "Robin"], actor="Alex", duration_days=14)
    alex = join_training(db, hub["id"], "Alex")
    robin = join_training(db, hub["id"], "Robin")
    assert alex["id"] != robin["id"]
    assert join_training(db, hub["id"], "Alex")["id"] == alex["id"]
    assert get_training(db, alex["id"])["learner"] == "Alex"
    assert get_training(db, hub["id"])["is_hub"]
    assert db.get_meet(source["id"]) == original
    assert db.list_coaches() == directory
    assert not db.list_assignment_history()
    assert db.list_assignment_history(meet_id=alex["id"], include_training=True)

    tick_training(db, alex["id"], actor="Alex", view="My Group")
    athlete = db.list_athletes(db.list_meet_events(alex["id"])[0]["id"])[0]
    db.set_pool_result(athlete["event_id"], athlete["id"], wins=3, losses=3, actor="Alex")
    assert db.get_athlete(athlete["event_id"], athlete["id"])["pool_wins"] == 3
    assert all(row["pool_wins"] is None for event in db.list_meet_events(robin["id"]) for row in db.list_athletes(event["id"]))

    stop_training(db, hub["id"], actor="Alex")
    assert get_training(db, alex["id"])["status"] == "stopped"
    with pytest.raises(EventLockedError):
        db.set_pool_result(athlete["event_id"], athlete["id"], wins=4, losses=2, actor="Alex")
    assert db.get_meet(source["id"]) == original


def test_cloud_complete_course_with_no_virtual_coverage_and_losses(postgres_db, clock):
    _complete_course(postgres_db, clock, variant=1, outcome="lost")
