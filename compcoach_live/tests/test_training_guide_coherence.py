"""Every practice instruction leads to an ordinary, reachable coach action."""

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from compcoach_live import training
from compcoach_live.de_bouts import create_de_bout
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import keyed


APP_PATH = str(Path(__file__).parents[1] / "app.py")
CASE_NAMES = (
    "identity", "pool_plan", "shared_help", "pool_result", "available", "de_plan",
    "call", "actual_strip", "physical_coverage", "busy_result", "wait_for_call",
    "takeover", "takeover_arrival", "other_athlete_help", "second_result",
    "urgent_accept", "urgent_arrival", "urgent_result", "bye", "afm_pair",
    "afm_result", "final_arrival", "final_result", "complete",
)


class GuideDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.guides = []
        self.title = []
        self.action = []
        self.step = []
        self._mode = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = str(attrs.get("class") or "").split()
        if "cc-practice-guide" in classes:
            self.guides.append(attrs)
        for kind in ("title", "action", "step"):
            if f"cc-practice-guide-{kind}" in classes:
                self._mode = kind

    def handle_endtag(self, tag):
        if tag in {"span", "div"}:
            self._mode = None

    def handle_data(self, data):
        if self._mode:
            getattr(self, self._mode).append(data)


def _guide(app):
    dom = GuideDOM()
    for item in app.get("html"):
        dom.feed(str(item.proto.body))
    return dom


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    """Save realistic states reached through real commands, never fake UI taps."""
    folder = tmp_path_factory.mktemp("guide-course")
    database = CompCoachDB(folder / "course.sqlite")
    patch = pytest.MonkeyPatch()
    now = [datetime.now(timezone.utc).replace(microsecond=0)]
    patch.setattr(training, "utc_now", lambda: now[0].isoformat())
    hub = training.start_training(database, coach_names=[], actor="Admin", open_entry=True)
    run = training.join_training(database, hub["id"], "Coach Jamie", participant_key=uuid4().hex)
    learner = "Coach Jamie"
    result = {}

    def target(key):
        state = training.get_training(database, run["id"])["state"]
        return next(
            athlete for event in database.list_meet_events(run["id"])
            for athlete in database.list_athletes(event["id"]) if athlete["id"] == state[key]
        )

    def tick(stage, view="My Group"):
        metadata = training.tick_training(database, run["id"], learner, view)
        assert metadata["stage_index"] == stage
        return metadata

    def save(name):
        path = folder / f"{name}.sqlite"
        with database._connection() as source, sqlite3.connect(path) as destination:
            source.backup(destination)
        result[name] = {
            "path": path, "run": run, "metadata": training.get_training(database, run["id"]),
            "clock": now[0], "set_clock": lambda value: now.__setitem__(0, value),
        }

    save("identity")
    tick(1); save("pool_plan")
    tick(2, "Live"); save("shared_help")
    primary = target("primary_id")
    database.acknowledge_help(primary["event_id"], primary["id"], learner)
    tick(3); save("pool_result")
    database.set_pool_result(primary["event_id"], primary["id"], wins=3, losses=3, actor=learner)
    tick(4); save("available")
    database.set_coach_availability(run["id"], learner, True, learner)
    tick(5); save("de_plan")
    tick(6, "Live"); save("call")
    primary = target("primary_id")
    database.report_call(primary["event_id"], primary["id"], status="on_deck", location="", actor=learner)
    tick(7); save("actual_strip")
    database.report_call(primary["event_id"], primary["id"], status="now", location="C3", actor=learner)
    tick(7); save("physical_coverage")
    database.cover_athlete(primary["event_id"], primary["id"], learner, learner, location="C3")
    tick(8)
    now[0] += timedelta(seconds=16)
    tick(8); save("busy_result")
    database.mark_result(primary["event_id"], primary["id"], outcome="won", actor=learner)
    tick(9); save("wait_for_call")
    now[0] += timedelta(seconds=9)
    tick(9); save("takeover")
    secondary = target("secondary_id")
    database.take_over_athlete(secondary["event_id"], secondary["id"], learner, learner)
    tick(10); save("takeover_arrival")
    database.cover_athlete(secondary["event_id"], secondary["id"], learner, learner, location="J2")
    tick(10); save("other_athlete_help")
    help_target = target("help_target_id")
    assert help_target["id"] != secondary["id"]
    database.request_help(help_target["event_id"], help_target["id"], learner)
    tick(11); save("second_result")
    database.mark_result(secondary["event_id"], secondary["id"], outcome="won", actor=learner)
    tick(12); save("urgent_accept")
    request_id = training.get_training(database, run["id"])["state"]["coverage_request_id"]
    database.accept_coverage_request(run["id"], request_id, learner, actor=learner)
    tick(12); save("urgent_arrival")
    reassigned = target("reassigned_id")
    database.cover_athlete(reassigned["event_id"], reassigned["id"], learner, learner, location="E2")
    tick(12); save("urgent_result")
    database.mark_result(reassigned["event_id"], reassigned["id"], outcome="won", actor=learner)
    tick(13); save("bye")
    bye = target("bye_id")
    database.mark_bye(bye["event_id"], bye["id"], actor=learner)
    tick(14); save("afm_pair")
    pair_a, pair_b = target("pair_a_id"), target("pair_b_id")
    create_de_bout(database, pair_a["event_id"], pair_a["id"], pair_b["id"], actor=learner, round_label="Practice")
    tick(15, "Live"); save("afm_result")
    database.mark_result(pair_a["event_id"], pair_a["id"], outcome="won", actor=learner)
    tick(16); save("final_arrival")
    final = target("final_id")
    database.cover_athlete(final["event_id"], final["id"], learner, learner, location="D4")
    tick(16); save("final_result")
    database.mark_result(final["event_id"], final["id"], outcome="won", actor=learner)
    tick(17); save("complete")
    assert set(result) == set(CASE_NAMES)
    try:
        yield result
    finally:
        patch.undo()


def _panel(path, run_id, metadata, view="My Group", allow_navigation=False):
    from compcoach_live.training_ui import render_training_panel
    from compcoach_live.storage import CompCoachDB
    import streamlit as st
    database = CompCoachDB(path)

    def navigate(destination):
        st.session_state["guide_test_navigation"] = destination

    render_training_panel(
        database, database.get_meet(run_id), metadata, "coach", "Coach Jamie",
        current_view=view, navigate_callback=navigate if allow_navigation else None,
    )


@pytest.mark.parametrize("name", CASE_NAMES)
def test_all_course_steps_and_substeps_render_exact_engine_action_and_target(cases, name):
    case = cases[name]
    case["set_clock"](case["clock"])
    metadata = case["metadata"]
    app = AppTest.from_function(_panel, args=(str(case["path"]), case["run"]["id"], metadata)).run()
    assert not app.exception
    dom = _guide(app)
    if metadata["status"] == "completed":
        assert not dom.guides
        assert any(button.label == "Practice again" for button in app.button)
        return
    assert len(dom.guides) == 1
    assert "".join(dom.title) == metadata["stage_title"]
    assert "".join(dom.action) == metadata["guide_instruction"]
    assert f"Step {metadata['stage_index'] + 1} of {len(training.TRAINING_STEPS)}" in "".join(dom.step)
    assert metadata["stage_count"] == len(training.TRAINING_STEPS)
    assert dom.guides[0]["data-guide-view"] == metadata["guide_view"]
    assert dom.guides[0]["data-target-athlete"] == metadata["guide_target_id"]
    assert dom.guides[0]["data-target-name"] == metadata["guide_target_name"]
    if metadata["guide_target_name"]:
        assert metadata["guide_target_name"] in "".join(dom.action)
    assert "{primary_name}" not in "".join(dom.action) and "the practice athlete" not in "".join(dom.action)


@pytest.mark.parametrize("name", ["physical_coverage", "afm_pair"])
def test_navigation_shortcut_is_deliberate_and_never_advances_or_performs_coach_action(cases, name):
    case = cases[name]
    case["set_clock"](case["clock"])
    metadata = case["metadata"]
    database = CompCoachDB(case["path"])
    before = training.get_training(database, case["run"]["id"])
    destination = metadata["guide_view"]
    wrong_view = "Live" if destination == "My Group" else "My Group"
    app = AppTest.from_function(
        _panel, args=(str(case["path"]), case["run"]["id"], metadata, wrong_view, True),
    ).run()
    label = "Open Team situation" if destination == "Live" else "Open My Group"
    shortcut = next(button for button in app.button if button.label == label)
    shortcut.click().run()
    assert app.session_state["guide_test_navigation"] == destination
    assert training.get_training(database, case["run"]["id"]) == before
    correct = AppTest.from_function(
        _panel, args=(str(case["path"]), case["run"]["id"], metadata, destination, True),
    ).run()
    assert not any(button.label in {"Open My Group", "Open Team situation"} for button in correct.button)
    assert not correct.exception


def _open_case(case, monkeypatch, view="My Group"):
    case["set_clock"](case["clock"])
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(case["path"]))
    for name in ("COMPCOACH_DATABASE_URL", "COMPCOACH_REQUIRE_CLOUD", "COMPCOACH_ADMIN_PIN", "COMPCOACH_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    st.cache_resource.clear()
    app = AppTest.from_file(APP_PATH, default_timeout=25)
    app.query_params.update(event=case["run"]["id"], token=case["run"]["coach_token"], who="Coach Jamie")
    app.session_state[f"nav_choice_coach_nav_{case['run']['id']}"] = view
    return app.run()


ACTION_CASES = (
    ("shared_help", "My Group", "help_ack_", "I’m coming"),
    ("pool_result", "My Group", "pool_result_", "Save pool result"),
    ("available", "My Group", "availability_on_", "I’m available to help"),
    ("call", "My Group", "call_on_deck", "On deck"),
    ("physical_coverage", "My Group", "_busy", "I’m with "),
    ("busy_result", "My Group", "current_won_", "Won"),
    ("takeover", "My Group", "global_takeover_", "I’ll take over"),
    ("takeover_arrival", "My Group", "_busy", "I’m with "),
    ("other_athlete_help", "My Group", "help_request_", "🚨 Need help now"),
    ("second_result", "My Group", "current_won_", "Won"),
    ("urgent_accept", "My Group", "accept_coverage_request_", "I'll cover this bout"),
    ("urgent_arrival", "My Group", "_busy", "I’m with "),
    ("urgent_result", "My Group", "current_won_", "Won"),
    ("bye", "My Group", "bye_", "Bye"),
    ("afm_pair", "Live", "create_form_", "Review pairing"),
    ("afm_result", "Live", "_a_wins", " wins"),
    ("final_arrival", "My Group", "_busy", "I’m with "),
    ("final_result", "My Group", "current_won_", "Won"),
)


@pytest.mark.parametrize("name,view,key_piece,label_piece", ACTION_CASES)
def test_named_training_action_is_reachable_in_real_coach_interface(cases, monkeypatch, name, view, key_piece, label_piece):
    case = cases[name]
    app = _open_case(case, monkeypatch, view)
    assert not app.exception
    metadata = training.get_training(CompCoachDB(case["path"]), case["run"]["id"])
    target_id = metadata["guide_target_id"]
    target_name = metadata["guide_target_name"]
    buttons = [button for button in app.button if key_piece in str(button.key) and label_piece in button.label]
    if name == "urgent_accept":
        buttons = [button for button in buttons if metadata["state"]["coverage_request_id"] in button.key]
    elif name not in {"available", "afm_result", "afm_pair"}:
        buttons = [button for button in buttons if target_id in button.key]
    assert buttons, (name, metadata["guide_instruction"], [(button.key, button.label) for button in app.button])
    assert any(not button.disabled for button in buttons)
    if label_piece == "I’m with ":
        assert any(button.label == f"I’m with {target_name}" for button in buttons)
    if name == "other_athlete_help":
        assert target_id != metadata["state"]["secondary_id"]
        assert target_id not in {
            button.key.removeprefix("help_request_")
            for section in app.expander if section.label == "Current bout details"
            for button in section.button
        }
    st.cache_resource.clear()


def test_help_lesson_covers_one_athlete_and_requests_support_for_a_different_call(cases):
    arrival, help_case = cases["takeover_arrival"], cases["other_athlete_help"]
    first, second = arrival["metadata"], help_case["metadata"]
    assert first["stage_index"] == second["stage_index"]
    assert first["guide_target_id"] == first["state"]["secondary_id"]
    assert second["guide_target_id"] == second["state"]["help_target_id"]
    assert first["guide_target_id"] != second["guide_target_id"]
    assert "I’m with" in first["guide_instruction"] and "J2" in first["guide_instruction"]
    assert "🚨 Need help now" in second["guide_instruction"] and "K4" in second["guide_instruction"]
    database = CompCoachDB(help_case["path"])
    athletes = [row for event in database.list_meet_events(help_case["run"]["id"]) for row in database.list_athletes(event["id"])]
    covered = next(row for row in athletes if row["id"] == first["guide_target_id"])
    uncoached = next(row for row in athletes if row["id"] == second["guide_target_id"])
    assert covered["covered_by"] == "Coach Jamie" and covered["live_location"] == "J2"
    assert not uncoached["covered_by"] and uncoached["call_status"] == "now" and uncoached["live_location"] == "K4"


@pytest.mark.parametrize("name", ["physical_coverage", "afm_pair"])
def test_real_training_shortcut_opens_requested_view_without_replaying_coach_actions(cases, monkeypatch, name):
    case = cases[name]
    destination = case["metadata"]["guide_view"]
    wrong_view = "Live" if destination == "My Group" else "My Group"
    database = CompCoachDB(case["path"])
    with database._connection() as connection:
        before_actions = [dict(row) for row in connection.execute("SELECT * FROM actions ORDER BY id")]
    app = _open_case(case, monkeypatch, wrong_view)
    if destination == "Live":
        app.session_state[f"nav_choice_live_view_{case['run']['id']}_coach"] = "All assignments"
    label = "Open Team situation" if destination == "Live" else "Open My Group"
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception
    assert keyed(app.get("button_group"), "coach_nav_").value == destination
    if destination == "Live":
        assert keyed(app.get("button_group"), "live_view_").value == "Uncovered"
    assert training.get_training(database, case["run"]["id"])["stage_index"] == case["metadata"]["stage_index"]
    with database._connection() as connection:
        assert [dict(row) for row in connection.execute("SELECT * FROM actions ORDER BY id")] == before_actions
    assert not any(button.label == label for button in app.button)
    st.cache_resource.clear()


def test_guide_attribute_targets_are_escaped_without_changing_literal_action_text(cases):
    case = cases["call"]
    metadata = {**case["metadata"], "guide_target_id": 'id" onload="bad', "guide_target_name": "<script>name</script>", "guide_instruction": "Tap On deck for <script>name</script>."}
    app = AppTest.from_function(_panel, args=(str(case["path"]), case["run"]["id"], metadata)).run()
    dom = _guide(app)
    assert dom.guides[0]["data-target-athlete"] == 'id" onload="bad'
    assert "onload" not in dom.guides[0]
    assert "".join(dom.action) == metadata["guide_instruction"]
    assert not app.exception


def _copy_case(case, path):
    with sqlite3.connect(case["path"]) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    return {**case, "path": path}


def _review_training_pair(app, database, metadata):
    state = metadata["state"]
    athletes = [row for event in database.list_meet_events(metadata["meet_id"]) for row in database.list_athletes(event["id"])]
    first = next(row for row in athletes if row["id"] == state["pair_a_id"])
    event_id = first["event_id"]
    keyed(app.selectbox, f"live_first_{event_id}").set_value(state["pair_a_id"])
    keyed(app.selectbox, f"live_second_{event_id}").set_value(state["pair_b_id"])
    next(button for button in app.button if event_id in str(button.key) and button.label == "Review pairing").click().run()
    return event_id


def test_compact_pairing_guide_switches_to_only_the_actual_confirmation_button(cases, tmp_path, monkeypatch):
    case = _copy_case(cases["afm_pair"], tmp_path / "pair-confirm.sqlite")
    database = CompCoachDB(case["path"])
    app = _open_case(case, monkeypatch, "Live")
    initial = "".join(_guide(app).action)
    assert "Review pairing" in initial and "Confirm pairing" not in initial
    event_id = _review_training_pair(app, database, case["metadata"])
    action = "".join(_guide(app).action)
    assert "Confirm pairing" in action and "Review pairing" not in action
    assert case["metadata"]["state"]["pair_a_name"] in action
    assert case["metadata"]["state"]["pair_b_name"] in action
    assert training.get_training(database, case["run"]["id"])["stage_index"] == case["metadata"]["stage_index"]
    keyed(app.button, f"live_create_confirm_{event_id}").click().run()
    assert not app.exception
    metadata = training.get_training(database, case["run"]["id"])
    assert metadata["stage_index"] == case["metadata"]["stage_index"] + 1
    assert f"{metadata['state']['pair_a_name']} wins" in "".join(_guide(app).action)
    assert any(button.label == f"{metadata['state']['pair_a_name']} wins" for button in app.button)
    st.cache_resource.clear()


def test_stale_pairing_review_does_not_keep_instructing_a_missing_confirm_button(cases, tmp_path, monkeypatch):
    case = _copy_case(cases["afm_pair"], tmp_path / "pair-stale.sqlite")
    database = CompCoachDB(case["path"])
    app = _open_case(case, monkeypatch, "Live")
    event_id = _review_training_pair(app, database, case["metadata"])
    assert "Confirm pairing" in "".join(_guide(app).action)
    database.report_call(event_id, case["metadata"]["state"]["pair_a_id"], status="now", location="G2", actor="Coach Jamie")
    app.run()
    assert not app.exception
    action = "".join(_guide(app).action)
    assert "Review pairing" in action and "Confirm pairing" not in action
    assert not any(button.key == f"live_create_confirm_{event_id}" for button in app.button)
    assert any(button.label == "Review pairing" and event_id in str(button.key) for button in app.button)
    st.cache_resource.clear()


def test_call_saved_while_inspecting_de_plan_leads_to_arrival_instead_of_hidden_on_deck(cases, tmp_path, monkeypatch):
    case = _copy_case(cases["de_plan"], tmp_path / "call-before-plan-review.sqlite")
    database = CompCoachDB(case["path"])
    primary_id = case["metadata"]["state"]["primary_id"]
    app = _open_case(case, monkeypatch, "My Group")
    keyed(app.button, f"live_personal_{primary_id}_call_on_deck").click().run()
    assert not app.exception
    assert training.get_training(database, case["run"]["id"])["stage_index"] == case["metadata"]["stage_index"]
    next(button for button in app.button if button.label == "Open Team situation").click().run()
    metadata = training.get_training(database, case["run"]["id"])
    assert metadata["stage_index"] == case["metadata"]["stage_index"] + 2
    assert "I’m with" in "".join(_guide(app).action)
    next(button for button in app.button if button.label == "Open My Group").click().run()
    assert not app.exception
    assert keyed(app.button, f"live_personal_{primary_id}_busy").label == f"I’m with {metadata['state']['primary_name']}"
    assert not any(button.key == f"live_personal_{primary_id}_call_on_deck" for button in app.button)
    assert training.get_training(database, case["run"]["id"])["stage_index"] == metadata["stage_index"]
    st.cache_resource.clear()


def test_known_non_c3_call_guides_to_modify_call_before_actual_strip_and_arrival(cases, tmp_path, monkeypatch):
    case = _copy_case(cases["actual_strip"], tmp_path / "change-known-strip.sqlite")
    database = CompCoachDB(case["path"])
    primary_id = case["metadata"]["state"]["primary_id"]
    athletes = [row for event in database.list_meet_events(case["run"]["id"]) for row in database.list_athletes(event["id"])]
    primary = next(row for row in athletes if row["id"] == primary_id)
    database.report_call(primary["event_id"], primary_id, status="on_deck", location="G4", actor="Coach Jamie")
    app = _open_case(case, monkeypatch, "My Group")
    base = f"live_personal_{primary_id}"
    assert not any(str(widget.key).startswith(f"{base}_strip_v") for widget in app.text_input)
    assert "Modify call" in "".join(_guide(app).action)
    keyed(app.toggle, f"{base}_modify_v").set_value(True).run()
    keyed(app.text_input, f"{base}_strip_v").input("C3")
    keyed(app.button, f"{base}_busy").click().run()
    saved = database.get_athlete(primary["event_id"], primary_id)
    assert saved["covered_by"] == "Coach Jamie" and saved["live_location"] == "C3"
    assert training.get_training(database, case["run"]["id"])["stage_index"] == case["metadata"]["stage_index"] + 1
    assert not app.exception
    st.cache_resource.clear()
