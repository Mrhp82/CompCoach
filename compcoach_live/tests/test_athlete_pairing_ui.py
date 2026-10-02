"""Card-anchored AFM pairings retain review snapshots and shared bout rules."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compcoach_live.de_bouts import create_de_bout, list_de_bouts
from compcoach_live.storage import CompCoachDB


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "inline-pairing.sqlite"
    database = CompCoachDB(path)
    meet = database.create_meet("Test competition", ["Alex", "Taylor"], ["Jordan"], first_event_name="Junior Epee")
    event = database.list_meet_events(meet["id"])[0]
    database.merge_import(event["id"], [
        {"athlete_id": name.lower(), "name": f"{name} Athlete", "phase": "de",
         "strip": "B1", "pod": "B", "pool": "", "time": ""}
        for name in ("CASEY", "RILEY", "JAMIE", "DREW", "TAYLOR", "ROBIN", "BLAIR")
    ], "Jordan")
    database.assign_de_pods(event["id"], pods=["B"], coaches=["Alex", "Taylor"], actor="Jordan")
    rows = {row["name"].split()[0]: row for row in database.list_athletes(event["id"])}
    return database, path, meet, event, rows


def _open(context, *names, actor="Alex", prefixes=None, event_override=None, meet_override=None, cached_bouts=False):
    _database, path, _meet, event, rows = context
    athletes = [(rows[name]["id"], (prefixes or {}).get(name, "personal")) for name in names]
    source = f'''\
import streamlit as st
from compcoach_live.storage import CompCoachDB
from compcoach_live.de_bouts import list_de_bouts
from compcoach_live.de_bout_controls import render_athlete_pairing_control
database = CompCoachDB({str(path)!r})
original_meet = database.get_meet_for_event
database.get_meet_for_event = lambda event_id: {{**original_meet(event_id), **{meet_override or {}!r}}}
if st.session_state.pop("de_fast_refresh", False):
    st.rerun(scope="app")
notice = st.session_state.pop("de_fast_notice", None)
if notice:
    (st.success if notice[0] else st.error)(notice[1])
event = {{**database.get_event({event['id']!r}), **{event_override or {}!r}}}
bouts = list_de_bouts(database, event['id']) if {cached_bouts!r} else None
for athlete_id, prefix in {athletes!r}:
    athlete = database.get_athlete(event['id'], athlete_id)
    render_athlete_pairing_control(database, event, {actor!r}, athlete, key_prefix=prefix, bouts=bouts)
'''
    app = AppTest.from_string(source, default_timeout=10).run()
    assert not app.exception
    return app


def _key(app, kind, context, name, suffix, prefix="personal"):
    key = f"{prefix}_athlete_pair_{context[4][name]['id']}_{suffix}"
    widgets = [widget for widget in getattr(app, kind) if widget.key == key]
    assert len(widgets) == 1, (key, [widget.key for widget in getattr(app, kind)])
    return widgets[0]


def _review(app, context, anchor="CASEY", opponent="RILEY", label="", prefix="personal"):
    _key(app, "selectbox", context, anchor, "opponent", prefix).set_value(context[4][opponent]["id"])
    _key(app, "text_input", context, anchor, "round", prefix).set_value(label)
    _key(app, "button", context, anchor, "review", prefix).click().run()
    assert not app.exception


@pytest.mark.parametrize("actor", ["Alex", "Taylor", "Jordan"])
def test_review_then_confirm_pairs_current_athlete_without_assigning_or_entering_result(context, actor):
    database, _path, _meet, event, rows = context
    before = {name: database.get_athlete(event["id"], rows[name]["id"]) for name in ("CASEY", "RILEY")}
    app = _open(context, "CASEY", actor=actor, cached_bouts=True)
    assert len(app.selectbox) == 1 and app.selectbox[0].label == "Opponent"
    assert rows["CASEY"]["name"] not in app.selectbox[0].options
    _review(app, context, label=" T32 ")
    assert not list_de_bouts(database, event["id"])
    assert any("CASEY Athlete vs RILEY Athlete · T32" in item.value for item in app.warning)
    _key(app, "button", context, "CASEY", "confirm").click().run()
    bout, = list_de_bouts(database, event["id"])
    assert (bout["athlete_a_id"], bout["athlete_b_id"]) == (rows["CASEY"]["id"], rows["RILEY"]["id"])
    assert bout["status"] == "pending" and bout["round_label"] == "T32" and bout["created_by"] == actor
    for name, old in before.items():
        saved = database.get_athlete(event["id"], rows[name]["id"])
        for field in ("active_state", "de_wins", "de_byes", "last_de_result", "de_coaches", "main_coach", "side_coach"):
            assert saved[field] == old[field]
    assert not app.selectbox and any("Pairing already recorded" in item.value for item in app.caption)
    assert app.success and not app.exception


def test_opponent_list_excludes_other_events_pools_absent_out_and_pending_pairs(context):
    database, _path, meet, event, rows = context
    create_de_bout(database, event["id"], rows["JAMIE"]["id"], rows["DREW"]["id"], actor="Taylor")
    database.mark_result(event["id"], rows["TAYLOR"]["id"], outcome="lost", actor="Taylor")
    database.set_athlete_participation(event["id"], rows["ROBIN"]["id"], "absent", "Taylor")
    database.merge_import(event["id"], [{
        "athlete_id": rows["BLAIR"]["athlete_key"], "name": rows["BLAIR"]["name"],
        "phase": "pools", "strip": "B1", "pod": "B", "pool": "3", "time": "",
    }], "Jordan")
    other = database.add_meet_event(meet["id"], "Cadet Epee")
    database.merge_import(other["id"], [{"athlete_id": "other", "name": "OTHER Athlete", "phase": "de", "strip": "C1", "pod": "C"}], "Jordan")
    app = _open(context, "CASEY")
    assert app.selectbox[0].options == ["Choose an opponent", "RILEY Athlete"]


def test_empty_opponent_cannot_enter_review_or_write_a_pair(context):
    database, _path, _meet, event, _rows = context
    app = _open(context, "CASEY")
    _key(app, "button", context, "CASEY", "review").click().run()
    assert not list_de_bouts(database, event["id"])
    assert app.error and not any(button.label == "Confirm AFM pairing" for button in app.button)


@pytest.mark.parametrize("changed", ["CASEY", "RILEY"])
@pytest.mark.parametrize("when", ["before_review", "after_review"])
def test_changed_athlete_revision_cannot_be_paired_from_older_phone(context, changed, when):
    database, _path, _meet, event, rows = context
    app = _open(context, "CASEY")
    if when == "after_review":
        _review(app, context)
    newer = database.report_call(event["id"], rows[changed]["id"], status="now", location="K4", actor="Jordan")
    if when == "before_review":
        _review(app, context)
        assert app.info and not any(button.label == "Confirm AFM pairing" for button in app.button)
    else:
        _key(app, "button", context, "CASEY", "confirm").click().run()
        assert app.error
    saved = database.get_athlete(event["id"], rows[changed]["id"])
    assert saved["version"] == newer["version"] and saved["live_location"] == "K4"
    assert not list_de_bouts(database, event["id"]) and not app.exception


def test_reverse_pair_created_on_another_phone_is_not_duplicated(context):
    database, _path, _meet, event, rows = context
    app = _open(context, "CASEY")
    _review(app, context)
    existing = create_de_bout(database, event["id"], rows["RILEY"]["id"], rows["CASEY"]["id"], actor="Taylor")
    _key(app, "button", context, "CASEY", "confirm").click().run()
    assert [row["id"] for row in list_de_bouts(database, event["id"])] == [existing["id"]]
    assert app.error and not app.exception
    assert not app.selectbox


def test_existing_pair_shows_opponent_without_duplicate_creation_controls(context):
    database, _path, _meet, event, rows = context
    create_de_bout(database, event["id"], rows["CASEY"]["id"], rows["RILEY"]["id"], actor="Taylor", round_label="T16")
    app = _open(context, "CASEY", "RILEY", cached_bouts=True)
    captions = "\n".join(item.value for item in app.caption)
    assert "RILEY Athlete · T16 · Pairing already recorded" in captions
    assert "CASEY Athlete · T16 · Pairing already recorded" in captions
    assert not app.button and not app.selectbox


@pytest.mark.parametrize("event_override,meet_override,actor", [
    ({"status": "locked"}, {}, "Alex"), ({}, {"status": "locked"}, "Alex"),
    ({}, {"day_status": "planned"}, "Alex"), ({}, {"ended_at": "2026-10-01T12:00:00Z"}, "Alex"),
    ({}, {}, ""),
])
def test_archive_and_inactive_day_have_no_pairing_form(context, event_override, meet_override, actor):
    database, _path, _meet, event, _rows = context
    app = _open(context, "CASEY", event_override=event_override, meet_override=meet_override, actor=actor)
    assert not app.selectbox and not app.button and not app.text_input
    assert not list_de_bouts(database, event["id"])


@pytest.mark.parametrize("change", ["lost", "absent", "pools"])
def test_anchor_no_longer_active_in_de_has_no_creation_controls(context, change):
    database, _path, _meet, event, rows = context
    athlete = rows["CASEY"]
    if change == "lost":
        database.mark_result(event["id"], athlete["id"], outcome="lost", actor="Alex")
    elif change == "absent":
        database.set_athlete_participation(event["id"], athlete["id"], "absent", "Alex")
    else:
        database.merge_import(event["id"], [{"athlete_id": athlete["athlete_key"], "name": athlete["name"], "phase": "pools", "strip": "B1", "pool": "2"}], "Alex")
    app = _open(context, "CASEY")
    assert not app.button and not app.selectbox


def test_confirmation_after_archive_does_not_write(context):
    database, _path, _meet, event, _rows = context
    app = _open(context, "CASEY")
    _review(app, context)
    database.set_event_locked(event["id"], True)
    _key(app, "button", context, "CASEY", "confirm").click().run()
    assert app.error and not app.exception and not list_de_bouts(database, event["id"])
    assert not app.selectbox


def test_cancel_discards_review_without_result_or_pair(context):
    database, _path, _meet, event, _rows = context
    app = _open(context, "CASEY")
    _review(app, context)
    _key(app, "button", context, "CASEY", "cancel").click().run()
    assert not list_de_bouts(database, event["id"])
    assert app.selectbox and not any(button.label == "Confirm AFM pairing" for button in app.button)


def test_two_inline_cards_have_distinct_keys_before_and_after_pairing(context):
    database, _path, _meet, event, _rows = context
    app = _open(context, "CASEY", "RILEY")
    keys = [widget.key for widgets in (app.selectbox, app.text_input, app.button) for widget in widgets]
    assert len(keys) == len(set(keys)) and len(app.selectbox) == 2
    _review(app, context)
    _key(app, "button", context, "CASEY", "confirm").click().run()
    assert not app.exception and len(list_de_bouts(database, event["id"])) == 1
    assert not app.selectbox


def test_same_athlete_in_personal_and_live_views_has_distinct_widget_keys(context):
    _database, path, _meet, event, rows = context
    source = f'''\
from compcoach_live.storage import CompCoachDB
from compcoach_live.de_bout_controls import render_athlete_pairing_control
database = CompCoachDB({str(path)!r})
event = database.get_event({event['id']!r})
athlete = database.get_athlete(event['id'], {rows['CASEY']['id']!r})
for prefix in ('personal', 'live', 'current'):
    render_athlete_pairing_control(database, event, 'Alex', athlete, key_prefix=prefix)
'''
    app = AppTest.from_string(source, default_timeout=10).run()
    assert not app.exception and len(app.selectbox) == 3
    keys = [widget.key for widgets in (app.selectbox, app.text_input, app.button) for widget in widgets]
    assert len(keys) == len(set(keys))
