"""Shared coach groups preserve operational filters and equal DE assignments."""

from html import unescape
import re

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    create_multi_event,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def grouped_scenario(tmp_path, monkeypatch):
    path = tmp_path / "team-grouping-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    db = CompCoachDB(path)
    meet, events = create_multi_event(
        db, ["Cadet Pools", "Junior DE", "Cadet DE"],
        coaches=("Casey", "Robin", "Sam", "Taylor"), coordinators=("Jordan",),
    )
    for event, table in zip(events, (
        "Name\tStrip #\tPool #\nEXAMPLE Pool main\tB1\t1\nEXAMPLE Pool side\tB2\t2\nEXAMPLE Pool unassigned\tB3\t3",
        "Name\tStrip #\nEXAMPLE Waiting\tA1\nEXAMPLE Called\tB1\nEXAMPLE Covered\tC1\nEXAMPLE Out\tD1\nEXAMPLE DE unassigned\tE1\nEXAMPLE Reverse coaches\tF1",
        "Name\tStrip #\nEXAMPLE Cross event\tP1\nEXAMPLE Shared cross event\tQ1",
    )):
        db.merge_import(event["id"], parse_pasted_table(table).records, "Jordan")
    athletes = {
        row["name"]: row for event in events for row in db.list_athletes(event["id"])
    }
    main, side = athletes["EXAMPLE Pool main"], athletes["EXAMPLE Pool side"]
    db.assign_athletes(main["event_id"], [main["id"]], main_coach="Sam", side_coach="Taylor", actor="Jordan")
    db.assign_athletes(side["event_id"], [side["id"]], main_coach="", side_coach="Taylor", actor="Jordan")
    for name, coaches in (
        ("EXAMPLE Waiting", ["Sam"]), ("EXAMPLE Cross event", ["Sam"]),
        ("EXAMPLE Called", ["Sam", "Taylor"]),
        ("EXAMPLE Covered", ["Sam", "Taylor"]),
        ("EXAMPLE Out", ["Sam", "Taylor"]),
        ("EXAMPLE Reverse coaches", ["Taylor", "Sam"]),
        ("EXAMPLE Shared cross event", ["Taylor", "Sam"]),
    ):
        row = athletes[name]
        db.assign_de_athletes(row["event_id"], [row["id"]], coaches=coaches, actor="Jordan")
    called = athletes["EXAMPLE Called"]
    db.report_call(called["event_id"], called["id"], status="now", location="M4", actor="Jordan")
    shared = athletes["EXAMPLE Shared cross event"]
    db.report_call(shared["event_id"], shared["id"], status="in_hole", location="Q4", actor="Jordan")
    covered = athletes["EXAMPLE Covered"]
    db.report_call(covered["event_id"], covered["id"], status="now", location="D7", actor="Jordan")
    db.cover_athlete(covered["event_id"], covered["id"], "Robin", "Jordan", location="D7")
    out = athletes["EXAMPLE Out"]
    db.mark_result(out["event_id"], out["id"], outcome="lost", actor="Jordan")
    yield db, meet, events, athletes
    st.cache_resource.clear()


def _team(meet, *, role="coach", actor="Casey", view="Uncovered"):
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    nav_named(app, "live_view_").set_value(view).run()
    assert not app.exception
    return app


def _group_card_names(app, athletes):
    groups = {}
    current = None
    for value in markdown_values(app):
        heading = re.search(r"<span[^>]*class=['\"][^'\"]*cc-team-coach-label[^'\"]*['\"][^>]*>(.*?)</span>", value)
        if heading:
            label = unescape(heading.group(1))
            assert label not in groups
            current = label
            groups[current] = []
        elif current and "class='cc-athlete'" in value:
            matches = [name for name in athletes if name in value]
            assert len(matches) == 1, value
            groups[current].append(matches[0])
    return groups


FILTERED_NAMES = {
    "Uncovered": {
        "EXAMPLE Pool main", "EXAMPLE Pool side", "EXAMPLE Pool unassigned",
        "EXAMPLE Waiting", "EXAMPLE Called", "EXAMPLE DE unassigned",
        "EXAMPLE Reverse coaches", "EXAMPLE Cross event", "EXAMPLE Shared cross event",
    },
    "Needs Coach": {"EXAMPLE Called", "EXAMPLE Shared cross event"},
    "Covered Now": {"EXAMPLE Covered"},
    "No Current Call": {
        "EXAMPLE Pool main", "EXAMPLE Pool side", "EXAMPLE Pool unassigned",
        "EXAMPLE Waiting", "EXAMPLE DE unassigned", "EXAMPLE Reverse coaches", "EXAMPLE Cross event",
    },
    "Out": {"EXAMPLE Out"},
}


@pytest.mark.parametrize("role,actor", [("coach", "Casey"), ("admin", "Casey"), ("coordinator", "Jordan")])
@pytest.mark.parametrize("view", list(FILTERED_NAMES))
def test_every_staff_view_keeps_filters_and_has_each_athlete_once(grouped_scenario, role, actor, view):
    _db, meet, _events, athletes = grouped_scenario
    app = _team(meet, role=role, actor=actor, view=view)
    groups = _group_card_names(app, athletes)
    names = [name for rows in groups.values() for name in rows]
    assert set(names) == FILTERED_NAMES[view]
    assert len(names) == len(set(names))
    keys = [button.key for button in app.button if button.key is not None]
    assert len(keys) == len(set(keys))
    for name in names:
        row = athletes[name]
        if row["phase"] == "de" and view != "Out":
            assert sum(button.key == f"won_{row['id']}" for button in app.button) == 1
            assert sum(button.key == f"lost_{row['id']}" for button in app.button) == 1


def test_pool_main_side_fallback_and_equal_shared_de_groups_cross_events(grouped_scenario):
    _db, meet, _events, athletes = grouped_scenario
    app = _team(meet)
    groups = _group_card_names(app, athletes)
    assert set(groups) == {"Coach Sam", "Coaches Sam · Taylor", "Coach Taylor", "Unassigned"}
    assert set(groups["Coach Sam"]) == {"EXAMPLE Pool main", "EXAMPLE Waiting", "EXAMPLE Cross event"}
    assert groups["Coach Taylor"] == ["EXAMPLE Pool side"]
    assert set(groups["Coaches Sam · Taylor"]) == {
        "EXAMPLE Called", "EXAMPLE Reverse coaches", "EXAMPLE Shared cross event",
    }
    assert set(groups["Unassigned"]) == {"EXAMPLE Pool unassigned", "EXAMPLE DE unassigned"}
    assert list(groups)[-1] == "Unassigned"
    assert not any(label in groups for label in {"Coach Robin", "Coach Casey"})
    assert any(section.label == "All assignments" for section in app.expander)


def test_won_rotates_to_next_round_after_waiting_athletes_within_saved_coach_group(grouped_scenario):
    db, meet, _events, athletes = grouped_scenario
    app = _team(meet, view="No Current Call")
    before = _group_card_names(app, athletes)["Coach Sam"]
    assert before.index("EXAMPLE Waiting") < before.index("EXAMPLE Cross event")
    waiting = athletes["EXAMPLE Waiting"]
    next(button for button in app.button if button.key == f"won_{waiting['id']}").click().run()
    assert not app.exception
    assert db.get_athlete(waiting["event_id"], waiting["id"])["de_wins"] == 1
    app = _team(meet, view="No Current Call")
    rows = _group_card_names(app, athletes)["Coach Sam"]
    assert rows.index("EXAMPLE Cross event") < rows.index("EXAMPLE Waiting")
    assert rows.count("EXAMPLE Waiting") == 1
    assert any("Next bout · 1 round passed" in text for text in markdown_values(app))


def test_takeover_and_actual_covering_coach_do_not_reassign_the_planned_group(grouped_scenario):
    db, meet, _events, athletes = grouped_scenario
    called = athletes["EXAMPLE Called"]
    original = db.get_athlete(called["event_id"], called["id"])["de_coaches"]
    db.take_over_athlete(called["event_id"], called["id"], "Casey", "Casey")
    app = _team(meet, role="coordinator", actor="Jordan", view="Needs Coach")
    assert "EXAMPLE Called" in _group_card_names(app, athletes)["Coaches Sam · Taylor"]
    assert "Coach Casey" not in _group_card_names(app, athletes)
    assert any("Takeover accepted by Casey" in item.value for item in app.caption)
    db.cover_athlete(called["event_id"], called["id"], "Casey", "Casey", location="M4")
    app = _team(meet, role="coordinator", actor="Jordan", view="Covered Now")
    groups = _group_card_names(app, athletes)
    assert set(groups) == {"Coaches Sam · Taylor"}
    assert set(groups["Coaches Sam · Taylor"]) == {"EXAMPLE Called", "EXAMPLE Covered"}
    assert any("With Casey" in text for text in markdown_values(app))
    assert db.get_athlete(called["event_id"], called["id"])["de_coaches"] == original


def test_busy_coach_has_one_current_card_above_the_other_coach_groups(grouped_scenario):
    db, meet, _events, athletes = grouped_scenario
    called = athletes["EXAMPLE Called"]
    db.cover_athlete(called["event_id"], called["id"], "Casey", "Casey", location="M4")
    app = _team(meet, view="Covered Now")
    cards = [value for value in markdown_values(app) if "class='cc-athlete'" in value]
    assert sum(called["name"] in value for value in cards) == 1
    assert called["name"] in cards[0]
    assert _group_card_names(app, athletes) == {"Coaches Sam · Taylor": ["EXAMPLE Covered"]}
    keys = [button.key for button in app.button if button.key is not None]
    assert len(keys) == len(set(keys))
    assert keys.count(f"current_won_{called['id']}") == 1
    assert keys.index(f"current_won_{called['id']}") < keys.index(f"won_{athletes['EXAMPLE Covered']['id']}")
    assert not app.exception
