"""Coach names entered in the initial setup stay useful beyond that form."""

from datetime import timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


APP_PATH = str(Path(__file__).parents[1] / "app.py")


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "inline-coach.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _home():
    app = AppTest.from_file(APP_PATH, default_timeout=20)
    app.session_state["landing_admin_unlocked"] = True
    app.session_state["home_admin_actor"] = "Carmine"
    return app.run()


def _named(items, label):
    matches = [item for item in items if item.label == label]
    assert len(matches) == 1, (label, [item.label for item in items])
    return matches[0]


def _submit(app, coaches, coordinators=()):
    _named(app.text_input, "Competition or travel day").set_value("Inline staff test")
    _named(app.text_input, "First event (optional)").set_value("Cadet Epee")
    _named(app.multiselect, "Coaches present").set_value(list(coaches))
    _named(app.multiselect, "Coordinators").set_value(list(coordinators))
    _named(app.button, "Create competition").click().run()
    assert not app.exception


def test_new_competition_accepts_typed_coach_and_adds_day_presence(database):
    app = _home()
    picker = _named(app.multiselect, "Coaches present")
    assert picker.proto.accept_new_options
    assert "Jordan Reed" not in picker.options
    _submit(app, ["Jordan Reed"])

    meet = database.list_meets()[0]
    assert meet["active_coaches"] == ["Jordan Reed"]
    directory = database.list_coaches()
    assert [person["name"] for person in directory] == ["Jordan Reed"]
    day_staff = database.list_day_coaches(meet["id"])
    assert [(row["coach_id"], row["presence_status"]) for row in day_staff] == [
        (directory[0]["id"], "present")
    ]
    assert database.list_meet_events(meet["id"])[0]["active_coaches"] == ["Jordan Reed"]

    # Another setup offers the saved directory entry without typing it again.
    fresh = _home()
    assert "Jordan Reed" in _named(fresh.multiselect, "Coaches present").options


def test_new_competition_uses_directory_spelling_and_deduplicates_names(database):
    original = database.create_coach("Jordan Reed")
    app = _home()
    _submit(app, ["jordan reed", "  Jordan   Reed  ", "Morgan Cole"])

    meet = database.list_meets()[0]
    assert meet["active_coaches"] == ["Jordan Reed", "Morgan Cole"]
    assert {row["name"] for row in database.list_coaches()} == {"Jordan Reed", "Morgan Cole"}
    assert database.get_coach(original["id"])["name"] == "Jordan Reed"
    assert len(database.list_day_coaches(meet["id"])) == 2


def test_new_competition_accepts_typed_coordinator_without_changing_coach_role(database):
    app = _home()
    assert _named(app.multiselect, "Coordinators").proto.accept_new_options
    _submit(app, ["Jordan Reed"], ["  Avery   Stone  ", "avery stone"])

    meet = database.list_meets()[0]
    assert meet["active_coaches"] == ["Jordan Reed"]
    assert meet["coordinators"] == ["Avery Stone"]
    roles = {row["name"]: set(row["roles"]) for row in database.list_competition_coaches(meet["competition_id"])}
    assert roles == {"Jordan Reed": {"coach"}, "Avery Stone": {"coach", "coordinator"}}


def test_typing_coach_in_unsubmitted_competition_form_does_not_save(database):
    app = _home()
    _named(app.multiselect, "Coaches present").set_value(["Jordan Reed"]).run()
    assert not app.exception
    assert database.list_coaches() == []
    assert database.list_meets() == []


def test_explicitly_selecting_inactive_coach_reuses_and_reactivates_record(database):
    original = database.create_coach("Sam", active=False)
    app = _home()
    picker = _named(app.multiselect, "Coaches present")
    assert "Sam" not in picker.options
    assert "Sam" not in picker.value
    _submit(app, ["sam"])

    assert database.get_coach(original["id"])["is_active"] is True
    assert [row["name"] for row in database.list_coaches()] == ["Sam"]
    assert database.list_meets()[0]["active_coaches"] == ["Sam"]


def test_invalid_dates_do_not_add_or_reactivate_typed_coaches(database):
    original = database.create_coach("Sam", active=False)
    app = _home()
    start = _named(app.date_input, "First competition day").value
    _named(app.date_input, "Last competition day").set_value(start - timedelta(days=1))
    _submit(app, ["Sam", "Jordan Reed"])

    assert any("cannot be before" in item.value for item in app.error)
    assert database.get_coach(original["id"])["is_active"] is False
    assert [row["name"] for row in database.list_coaches()] == ["Sam"]
    assert database.list_meets() == []
