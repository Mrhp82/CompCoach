"""Event-selective WhatsApp snapshots never leak unselected event plans."""

from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.whatsapp_scope import resolve_share_events


EVENTS = [
    {"id": "morning", "name": "Cadet Men", "phase": "pools"},
    {"id": "afternoon", "name": "Junior Women", "phase": "de"},
    {"id": "evening", "name": "Senior Men", "phase": "pools"},
]


def test_none_preserves_all_events_and_order_without_mutation():
    before = deepcopy(EVENTS)
    assert resolve_share_events(iter(EVENTS)) == EVENTS
    assert EVENTS == before
    assert resolve_share_events(EVENTS)[0] is EVENTS[0]


def test_empty_selection_is_empty_and_cannot_fall_back_to_all_events():
    assert resolve_share_events(EVENTS, []) == []
    assert resolve_share_events(EVENTS, iter([])) == []
    assert resolve_share_events([]) == []


def test_one_several_and_reversed_selection_preserve_competition_order():
    assert resolve_share_events(EVENTS, ["afternoon"]) == [EVENTS[1]]
    assert resolve_share_events(EVENTS, ["evening", "morning"]) == [EVENTS[0], EVENTS[2]]


def test_duplicate_event_rows_and_selected_ids_are_deduplicated():
    assert resolve_share_events([*EVENTS, {**EVENTS[0], "name": "Duplicate"}]) == EVENTS
    assert resolve_share_events(EVENTS, ["morning", "morning"]) == [EVENTS[0]]


@pytest.mark.parametrize("selection", [["unknown"], ["morning", "deleted"], [None]])
def test_unknown_or_stale_selection_fails_instead_of_exporting_other_events(selection):
    with pytest.raises(ValueError, match="Unknown event selection"):
        resolve_share_events(EVENTS, selection)


@pytest.mark.parametrize("event", [{"name": "No ID"}, {"id": ""}, {"id": 1}])
def test_malformed_source_event_is_rejected(event):
    with pytest.raises(ValueError, match="non-empty string ID"):
        resolve_share_events([event])


def test_plain_string_cannot_be_misinterpreted_as_an_id_collection():
    with pytest.raises(TypeError, match="collection"):
        resolve_share_events(EVENTS, "morning")


def _open(events=EVENTS, prefixes=("schedule",)):
    source = f'''\
import streamlit as st
from compcoach_live.whatsapp_scope import render_share_event_scope
events = st.session_state.get("source_events", {events!r})
for prefix in {prefixes!r}:
    selected = render_share_event_scope(events, prefix)
    st.json({{"prefix": prefix, "selected": selected}})
    st.button("Open in WhatsApp", key=prefix + "_export", disabled=not selected)
'''
    app = AppTest.from_string(source, default_timeout=10).run()
    assert not app.exception
    return app


def _selected(app, index=0):
    import json
    return json.loads(app.json[index].value)["selected"]


def test_ui_defaults_to_all_events_and_choose_events_defaults_to_first():
    app = _open()
    assert _selected(app) == ["morning", "afternoon", "evening"]
    assert not app.multiselect
    app.radio[0].set_value("Choose events").run()
    assert not app.exception
    assert app.multiselect[0].value == ["morning"]
    assert _selected(app) == ["morning"]


def test_ui_can_share_single_or_multiple_without_changing_input_events():
    app = _open()
    app.radio[0].set_value("Choose events").run()
    assert app.multiselect[0].options == ["Cadet Men", "Junior Women", "Senior Men"]
    app.multiselect[0].set_value(["afternoon"]).run()
    assert _selected(app) == ["afternoon"] and not app.button[0].disabled
    app.multiselect[0].set_value(["evening", "morning"]).run()
    assert not app.exception
    assert _selected(app) == ["morning", "evening"]


def test_empty_ui_selection_warns_and_disables_export():
    app = _open()
    app.radio[0].set_value("Choose events").run()
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert _selected(app) == []
    assert app.warning[0].value == "Select at least one event to create a WhatsApp message."
    assert app.button[0].disabled


@pytest.mark.parametrize("selection", [["afternoon"], []])
def test_switching_to_all_and_back_remembers_selection_including_empty(selection):
    app = _open()
    app.radio[0].set_value("Choose events").run()
    app.multiselect[0].set_value(selection).run()
    app.radio[0].set_value("All events").run()
    assert _selected(app) == ["morning", "afternoon", "evening"]
    app.radio[0].set_value("Choose events").run()
    assert not app.exception
    assert _selected(app) == selection


def test_removed_events_are_filtered_before_widget_instantiation():
    app = _open()
    app.radio[0].set_value("Choose events").run()
    app.multiselect[0].set_value(["morning", "afternoon"]).run()
    app.session_state["source_events"] = [EVENTS[0], EVENTS[2]]
    app.run()
    assert not app.exception
    assert app.multiselect[0].value == ["morning"]
    assert _selected(app) == ["morning"]


def test_deleted_selection_does_not_default_to_a_different_event():
    app = _open()
    app.radio[0].set_value("Choose events").run()
    app.multiselect[0].set_value(["afternoon"]).run()
    app.session_state["source_events"] = [EVENTS[0], EVENTS[2]]
    app.run()
    assert not app.exception
    assert _selected(app) == [] and app.button[0].disabled


def test_additional_event_is_excluded_from_explicit_selection_and_included_in_all():
    app = _open(EVENTS[:1])
    app.radio[0].set_value("Choose events").run()
    app.session_state["source_events"] = EVENTS
    app.run()
    assert not app.exception
    assert _selected(app) == ["morning"]
    app.radio[0].set_value("All events").run()
    assert _selected(app) == ["morning", "afternoon", "evening"]


def test_no_events_disables_export_without_instantiating_selector():
    app = _open([])
    assert not app.radio and not app.multiselect
    assert _selected(app) == [] and app.button[0].disabled
    assert app.warning[0].value == "No events are available to share."


def test_identical_names_have_distinct_selection_labels():
    app = _open([{**event, "name": "Epee"} for event in EVENTS])
    app.radio[0].set_value("Choose events").run()
    assert app.multiselect[0].options == ["Epee · Event 1", "Epee · Event 2", "Epee · Event 3"]
    app.multiselect[0].set_value(["afternoon"]).run()
    assert _selected(app) == ["afternoon"]


def test_different_competitions_do_not_share_widget_state():
    app = _open(prefixes=("first_meet", "second_meet"))
    app.radio[0].set_value("Choose events").run()
    assert not app.exception
    assert _selected(app, 0) == ["morning"]
    assert _selected(app, 1) == ["morning", "afternoon", "evening"]
