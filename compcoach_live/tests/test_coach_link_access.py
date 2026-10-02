"""Public coach tokens require only coach identity, never the Admin PIN.

These app tests cover CompCoach's entry flow. Streamlit hosting visibility is
configured outside the application and cannot be verified by AppTest.
"""

import pytest
import streamlit as st
from streamlit.proto.TextInput_pb2 import TextInput

from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import button_named, nav_named, open_board
from compcoach_live.training import get_training, start_training


@pytest.fixture
def public_database(tmp_path, monkeypatch):
    path = tmp_path / "public-coach-link.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach-example.streamlit.app")
    for name in (
        "COMPCOACH_ADMIN_PIN", "COMPCOACH_DATABASE_URL", "COMPCOACH_REQUIRE_CLOUD",
        "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _assert_no_password_or_pin(app):
    assert not any("pin" in field.label.casefold() for field in app.text_input)
    assert not any(field.proto.type == TextInput.PASSWORD for field in app.text_input)
    assert not any("login" in button.label.casefold() or "sign in" in button.label.casefold() for button in app.button)
    assert not app.exception


def test_public_coach_link_opens_identity_and_normal_navigation_without_admin_pin(public_database):
    database = public_database
    meet = database.create_meet("Public competition", ["Casey", "Jordan"], ["Coordinator"])
    app = open_board(meet["id"], meet["coach_token"])

    assert any(item.value == "Who are you?" for item in app.subheader)
    assert {"Casey", "Jordan"} <= {button.label for button in app.button}
    assert not app.get("button_group")
    _assert_no_password_or_pin(app)

    button_named(app, "Casey").click().run()
    navigation = nav_named(app, "coach_nav_")
    assert navigation.options == ["My Group", "Live"]
    assert navigation.value == "My Group"
    _assert_no_password_or_pin(app)
    navigation.set_value("Live").run()
    assert nav_named(app, "coach_nav_").value == "Live"
    _assert_no_password_or_pin(app)
    nav_named(app, "coach_nav_").set_value("My Group").run()
    assert nav_named(app, "coach_nav_").value == "My Group"
    _assert_no_password_or_pin(app)


def test_public_practice_link_requires_only_name_and_starts_without_admin_pin(public_database):
    database = public_database
    hub = start_training(database, coach_names=[], actor="Admin", open_entry=True)
    app = open_board(hub["id"], hub["coach_token"])

    assert [field.label for field in app.text_input] == ["Your name"]
    assert [button.label for button in app.button] == ["Start my practice"]
    assert not app.selectbox and not app.multiselect
    _assert_no_password_or_pin(app)

    app.text_input[0].set_value("Coach Jamie")
    button_named(app, "Start my practice").click().run()
    event = app.query_params["event"]
    run_id = event[0] if isinstance(event, list) else event
    assert run_id != hub["id"]
    assert get_training(database, run_id)["learner"] == "Coach Jamie"
    assert nav_named(app, "coach_nav_").options == ["My Group", "Live"]
    _assert_no_password_or_pin(app)
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert nav_named(app, "coach_nav_").value == "Live"
    _assert_no_password_or_pin(app)
