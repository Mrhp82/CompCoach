"""Choose which competition events appear in a WhatsApp assignment snapshot.

This module only selects existing events. It does not change their visibility,
assignments, phase or date, and an empty selection never expands to all events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def resolve_share_events(
    all_events: Iterable[Mapping[str, Any]],
    event_ids: Iterable[str] | None = None,
) -> list[Mapping[str, Any]]:
    """Return selected events in competition order, without modifying them.

    ``None`` retains the legacy all-events export. An explicit empty iterable
    means no export. Duplicate IDs are collapsed; unknown selected IDs raise
    ``ValueError`` rather than exporting the wrong schedule.
    """
    events_by_id: dict[str, Mapping[str, Any]] = {}
    for event in all_events:
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Every event must have a non-empty string ID.")
        events_by_id.setdefault(event_id, event)
    if event_ids is None:
        return list(events_by_id.values())
    if isinstance(event_ids, (str, bytes)):
        raise TypeError("event_ids must be a collection of event IDs, not a string.")
    selected = set(event_ids)
    unknown = selected.difference(events_by_id)
    if unknown:
        raise ValueError("Unknown event selection: " + ", ".join(sorted(map(str, unknown))))
    return [event for event_id, event in events_by_id.items() if event_id in selected]


def render_share_event_scope(
    events: Iterable[Mapping[str, Any]], key_prefix: str,
) -> list[str]:
    """Render the compact All events / Choose events export selector.

    A separate non-widget state remembers the chosen IDs while the multiselect
    is hidden in All events mode. Stale choices are removed before the widget
    is created, so a day/event change cannot produce an invalid widget value or
    silently include an event the user did not choose.
    """
    import streamlit as st

    ordered_events = resolve_share_events(events)
    ids = [event["id"] for event in ordered_events]
    if not ids:
        st.warning("No events are available to share.")
        return []

    mode_key = f"{key_prefix}_scope_mode"
    selected_key = f"{key_prefix}_scope_selected"
    remembered_key = f"{key_prefix}_scope_remembered"
    mode = st.radio(
        "Events to include", ["All events", "Choose events"],
        key=mode_key, horizontal=True,
    )
    if mode == "All events":
        return ids

    # A saved empty list is intentional. Default to the first event only when
    # the user has never made a selection, not when old events were removed.
    remembered = st.session_state.get(remembered_key, [ids[0]])
    remembered = [event_id for event_id in remembered if event_id in ids]
    st.session_state[remembered_key] = remembered
    if selected_key in st.session_state:
        current = st.session_state[selected_key]
        valid = [event_id for event_id in current if event_id in ids]
        if valid != current:
            st.session_state[selected_key] = valid
    else:
        st.session_state[selected_key] = remembered

    names = [str(event.get("name") or "Unnamed event") for event in ordered_events]
    labels = {
        event["id"]: name if names.count(name) == 1 else f"{name} · Event {index + 1}"
        for index, (event, name) in enumerate(zip(ordered_events, names))
    }
    selected = st.multiselect(
        "Include these events", ids, key=selected_key,
        format_func=labels.__getitem__,
        placeholder="Select one or more events",
    )
    selected_ids = [event["id"] for event in resolve_share_events(ordered_events, selected)]
    st.session_state[remembered_key] = selected_ids
    if not selected_ids:
        st.warning("Select at least one event to create a WhatsApp message.")
    else:
        st.caption("Only the selected events will appear in this WhatsApp message.")
    return selected_ids
