"""Equal DE coach groups stay consistent throughout the shared app."""

import pytest
import streamlit as st

from compcoach_live.de_bouts import create_de_bout
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
def database(tmp_path, monkeypatch):
    path = tmp_path / "equal-de-coaches.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    yield CompCoachDB(path)
    st.cache_resource.clear()


def _import_de(database, event, *, pods=("P",)):
    lines = ["Name\tStrip #"]
    lines.extend(f"TEST Athlete{index}\t{pod}1" for index, pod in enumerate(pods, 1))
    database.merge_import(event["id"], parse_pasted_table("\n".join(lines)).records, "Carmine")
    return database.list_athletes(event["id"])


def _open_de_assignments(meet, event):
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    keyed(app.get("button_group"), f"assignment_phase_{event['id']}").set_value("Direct Elimination").run()
    assert not app.exception
    return app


def _active_cards(app):
    return "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)


def _visible_text(app):
    return "\n".join([*markdown_values(app), *(caption.value for caption in app.caption)])


def test_four_equal_pod_coaches_receive_same_athlete_and_event_highlight(database):
    coaches = ["Carmine", "Sam", "Vivien", "Igor"]
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"], coaches=coaches)
    event = events[0]
    athlete = _import_de(database, event)[0]
    app = _open_de_assignments(meet, event)
    keyed(app.multiselect, "de_assignment_pod_coaches_").set_value(coaches)
    button_named(app, "Save pod coaches").click().run()

    assert not app.exception
    assert database.get_athlete(event["id"], athlete["id"])["de_coaches"] == coaches
    assert database.list_pod_assignments(event["id"], phase="de")[0]["coaches"] == coaches
    assert not any(widget.label in {"Main coach", "Side coach"} for widget in app.selectbox)

    # Neither the third nor the fourth peer may disappear from their own group,
    # the assigned event highlight, or the meet-wide workload/availability model.
    for coach in coaches[2:]:
        board = open_board(meet["id"], meet["coach_token"], coach)
        assert athlete["name"] in _active_cards(board)
        markup = "\n".join(markdown_values(board))
        assert "◆ Pod coach" in markup
        assert "Main:" not in markup and "Side:" not in markup
        highlight = next(value for value in markdown_values(board) if "cc-event-status-mine" in value and "Junior Epee" in value)
        assert "YOUR EVENT" in highlight
        assert any("1 active work" in caption.value and "1 DE" in caption.value for caption in board.caption)
        status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == coach)
        assert status["unfinished_count"] == 1
        assert status["suggested_available"] is False
        assert not board.exception


@pytest.mark.parametrize("pod_count", [1, 2, 3, 4])
def test_coach_can_optionally_cover_one_to_four_pods_in_one_action(database, pod_count):
    meet, events = create_multi_event(database, ["Junior Epee"], coaches=("Carmine", "Sam", "Vivien"))
    event = events[0]
    pods = ["P", "Q", "R", "S"]
    _import_de(database, event, pods=pods)
    database.assign_de_pods(event["id"], pods=pods, coaches=["Vivien"], actor="Carmine")
    app = _open_de_assignments(meet, event)
    keyed(app.radio, "de_assignment_mode_").set_value("Coach → pods").run()
    keyed(app.selectbox, "de_assignment_multi_coach_").set_value("Sam")
    keyed(app.multiselect, "de_assignment_multi_pods_").set_value(pods[:pod_count])
    button_named(app, "Add coach to selected pods").click().run()

    assert not app.exception
    assert keyed(app.multiselect, "de_assignment_multi_pods_").value == []
    assignments = {row["pod"]: row["coaches"] for row in database.list_pod_assignments(event["id"], phase="de")}
    assert all(assignments[pod] == (["Vivien", "Sam"] if pod in pods[:pod_count] else ["Vivien"]) for pod in pods)
    board = open_board(meet["id"], meet["coach_token"], "Sam")
    cards = _active_cards(board)
    athletes = database.list_athletes(event["id"])
    assert sum(row["name"] in cards for row in athletes) == pod_count
    assert any(f"{pod_count} active work" in caption.value for caption in board.caption)
    assert not board.exception


def test_team_plan_and_whatsapp_show_all_peers_and_keep_pool_main_side_style(database):
    coaches = ["Carmine", "Sam", "Vivien", "Igor"]
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"], coaches=coaches)
    de, pools = events
    athlete = _import_de(database, de)[0]
    database.assign_de_pods(de["id"], pods=["P"], coaches=coaches, actor="Carmine")
    database.merge_import(pools["id"], parse_pasted_table("Name\tStrip #\tPool #\nPOOL Example\tB2\t1").records, "Carmine")
    pool_athlete = database.list_athletes(pools["id"])[0]
    database.assign_athletes(pools["id"], [pool_athlete["id"]], main_coach="Carmine", side_coach="Sam", actor="Carmine")
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Live").run()
    de_section = next(section for section in app.expander if "Junior Epee · 1 active" in section.label)
    peer_markup = "\n".join(value for value in markdown_values(de_section) if "cc-plan-group" in value)
    assert peer_markup.count(f"◆ {athlete['name']}") == 4
    assert all(f"🤺 {coach} · 1" in peer_markup for coach in coaches)
    assert "Main:" not in peer_markup and "Side:" not in peer_markup
    assert "<b>" not in peer_markup
    pool_section = next(section for section in app.expander if "Cadet Epee · 1 active" in section.label)
    pool_markup = "\n".join(value for value in markdown_values(pool_section) if "cc-plan-group" in value)
    assert "🔹 <b>POOL Example</b>" in pool_markup
    assert "🔸 POOL Example" in pool_markup
    assert "Main: Carmine" in pool_markup and "Side: Sam" in pool_markup

    nav_named(app, "admin_nav_").set_value("Live").run()
    sector_markup = "\n".join(value for value in markdown_values(app) if "Sector P" in value)
    assert all(coach in sector_markup for coach in coaches)
    assert "Coaches:" in sector_markup
    assert "Main:" not in sector_markup and "Side:" not in sector_markup

    nav_named(app, "admin_nav_").set_value("Share").run()
    message = "\n".join(code.value for code in app.code)
    de_message = message.split("*DIRECT ELIMINATION*", 1)[1].split("📍 *CADET EPEE*", 1)[0]
    assert "◆ *POD P*" in de_message
    assert all(coach in de_message for coach in coaches)
    assert "Main" not in de_message and "Side" not in de_message
    assert not any(f"*{coach.upper()}*" in de_message for coach in coaches)
    assert "🤺 *CARMINE*" in message
    assert "🤺 SAM" in message
    assert "🔹 B2: POOL Example [S: SAM]" in message
    assert "🔸 B2: POOL Example [M: *CAR*]" in message
    assert not app.exception


def test_available_coach_deployment_adds_fourth_peer_without_replacing_three(database):
    coaches = ["Carmine", "Sam", "Vivien", "Igor"]
    meet, events = create_multi_event(database, ["Junior Epee"], coaches=coaches)
    event = events[0]
    _import_de(database, event)
    database.assign_de_pods(event["id"], pods=["P"], coaches=coaches[:3], actor="Carmine")
    database.set_coach_availability(meet["id"], "Igor", True, "Igor")
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    assert any("Igor" in value for value in markdown_values(coordinator) if "cc-available-coaches-banner" in value)
    button_named(coordinator, "Send Igor to Sector P").click().run()
    assert not coordinator.exception
    assert database.list_pod_assignments(event["id"], phase="de")[0]["coaches"] == coaches
    assert database.list_athletes(event["id"])[0]["de_coaches"] == coaches
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Igor")
    assert status["is_available"] is False
    observer = open_board(meet["id"], meet["coach_token"], "Sam")
    assert not any("Igor" in value for value in markdown_values(observer) if "cc-available-coaches-banner" in value)
    assert not observer.exception


def test_individual_equal_coach_override_is_editable_without_changing_pod(database):
    meet, events = create_multi_event(database, ["Junior Epee"], coaches=("Carmine", "Sam", "Vivien", "Igor"))
    event = events[0]
    athlete = _import_de(database, event)[0]
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine")
    app = _open_de_assignments(meet, event)
    keyed(app.multiselect, "de_assignment_athletes_").set_value([athlete["id"]]).run()
    keyed(app.multiselect, "de_assignment_individual_coaches_").set_value(["Vivien", "Igor"]).run()
    button_named(app, "Apply to 1 athlete").click().run()
    assert not app.exception
    assert database.get_athlete(event["id"], athlete["id"])["de_coaches"] == ["Vivien", "Igor"]
    assert database.list_pod_assignments(event["id"], phase="de")[0]["coaches"] == ["Carmine", "Sam"]
    assert keyed(app.multiselect, "de_assignment_athletes_").value == []
    assert keyed(app.multiselect, "de_assignment_athletes_").options[0].startswith("✓ TEST Athlete1")
    observer = open_board(meet["id"], meet["coach_token"], "Igor")
    assert athlete["name"] in _active_cards(observer)
    assert not observer.exception


def test_bye_is_saved_with_one_tap_and_rounds_include_byes_without_counting_a_win(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    athlete = _import_de(database, event)[0]
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    button_named(app, "Bye").click().run()
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["active_state"] == "active"
    assert saved["de_wins"] == 0 and saved["de_byes"] == 1
    assert saved["de_awaiting_next"] == 1
    assert not any(button.label == "Confirm Bye" for button in app.button)
    assert not app.exception
    for _ in range(2):
        database.report_call(event["id"], athlete["id"], status="on_deck", location="P1", actor="Sam")
        database.mark_result(event["id"], athlete["id"], outcome="won", actor="Sam")
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["de_byes"] == 1 and saved["de_wins"] == 2
    assert saved["de_rounds_passed"] == 3
    progress = "1 bye · 2 DE wins · Waiting for DE bout 3"
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert progress in _visible_text(observer)
    assert athlete["name"] in _active_cards(observer)
    assert not any("Ready for next bout" in button.label for button in observer.button)

    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Live").run()
    assert progress in _visible_text(admin)
    assignments = next(section for section in admin.expander if section.label == "All assignments")
    assert progress in "\n".join(markdown_values(assignments))
    nav_named(admin, "admin_nav_").set_value("Share").run()
    message = "\n".join(code.value for code in admin.code)
    assert progress in message
    assert not observer.exception and not admin.exception


def test_admin_can_undo_bye_and_other_coach_sees_zero_byes_without_losing_assignment(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    athlete = _import_de(database, event)[0]
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine")
    database.mark_bye(event["id"], athlete["id"], actor="Sam")
    action = next(row for row in database.recent_actions(event["id"]) if row["action"] in {"bye", "de_bye"})
    observer = open_board(meet["id"], meet["coach_token"], "Sam")
    assert "1 bye" in _visible_text(observer)
    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Setup").run()
    nav_named(admin, "admin_setup_nav_").set_value("Activity").run()
    keyed(admin.button, f"undo_action_{action['id']}").click().run()
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["active_state"] == "active"
    assert saved["de_byes"] == saved["de_wins"] == 0
    assert saved["de_coaches"] == ["Carmine", "Sam"]
    observer.run()
    assert "1 bye" not in _visible_text(observer)
    assert athlete["name"] in _active_cards(observer)
    assert not admin.exception and not observer.exception


def test_bye_is_hidden_for_both_athletes_in_a_pending_afm_bout(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    athletes = _import_de(database, event, pods=("P", "Q"))
    database.assign_de_pods(event["id"], pods=["P", "Q"], coaches=["Carmine"], actor="Carmine")
    create_de_bout(database, event["id"], athletes[0]["id"], athletes[1]["id"], actor="Carmine", round_label="T32")
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert all(row["name"] in _active_cards(app) for row in athletes)
    assert not any(button.label == "Bye" for button in app.button)
    assert not app.exception


def test_stale_bye_tap_does_not_overwrite_a_new_update_on_another_phone(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    athlete = _import_de(database, event)[0]
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine"], actor="Carmine")
    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    database.report_call(event["id"], athlete["id"], status="now", location="P9", actor="Irina")
    button_named(app, "Bye").click().run()
    assert not any(button.label == "Confirm Bye" for button in app.button)
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["de_byes"] == saved["de_wins"] == 0
    assert saved["live_location"] == "P9" and saved["call_status"] == "now"
    assert not app.exception


def test_coach_can_undo_last_bye_from_dedicated_corrections_without_crowding_group(database):
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    athlete = _import_de(database, event)[0]
    database.assign_de_pods(event["id"], pods=["P"], coaches=["Carmine", "Sam"], actor="Carmine")
    database.mark_bye(event["id"], athlete["id"], actor="Sam")
    app = open_board(meet["id"], meet["coach_token"], "Sam")
    assert not any(button.label == "Undo last bye" for button in app.button)
    keyed(app.selectbox, "personal_de_correction_athlete_").set_value(athlete["id"]).run()
    button_named(app, "Undo last result").click().run()
    assert database.get_athlete(event["id"], athlete["id"])["de_byes"] == 1
    button_named(app, "Confirm undo").click().run()
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["de_byes"] == saved["de_wins"] == 0
    assert saved["active_state"] == "active" and saved["de_coaches"] == ["Carmine", "Sam"]
    assert not app.exception
    observer = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert "1 bye" not in _visible_text(observer)
    assert athlete["name"] in _active_cards(observer)
    assert not observer.exception
