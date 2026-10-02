"""A reviewed card pairing teaches the confirmation that is actually visible."""

from copy import deepcopy
from html.parser import HTMLParser

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachDB


class GuideDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = {}
        self.action = []
        self.in_action = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = str(attrs.get("class") or "").split()
        if "cc-practice-guide" in classes:
            self.attributes = attrs
        if "cc-practice-guide-action" in classes:
            self.in_action = True

    def handle_endtag(self, tag):
        if tag == "div":
            self.in_action = False

    def handle_data(self, data):
        if self.in_action:
            self.action.append(data)


def _guide(app):
    dom = GuideDOM()
    for element in app.get("html"):
        dom.feed(element.proto.body)
    return "".join(dom.action), dom.attributes


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "inline-guide.sqlite"
    database = CompCoachDB(path)
    meet = database.create_meet(
        "Practice", ["Jamie", "Taylor"], ["Jordan"], first_event_name="Junior Epee",
    )
    event = database.list_meet_events(meet["id"])[0]
    database.merge_import(event["id"], [
        {"athlete_id": name.lower(), "name": name, "phase": "de", "strip": "B1", "pod": "B"}
        for name in ("CASEY Athlete", "RILEY Athlete", "OTHER Athlete")
    ], "Jordan")
    rows = database.list_athletes(event["id"])
    by_name = {row["name"]: row for row in rows}
    first, second = by_name["CASEY Athlete"], by_name["RILEY Athlete"]
    metadata = {
        "meet_id": meet["id"], "kind": "run", "learner": "Jamie", "status": "running",
        "stage_index": 14, "stage_count": 18, "stage_title": "AFM vs AFM",
        "guide_view": "Live", "guide_target_event_id": event["id"],
        "guide_target_id": first["id"], "guide_target_name": first["name"],
        "guide_pair_versions": [first["version"], second["version"]],
        "guide_instruction": "In Team situation → AFM vs AFM, tap Review pairing.",
        "instruction": "Review pairing, then Confirm pairing for the two practice athletes.",
        "state": {
            "pair_a_id": first["id"], "pair_b_id": second["id"],
            "pair_a_name": first["name"], "pair_b_name": second["name"], "learner": "Jamie",
        },
    }
    return database, str(path), meet, event, first, second, by_name["OTHER Athlete"], metadata


def _pending(context, prefix="personal", reverse=False):
    first, second = context[4:6]
    if reverse:
        first, second = second, first
    return {
        f"{prefix}_athlete_pair_{first['id']}_pending": {
            "a": first["id"], "b": second["id"], "actor": "Jamie", "round": "Practice",
            "versions": [first["version"], second["version"]],
        },
    }


def _panel(path, meet_id, metadata, current_view, pending, actor="Jamie"):
    import streamlit as st
    from compcoach_live.storage import CompCoachDB
    from compcoach_live.training_ui import render_training_panel

    database = CompCoachDB(path)
    st.session_state["inline_guide_athlete_reads"] = []
    if not st.session_state.get("inline_guide_initialized"):
        for key, value in pending.items():
            st.session_state[key] = value
        st.session_state["inline_guide_initialized"] = True

    class TrackedDB:
        def get_athlete(self, event_id, athlete_id):
            st.session_state["inline_guide_athlete_reads"].append((event_id, athlete_id))
            return database.get_athlete(event_id, athlete_id)

    def navigate(destination):
        st.session_state["inline_guide_navigation"] = destination

    render_training_panel(
        TrackedDB(), database.get_meet(meet_id), metadata, "coach", actor,
        current_view=current_view, navigate_callback=navigate,
    )


def _open(context, pending, current_view="My Group", metadata=None, actor="Jamie"):
    return AppTest.from_function(
        _panel,
        args=(context[1], context[2]["id"], metadata or context[-1], current_view, pending, actor),
    ).run()


@pytest.mark.parametrize("prefix,view", [("personal", "My Group"), ("live", "Live")])
def test_reviewed_inline_pair_names_actual_confirmation_and_keeps_current_view(context, prefix, view):
    database, _path, _meet, event, first, second, _other, metadata = context
    before = deepcopy(metadata), database.list_athletes(event["id"])
    app = _open(context, _pending(context, prefix), view)
    assert not app.exception
    action, attrs = _guide(app)
    assert action == f"In More actions · {first['name']}, tap Confirm AFM pairing."
    assert attrs["data-guide-view"] == view
    assert not any(button.label in {"Open My Group", "Open Team situation"} for button in app.button)
    assert list(app.session_state["inline_guide_athlete_reads"]) == [(event["id"], first["id"]), (event["id"], second["id"])]
    assert (metadata, database.list_athletes(event["id"])) == before


def test_reverse_inline_anchor_names_the_card_that_contains_confirmation(context):
    app = _open(context, _pending(context, reverse=True))
    assert not app.exception
    assert _guide(app)[0] == f"In More actions · {context[5]['name']}, tap Confirm AFM pairing."


@pytest.mark.parametrize("prefix,destination,current", [
    ("personal", "My Group", "Live"), ("live", "Live", "My Group"),
])
def test_inline_navigation_shortcut_targets_actual_card_view_without_database_writes(context, prefix, destination, current):
    database, _path, _meet, event, *_rest = context
    before = database.list_athletes(event["id"])
    app = _open(context, _pending(context, prefix), current)
    label = "Open My Group" if destination == "My Group" else "Open Team situation"
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception
    assert app.session_state["inline_guide_navigation"] == destination
    assert _guide(app)[1]["data-guide-view"] == destination
    assert database.list_athletes(event["id"]) == before


@pytest.mark.parametrize("view,anchor", [("My Group", 4), ("Live", 5)])
def test_two_matching_pending_views_prefer_the_one_coach_is_using(context, view, anchor):
    pending = {**_pending(context), **_pending(context, "live", reverse=True)}
    app = _open(context, pending, view)
    assert not app.exception
    assert _guide(app)[0] == f"In More actions · {context[anchor]['name']}, tap Confirm AFM pairing."
    assert _guide(app)[1]["data-guide-view"] == view
    assert len(app.session_state["inline_guide_athlete_reads"]) == 2


@pytest.mark.parametrize("change", [
    "other_stage", "wrong_pair", "wrong_actor", "wrong_key_anchor", "missing_event", "malformed_versions",
])
def test_unmatched_inline_state_keeps_shared_guide_without_reading_athletes(context, change):
    metadata = deepcopy(context[-1])
    pending = _pending(context)
    key = next(iter(pending))
    if change == "other_stage":
        metadata["stage_index"] = 13
    elif change == "wrong_pair":
        pending[key]["b"] = context[6]["id"]
    elif change == "wrong_actor":
        pending[key]["actor"] = "Taylor"
    elif change == "wrong_key_anchor":
        pending[f"personal_athlete_pair_{context[6]['id']}_pending"] = pending.pop(key)
    elif change == "missing_event":
        metadata["guide_target_event_id"] = ""
    else:
        pending[key]["versions"] = None
    app = _open(context, pending, metadata=metadata)
    assert not app.exception
    assert _guide(app)[0] == metadata["guide_instruction"]
    assert _guide(app)[1]["data-guide-view"] == "Live"
    assert list(app.session_state["inline_guide_athlete_reads"]) == []


@pytest.mark.parametrize("change", ["revision", "out", "absent", "pools", "wrong_event", "missing_athlete"])
def test_changed_or_ineligible_pair_falls_back_to_shared_review(context, change):
    database, _path, _meet, event, first, second, _other, metadata = context
    pending = _pending(context)
    metadata = deepcopy(metadata)
    if change == "revision":
        database.report_call(event["id"], second["id"], status="now", location="C3", actor="Taylor")
    elif change == "out":
        database.mark_result(event["id"], second["id"], outcome="lost", actor="Taylor")
    elif change == "absent":
        database.set_athlete_participation(event["id"], second["id"], "absent", "Taylor")
    elif change == "pools":
        with database._connection() as connection:
            connection.execute("UPDATE athletes SET phase = 'pools' WHERE id = ?", (second["id"],))
    elif change == "wrong_event":
        other_event = database.add_meet_event(context[2]["id"], "Cadet Epee")
        metadata["guide_target_event_id"] = other_event["id"]
    else:
        with database._connection() as connection:
            connection.execute("DELETE FROM athletes WHERE id = ?", (second["id"],))
    app = _open(context, pending, metadata=metadata)
    assert not app.exception
    assert _guide(app)[0] == metadata["guide_instruction"]
    assert _guide(app)[1]["data-guide-view"] == "Live"
    assert len(app.session_state["inline_guide_athlete_reads"]) == 2


def test_stale_current_view_review_can_use_fresh_review_in_other_view(context):
    pending = {**_pending(context), **_pending(context, "live", reverse=True)}
    first_key = next(iter(pending))
    pending[first_key]["versions"][0] -= 1
    app = _open(context, pending, "My Group")
    assert not app.exception
    assert _guide(app)[0] == f"In More actions · {context[5]['name']}, tap Confirm AFM pairing."
    assert _guide(app)[1]["data-guide-view"] == "Live"
    assert len(app.session_state["inline_guide_athlete_reads"]) == 2


def test_existing_shared_review_keeps_its_original_label_and_team_situation_destination(context):
    first, second = context[4:6]
    pending = {
        f"live_create_pending_{context[3]['id']}": {
            "a": first["id"], "b": second["id"], "actor": "Jamie", "round": "Practice",
            "versions": [first["version"], second["version"]],
        },
    }
    app = _open(context, pending)
    assert not app.exception
    assert _guide(app)[0] == (
        f"In Team situation → AFM vs AFM, tap Confirm pairing for {first['name']} and {second['name']}."
    )
    assert _guide(app)[1]["data-guide-view"] == "Live"
    assert any(button.label == "Open Team situation" for button in app.button)


def test_valid_inline_review_takes_precedence_over_shared_review(context):
    first, second = context[4:6]
    pending = _pending(context)
    pending[f"live_create_pending_{context[3]['id']}"] = {
        "a": first["id"], "b": second["id"], "actor": "Jamie", "round": "Practice",
        "versions": [first["version"], second["version"]],
    }
    app = _open(context, pending)
    assert not app.exception
    assert _guide(app)[0] == f"In More actions · {first['name']}, tap Confirm AFM pairing."
    assert _guide(app)[1]["data-guide-view"] == "My Group"
    assert len(app.session_state["inline_guide_athlete_reads"]) == 2
