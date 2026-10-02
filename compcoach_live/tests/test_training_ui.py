"""Practice entry points remain clear and separate from real coach actions."""

from copy import deepcopy

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB, CompCoachError


class PracticeEngine:
    def __init__(self):
        self.starts = []
        self.restarts = []
        self.stops = []
        self.error = None
        self.metadata = {
            "meet_id": "practice-id",
            "status": "running",
            "stage_index": 3,
            "stage_count": 16,
            "stage_title": "Finish your pool",
            "instruction": "Open My Group and enter one pool result.",
            "hint": "Use the tapping numbers for wins and losses.",
            "scenario_message": "Your pool has just finished.",
            "last_feedback": "Help request acknowledged.",
            "actor_tasks": {"Morgan": "Morgan: open My Group and enter 3 wins / 3 losses."},
            "progress": 3 / 16,
            "participants": [{"name": "Morgan", "views": ["My Group"], "milestones": ["help"]}],
        }

    def start_training(self, db, **kwargs):
        if self.error:
            raise self.error
        self.starts.append(deepcopy(kwargs))
        return {"id": "practice-id", "name": "Practice"}

    def get_training(self, db, meet_id):
        return deepcopy(self.metadata)

    def restart_training(self, db, meet_id, actor):
        if self.error:
            raise self.error
        self.restarts.append((meet_id, actor))
        self.metadata["status"] = "running"
        self.metadata["stage_index"] = 0
        self.metadata["stage_title"] = "Meet your group"
        return {"id": "practice-id", "name": "Practice"}

    def stop_training(self, db, meet_id, actor):
        if self.error:
            raise self.error
        self.stops.append((meet_id, actor))
        self.metadata["status"] = "stopped"
        return deepcopy(self.metadata)


@pytest.fixture
def practice(tmp_path, monkeypatch):
    from compcoach_live import training_ui

    path = tmp_path / "training-ui.db"
    database = CompCoachDB(path)
    source = database.create_meet("Real competition", ["Morgan", "Taylor"], ["Avery"])
    engine = PracticeEngine()
    monkeypatch.setattr(training_ui, "training", engine)
    return str(path), database, source, engine


def _render_start(path, source_id):
    from compcoach_live.storage import CompCoachDB
    from compcoach_live.training_ui import render_training_start
    import streamlit as st

    database = CompCoachDB(path)
    source = database.get_meet(source_id) if source_id else None

    def opened(meet):
        st.session_state["training_test_opened"] = meet["id"]

    render_training_start(database, source, "Avery", opened)


def _render_panel(path, source_id, role, actor):
    from compcoach_live.storage import CompCoachDB
    from compcoach_live import training_ui
    import streamlit as st

    database = CompCoachDB(path)
    meet = database.get_meet(source_id)

    def opened(meet):
        st.session_state["training_test_opened"] = meet["id"]

    def home():
        st.session_state["training_test_home"] = True

    training_ui.render_training_panel(
        database, meet, training_ui.training.get_training(database, source_id),
        role, actor, open_callback=opened, home_callback=home,
    )


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def _text(app):
    return "\n".join([
        *(item.value for item in app.markdown),
        *(getattr(item.proto, "body", "") for item in app.get("html")),
        *(item.value for item in app.caption),
        *(item.value for item in app.info),
        *(item.value for item in app.success),
        *(item.value for item in app.error),
    ])


def test_activation_opens_free_name_entry_without_touching_source(practice):
    path, database, source, engine = practice
    source_before = database.get_meet(source["id"])
    app = AppTest.from_function(_render_start, args=(path, source["id"])).run()
    assert not app.exception
    assert not app.multiselect and not app.text_area
    assert app.radio[0].value == 7
    app.radio[0].set_value(14)
    _button(app, "Activate autonomous practice").click().run()

    assert not app.exception
    assert engine.starts == [{
        "source_meet_id": source["id"],
        "coach_names": [], "open_entry": True,
        "actor": "Avery", "duration_days": 14,
    }]
    assert app.session_state["training_test_opened"] == "practice-id"
    assert database.get_meet(source["id"]) == source_before
    assert "practice link" in _text(app)


def test_empty_real_roster_does_not_block_autonomous_practice(practice):
    path, _, source, engine = practice
    app = AppTest.from_function(_render_start, args=(path, source["id"])).run()
    assert not app.multiselect
    _button(app, "Activate autonomous practice").click().run()
    assert not app.exception
    assert engine.starts[0]["coach_names"] == []
    assert engine.starts[0]["open_entry"] is True
    assert not app.error


def test_home_practice_uses_free_name_entry_without_existing_day(practice):
    path, _, _, engine = practice
    app = AppTest.from_function(_render_start, args=(path, None)).run()
    _button(app, "Activate autonomous practice").click().run()
    assert not app.exception
    assert engine.starts[0]["source_meet_id"] is None
    assert engine.starts[0]["coach_names"] == []
    assert engine.starts[0]["open_entry"] is True


def test_practice_activation_does_not_read_or_modify_real_coach_directory(practice):
    path, database, source, engine = practice
    directory_before = database.list_coaches()
    source_before = database.get_meet(source["id"])
    app = AppTest.from_function(_render_start, args=(path, source["id"])).run()
    assert not app.exception
    assert not app.multiselect and not app.text_area
    _button(app, "Activate autonomous practice").click().run()

    assert not app.exception
    assert engine.starts[0]["coach_names"] == []
    assert engine.starts[0]["open_entry"] is True
    assert database.list_coaches() == directory_before
    assert database.get_meet(source["id"]) == source_before


def test_new_install_opens_free_practice_without_preselected_names(tmp_path, monkeypatch):
    from compcoach_live import training_ui

    path = tmp_path / "first-practice.db"
    database = CompCoachDB(path)
    engine = PracticeEngine()
    monkeypatch.setattr(training_ui, "training", engine)
    app = AppTest.from_function(_render_start, args=(str(path), None)).run()
    assert not app.exception
    assert not app.multiselect and not app.text_area
    assert database.list_coaches() == []
    _button(app, "Activate autonomous practice").click().run()
    assert not app.exception
    assert engine.starts[0]["coach_names"] == []
    assert engine.starts[0]["open_entry"] is True
    assert database.list_coaches() == []


@pytest.mark.parametrize("role", ["coach", "coordinator"])
def test_coach_guidance_uses_real_workflow_and_hides_admin_controls(practice, role):
    path, _, source, _ = practice
    app = AppTest.from_function(_render_panel, args=(path, source["id"], role, "Morgan")).run()
    assert not app.exception
    if role == "coach":
        assert 'class="cc-practice-guide"' in _text(app)
        assert "Step 4 of 16" in _text(app) and "Finish your pool" in _text(app)
    else:
        assert "TRAINING · Practice only" in _text(app)
        assert "Step 4 of 16 · Finish your pool" in _text(app)
    assert "Morgan: open My Group and enter 3 wins / 3 losses." in _text(app)
    assert [button.label for button in app.button] == (["Restart my practice"] if role == "coach" else [])
    assert not any(expander.label == "Practice management" for expander in app.expander)
    assert [expander.label for expander in app.expander] == (
        ["Need a hint?", "Practice options"] if role == "coach" else ["Need a hint?"]
    )
    assert "Your pool has just finished." in "\n".join(item.value for item in app.expander[0].info)


def test_admin_management_ends_practice_without_instructor_progress_buttons(practice):
    path, _, source, engine = practice
    app = AppTest.from_function(_render_panel, args=(path, source["id"], "admin", "Avery")).run()
    assert not app.exception
    assert [button.label for button in app.button] == ["End practice access"]
    _button(app, "End practice access").click().run()
    assert not app.exception
    assert engine.stops == [("practice-id", "Avery")]
    assert app.session_state["training_test_home"] is True
    assert "exercise has ended" in _text(app)


def test_completed_coach_can_restart_own_practice_with_same_link(practice):
    path, _, source, engine = practice
    engine.metadata["status"] = "completed"
    app = AppTest.from_function(_render_panel, args=(path, source["id"], "coach", "Morgan")).run()
    assert not app.exception
    assert "Skills practiced: help" in _text(app)
    assert [button.label for button in app.button] == ["Practice again"]
    _button(app, "Practice again").click().run()
    assert not app.exception
    assert engine.restarts == [("practice-id", "Morgan")]
    assert app.session_state["training_test_opened"] == "practice-id"
    assert "Step 1 of 16" in _text(app) and "Meet your group" in _text(app)
    # AppTest can retain the previous button tree after a full rerun. A fresh
    # phone session reads the saved running state and must not offer restart.
    reopened = AppTest.from_function(_render_panel, args=(path, source["id"], "coach", "Morgan")).run()
    assert not reopened.exception
    assert [button.label for button in reopened.button] == ["Restart my practice"]


def test_running_coach_can_restart_personal_course_without_admin(practice):
    path, _, source, engine = practice
    engine.metadata.update(kind="run", learner="Morgan")
    app = AppTest.from_function(_render_panel, args=(path, source["id"], "coach", "Morgan")).run()
    assert not app.exception
    assert "other coaches keep their progress" in _text(app)
    _button(app, "Restart my practice").click().run()
    assert not app.exception
    assert engine.restarts == [("practice-id", "Morgan")]
    assert engine.stops == []
    assert app.session_state["training_test_opened"] == "practice-id"
    assert "Step 1 of 16" in _text(app) and "Meet your group" in _text(app)


def test_expired_completed_coach_cannot_restart_from_ui(practice):
    path, _, source, engine = practice
    engine.metadata.update(status="completed", expired=True)
    app = AppTest.from_function(_render_panel, args=(path, source["id"], "coach", "Morgan")).run()
    assert not app.exception
    assert app.button == []
    assert "exercise has ended" in _text(app)


def test_hub_shows_independent_progress_instead_of_a_coach_task(practice):
    path, _, source, engine = practice
    engine.metadata.update(
        kind="hub", is_hub=True, expires_at="2026-10-15T20:00:00+00:00",
        participants=[
            {"name": "Morgan", "status": "completed", "milestones": ["help"]},
            {"name": "Taylor", "status": "running", "stage_index": 8, "stage_title": "Take over"},
            {"name": "Riley", "views": [], "milestones": []},
        ],
    )
    app = AppTest.from_function(_render_panel, args=(path, source["id"], "admin", "Avery")).run()
    assert not app.exception
    assert "Autonomous practice is open" in _text(app)
    assert "You can leave the app" in _text(app)
    assert "Morgan** · ✅ Course complete" in _text(app)
    assert "Taylor** · Step 9 · Take over" in _text(app)
    assert "Riley** · Not started yet" in _text(app)
    assert "Oct 15, 2026 · 1:00 PM" in _text(app)
    assert "Step 4 of 16 · Finish your pool" not in _text(app)


def test_backend_validation_is_readable_and_does_not_navigate(practice):
    path, _, source, engine = practice
    engine.error = CompCoachError("Practice access has expired.")
    app = AppTest.from_function(_render_start, args=(path, source["id"])).run()
    _button(app, "Activate autonomous practice").click().run()
    assert not app.exception
    assert "Practice access has expired." in _text(app)
    assert engine.starts == []
