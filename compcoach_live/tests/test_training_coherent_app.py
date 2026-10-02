"""Public coach taps carry the course from entry through the first DE call.

No practice domain command, stage skip or direct state mutation drives these
lessons. This exercises phase transitions and Streamlit callback reruns as a
coach encounters them, rather than opening an artificially advanced lesson.
"""

from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st

from compcoach_live import training
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named,
    keyed,
    markdown_values,
    nav_named,
    open_board,
    query_value,
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "training-coherent-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _metadata(database, app):
    return training.get_training(database, query_value(app, "event"))


def _assert_stage(database, app, expected):
    metadata = _metadata(database, app)
    assert not app.exception
    assert metadata["stage"] == expected, {
        "expected": expected, "stage": metadata["stage"],
        "state": metadata["state"],
        "session": app.session_state.filtered_state,
        "errors": [item.value for item in app.error],
    }
    return metadata


def _target(database, app, key="primary_id"):
    metadata = _metadata(database, app)
    return next(
        row for event in database.list_meet_events(metadata["meet_id"])
        for row in database.list_athletes(event["id"])
        if row["id"] == metadata["state"][key]
    )


def _pool_editor(app, athlete_id):
    matches = [
        section for section in app.expander if section.label == "Add pool result"
        and any(athlete_id in (group.key or "") for group in section.get("button_group"))
    ]
    assert len(matches) == 1
    return matches[0]


def _join_and_finish_pools(database, *, restart=False):
    source = database.create_meet("Actual competition", ["Jordan", "Taylor"], [])
    hub = training.start_training(database, source["id"], actor="Jordan", open_entry=True)
    app = open_board(hub["id"], hub["coach_token"])
    next(item for item in app.text_input if item.label == "Your name").set_value("Casey")
    button_named(app, "Start my practice").click().run()
    assert query_value(app, "event") != hub["id"]
    _assert_stage(database, app, 1)
    if restart:
        # The public Restart control creates the no-response variation. No
        # production state or lesson boundary is changed directly by this test.
        button_named(app, "Restart my practice").click().run()
        own = database.get_meet(query_value(app, "event"))
        app = open_board(own["id"], own["coach_token"], "Casey")
        assert _assert_stage(database, app, 1)["state"]["variant"] == 1

    nav_named(app, "coach_nav_").set_value("Live").run()
    _assert_stage(database, app, 2)
    primary = _target(database, app)
    keyed(app.button, f"help_ack_{primary['id']}").click().run()
    _assert_stage(database, app, 3)

    nav_named(app, "coach_nav_").set_value("My Group").run()
    primary = _target(database, app)
    editor = _pool_editor(app, primary["id"])
    keyed(editor.get("button_group"), "pool_wins_").set_value(3)
    keyed(editor.get("button_group"), "pool_losses_").set_value(3)
    button_named(editor, "Save pool result").click().run()
    _assert_stage(database, app, 4)

    # AppTest retains form widgets from the old manual fragment tree after
    # Streamlit removed their versioned state. Reopen the same personal URL to
    # inspect the complete post-result page; this never skips a training step.
    own = database.get_meet(query_value(app, "event"))
    app = open_board(own["id"], own["coach_token"], "Casey")
    _assert_stage(database, app, 4)

    button_named(app, "I’m available to help").click().run()
    _assert_stage(database, app, 5)
    return app


@pytest.mark.parametrize("method,status,strip", [
    ("native", "on_deck", ""),
    ("native", "in_hole", ""),
    ("native", "on_deck", "G4"),
    ("quick", "on_deck", "G4"),
])
def test_first_team_call_after_public_pool_to_de_transition_counts_once(
    database, method, status, strip,
):
    app = _join_and_finish_pools(database)
    nav_named(app, "coach_nav_").set_value("Live").run()
    _assert_stage(database, app, 6)
    primary = _target(database, app)
    if method == "native":
        nav_named(app, "live_view_").set_value("Uncovered").run()
        base = f"live_live_{primary['id']}"
        if strip:
            keyed(app.text_input, f"{base}_strip_v").set_value(strip).run()
        keyed(app.button, f"{base}_call_{status}").click().run()
    else:
        keyed(app.selectbox, "call_athlete_").select(primary["id"]).run()
        nav_named(app, "call_status_").set_value("On Deck").run()
        keyed(app.text_input, "call_location_").set_value(strip).run()
        button_named(app, "Publish update").click().run()

    metadata = _assert_stage(database, app, 7)
    assert nav_named(app, "coach_nav_").value == "Live"
    saved = database.get_athlete(primary["event_id"], primary["id"])
    assert (saved["call_status"], saved["live_location"]) == (status, strip)
    actions = [
        action for action in database.recent_actions(primary["event_id"])
        if action["actor"] == "Casey" and action["action"] == "live_update"
        and action["athlete_id"] == primary["id"]
    ]
    assert len(actions) == 1
    assert any("Step 8 of" in element.proto.body for element in app.get("html"))
    assert "Call reported" in metadata["state"]["participants"]["Casey"]["milestones"]

    nav_named(app, "coach_nav_").set_value("My Group").run()
    _assert_stage(database, app, 7)
    assert database.get_athlete(primary["event_id"], primary["id"]) == saved
    assert not any(button.key == f"live_personal_{primary['id']}_call_{status}" for button in app.button)
    assert any(f"Actual strip <b>{strip or 'TBD'}</b>" in text for text in markdown_values(app))


@pytest.mark.parametrize("role,actor", [("coach", "Casey"), ("admin", "Jordan")])
def test_real_phase_start_and_first_team_call_are_shared_with_my_group(database, role, actor):
    meet = database.create_meet("Actual competition", ["Jordan", "Casey"], [])
    event = database.list_meet_events(meet["id"])[0]
    database.merge_import(
        event["id"], parse_pasted_table("Name\tStrip #\nEXAMPLE Riley\tB1").records, "Jordan",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_de_athletes(
        event["id"], [athlete["id"]], coaches=["Jordan", "Casey"], actor="Jordan",
    )
    controller = open_board(meet["id"], meet["admin_token"], "Jordan")
    button_named(controller, "Start Direct Elimination").click().run()
    assert database.get_phase_states(event["id"])["de"]["started"]

    app = controller if role == "admin" else open_board(meet["id"], meet["coach_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value("Uncovered").run()
    base = f"live_live_{athlete['id']}"
    keyed(app.text_input, f"{base}_strip_v").set_value("G4").run()
    keyed(app.button, f"{base}_call_on_deck").click().run()
    assert not app.exception and not app.error
    saved = database.get_athlete(event["id"], athlete["id"])
    assert (saved["call_status"], saved["live_location"]) == ("on_deck", "G4")
    nav_named(app, f"{role}_nav_").set_value("My Group").run()
    assert not app.exception
    assert database.get_athlete(event["id"], athlete["id"]) == saved
    assert any("Actual strip <b>G4</b>" in text for text in markdown_values(app))
    assert not any(button.key == f"live_personal_{athlete['id']}_call_on_deck" for button in app.button)


@pytest.mark.parametrize("restart,correct_before_bye,final_outcome", [
    pytest.param(False, False, "won", id="ordinary-response-final-win"),
    pytest.param(False, True, "lost", id="corrected-result-response-final-loss"),
    pytest.param(True, False, "lost", id="no-response-final-loss"),
])
def test_public_course_finishes_with_literal_controls(
    database, monkeypatch, restart, correct_before_bye, final_outcome,
):
    """Finish the entire public course with ordinary coach controls only."""
    from compcoach_live import refresh

    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training, "utc_now", lambda: clock[0].isoformat())

    class CourseClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0]

    monkeypatch.setattr(refresh, "datetime", CourseClock)
    app = _join_and_finish_pools(database, restart=restart)
    nav_named(app, "coach_nav_").set_value("Live").run()
    _assert_stage(database, app, 6)
    primary = _target(database, app)
    keyed(app.button, f"live_live_{primary['id']}_call_on_deck").click().run()
    _assert_stage(database, app, 7)
    nav_named(app, "coach_nav_").set_value("My Group").run()
    keyed(app.text_input, f"live_personal_{primary['id']}_strip_v").set_value("C3").run()
    arrival = keyed(app.button, f"live_personal_{primary['id']}_busy")
    assert arrival.label == f"I’m with {primary['name']}"
    arrival.click().run()
    busy_metadata = _assert_stage(database, app, 8)
    virtual_event = next(
        event for event in busy_metadata["state"]["pending_events"]
        if event["kind"] in {"other_coverage", "no_response"}
    )
    assert virtual_event["kind"] == ("no_response" if restart else "other_coverage")

    # A coach can finish before the virtual team responds. The saved result
    # must turn into a wait instruction instead of demanding another Won tap.
    keyed(app.button, f"current_won_{primary['id']}").click().run()
    waiting = _assert_stage(database, app, 8)
    assert "Your result" in waiting["guide_instruction"]
    assert "shortly" in waiting["guide_instruction"]
    assert not any(button.key == f"current_won_{primary['id']}" for button in app.button)
    button_named(app, "Open Team situation").click().run()
    assert nav_named(app, "coach_nav_").value == "Live"
    # Only the simulation clock advances. A normal observation runs the timer.
    clock[0] += timedelta(seconds=16)
    app.run()
    waiting = _assert_stage(database, app, 9)
    assert "wait for" in waiting["guide_instruction"]
    assert waiting["guide_view"] == "Live"
    own = database.get_meet(query_value(app, "event"))
    app = open_board(own["id"], own["coach_token"], "Casey")
    clock[0] += timedelta(seconds=9)
    app.run()
    secondary = _target(database, app, "secondary_id")
    takeover = next(
        button for button in app.button
        if secondary["id"] in (button.key or "") and button.label == "I’ll take over"
    )
    takeover.click().run()
    _assert_stage(database, app, 10)
    if nav_named(app, "coach_nav_").value != "My Group":
        button_named(app, "Open My Group").click().run()
    assert any(secondary["name"] in element.proto.body for element in app.get("html"))
    arrival = keyed(app.button, f"live_personal_{secondary['id']}_busy")
    assert arrival.label == f"I’m with {secondary['name']}"
    arrival.click().run()
    metadata = _assert_stage(database, app, 10)
    help_target = _target(database, app, "help_target_id")
    assert help_target["id"] != secondary["id"]
    assert help_target["call_status"] == "now"
    assert help_target["live_location"] == "K4"
    assert not help_target["covered_by"] and not help_target["takeover_coach"]
    guide = "\n".join(element.proto.body for element in app.get("html"))
    assert help_target["name"] in guide and "Need help" in guide
    assert help_target["name"] in metadata["instruction"]
    assert database.get_athlete(secondary["event_id"], secondary["id"])["covered_by"] == "Casey"

    keyed(app.button, f"help_request_{help_target['id']}").click().run()
    _assert_stage(database, app, 11)
    target_after = database.get_athlete(help_target["event_id"], help_target["id"])
    assert target_after["help_requested_by"] == "Casey" and target_after["help_requested_at"]
    secondary_after = database.get_athlete(secondary["event_id"], secondary["id"])
    assert secondary_after["covered_by"] == "Casey" and not secondary_after["help_requested_at"]

    # The virtual response follows the shared alert without an instructor tap.
    clock[0] += timedelta(seconds=6)
    app.run()
    helped = database.get_athlete(help_target["event_id"], help_target["id"])
    assert helped["help_acknowledged_by"] and helped["covered_by"]
    assert helped["covered_by"] != "Casey"
    keyed(app.button, f"current_lost_{secondary['id']}").click().run()
    metadata = _assert_stage(database, app, 12)
    assert database.get_athlete(secondary["event_id"], secondary["id"])["active_state"] == "eliminated"
    reassigned = _target(database, app, "reassigned_id")
    assert "Casey" not in reassigned["de_coaches"]
    request = next(
        row for row in database.list_coverage_requests(metadata["meet_id"], "Casey")
        if row["id"] == metadata["state"]["coverage_request_id"]
    )
    assert len(request["recipients"]) >= 2
    assert metadata["guide_target_id"] == reassigned["id"]
    accept = keyed(app.button, f"accept_coverage_request_{request['id']}")
    assert accept.label == "I'll cover this bout"
    accept.click().run()
    metadata = _assert_stage(database, app, 12)
    reserved = database.get_athlete(reassigned["event_id"], reassigned["id"])
    assert reserved["takeover_coach"] == "Casey" and not reserved["covered_by"]
    assert reserved["de_coaches"] == reassigned["de_coaches"]
    assert not any(button.key == f"accept_coverage_request_{request['id']}" for button in app.button)
    assert "I’m with" in metadata["guide_instruction"]
    arrival = keyed(app.button, f"live_personal_{reassigned['id']}_busy")
    assert arrival.label == f"I’m with {reassigned['name']}"
    arrival.click().run()
    metadata = _assert_stage(database, app, 12)
    assert "Won or Lost" in metadata["guide_instruction"]
    keyed(app.button, f"current_won_{reassigned['id']}").click().run()
    _assert_stage(database, app, 13)

    bye = _target(database, app, "bye_id")
    assert not bye["covered_by"] and not bye["takeover_coach"]
    assert bye["de_wins"] == bye["de_byes"] == 0
    if correct_before_bye:
        # The separate correction variant restores an accidental Lost through
        # the documented review controls before recording the requested Bye.
        keyed(app.button, f"lost_{bye['id']}").click().run()
        _assert_stage(database, app, 13)
        assert database.get_athlete(bye["event_id"], bye["id"])["active_state"] == "eliminated"
        next(box for box in app.selectbox if box.label == "Athlete to correct").select(bye["id"]).run()
        button_named(app, "Undo last result").click().run()
        button_named(app, "Confirm undo").click().run()
        _assert_stage(database, app, 13)
        restored = database.get_athlete(bye["event_id"], bye["id"])
        assert restored["active_state"] == "active" and restored["last_de_result"] == bye["last_de_result"]
    keyed(app.button, f"bye_{bye['id']}").click().run()
    metadata = _assert_stage(database, app, 14)
    passed = database.get_athlete(bye["event_id"], bye["id"])
    assert passed["de_byes"] == int(bye["de_byes"]) + 1
    assert passed["de_wins"] == bye["de_wins"]

    # The guide's own navigation shortcut opens the shared pairing controls.
    button_named(app, "Open Team situation").click().run()
    assert nav_named(app, "coach_nav_").value == "Live"
    pair_a = _target(database, app, "pair_a_id")
    pair_b = _target(database, app, "pair_b_id")
    assert pair_a["event_id"] == pair_b["event_id"]
    event_id = pair_a["event_id"]
    keyed(app.selectbox, f"live_first_{event_id}").set_value(pair_a["id"])
    keyed(app.selectbox, f"live_second_{event_id}").set_value(pair_b["id"])
    keyed(app.text_input, f"live_round_{event_id}").set_value("Practice round")
    review = next(
        button for button in app.button if button.label == "Review pairing"
        and event_id in (button.key or "")
    )
    review.click().run()
    _assert_stage(database, app, 14)
    keyed(app.button, f"live_create_confirm_{event_id}").click().run()
    _assert_stage(database, app, 15)
    button_named(app, f"{pair_a['name']} wins").click().run()
    _assert_stage(database, app, 16)
    assert database.get_athlete(pair_a["event_id"], pair_a["id"])["active_state"] == "active"
    assert database.get_athlete(pair_b["event_id"], pair_b["id"])["active_state"] == "eliminated"

    button_named(app, "Open My Group").click().run()
    final = _target(database, app, "final_id")
    assert final["call_status"] == "now" and final["live_location"] == "D4"
    arrival = keyed(app.button, f"live_personal_{final['id']}_busy")
    assert arrival.label == f"I’m with {final['name']}"
    arrival.click().run()
    _assert_stage(database, app, 16)
    keyed(app.button, f"current_{final_outcome}_{final['id']}").click().run()
    metadata = _assert_stage(database, app, 17)
    assert metadata["status"] == "completed"
    final_saved = database.get_athlete(final["event_id"], final["id"])
    assert final_saved["last_de_result"] == final_outcome
    assert final_saved["active_state"] == ("active" if final_outcome == "won" else "eliminated")
    assert database.get_meet(metadata["meet_id"])["ended_at"]
    assert any("Exercise complete!" in item.value for item in app.success)
    assert button_named(app, "Practice again")
    assert not any((button.key or "").startswith(("current_won_", "current_lost_", "live_personal_")) for button in app.button)
