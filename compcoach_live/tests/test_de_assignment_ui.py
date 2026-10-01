"""Exercise equal-coach DE assignment actions without unrelated app panels."""

from __future__ import annotations

import json
import re

from streamlit.testing.v1 import AppTest

from compcoach_live.storage import CompCoachError


class AssignmentDB:
    """A persisted test double for inspecting the UI's command boundary."""

    def __init__(self, path):
        self.path = path

    def read(self):
        with open(self.path, encoding="utf-8") as source:
            return json.load(source)

    def write(self, state):
        with open(self.path, "w", encoding="utf-8") as target:
            json.dump(state, target)

    def list_pod_assignments(self, event_id, *, phase):
        return [
            {"event_id": event_id, "phase": phase, "pod": pod, "coaches": coaches, "version": self.read().get("revision", 0)}
            for pod, coaches in self.read()["assignments"].items()
        ]

    def visible(self):
        state = self.read()
        return [
            {**athlete, "de_coaches": athlete.get("override", state["assignments"].get(athlete["pod"], []))}
            for athlete in state["athletes"]
        ]

    def assign_de_pods(self, event_id, *, pods, coaches, actor, mode):
        state = self.read()
        if state.get("fail"):
            raise CompCoachError("Assignment could not be saved.")
        state["calls"].append({"action": "pods", "event_id": event_id, "pods": pods, "coaches": coaches, "actor": actor, "mode": mode})
        for pod in pods:
            state["assignments"][pod] = list(dict.fromkeys([*state["assignments"].get(pod, []), *coaches])) if mode == "add" else list(coaches)
        state["revision"] = state.get("revision", 0) + 1
        self.write(state)

    def assign_de_athletes(self, event_id, athlete_ids, *, coaches, actor):
        state = self.read()
        if state.get("fail"):
            raise CompCoachError("Assignment could not be saved.")
        state["calls"].append({"action": "athletes", "event_id": event_id, "athlete_ids": athlete_ids, "coaches": coaches, "actor": actor})
        for athlete in state["athletes"]:
            if athlete["id"] in athlete_ids:
                athlete["override"] = coaches
                athlete["version"] += 1
        self.write(state)


def natural(value):
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", str(value)) if part)


def seed(tmp_path, *, status="open", assignments=None):
    path = tmp_path / "assignments.json"
    state = {
        "event": {"id": "event-de", "status": status},
        "athletes": [
            {"id": f"a{index}", "name": f"Athlete {index}", "pod": pod, "source_strip": f"{pod}1", "phase": "de", "version": 1}
            for index, pod in enumerate(["B10", "B2", "M", "P", "Q"], 1)
        ],
        "assignments": assignments or {}, "calls": [],
    }
    db = AssignmentDB(str(path))
    db.write(state)
    return db


def app_for(db, *, actor="Jordan"):
    source = f'''\
from compcoach_live.de_assignment_ui import render_de_pod_assignments, render_de_individual_assignments
from compcoach_live.tests.test_de_assignment_ui import AssignmentDB, natural
db = AssignmentDB({str(db.path)!r})
event = db.read()['event']
args = (db, event, {actor!r}, db.visible(), ['Alex', 'Taylor', 'Morgan', 'Riley'], {{'Alex'}})
kwargs = dict(natural_sort_key=natural, coach_format=lambda coach: ('✓ ' if coach == 'Alex' else '') + coach)
render_de_pod_assignments(*args, **kwargs)
render_de_individual_assignments(*args, **kwargs)
'''
    return AppTest.from_string(source, default_timeout=10).run()


def button(app, label):
    matches = [entry for entry in app.button if entry.label == label]
    assert len(matches) == 1
    return matches[0]


def picker(app, label):
    matches = [entry for entry in app.multiselect if entry.label == label]
    assert len(matches) == 1
    return matches[0]


def test_three_equal_coaches_save_and_edit_prefills_latest_pod_group(tmp_path):
    db = seed(tmp_path, assignments={"B2": ["Alex", "Taylor"]})
    app = app_for(db)
    assert not app.exception
    assert list(app.selectbox[0].options) == ["B2", "B10", "M", "P", "Q"]
    assert picker(app, "Coaches").value == ["Alex", "Taylor"]
    picker(app, "Coaches").set_value(["Alex", "Taylor", "Morgan"])
    button(app, "Save pod coaches").click().run()
    assert not app.exception
    assert db.read()["assignments"]["B2"] == ["Alex", "Taylor", "Morgan"]
    assert picker(app, "Coaches").value == ["Alex", "Taylor", "Morgan"]
    assert db.read()["calls"][-1]["mode"] == "replace"
    assert all("Main" not in entry.label and "Side" not in entry.label for entry in [*app.multiselect, *app.selectbox])


def test_switching_pod_prefills_independent_group_and_blank_submission_never_clears(tmp_path):
    db = seed(tmp_path, assignments={"B2": ["Alex"], "M": ["Morgan", "Riley"]})
    app = app_for(db)
    app.selectbox[0].set_value("M").run()
    assert picker(app, "Coaches").value == ["Morgan", "Riley"]
    picker(app, "Coaches").set_value([])
    button(app, "Save pod coaches").click().run()
    assert not app.exception
    assert "removal button" in app.error[0].value
    assert db.read()["assignments"]["M"] == ["Morgan", "Riley"]
    assert not db.read()["calls"]


def test_one_coach_added_to_four_pods_preserves_other_coaches_and_clears_selection(tmp_path):
    db = seed(tmp_path, assignments={"B2": ["Alex"], "M": ["Taylor"]})
    app = app_for(db)
    app.radio[0].set_value("Coach → pods").run()
    app.selectbox[0].set_value("Morgan")
    pods = picker(app, "Pods · choose up to 4")
    assert pods.proto.max_selections == 4
    pods.set_value(["B2", "B10", "M", "P"])
    button(app, "Add coach to selected pods").click().run()
    assert not app.exception
    assigned = db.read()["assignments"]
    assert assigned["B2"] == ["Alex", "Morgan"]
    assert assigned["M"] == ["Taylor", "Morgan"]
    assert assigned["P"] == assigned["B10"] == ["Morgan"]
    assert "Q" not in assigned
    assert picker(app, "Pods · choose up to 4").value == []


def test_pod_removal_requires_second_tap_and_cancel_keeps_assignment(tmp_path):
    db = seed(tmp_path, assignments={"B2": ["Alex", "Taylor", "Morgan"]})
    app = app_for(db)
    button(app, "Remove coaches from pod B2").click().run()
    assert not app.exception
    assert db.read()["assignments"]["B2"] == ["Alex", "Taylor", "Morgan"]
    button(app, "Cancel").click().run()
    assert not app.exception
    assert not any(entry.label == "Confirm removal" for entry in app.button)
    button(app, "Remove coaches from pod B2").click().run()
    button(app, "Confirm removal").click().run()
    assert not app.exception
    assert db.read()["assignments"]["B2"] == []
    assert picker(app, "Coaches").value == []


def test_individual_assignment_prefills_coaches_then_clears_selected_athletes(tmp_path):
    db = seed(tmp_path, assignments={"B2": ["Alex", "Taylor"]})
    app = app_for(db)
    picker(app, "Athletes").set_value(["a2"]).run()
    assert picker(app, "Coaches for selected athletes").value == ["Alex", "Taylor"]
    picker(app, "Coaches for selected athletes").set_value(["Morgan", "Riley", "Taylor"])
    button(app, "Apply to 1 athlete").click().run()
    assert not app.exception
    assert db.read()["athletes"][1]["override"] == ["Morgan", "Riley", "Taylor"]
    assert db.read()["assignments"]["B2"] == ["Alex", "Taylor"]
    assert picker(app, "Athletes").value == []


def test_failed_individual_assignment_keeps_selection_and_error(tmp_path):
    db = seed(tmp_path)
    state = db.read()
    state["fail"] = True
    db.write(state)
    app = app_for(db)
    picker(app, "Athletes").set_value(["a2"]).run()
    picker(app, "Coaches for selected athletes").set_value(["Morgan"])
    button(app, "Apply to 1 athlete").click().run()
    assert not app.exception
    assert picker(app, "Athletes").value == ["a2"]
    assert app.error[0].value == "Assignment could not be saved."
    assert not db.read()["calls"]


def test_locked_event_never_offers_enabled_assignment_actions(tmp_path):
    db = seed(tmp_path, status="locked", assignments={"B2": ["Alex"]})
    app = app_for(db)
    assert not app.exception
    assert all(entry.disabled for entry in app.button if entry.label != "Cancel")
    assert not db.read()["calls"]
