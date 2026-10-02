"""A typed name starts an isolated practice; retry keys never join by name."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live import training
from compcoach_live.storage import CompCoachDB, CompCoachError, EventLockedError


@pytest.fixture
def db(tmp_path):
    return CompCoachDB(tmp_path / "free-entry.sqlite")


def open_hub(db, source_id=None):
    return training.start_training(
        db, source_meet_id=source_id, coach_names=[], actor="Admin",
        duration_days=14, open_entry=True,
    )


def test_same_display_name_starts_separate_courses_without_real_directory_changes(db):
    real = db.create_meet("Actual competition", ["Actual Coach"], coordinators=[])
    real_before = db.get_meet(real["id"])
    directory_before = db.list_coaches()
    hub = open_hub(db, real["id"])
    first = training.join_training(db, hub["id"], "  Coach   Jamie  ", participant_key=uuid4().hex)
    training.tick_training(db, first["id"], actor="Coach Jamie", view="My Group")
    second = training.join_training(db, hub["id"], "Coach Jamie", participant_key=uuid4().hex)

    assert first["id"] != second["id"]
    assert first["coach_token"] != second["coach_token"]
    assert len({real["competition_id"], hub["competition_id"], first["competition_id"], second["competition_id"]}) == 4
    assert training.get_training(db, first["id"])["stage"] == 1
    assert training.get_training(db, second["id"])["stage"] == 0
    participants = training.get_training(db, hub["id"])["participants"]
    assert len(participants) == 2
    assert {person["name"] for person in participants} == {"Coach Jamie"}
    assert {person["run_meet_id"] for person in participants} == {first["id"], second["id"]}
    assert {person["stage_index"] for person in participants} == {0, 1}
    assert db.list_coaches() == directory_before
    assert db.get_meet(real["id"]) == real_before
    assert all(row["meet_id"] == real["id"] for row in db.list_assignment_history())


def test_parallel_submission_retries_one_key_only_create_one_course(db):
    hub = open_hub(db)
    key = uuid4()
    with ThreadPoolExecutor(max_workers=4) as workers:
        joined = list(workers.map(
            lambda _: training.join_training(db, hub["id"], "Taylor", participant_key=str(key)),
            range(4),
        ))
    assert len({run["id"] for run in joined}) == 1
    assert len(training.get_training(db, hub["id"])["participants"]) == 1
    retried = training.join_training(db, hub["id"], "Changed field", participant_key=key.hex)
    assert retried["id"] == joined[0]["id"]
    assert training.get_training(db, retried["id"])["learner"] == "Taylor"


def test_restart_retains_retry_identity_and_preserves_same_named_peer(db):
    hub = open_hub(db)
    key, peer_key = uuid4().hex, uuid4().hex
    run = training.join_training(db, hub["id"], "Robin", participant_key=key)
    peer = training.join_training(db, hub["id"], "Robin", participant_key=peer_key)
    training.tick_training(db, run["id"], "Robin", "My Group")
    training.tick_training(db, peer["id"], "Robin", "My Group")
    restarted = training.restart_training(db, run["id"], "Robin")
    assert restarted["coach_token"] == run["coach_token"]
    assert training.get_training(db, run["id"])["stage"] == 0
    assert training.get_training(db, peer["id"])["stage"] == 1
    assert training.join_training(db, hub["id"], "Robin", participant_key=key)["id"] == run["id"]
    assert len(training.get_training(db, hub["id"])["participants"]) == 2


def test_existing_invited_hub_accepts_new_typed_coaches_without_merging_legacy_progress(db):
    hub = training.start_training(db, coach_names=["Morgan"], actor="Admin")
    legacy = training.join_training(db, hub["id"], "Morgan")
    training.tick_training(db, legacy["id"], "Morgan", "My Group")
    new = training.join_training(db, hub["id"], "Morgan", participant_key=uuid4().hex)
    extra = training.join_training(db, hub["id"], "New Coach", participant_key=uuid4().hex)
    assert training.get_training(db, new["id"])["stage"] == 0
    assert new["id"] != legacy["id"]
    assert training.join_training(db, hub["id"], "MORGAN")["id"] == legacy["id"]
    assert {person["run_meet_id"] for person in training.get_training(db, hub["id"])["participants"]} == {
        legacy["id"], new["id"], extra["id"],
    }


@pytest.mark.parametrize("name", ["", "   ", "1234", "A" * 81, "Coach\x00Name"])
def test_free_entry_rejects_unreadable_names_before_creating_any_course(db, name):
    hub = open_hub(db)
    with pytest.raises(CompCoachError):
        training.join_training(db, hub["id"], name, participant_key=uuid4().hex)
    assert training.get_training(db, hub["id"])["participants"] == []


def test_invalid_retry_key_does_not_leak_input_in_error(db):
    hub = open_hub(db)
    value = "private retry value"
    with pytest.raises(CompCoachError) as error:
        training.join_training(db, hub["id"], "Avery", participant_key=value)
    assert value not in str(error.value)
    assert training.get_training(db, hub["id"])["participants"] == []


@pytest.mark.parametrize("reason", ["stop", "expiry"])
def test_free_entry_courses_follow_hub_stop_and_expiry_guards(db, reason):
    hub = open_hub(db)
    run = training.join_training(db, hub["id"], "Casey", participant_key=uuid4().hex)
    if reason == "stop":
        training.stop_training(db, hub["id"], "Admin")
    else:
        with db._connection() as conn:
            row = training._row(conn, hub["id"])
            state = training._load(row["state_json"], {})
            state["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            training._persist(conn, row, state)
        training.tick_training(db, run["id"], "Casey", "My Group")
    event = db.list_meet_events(run["id"])[0]
    athlete = db.list_athletes(event["id"])[0]
    with pytest.raises(EventLockedError):
        db.set_pool_result(event["id"], athlete["id"], wins=3, losses=3, actor="Casey")
    with pytest.raises(CompCoachError):
        training.join_training(db, hub["id"], "Other Coach", participant_key=uuid4().hex)
    with pytest.raises(CompCoachError):
        training.restart_training(db, run["id"], "Casey")


def _render_open_start(path):
    import streamlit as st
    from compcoach_live.storage import CompCoachDB
    from compcoach_live.training_ui import render_training_start

    render_training_start(
        CompCoachDB(path), None, "Admin",
        lambda meet: st.session_state.__setitem__("started_hub", meet["id"]),
    )


def test_practice_activation_requires_only_duration_and_never_a_coach_roster(tmp_path):
    path = tmp_path / "entry-ui.sqlite"
    app = AppTest.from_function(_render_open_start, args=(str(path),)).run()
    assert not app.exception
    assert not app.multiselect
    assert not app.text_area
    app.radio[0].set_value(14)
    next(button for button in app.button if button.label == "Activate autonomous practice").click().run()
    assert not app.exception
    db = CompCoachDB(path)
    metadata = training.get_training(db, app.session_state["started_hub"])
    assert metadata["is_hub"]
    assert metadata["state"]["duration_days"] == 14
    assert metadata["state"]["open_entry"] is True
    assert metadata["state"]["coaches"] == []
    assert db.list_coaches() == []


def _render_practice_guide(path, meet_id, metadata, role):
    from compcoach_live.storage import CompCoachDB
    from compcoach_live.training_ui import render_training_panel

    database = CompCoachDB(path)
    render_training_panel(database, database.get_meet(meet_id), metadata, role, "Jamie")


def test_mobile_practice_guide_keeps_next_action_fixed_with_reserved_space(db):
    hub = open_hub(db)
    run = training.join_training(db, hub["id"], "Jamie", participant_key=uuid4().hex)
    metadata = training.get_training(db, run["id"])
    app = AppTest.from_function(
        _render_practice_guide, args=(str(db.path), run["id"], metadata, "coach"),
    ).run()
    assert not app.exception
    markup = "\n".join(item.value for item in app.markdown)
    assert '<div class="cc-practice-guide"' in markup
    assert "position: fixed" in markup
    assert "safe-area-inset-top" in markup
    assert "padding-top:" in markup
    assert "--cc-guide-height: 110px" in markup
    assert "Step 1 of 18" in markup
    assert metadata["guide_instruction"] in markup
    assert not app.get("progress")
    assert "Need a hint?" in [item.label for item in app.expander]


@pytest.mark.parametrize("state", ["hub", "completed", "stopped", "expired", "admin"])
def test_fixed_guide_only_appears_in_an_active_learner_course(db, state):
    hub = open_hub(db)
    run = training.join_training(db, hub["id"], "Jamie", participant_key=uuid4().hex)
    target = hub if state == "hub" else run
    metadata = training.get_training(db, target["id"])
    if state in {"completed", "stopped"}:
        metadata["status"] = state
    elif state == "expired":
        metadata["expired"] = True
    app = AppTest.from_function(
        _render_practice_guide,
        args=(str(db.path), target["id"], metadata, "admin" if state == "admin" else "coach"),
    ).run()
    assert not app.exception
    assert not any('<div class="cc-practice-guide"' in item.value for item in app.markdown)


def test_fixed_guide_escapes_instruction_and_title_html(db):
    hub = open_hub(db)
    run = training.join_training(db, hub["id"], "Jamie", participant_key=uuid4().hex)
    metadata = training.get_training(db, run["id"])
    metadata.update(stage_title='<script>Title</script>', guide_instruction='Check <b>name</b> & strip.')
    app = AppTest.from_function(
        _render_practice_guide, args=(str(db.path), run["id"], metadata, "coach"),
    ).run()
    assert not app.exception
    markup = "\n".join(item.value for item in app.markdown)
    assert "Check &lt;b&gt;name&lt;/b&gt; &amp; strip." in markup
    assert "&lt;script&gt;Title&lt;/script&gt;" in markup
    assert "<script>Title</script>" not in markup


def test_pool_help_lesson_teaches_emergencies_and_keeps_simulated_request(db):
    hub = open_hub(db)
    run = training.join_training(db, hub["id"], "Jamie", participant_key=uuid4().hex)
    training.tick_training(db, run["id"], "Jamie", "My Group")
    lesson = training.tick_training(db, run["id"], "Jamie", "Live")
    assert lesson["stage_index"] == 2
    assert "emergency" in lesson["instruction"].lower()
    assert "only for a real emergency" in lesson["hint"]
    assert "missed several bouts" in lesson["scenario_message"]
    requests = [
        athlete for event in db.list_meet_events(run["id"])
        for athlete in db.list_athletes(event["id"]) if athlete["help_requested_at"]
    ]
    assert len(requests) == 1
