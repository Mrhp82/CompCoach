"""An athlete card can report the same AFM bout as the shared team section.

Exercise the real application, including reviewed snapshots from another phone
and training recognition. Ordinary results remain atomic for both opponents.
"""

from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st

from compcoach_live import training
from compcoach_live.de_bouts import create_de_bout, list_de_bouts
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    create_multi_event,
    keyed,
    markdown_values,
    nav_named,
    open_board,
)
from compcoach_live.tests.test_training import first_half, make_run, step, target


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "athlete-pairing-app.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


@pytest.fixture
def scenario(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\nTEST Alex\tP1\nTEST Robin\tP2\nTEST Taylor\tP3"
        ).records,
        "Carmine",
    )
    database.assign_de_pods(
        event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine",
    )
    athletes = {row["name"]: row for row in database.list_athletes(event["id"])}
    return database, meet, event, athletes["TEST Alex"], athletes["TEST Robin"], athletes["TEST Taylor"]


def _open(meet, view="My Group", *, role="coach", actor="Sam", archive=False):
    app = open_board(meet["id"], meet[f"{role}_token"], actor, archive=archive)
    navigation = nav_named(app, f"{role}_nav_")
    if navigation.value != view:
        navigation.set_value(view).run()
    assert not app.exception
    return app


def _review(app, athlete, opponent, *, prefix="personal", round_label="T32"):
    base = f"{prefix}_athlete_pair_{athlete['id']}"
    keyed(app.selectbox, f"{base}_opponent").set_value(opponent["id"])
    keyed(app.text_input, f"{base}_round").set_value(round_label)
    keyed(app.button, f"{base}_review").click().run()
    assert not app.exception
    assert any(
        athlete["name"] in warning.value and opponent["name"] in warning.value
        for warning in app.warning
    )
    return base


def _state(database, athlete):
    row = database.get_athlete(athlete["event_id"], athlete["id"])
    return row["active_state"], row["de_wins"], row["last_de_result"]


@pytest.mark.parametrize("view,prefix", [("My Group", "personal"), ("Live", "live")])
def test_inline_pairing_from_either_real_coach_view_is_shared_without_changing_results(scenario, view, prefix):
    database, meet, event, alex, robin, taylor = scenario
    before = database.list_athletes(event["id"])
    app = _open(meet, view)
    assert any(section.label == f"More actions · {alex['name']}" for section in app.expander)
    base = _review(app, alex, robin, prefix=prefix)
    assert not list_de_bouts(database, event["id"])
    keyed(app.button, f"{base}_confirm").click().run()
    assert not app.exception

    bouts = list_de_bouts(database, event["id"])
    assert len(bouts) == 1
    assert bouts[0]["status"] == "pending" and bouts[0]["round_label"] == "T32"
    assert bouts[0]["created_by"] == "Sam"
    assert database.list_athletes(event["id"]) == before

    observer = _open(meet, actor="Carmine")
    badges = "\n".join(
        value for value in markdown_values(observer) if 'class="cc-afm-bout-badge"' in value
    )
    assert "AFM vs AFM · TEST Robin · T32" in badges
    assert "AFM vs AFM · TEST Alex · T32" in badges
    assert taylor["name"] not in badges
    assert any(
        section.label.startswith("AFM vs AFM ·") and section.label.endswith("· 1 pending")
        for section in observer.expander
    )
    assert not any(
        (button.key or "").startswith(f"personal_athlete_pair_{alex['id']}_review")
        for button in observer.button
    )


def test_card_won_after_inline_pairing_resolves_both_opponents_once(scenario):
    database, meet, event, alex, robin, taylor = scenario
    unchanged = database.get_athlete(event["id"], taylor["id"])
    app = _open(meet)
    base = _review(app, alex, robin)
    keyed(app.button, f"{base}_confirm").click().run()
    assert not app.exception
    keyed(app.button, f"won_{alex['id']}").click().run()
    assert not app.exception
    assert _state(database, alex) == ("active", 1, "won")
    assert _state(database, robin) == ("eliminated", 0, "lost")
    assert database.get_athlete(event["id"], taylor["id"]) == unchanged
    assert list_de_bouts(database, event["id"])[0]["status"] == "resolved"
    observer = _open(meet, actor="Carmine")
    assert not any('class="cc-afm-bout-badge"' in value for value in markdown_values(observer))
    assert any(section.label == "Completed / Out · 1" for section in observer.expander)


def test_stale_inline_confirmation_cannot_adopt_a_newer_opponent_call(scenario):
    database, meet, event, alex, robin, _taylor = scenario
    app = _open(meet)
    base = _review(app, alex, robin)
    database.report_call(event["id"], robin["id"], status="now", location="Q7", actor="Irina")
    newest = database.get_athlete(event["id"], robin["id"])
    keyed(app.button, f"{base}_confirm").click().run()
    assert not app.exception
    assert app.error
    assert not list_de_bouts(database, event["id"])
    assert database.get_athlete(event["id"], robin["id"]) == newest
    assert _state(database, alex) == ("active", 0, "")
    assert not any(button.key == f"{base}_confirm" for button in app.button)


def test_cancel_inline_review_keeps_both_athletes_and_shared_pairing_history_unchanged(scenario):
    database, meet, event, alex, robin, _taylor = scenario
    before = database.list_athletes(event["id"])
    app = _open(meet)
    base = _review(app, alex, robin)
    keyed(app.button, f"{base}_cancel").click().run()
    assert not app.exception
    assert not list_de_bouts(database, event["id"])
    assert database.list_athletes(event["id"]) == before
    assert any(button.key == f"{base}_review" for button in app.button)
    assert not any(button.key == f"{base}_confirm" for button in app.button)


def test_archived_admin_can_review_shared_pairing_but_has_no_inline_action_widgets(scenario):
    database, meet, event, alex, robin, _taylor = scenario
    create_de_bout(database, event["id"], alex["id"], robin["id"], actor="Sam", round_label="T32")
    before = list_de_bouts(database, event["id"])
    database.finish_meet(meet["id"], "Carmine")
    app = _open(meet, "Live", role="admin", actor="Carmine", archive=True)
    assert any("archive is read-only" in item.value for item in app.info)
    assert any(section.label.startswith("AFM vs AFM ·") for section in app.expander)
    assert not any("_athlete_pair_" in (widget.key or "") for widget in app.button)
    assert not any("_athlete_pair_" in (widget.key or "") for widget in app.selectbox)
    assert list_de_bouts(database, event["id"]) == before


@pytest.mark.parametrize("view,prefix", [("My Group", "personal"), ("Live", "live")])
def test_training_pairing_lesson_recognizes_the_ordinary_inline_card_form(database, monkeypatch, view, prefix):
    """Reach the lesson through domain commands, then perform its public form."""
    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    monkeypatch.setattr(training, "utc_now", lambda: clock[0].isoformat())

    def advance(seconds):
        clock[0] += timedelta(seconds=seconds)

    _real, _hub, run = make_run(database)
    first_half(database, run, advance)
    actor = training.get_training(database, run["id"])["learner"]
    reassigned = target(database, run, "reassigned_id")
    database.cover_athlete(reassigned["event_id"], reassigned["id"], actor, actor, location="E2")
    database.mark_result(reassigned["event_id"], reassigned["id"], outcome="won", actor=actor)
    step(database, run, 13)
    bye = target(database, run, "bye_id")
    database.mark_bye(bye["event_id"], bye["id"], actor=actor)
    step(database, run, 14)
    a, b = target(database, run, "pair_a_id"), target(database, run, "pair_b_id")

    app = _open(run, view, actor=actor)
    base = _review(app, a, b, prefix=prefix, round_label="Practice round")
    assert training.get_training(database, run["id"])["stage"] == 14
    assert nav_named(app, "coach_nav_").value == view
    guides = [
        element.proto.body for element in app.get("html")
        if 'class="cc-practice-guide"' in element.proto.body
    ]
    assert len(guides) == 1
    assert f"In More actions · {a['name']}, tap Confirm AFM pairing." in guides[0]
    assert f'data-guide-view="{view}"' in guides[0]
    keyed(app.button, f"{base}_confirm").click().run()
    assert not app.exception
    assert nav_named(app, "coach_nav_").value == view
    assert training.get_training(database, run["id"])["stage"] == 15
    bouts = list_de_bouts(database, a["event_id"])
    assert len(bouts) == 1 and bouts[0]["created_by"] == actor
    assert {bouts[0]["athlete_a_id"], bouts[0]["athlete_b_id"]} == {a["id"], b["id"]}
