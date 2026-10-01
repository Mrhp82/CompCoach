"""Exercise coach creation through the same forms used on a phone."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_admin_v06_ui import _input_named, _open_settings
from compcoach_live.tests.test_app_smoke import button_named, nav_named, selectbox_named


DAY = "This day · Present"
COMPETITION = "This competition only"
DIRECTORY = "General directory only"


@pytest.mark.parametrize("scope", [DAY, COMPETITION, DIRECTORY])
def test_add_coach_with_existing_picker_selects_created_coach_once(
    tmp_path, monkeypatch, scope
):
    path = tmp_path / "add-coach.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "October NAC · Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Epee",
    )
    second = database.add_meet_event(meet["id"], "Junior Epee")
    first = database.list_meet_events(meet["id"])[0]
    database.merge_import(
        first["id"], parse_pasted_table("Name\tStrip #\tPool #\nTEST ATHLETE\tA1\t1").records,
        "Carmine",
    )
    original = {row["name"]: row for row in database.list_coaches()}
    database.set_competition_coach_roles(
        meet["competition_id"], original["Carmine"]["id"], ["coach", "admin"]
    )
    roles_before = database.list_competition_coaches(meet["competition_id"])

    app = _open_settings(meet, "Coaches")
    picker = selectbox_named(app, "Coach to manage")
    picker.select(next(option for option in picker.options if option.startswith("Sam"))).run()
    _input_named(app.text_input, "Coach name").set_value("Gregory")
    assert _input_named(app.radio, "Add to").value == DAY
    _input_named(app.radio, "Add to").set_value(scope)
    button_named(app, "Add coach").click().run()

    assert not app.exception
    created_rows = [row for row in database.list_coaches() if row["name"] == "Gregory"]
    assert len(created_rows) == 1
    created_id = created_rows[0]["id"]
    assert selectbox_named(app, "Coach to manage").value == created_id
    assert any("Gregory" in item.value for item in app.success)
    roles_after = database.list_competition_coaches(meet["competition_id"])
    assert [row for row in roles_after if row["coach_id"] != created_id] == roles_before
    created_roles = [row for row in roles_after if row["coach_id"] == created_id]
    assert [row["roles"] for row in created_roles] == (
        [["coach"]] if scope in {DAY, COMPETITION} else []
    )
    day_rows = [
        row for row in database.list_day_coaches(meet["id"])
        if row["coach_id"] == created_id
    ]
    assert [row["presence_status"] for row in day_rows] == (
        ["present"] if scope == DAY else []
    )
    assert ("Gregory" in database.get_meet(meet["id"])["active_coaches"]) == (scope == DAY)
    for child_id in [first["id"], second["id"]]:
        assert ("Gregory" in database.get_event(child_id)["active_coaches"]) == (scope == DAY)

    # Normal subsequent renders must keep the selection and not repeat creation.
    app.run()
    assert not app.exception
    assert selectbox_named(app, "Coach to manage").value == created_id
    assert len([row for row in database.list_coaches() if row["name"] == "Gregory"]) == 1

    # Retrying the same name must neither duplicate the directory entry nor
    # overwrite roles already given to that person.
    if scope != DIRECTORY:
        database.set_competition_coach_roles(
            meet["competition_id"], created_id, ["coach", "coordinator", "admin"]
        )
    _input_named(app.text_input, "Coach name").set_value("Gregory")
    _input_named(app.radio, "Add to").set_value(scope)
    button_named(app, "Add coach").click().run()
    assert not app.exception
    assert len([row for row in database.list_coaches() if row["name"] == "Gregory"]) == 1
    repeated_roles = [
        row for row in database.list_competition_coaches(meet["competition_id"])
        if row["coach_id"] == created_id
    ]
    assert [set(row["roles"]) for row in repeated_roles] == (
        [{"coach", "coordinator", "admin"}] if scope != DIRECTORY else []
    )

    if scope != DIRECTORY:
        # The next edit must affect Gregory, rather than the previous selection.
        edited_status = "present" if scope == DAY else "scheduled"
        selectbox_named(app, "Status today").set_value(edited_status)
        button_named(app, "Save today's status").click().run()
        assert not app.exception
        saved = next(
            row for row in database.list_day_coaches(meet["id"])
            if row["coach_id"] == created_id
        )
        assert saved["presence_status"] == edited_status
        sam = next(
            row for row in database.list_day_coaches(meet["id"])
            if row["coach_id"] == original["Sam"]["id"]
        )
        assert sam["presence_status"] == "present"

    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    assert not app.exception
    assert ("Gregory" in selectbox_named(app, "Main coach").options) == (scope == DAY)
    if scope == DAY:
        athlete = database.list_athletes(first["id"])[0]
        _input_named(app.multiselect, "Athletes").set_value([athlete["id"]])
        selectbox_named(app, "Main coach").set_value("Gregory").run()
        button_named(app, "Apply to 1 athlete").click().run()
        assert not app.exception
        assert database.list_athletes(first["id"])[0]["main_coach"] == "Gregory"

def _render_empty_directory(path, meet_id):
    from compcoach_live.coach_setup import render_coach_management
    from compcoach_live.storage import CompCoachDB

    database = CompCoachDB(path)
    render_coach_management(database, database.get_meet(meet_id), "Test admin")


def test_first_coach_is_selected_when_directory_was_empty(tmp_path):
    path = tmp_path / "first-coach.db"
    database = CompCoachDB(path)
    meet = database.create_meet("Empty staff", [], [], first_event_name=None)
    assert database.list_coaches() == []
    app = AppTest.from_function(
        _render_empty_directory, args=(str(path), meet["id"]), default_timeout=15
    ).run()
    assert not any(item.label == "Coach to manage" for item in app.selectbox)

    _input_named(app.text_input, "Coach name").set_value("Jordan")
    button_named(app, "Add coach").click().run()

    assert not app.exception
    coaches = database.list_coaches()
    assert [row["name"] for row in coaches] == ["Jordan"]
    assert selectbox_named(app, "Coach to manage").value == coaches[0]["id"]
    assert database.list_competition_coaches(meet["competition_id"])[0]["roles"] == ["coach"]
    assert database.list_day_coaches(meet["id"])[0]["presence_status"] == "present"
    assert database.get_meet(meet["id"])["active_coaches"] == ["Jordan"]
