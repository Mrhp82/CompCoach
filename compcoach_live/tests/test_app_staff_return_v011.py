"""Remembered browser data is reauthorized before staff routing resumes."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live import remembered_access, training
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import APP_PATH, query_value


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "staff-return.sqlite"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach.example")
    for variable in ("COMPCOACH_DATABASE_URL", "COMPCOACH_REQUIRE_CLOUD"):
        monkeypatch.delenv(variable, raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _meet(database):
    return database.create_meet("October NAC", ["Carmine", "Taylor"], ["Jordan"],
                                first_event_name="Cadet Epee")


def _candidate(meet, role="admin", actor="Carmine", **changes):
    return {"event": meet["id"], "token": meet[f"{role}_token"], "who": actor,
            "role": role, "relative_url": "unused by server routing", **changes}


def _restore(monkeypatch, candidate):
    calls, cleared = [], []

    def restore(*, force_home=False):
        calls.append(force_home)
        return None if force_home else candidate

    monkeypatch.setattr(remembered_access, "restore_remembered_access", restore)
    monkeypatch.setattr(remembered_access, "clear_remembered_access", lambda: cleared.append(True))
    return calls, cleared


def _assert_pin_only(app):
    assert not app.exception
    assert not query_value(app, "event") and not query_value(app, "token")
    assert any(widget.label == "Admin PIN" for widget in app.text_input)
    assert not any(item.value == "Who are you?" for item in app.subheader)
    assert not app.get("button_group")


@pytest.mark.parametrize("role,actor", [("admin", "Carmine"), ("coach", "Taylor"), ("coordinator", "Jordan")])
def test_verified_staff_candidate_resumes_identity_role_and_board_without_pin(database, monkeypatch, role, actor):
    meet = _meet(database)
    calls, cleared = _restore(monkeypatch, _candidate(meet, role, actor))
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    assert not app.exception
    assert query_value(app, "event") == meet["id"]
    assert query_value(app, "token") == meet[f"{role}_token"]
    assert query_value(app, "who") == actor
    assert not any(widget.label == "Admin PIN" for widget in app.text_input)
    assert not any(item.value == "Who are you?" for item in app.subheader)
    assert any(f"You are {actor}" in item.value for item in app.markdown)
    assert calls == [False] and cleared == []


def test_archived_admin_candidate_keeps_saved_day_instead_of_following_new_day(database, monkeypatch):
    meet = _meet(database)
    database.prepare_next_day(meet["id"], "October NAC Day 2", "2026-10-15", ["Junior Epee"], "Carmine")
    calls, cleared = _restore(monkeypatch, _candidate(meet, archive="1"))
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    assert not app.exception
    assert query_value(app, "event") == meet["id"] and query_value(app, "archive") == "1"
    assert query_value(app, "who") == "Carmine"
    assert calls == [False] and not cleared


@pytest.mark.parametrize("invalid", ["unknown_meet", "invalid_token", "replaced_token", "claimed_admin_with_coach_token"])
def test_unknown_or_replaced_capability_cannot_grant_staff_access(database, monkeypatch, invalid):
    meet = _meet(database)
    candidate = _candidate(meet)
    if invalid == "unknown_meet":
        candidate["event"] = "missing-meet"
    elif invalid == "invalid_token":
        candidate["token"] = "wrong-capability"
    elif invalid == "replaced_token":
        candidate = _candidate(meet, "coach", "Taylor")
        database.rotate_meet_token(meet["id"], "coach")
    else:
        candidate["token"] = meet["coach_token"]
    calls, cleared = _restore(monkeypatch, candidate)
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    _assert_pin_only(app)
    assert calls == [False] and cleared == [True]


@pytest.mark.parametrize("role,actor", [("coach", "Taylor"), ("coordinator", "Jordan")])
def test_removed_staff_identity_cannot_resume_even_with_valid_role_token(database, monkeypatch, role, actor):
    meet = _meet(database)
    candidate = _candidate(meet, role, actor)
    database.update_meet_staff(meet["id"], name=meet["name"], active_coaches=["Carmine"],
                              coordinators=[], timezone_name=meet["timezone"])
    calls, cleared = _restore(monkeypatch, candidate)
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    _assert_pin_only(app)
    assert calls == [False] and cleared == [True]


@pytest.mark.parametrize("kind", ["hub", "personal_course"])
def test_training_record_is_never_used_as_staff_return_destination(database, monkeypatch, kind):
    source = _meet(database)
    hub = training.start_training(database, source["id"], actor="Carmine", open_entry=True)
    target = hub if kind == "hub" else training.join_training(database, hub["id"], "Taylor")
    calls, cleared = _restore(monkeypatch, _candidate(target))
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    _assert_pin_only(app)
    assert calls == [False] and cleared == [True]


def test_deliberate_home_suppresses_remembered_redirect_and_preserves_pin(database, monkeypatch):
    meet = _meet(database)
    calls, cleared = _restore(monkeypatch, _candidate(meet))
    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.query_params["home"] = "1"
    app.run()
    _assert_pin_only(app)
    assert query_value(app, "home") == "1"
    assert calls == [True] and not cleared
