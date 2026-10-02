"""Exercise public coach attendance actions across the operational app views."""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_group_named,
    button_named,
    create_multi_event,
    keyed,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def pool_scenario(tmp_path, monkeypatch):
    path = tmp_path / "public-coach-attendance.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Epee", "Junior Epee"])
    cadet, junior = events
    database.merge_import(
        cadet["id"],
        parse_pasted_table(
            "Name\tStrip #\tPool #\n"
            "TEST Alex\tB1\t1\n"
            "TEST Jordan\tB2\t2\n"
            "TEST Taylor\tB3\t3"
        ).records,
        "Carmine",
    )
    database.merge_import(
        junior["id"],
        parse_pasted_table("Name\tStrip #\tPool #\nTEST Morgan\tP3\t3").records,
        "Carmine",
    )
    rows = {
        row["name"]: row
        for child in events
        for row in database.list_athletes(child["id"])
    }
    alex, jordan, morgan = (
        rows["TEST Alex"], rows["TEST Jordan"], rows["TEST Morgan"]
    )
    database.assign_athletes(
        cadet["id"], [alex["id"]], main_coach="Carmine", side_coach="Sam",
        actor="Carmine",
    )
    database.assign_athletes(
        cadet["id"], [jordan["id"], rows["TEST Taylor"]["id"]],
        main_coach="Sam", actor="Carmine",
    )
    database.assign_athletes(
        junior["id"], [morgan["id"]], main_coach="Sam", actor="Carmine",
    )
    database.request_help(junior["id"], morgan["id"], "Sam")
    return database, meet, cadet, junior, alex, jordan, morgan


def _athlete_button(app, label, athlete_id):
    matches = [
        button for button in app.button
        if button.label == label and athlete_id in (button.key or "")
    ]
    assert len(matches) == 1, (label, athlete_id, [b.key for b in matches])
    return matches[0]


def _absent_section(app, count):
    matches = [
        section for section in app.expander
        if section.label == f"Absent pool athletes · {count}"
    ]
    assert len(matches) == 1
    section = matches[0]
    assert not section.proto.expanded
    return section


def _active_card_names(app):
    return "\n".join(
        item.value for item in app.markdown if "class='cc-athlete'" in item.value
    )


def _help_markup(app):
    return "\n".join(markdown_values(app))


def test_coach_marks_pool_no_show_and_restores_without_losing_assignments(
    pool_scenario,
):
    database, meet, cadet, junior, alex, jordan, morgan = pool_scenario
    database.mark_absent(cadet["id"], jordan["id"], "Sam")
    other_event_before = database.get_athlete(junior["id"], morgan["id"])
    original = database.get_athlete(cadet["id"], alex["id"])
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert nav_named(app, "coach_nav_").value == "My Group"
    assert _athlete_button(app, "🚨 Need help now", alex["id"])
    assert button_group_named(app, "Wins")
    assert button_group_named(app, "Losses")

    _athlete_button(app, "Mark absent", alex["id"]).click().run()
    assert not app.exception
    assert database.get_athlete(cadet["id"], alex["id"])["participation_status"] == "active"
    _athlete_button(app, "Confirm absent", alex["id"]).click().run()
    assert not app.exception

    absent = database.get_athlete(cadet["id"], alex["id"])
    assert absent["participation_status"] == "absent"
    assert absent["active_state"] == "active"
    for field in ("main_coach", "side_coach", "pool_wins", "pool_losses"):
        assert absent[field] == original[field]
    assert database.get_athlete(junior["id"], morgan["id"]) == other_event_before
    actions = database.recent_actions(cadet["id"])
    attendance = [
        action for action in actions
        if action["athlete_id"] == alex["id"] and action["action"] == "participation_absent"
    ]
    assert len(attendance) == 1
    assert attendance[0]["actor"] == "Carmine"

    # AppTest retains removed fragment widgets after an app-scope rerun, while
    # their session-state keys are already gone. Inspect persistence above and
    # reopen the same public view for assertions on the complete page.
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    section = _absent_section(app, 1)
    assert "TEST Alex" in "\n".join(markdown_values(section))
    assert "TEST Jordan" not in "\n".join(markdown_values(section))
    assert not section.get("button_group")
    assert not any(b.label == "🚨 Need help now" for b in section.button)
    assert "TEST Alex" not in _active_card_names(app)
    assert not any(alex["id"] in (b.key or "") for b in app.button if b.label == "Mark absent")
    assert any("0 active work" in caption.value for caption in app.caption)
    assert "TEST Morgan · P3" in _help_markup(app)
    assert "Sam needs help" in _help_markup(app)

    # Another coach sees the same shared attendance state, with a recovery list
    # available in Live rather than an active coaching card.
    other = open_board(meet["id"], meet["coach_token"], "Sam")
    nav_named(other, "coach_nav_").set_value("Live").run()
    nav_named(other, "live_view_").set_value("No Current Call").run()
    assert not other.exception
    assert "TEST Alex" not in _active_card_names(other)
    assert "TEST Jordan" not in _active_card_names(other)
    assert "TEST Morgan" in _active_card_names(other)
    _absent_section(other, 2)

    # Admin assignment queues, the shared plan, and copied WhatsApp schedules
    # must all agree with the public coach's action.
    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Setup").run()
    nav_named(admin, "admin_setup_nav_").set_value("Assign").run()
    options = keyed(admin.multiselect, "selected_assign_").options
    assert all("TEST Alex" not in option for option in options)
    assert any("TEST Taylor" in option for option in options)
    nav_named(admin, "admin_nav_").set_value("Live").run()
    assignments = next(section for section in admin.expander if section.label == "All assignments")
    plan = "\n".join(markdown_values(assignments))
    assert "TEST Alex" not in plan
    assert "TEST Jordan" not in plan
    assert "TEST Morgan" in plan
    nav_named(admin, "admin_nav_").set_value("Share").run()
    schedule = "\n".join(code.value for code in admin.code)
    assert "TEST Alex" not in schedule
    assert "TEST Jordan" not in schedule
    assert "TEST Morgan" in schedule

    _athlete_button(app, "Restore to active list", alex["id"]).click().run()
    assert not app.exception
    restored = database.get_athlete(cadet["id"], alex["id"])
    assert restored["participation_status"] == "active"
    for field in ("main_coach", "side_coach", "pool_wins", "pool_losses"):
        assert restored[field] == original[field]
    assert _athlete_button(app, "🚨 Need help now", alex["id"])
    assert _athlete_button(app, "Mark absent", alex["id"])
    assert not any(section.label.startswith("Absent pool athletes") for section in app.expander)
    assert "TEST Morgan · P3" in _help_markup(app)
    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Share").run()
    schedule = "\n".join(code.value for code in admin.code)
    assert "TEST Alex" in schedule
    assert "TEST Jordan" not in schedule


@pytest.mark.parametrize(
    ("role", "actor"), [("coach", "Sam"), ("coordinator", "Irina"), ("admin", "Carmine")]
)
def test_all_staff_roles_can_cancel_or_confirm_pool_absence_on_live(
    pool_scenario, role, actor,
):
    database, meet, cadet, junior, alex, jordan, morgan = pool_scenario
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("No Current Call").run()
    before = database.get_athlete(cadet["id"], alex["id"])
    _athlete_button(app, "Mark absent", alex["id"]).click().run()
    _athlete_button(app, "Cancel", alex["id"]).click().run()
    assert not app.exception
    assert database.get_athlete(cadet["id"], alex["id"]) == before
    assert not any(
        "absence_pending" in name for name in app.session_state.filtered_state
    )
    app.run()
    assert not any(b.label == "Confirm absent" for b in app.button)

    _athlete_button(app, "Mark absent", alex["id"]).click().run()
    _athlete_button(app, "Confirm absent", alex["id"]).click().run()
    assert not app.exception
    assert database.get_athlete(cadet["id"], alex["id"])["participation_status"] == "absent"
    action = next(
        action for action in database.recent_actions(cadet["id"])
        if action["action"] == "participation_absent"
    )
    assert action["actor"] == actor
    # When the Admin's last assigned pool becomes absent, the availability
    # override checkbox disappears. AppTest retains that removed fragment
    # widget after the app rerun, even though Streamlit has cleaned its state.
    # Reopen the public Live view, as in the coach recovery test above, to
    # inspect the persisted absence and exercise its recovery controls.
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("No Current Call").run()
    _absent_section(app, 1)
    assert "TEST Alex" not in _active_card_names(app)
    assert "TEST Morgan · P3" in _help_markup(app)
    _athlete_button(app, "Restore to active list", alex["id"]).click().run()
    assert not app.exception
    assert database.get_athlete(cadet["id"], alex["id"])["participation_status"] == "active"
    assert _athlete_button(app, "Mark absent", alex["id"])


def test_completed_pool_results_survive_absence_and_restore_from_live(pool_scenario):
    database, meet, cadet, junior, alex, jordan, morgan = pool_scenario
    database.set_pool_result(cadet["id"], alex["id"], wins=3, losses=3, actor="Carmine")
    before = database.get_athlete(cadet["id"], alex["id"])
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert any(s.label == "Completed pool results · 1" for s in app.expander)
    nav_named(app, "coach_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("No Current Call").run()
    _athlete_button(app, "Mark absent", alex["id"]).click().run()
    _athlete_button(app, "Confirm absent", alex["id"]).click().run()
    assert not app.exception
    absent = database.get_athlete(cadet["id"], alex["id"])
    for field in ("main_coach", "side_coach", "pool_wins", "pool_losses", "pool_result_at", "pool_result_by"):
        assert absent[field] == before[field]
    nav_named(app, "coach_nav_").set_value("My Group").run()
    section = _absent_section(app, 1)
    assert not any(s.label.startswith("Completed pool results") for s in app.expander)
    _athlete_button(section, "Restore to active list", alex["id"]).click().run()
    assert not app.exception
    completed = next(s for s in app.expander if s.label == "Completed pool results · 1")
    assert button_group_named(completed, "Wins").value == 3
    assert button_group_named(completed, "Losses").value == 3
    assert button_named(completed, "Save pool result")
    assert database.get_athlete(cadet["id"], alex["id"])["pool_result_at"] == before["pool_result_at"]


def test_pool_absence_confirmation_is_invalidated_by_another_phone(pool_scenario):
    database, meet, cadet, junior, alex, jordan, morgan = pool_scenario
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    _athlete_button(app, "Mark absent", alex["id"]).click().run()
    database.set_pool_result(cadet["id"], alex["id"], wins=4, losses=2, actor="Sam")
    app.run()
    assert not app.exception
    assert not any(b.label == "Confirm absent" for b in app.button)
    saved = database.get_athlete(cadet["id"], alex["id"])
    assert saved["participation_status"] == "active"
    assert (saved["pool_wins"], saved["pool_losses"]) == (4, 2)
    assert not any(a["action"] == "participation_absent" for a in database.recent_actions(cadet["id"]))
