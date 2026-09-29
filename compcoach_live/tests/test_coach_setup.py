from compcoach_live.coach_setup import _coach_option, _coach_state
from compcoach_live.storage import CompCoachDB


def test_coach_state_joins_directory_roles_day_and_assignment_use(tmp_path):
    db = CompCoachDB(tmp_path / "coach-setup.db")
    meet = db.create_meet(
        "October NAC",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Men's Epee",
    )
    second = db.add_meet_event(meet["id"], "Cadet Women's Epee")
    first = db.list_meet_events(meet["id"])[0]
    coaches = {row["name"]: row for row in db.list_coaches()}

    db.set_competition_coach_roles(
        meet["competition_id"], coaches["Sam"]["id"], ["coach", "admin"]
    )
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
        presence_status="scheduled",
        home_event_id=second["id"],
    )
    db.assign_pod(
        second["id"],
        phase="pools",
        pod="B",
        main_coach="Carmine",
        side_coach="",
        actor="Irina",
    )
    inactive = db.create_coach("Retired Coach", active=False)

    state = _coach_state(db, db.get_meet(meet["id"]))
    by_name = {row["name"]: row for row in state["coaches"]}

    assert state["competition_id"] == meet["competition_id"]
    assert [row["name"] for row in state["events"]] == [
        "Cadet Men's Epee",
        "Cadet Women's Epee",
    ]
    assert set(by_name["Sam"]["roles"]) == {"coach", "admin"}
    assert by_name["Sam"]["presence_status"] == "scheduled"
    assert by_name["Sam"]["home_event_name"] == "Cadet Women's Epee"
    assert by_name["Carmine"]["assignment_count"] == 1
    assert by_name["Carmine"]["is_used"] is True
    assert by_name["Retired Coach"]["id"] == inactive["id"]
    assert by_name["Retired Coach"]["in_competition"] is False
    assert state["coaches"][-1]["name"] == "Retired Coach"
    assert [row["name"] for row in state["roster"]][:2] == [
        "Irina",
        "Carmine",
    ]


def test_coach_option_marks_status_usage_and_directory_scope():
    assert _coach_option(
        {
            "name": "Sam",
            "is_active": True,
            "presence_status": "present",
            "assignment_count": 2,
            "in_competition": True,
        }
    ) == "Sam · Present · 2 assignments"
    assert _coach_option(
        {
            "name": "Taylor",
            "is_active": False,
            "presence_status": None,
            "assignment_count": 0,
            "in_competition": False,
        }
    ) == "Taylor · inactive · not in this competition"
