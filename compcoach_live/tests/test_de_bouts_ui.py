"""Staff can mark an AFM pairing and safely record both DE results."""

import pytest
import streamlit as st

from compcoach_live import de_bout_controls
from compcoach_live.de_bouts import create_de_bout, list_de_bouts
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import (
    button_named,
    create_multi_event,
    keyed,
    markdown_values,
    nav_named,
    open_board,
)


@pytest.fixture
def paired_scenario(tmp_path, monkeypatch):
    path = tmp_path / "afm-pairing-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"], coaches=("Carmine", "Sam"))
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\nTEST Alex\tP1\nTEST Robin\tP2\nTEST Taylor\tP3").records,
        "Carmine",
    )
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine")
    rows = {row["name"]: row for row in database.list_athletes(event["id"])}
    yield database, meet, event, rows["TEST Alex"], rows["TEST Robin"], rows["TEST Taylor"]
    st.cache_resource.clear()


def _open_live(meet, role="coach", actor="Sam"):
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    if nav_named(app, f"{role}_nav_").value != "Live":
        nav_named(app, f"{role}_nav_").set_value("Live").run()
    assert not app.exception
    return app


def _bout_sections(app, pending_count):
    sections = [
        section for section in app.expander
        if section.label.startswith("AFM vs AFM ·") and section.label.endswith(f"· {pending_count} pending")
    ]
    assert len(sections) == 1
    return sections[0]


def _make_pair(database, event, alex, robin):
    return create_de_bout(database, event["id"], alex["id"], robin["id"], actor="Sam", round_label="T32")


def _result_state(database, event, athlete):
    row = database.get_athlete(event["id"], athlete["id"])
    return row["active_state"], row["de_wins"], row["last_de_result"]


@pytest.mark.parametrize(("role", "actor"), [("coach", "Sam"), ("admin", "Carmine"), ("coordinator", "Irina")])
def test_every_staff_role_can_mark_pair_and_other_coach_sees_opponent_badges(paired_scenario, role, actor):
    database, meet, event, alex, robin, taylor = paired_scenario
    app = _open_live(meet, role, actor)
    keyed(app.selectbox, "live_first_").set_value(alex["id"])
    keyed(app.selectbox, "live_second_").set_value(robin["id"])
    keyed(app.text_input, "live_round_").set_value("T32")
    button_named(app, "Review pairing").click().run()
    assert not list_de_bouts(database, event["id"])
    assert any("TEST Alex vs TEST Robin" in warning.value and "T32" in warning.value for warning in app.warning)
    button_named(app, "Confirm pairing").click().run()

    bouts = list_de_bouts(database, event["id"])
    assert len(bouts) == 1 and bouts[0]["status"] == "pending"
    assert bouts[0]["created_by"] == actor
    assert bouts[0]["round_label"] == "T32"
    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert not app.exception
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    badge_markup = "\n".join(value for value in markdown_values(observer) if 'class="cc-afm-bout-badge"' in value)
    assert "AFM vs AFM · TEST Robin · T32" in badge_markup
    assert "AFM vs AFM · TEST Alex · T32" in badge_markup
    assert "TEST Taylor" not in badge_markup
    _bout_sections(observer, 1)
    assert not observer.exception


def test_joint_winner_result_updates_both_and_undo_restores_shared_view(paired_scenario):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    untouched = database.get_athlete(event["id"], taylor["id"])
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    app = _open_live(meet)
    button_named(app, "TEST Alex wins").click().run()
    assert _result_state(database, event, alex) == ("active", 1, "won")
    assert _result_state(database, event, robin) == ("eliminated", 0, "lost")
    assert database.get_athlete(event["id"], taylor["id"]) == untouched
    assert not app.exception
    # Model a different device's next full refresh, including its own group.
    observer.run()
    cards = "\n".join(value for value in markdown_values(observer) if "class='cc-athlete'" in value)
    assert "TEST Alex" in cards and "TEST Robin" not in cards
    assert "TEST Alex" in "\n".join(markdown_values(observer))
    assert not any("Ready for next bout" in button.label for button in observer.button)
    assert not any('class="cc-afm-bout-badge"' in value for value in markdown_values(observer))
    assert any(section.label == "Completed / Out · 1" for section in observer.expander)
    app = _open_live(meet)
    button_named(app, "Undo both results").click().run()
    assert _result_state(database, event, robin)[0] == "eliminated"
    button_named(app, "Confirm undo both").click().run()

    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert all(database.get_athlete(event["id"], row["id"])["de_coaches"] == ["Carmine", "Sam"] for row in (alex, robin))
    assert list_de_bouts(database, event["id"])[0]["status"] == "pending"
    assert not app.exception
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    cards = "\n".join(value for value in markdown_values(observer) if "class='cc-athlete'" in value)
    assert "TEST Alex" in cards and "TEST Robin" in cards
    assert not observer.exception


@pytest.mark.parametrize("outcome", ["won", "lost"])
def test_individual_won_or_lost_resolves_the_linked_opponent_together(paired_scenario, outcome):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    untouched = database.get_athlete(event["id"], taylor["id"])
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    keyed(app.button, f"{outcome}_{alex['id']}").click().run()
    assert not app.exception
    expected_alex = ("active", 1, "won") if outcome == "won" else ("eliminated", 0, "lost")
    expected_robin = ("eliminated", 0, "lost") if outcome == "won" else ("active", 1, "won")
    assert _result_state(database, event, alex) == expected_alex
    assert _result_state(database, event, robin) == expected_robin
    assert database.get_athlete(event["id"], taylor["id"]) == untouched
    assert list_de_bouts(database, event["id"])[0]["status"] == "resolved"


def test_stale_individual_result_tap_is_rejected_when_only_opponent_changes(paired_scenario):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    database.report_call(event["id"], robin["id"], status="on_deck", location="Q7", actor="Irina")
    keyed(app.button, f"won_{alex['id']}").click().run()

    assert not any((button.key or "").startswith(f"confirm_won_{alex['id']}") for button in app.button)
    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert database.get_athlete(event["id"], robin["id"])["live_location"] == "Q7"
    assert not app.exception


def test_individual_fast_result_race_does_not_overwrite_opponent(paired_scenario, monkeypatch):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    original_mark_result = CompCoachDB.mark_result

    def concurrent_mark_result(self, *args, **kwargs):
        database.report_call(event["id"], robin["id"], status="now", location="Q7", actor="Irina")
        return original_mark_result(self, *args, **kwargs)

    monkeypatch.setattr(CompCoachDB, "mark_result", concurrent_mark_result)
    keyed(app.button, f"won_{alex['id']}").click().run()

    assert not app.exception
    assert app.error
    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert database.get_athlete(event["id"], robin["id"])["live_location"] == "Q7"
    assert list_de_bouts(database, event["id"])[0]["status"] == "pending"


def test_opponent_changed_on_another_phone_invalidates_stale_fast_winner_tap(paired_scenario):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    app = _open_live(meet)
    database.report_call(event["id"], robin["id"], status="on_deck", location="P9", actor="Irina")
    button_named(app, "TEST Alex wins").click().run()

    assert not any(button.label == "Confirm winner" for button in app.button)
    assert app.error
    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert database.get_athlete(event["id"], robin["id"])["live_location"] == "P9"
    assert not app.exception


def test_one_tap_winner_race_preserves_both_athletes_and_new_opponent_update(paired_scenario, monkeypatch):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    app = _open_live(meet)
    original_resolve = de_bout_controls.resolve_de_bout

    def concurrent_resolve(*args, **kwargs):
        database.report_call(event["id"], robin["id"], status="now", location="Q7", actor="Irina")
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(de_bout_controls, "resolve_de_bout", concurrent_resolve)
    button_named(app, "TEST Alex wins").click().run()

    assert not app.exception
    assert app.error
    assert _result_state(database, event, alex) == ("active", 0, "")
    assert _result_state(database, event, robin) == ("active", 0, "")
    assert database.get_athlete(event["id"], robin["id"])["live_location"] == "Q7"
    assert list_de_bouts(database, event["id"])[0]["status"] == "pending"


def test_remove_pairing_and_restore_it_never_changes_athlete_results(paired_scenario):
    database, meet, event, alex, robin, taylor = paired_scenario
    _make_pair(database, event, alex, robin)
    before = database.list_athletes(event["id"])
    app = _open_live(meet)
    button_named(app, "Remove pairing").click().run()
    button_named(app, "Confirm removal").click().run()
    assert not list_de_bouts(database, event["id"])
    assert database.list_athletes(event["id"]) == before
    app = _open_live(meet)
    button_named(app, "Restore pairing").click().run()
    assert list_de_bouts(database, event["id"])[0]["status"] == "pending"
    assert database.list_athletes(event["id"]) == before
    assert not app.exception


def test_after_pairing_saved_form_can_mark_another_pair_without_stale_selection(paired_scenario):
    database, meet, event, alex, robin, taylor = paired_scenario
    database.merge_import(event["id"], parse_pasted_table("Name\tStrip #\nTEST Casey\tQ1").records, "Carmine")
    casey = next(row for row in database.list_athletes(event["id"]) if row["name"] == "TEST Casey")
    app = _open_live(meet)
    keyed(app.selectbox, "live_first_").set_value(alex["id"])
    keyed(app.selectbox, "live_second_").set_value(robin["id"])
    button_named(app, "Review pairing").click().run()
    button_named(app, "Confirm pairing").click().run()
    assert not app.exception
    first = keyed(app.selectbox, "live_first_")
    second = keyed(app.selectbox, "live_second_")
    assert first.value == second.value == ""
    assert "TEST Alex" not in first.options and "TEST Robin" not in second.options
    first.set_value(taylor["id"])
    second.set_value(casey["id"])
    button_named(app, "Review pairing").click().run()
    button_named(app, "Confirm pairing").click().run()
    assert not app.exception
    assert len(list_de_bouts(database, event["id"])) == 2
    _bout_sections(app, 2)
