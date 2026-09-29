import io
from pathlib import Path

import streamlit as st
from PIL import Image
from streamlit.testing.v1 import AppTest

from compcoach_live import ocr_import
from compcoach_live.ocr_import import ScreenshotParseResult
from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB

APP_PATH = str(Path(__file__).parents[1] / "app.py")


def button_named(app, label):
    matches = [button for button in app.button if button.label == label]
    assert len(matches) == 1, f"Expected one {label!r} button, found {len(matches)}"
    return matches[0]


def keyed(elements, prefix):
    matches = [element for element in elements if (element.key or "").startswith(prefix)]
    assert len(matches) == 1, f"Expected one widget starting with {prefix!r}, found {len(matches)}"
    return matches[0]


def nav_named(app, prefix):
    return keyed(app.get("button_group"), prefix)


def button_group_named(app, label):
    matches = [group for group in app.get("button_group") if group.label == label]
    assert len(matches) == 1, f"Expected one {label!r} button group, found {len(matches)}"
    return matches[0]


def selectbox_named(app, label):
    matches = [widget for widget in app.selectbox if widget.label == label]
    assert len(matches) == 1, f"Expected one {label!r} selectbox, found {len(matches)}"
    return matches[0]


def markdown_values(app):
    return [item.value for item in app.markdown]


def query_value(app, name):
    value = app.query_params.get(name)
    return value[0] if isinstance(value, list) and value else value


def tiny_png(color="white"):
    payload = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(payload, format="PNG")
    return payload.getvalue()


def open_board(reference, token, who=None, *, archive=False):
    app = AppTest.from_file(APP_PATH, default_timeout=15)
    app.query_params["event"] = reference
    app.query_params["token"] = token
    if who:
        app.query_params["who"] = who
    if archive:
        app.query_params["archive"] = "1"
    return app.run()


def create_multi_event(
    database,
    names,
    coaches=("Carmine", "Sam"),
    coordinators=("Irina",),
):
    meet = database.create_meet(
        "October NAC",
        coaches,
        coordinators,
        first_event_name=names[0],
    )
    for name in names[1:]:
        database.add_meet_event(meet["id"], name)
    return database.get_meet(meet["id"]), database.list_meet_events(meet["id"])


def test_landing_creates_competition_then_requires_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(tmp_path / "landing.db"))
    st.cache_resource.clear()
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    button_named(app, "Create competition").click().run()

    assert not app.exception
    assert app.query_params["event"]
    assert app.query_params["token"]
    assert any(item.value == "Who are you?" for item in app.subheader)
    assert not app.get("button_group")

    event_id = query_value(app, "event")
    token = query_value(app, "token")
    app = open_board(event_id, token)
    button_named(app, "Carmine").click().run()
    assert query_value(app, "who") == "Carmine"
    assert nav_named(app, "admin_nav_").options == [
        "My Group",
        "Live",
        "Team Plan",
        "Setup",
        "Share",
    ]
    assert any("You are Carmine" in value for value in markdown_values(app))
    assert not app.exception


def test_identity_gate_rejects_unknown_query_and_change_returns_to_gate(
    tmp_path, monkeypatch
):
    path = tmp_path / "identity.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    event = database.create_event("Identity Test", ["Carmine", "Sam"], ["Irina"])

    app = open_board(event["id"], event["coach_token"], "Not a coach")
    assert any(item.value == "Who are you?" for item in app.subheader)
    assert not app.get("button_group")

    button_named(app, "Sam").click().run()
    assert query_value(app, "who") == "Sam"
    assert nav_named(app, "coach_nav_").value == "My Group"

    button_named(app, "Change").click().run()
    assert "who" not in app.query_params
    assert any(item.value == "Who are you?" for item in app.subheader)
    assert not app.get("button_group")
    assert not app.exception


def test_coordinator_can_publish_covered_live_call(tmp_path, monkeypatch):
    path = tmp_path / "coordinator.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    event = database.create_event("DE Test", ["Carmine", "Sam"], ["Irina"])
    parsed = parse_pasted_table("Name\tStrip #\nDING Max\tM1")
    database.merge_import(event["id"], parsed.records, "Carmine")

    app = open_board(event["id"], event["coordinator_token"], "Irina")
    keyed(app.get("button_group"), "call_status_").set_value("Now")
    selectbox_named(app, "Coverage").set_value("Sam").run()
    button_named(app, "Publish update").click().run()

    assert not app.exception
    max_row = database.list_athletes(event["id"])[0]
    assert max_row["call_status"] == "now"
    assert max_row["live_location"] == "M1"
    assert max_row["reported_by"] == "Irina"
    assert max_row["covered_by"] == "Sam"


def test_coordinator_update_keeps_current_coverage_by_default(tmp_path, monkeypatch):
    path = tmp_path / "preserve-coverage.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    event = database.create_event("DE Test", ["Carmine", "Sam"], ["Irina"])
    parsed = parse_pasted_table("Name\tStrip #\nDING Max\tM1")
    database.merge_import(event["id"], parsed.records, "Carmine")
    athlete = database.list_athletes(event["id"])[0]
    database.report_call(
        event["id"], athlete["id"], status="on_deck", location="M1", actor="Irina"
    )
    database.claim(event["id"], athlete["id"], "Sam")

    app = open_board(event["id"], event["coordinator_token"], "Irina")
    assert selectbox_named(app, "Coverage").value == "Keep current (Sam)"
    keyed(app.get("button_group"), "call_status_").set_value("Now").run()
    button_named(app, "Publish update").click().run()

    assert not app.exception
    max_row = database.get_athlete(event["id"], athlete["id"])
    assert max_row["call_status"] == "now"
    assert max_row["covered_by"] == "Sam"


def test_admin_event_list_is_hidden_without_pin(tmp_path, monkeypatch):
    path = tmp_path / "protected-landing.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_ADMIN_PIN", raising=False)
    monkeypatch.delenv("COMPCOACH_PUBLIC_URL", raising=False)
    st.cache_resource.clear()
    CompCoachDB(path).create_event("Private", ["Sam"], ["Irina"])

    app = AppTest.from_file(APP_PATH, default_timeout=15).run()

    assert not app.exception
    assert not any(button.label == "Open Admin" for button in app.button)
    assert any("Admin event list is hidden" in error.value for error in app.error)


def test_assignment_clears_selection_and_moves_completed_athlete_to_bottom(
    tmp_path, monkeypatch
):
    path = tmp_path / "assignments.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database,
        ["Y12 Women's Foil"],
        coaches=("Igor", "Carmine"),
    )
    event = events[0]
    parsed = parse_pasted_table(
        "Name\tStrip #\tTime\tPool #\n"
        "HSU Audrey\tB1\t\t2\n"
        "LEE Lizzie\tB2\t\t4\n"
    )
    database.merge_import(event["id"], parsed.records, "Carmine")
    athletes = database.list_athletes(event["id"])
    audrey = next(a for a in athletes if a["name"] == "HSU Audrey")

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    selection = keyed(app.multiselect, "selected_assign_")
    selection.set_value([audrey["id"]]).run()

    assert button_named(app, "Apply to 1 athlete").disabled

    keyed(app.selectbox, "main_").set_value("Igor")
    keyed(app.selectbox, "side_").set_value("Carmine").run()
    button_named(app, "Apply to 1 athlete").click().run()

    assert not app.exception
    selection = keyed(app.multiselect, "selected_assign_")
    assert selection.value == []
    assert selection.options[0].startswith("LEE Lizzie")
    assert selection.options[-1].startswith("✓ HSU Audrey")
    saved = database.get_athlete(event["id"], audrey["id"])
    assert saved["main_coach"] == "Igor"
    assert saved["side_coach"] == "Carmine"


def test_share_aggregates_all_events_into_one_message_and_common_links(
    tmp_path, monkeypatch
):
    path = tmp_path / "share.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_PUBLIC_URL", "https://compcoach.example")
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database,
        ["Y12 Women's Foil", "Y14 Men's Epee"],
        coaches=("Igor", "Carmine"),
    )
    foil, epee = events
    database.merge_import(
        foil["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\nHSU Audrey\tB1\t8:00 AM\t2"
        ).records,
        "Carmine",
    )
    database.merge_import(
        epee["id"],
        parse_pasted_table("Name\tStrip #\nDING Max\tM1").records,
        "Carmine",
    )
    audrey = database.list_athletes(foil["id"])[0]
    max_row = database.list_athletes(epee["id"])[0]
    database.assign_athletes(
        foil["id"],
        [audrey["id"]],
        main_coach="Igor",
        side_coach="Carmine",
        actor="Carmine",
    )
    database.assign_athletes(
        epee["id"], [max_row["id"]], main_coach="Carmine", actor="Carmine"
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Share").run()

    coach_link = app.code[0].value
    coordinator_link = app.code[1].value
    message = app.code[2].value
    assert coach_link.startswith(f"https://compcoach.example/?event={meet['id']}")
    assert coordinator_link.startswith(f"https://compcoach.example/?event={meet['id']}")
    assert foil["id"] not in coach_link and epee["id"] not in coach_link
    assert "🔹 *Main coach* · 🔸 Side coach" in message
    assert "📍 *Y12 WOMEN'S FOIL*" in message
    assert "📍 *Y14 MEN'S EPEE*" in message
    assert "🤺 *IGOR*" in message
    assert "🔹 8:00 AM · B1: HSU Audrey [S: CAR]" in message
    assert "🤺 CARMINE" in message
    assert "🔸 8:00 AM · B1: HSU Audrey [M: *IGO*]" in message
    assert "🔹 M1: DING Max" in message
    assert message.endswith(coach_link)
    assert not app.exception


def test_coach_my_group_saves_pool_result_and_global_help_crosses_events(
    tmp_path, monkeypatch
):
    path = tmp_path / "coach-dashboard.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])
    foil, epee = events
    database.merge_import(
        foil["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\nDING Max\tG1\t8:00 AM\t4"
        ).records,
        "Carmine",
    )
    database.merge_import(
        epee["id"],
        parse_pasted_table("Name\tStrip #\nNG Alexander\tP3").records,
        "Carmine",
    )
    max_row = database.list_athletes(foil["id"])[0]
    alex = database.list_athletes(epee["id"])[0]
    database.assign_athletes(
        foil["id"], [max_row["id"]], main_coach="Carmine", actor="Carmine"
    )
    database.assign_athletes(
        epee["id"], [alex["id"]], main_coach="Sam", actor="Carmine"
    )

    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    admin_group = nav_named(admin, "admin_nav_")
    assert admin_group.value == "My Group"
    assert any("⭐ Cadet Foil" in value for value in markdown_values(admin))
    assert any("🔹 Main" in value for value in markdown_values(admin))
    assert any(button.label == "🚨 Need help now" for button in admin.button)
    assert not admin.exception

    carmine = open_board(meet["id"], meet["coach_token"], "Carmine")
    my_group = nav_named(carmine, "coach_nav_")
    assert my_group.value == "My Group"
    assert any("🔹 Main" in value for value in markdown_values(carmine))
    assert any("⭐ Cadet Foil" in value for value in markdown_values(carmine))
    assert not any("⭐ Junior Epee" in value for value in markdown_values(carmine))
    assert "calc(4rem + env(safe-area-inset-top))" in carmine.markdown[0].value
    assert not any(button.label == "Out · did not advance" for button in carmine.button)
    assert any(button.label == "🚨 Need help now" for button in carmine.button)

    wins = button_group_named(carmine, "Wins")
    losses = button_group_named(carmine, "Losses")
    assert wins.options == [str(value) for value in range(7)]
    assert losses.options == [str(value) for value in range(7)]
    wins.set_value(3)
    losses.set_value(3)
    button_named(carmine, "Save pool result").click().run()
    saved = database.get_athlete(foil["id"], max_row["id"])
    assert saved["pool_wins"] == 3
    assert saved["pool_losses"] == 3

    database.request_help(epee["id"], alex["id"], "Carmine")

    st.cache_resource.clear()
    sam = open_board(meet["id"], meet["coach_token"], "Sam")
    markup = "\n".join(markdown_values(sam))
    assert "Junior Epee" in markup
    assert "NG Alexander · P3" in markup
    assert "Carmine needs help" in markup
    button_named(sam, "I’m coming").click().run()
    assert database.get_athlete(epee["id"], alex["id"])["help_acknowledged_by"] == "Sam"
    assert any("🚨 Responding" in value for value in markdown_values(sam))

    button_named(sam, "Resolved").click().run()
    resolved = database.get_athlete(epee["id"], alex["id"])
    assert resolved["help_requested_at"] is None
    assert database.get_athlete(foil["id"], max_row["id"])["help_requested_at"] is None
    assert not sam.exception


def test_paste_de_import_eliminates_missing_pool_athletes_only_when_confirmed(
    tmp_path, monkeypatch
):
    path = tmp_path / "de-whole-list-confirmation.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\n"
            "DING Max\tG1\t\t4\n"
            "OR Evan\tG2\t\t5"
        ).records,
        "Carmine",
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    keyed(app.text_area, "paste_").set_value(
        "Name\tStrip #\nDING Max\tM1"
    ).run()
    button_named(app, "Preview import").click().run()

    assert any(
        "complete Direct Elimination list" in warning.value
        for warning in app.warning
    )
    assert any("OR Evan" in value for value in markdown_values(app))
    complete = keyed(app.checkbox, "confirm_complete_de_")
    assert complete.value is False

    # Treat the first pass as a possibly partial live list. The listed athlete
    # advances, but an omitted Pools athlete must remain active.
    button_named(app, "Import 1 athletes").click().run()
    after_partial = {
        row["name"]: row for row in database.list_athletes(event["id"])
    }
    assert after_partial["DING Max"]["phase"] == "de"
    assert after_partial["DING Max"]["active_state"] == "active"
    assert after_partial["OR Evan"]["phase"] == "pools"
    assert after_partial["OR Evan"]["active_state"] == "active"

    # Re-previewing the same rows and explicitly confirming that it is the
    # complete DE list makes the omission actionable.
    st.cache_resource.clear()
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    keyed(app.text_area, "paste_").set_value(
        "Name\tStrip #\nDING Max\tM1"
    ).run()
    button_named(app, "Preview import").click().run()
    keyed(app.checkbox, "confirm_complete_de_").set_value(True).run()
    button_named(app, "Import 1 athletes · mark 1 Out").click().run()

    after_complete = {
        row["name"]: row for row in database.list_athletes(event["id"])
    }
    assert after_complete["DING Max"]["phase"] == "de"
    assert after_complete["DING Max"]["active_state"] == "active"
    assert after_complete["OR Evan"]["phase"] == "pools"
    assert after_complete["OR Evan"]["active_state"] == "eliminated"
    assert not app.exception


def test_complete_empty_de_table_can_mark_every_pool_athlete_out(
    tmp_path, monkeypatch
):
    path = tmp_path / "empty-de-whole-list.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\n"
            "DING Max\tG1\t\t4\n"
            "OR Evan\tG2\t\t5"
        ).records,
        "Carmine",
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    keyed(app.text_area, "paste_").set_value(
        "Name\tStrip #\tClub\nNo matching records found\t\t"
    ).run()
    button_named(app, "Preview import").click().run()

    assert any("By default" in item.value for item in app.info)
    assert not any(button.label.startswith("Mark 2 athlete") for button in app.button)
    confirmation = keyed(app.checkbox, "confirm_empty_de_")
    assert confirmation.value is False
    confirmation.set_value(True).run()
    button_named(app, "Mark 2 athlete(s) Out").click().run()

    athletes = database.list_athletes(event["id"])
    assert {row["active_state"] for row in athletes} == {"eliminated"}
    assert not app.exception


def test_changed_pasted_text_requires_a_fresh_preview(tmp_path, monkeypatch):
    path = tmp_path / "paste-preview-reset.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Junior Epee"])

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    pasted = keyed(app.text_area, "paste_")
    pasted.set_value("Name\tStrip #\nDING Max\tM1").run()
    button_named(app, "Preview import").click().run()
    assert any(button.label == "Import 1 athletes" for button in app.button)

    keyed(app.text_area, "paste_").set_value(
        "Name\tStrip #\nOR Evan\tP1"
    ).run()

    assert any("pasted text changed" in item.value for item in app.info)
    assert not any(button.label.startswith("Import 1 athlete") for button in app.button)
    assert database.list_athletes(database.list_meet_events(meet["id"])[0]["id"]) == []
    assert not app.exception


def test_team_plan_groups_same_named_athletes_by_event_and_marks_main_bold(
    tmp_path, monkeypatch
):
    path = tmp_path / "team-plan.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Foil"])
    for event, coach, strip in zip(events, ("Carmine", "Sam"), ("B1", "P3"), strict=True):
        database.merge_import(
            event["id"],
            parse_pasted_table(f"Name\tStrip #\nDING Max\t{strip}").records,
            "Carmine",
        )
        athlete = database.list_athletes(event["id"])[0]
        database.assign_athletes(
            event["id"], [athlete["id"]], main_coach=coach, actor="Carmine"
        )

    app = open_board(meet["id"], meet["coach_token"], "Carmine")
    nav_named(app, "coach_nav_").set_value("Team Plan").run()

    labels = [expander.label for expander in app.expander]
    assert "⭐ Cadet Foil · 1 active" in labels
    assert "Junior Foil · 1 active" in labels
    plan_markup = "\n".join(
        value for value in markdown_values(app) if "cc-plan-group" in value
    )
    assert "🤺 Carmine · 1" in plan_markup
    assert "🤺 Sam · 1" in plan_markup
    assert plan_markup.count("🔹 <b>DING Max</b>") == 2
    assert "B1" in plan_markup and "P3" in plan_markup
    assert len(database.list_athletes(events[0]["id"])) == 1
    assert len(database.list_athletes(events[1]["id"])) == 1
    assert not app.exception


def test_de_result_requires_confirmation_before_changing_athlete(tmp_path, monkeypatch):
    path = tmp_path / "de-confirmation.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    event = database.create_event("DE Test", ["Carmine"], ["Irina"])
    parsed = parse_pasted_table("Name\tStrip #\nDING Max\tM1")
    database.merge_import(event["id"], parsed.records, "Carmine")
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Carmine", actor="Carmine"
    )

    app = open_board(event["id"], event["coach_token"], "Carmine")

    assert not any(
        group.label in {"Wins", "Losses"} for group in app.get("button_group")
    )
    button_named(app, "Won").click().run()
    pending = database.get_athlete(event["id"], athlete["id"])
    assert pending["de_wins"] == 0
    assert any(button.label == "Confirm Won" for button in app.button)

    button_named(app, "Confirm Won").click().run()
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["de_wins"] == 1
    assert saved["last_de_result"] == "won"
    assert saved["active_state"] == "active"
    assert not app.exception


def test_phase_status_is_isolated_per_event_and_coaches_cannot_change_it(
    tmp_path, monkeypatch
):
    path = tmp_path / "phase-status.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])
    foil, epee = events

    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    selectbox_named(coordinator, "Event").set_value(foil["id"]).run()
    button_named(coordinator, "Start Pools").click().run()
    selectbox_named(coordinator, "Event").set_value(epee["id"]).run()
    button_named(coordinator, "Start Direct Elimination").click().run()

    foil_states = database.get_phase_states(foil["id"])
    epee_states = database.get_phase_states(epee["id"])
    assert foil_states["pools"]["started"] is True
    assert foil_states["de"]["started"] is False
    assert epee_states["pools"]["started"] is False
    assert epee_states["de"]["started"] is True
    assert foil_states["pools"]["changed_by"] == "Irina"

    st.cache_resource.clear()
    coach = open_board(meet["id"], meet["coach_token"], "Carmine")
    status_markup = [
        value
        for value in markdown_values(coach)
        if value.startswith("<div class='cc-event-status")
    ]
    assert len(status_markup) == 2
    foil_markup = next(value for value in status_markup if "Cadet Foil" in value)
    epee_markup = next(value for value in status_markup if "Junior Epee" in value)
    assert "🟢 Pools" in foil_markup and "🔴 DE" in foil_markup
    assert "🔴 Pools" in epee_markup and "🟢 DE" in epee_markup
    phase_controls = {
        "Start Pools",
        "Set Pools not started",
        "Start Direct Elimination",
        "Set Direct Elimination not started",
    }
    assert not any(button.label in phase_controls for button in coach.button)

    st.cache_resource.clear()
    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    assert any(button.label == "Set Pools not started" for button in admin.button)
    assert any(button.label == "Start Direct Elimination" for button in admin.button)
    assert not admin.exception


def test_four_event_meet_has_unique_widgets_across_primary_views(tmp_path, monkeypatch):
    path = tmp_path / "four-events.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database, ["Cadet Foil", "Junior Epee", "Y12 Sabre", "Y14 Foil"]
    )
    for index, event in enumerate(events, start=1):
        database.merge_import(
            event["id"],
            parse_pasted_table(
                f"Name\tStrip #\tTime\tPool #\nATHLETE {index}\tA{index}\t\t{index}"
            ).records,
            "Carmine",
        )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    assert (
        len(
            [
                value
                for value in markdown_values(app)
                if value.startswith("<div class='cc-event-status")
            ]
        )
        == 4
    )
    assert not app.exception

    nav_named(app, "admin_nav_").set_value("Team Plan").run()
    assert not app.exception
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Events").run()
    assert len([e for e in app.expander if e.label == "Remove empty event"]) == 0
    assert any("four-event limit" in info.value.lower() for info in app.info)
    assert not app.exception


def test_admin_zero_event_import_and_assign_show_safe_empty_state(
    tmp_path, monkeypatch
):
    path = tmp_path / "zero-events.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "Schedule Pending",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name=None,
    )
    assert database.list_meet_events(meet["id"]) == []

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    assert not app.exception
    nav_named(app, "admin_nav_").set_value("Setup").run()

    expected = (
        "No events yet. Create an event in Setup → Events before importing "
        "or assigning athletes."
    )
    assert any(expected in info.value for info in app.info)
    assert not any(widget.label == "Event to manage" for widget in app.selectbox)
    assert not app.exception

    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    assert any(expected in info.value for info in app.info)
    assert not any(widget.label == "Event to manage" for widget in app.selectbox)
    assert not app.exception

    nav_named(app, "admin_setup_nav_").set_value("Events").run()
    assert any(
        "No events yet. Add the first event when the competition schedule is ready."
        in info.value
        for info in app.info
    )
    assert not app.exception


def test_admin_can_delete_the_only_empty_event_and_return_to_zero(
    tmp_path, monkeypatch
):
    path = tmp_path / "delete-last-event.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Event 1"])

    # Opening Live initializes harmless phase-state rows; they must not make an
    # otherwise empty event impossible to remove.
    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Events").run()
    keyed(app.checkbox, "confirm_delete_event_").set_value(True).run()
    button_named(app, "Delete empty event").click().run()

    assert database.list_meet_events(meet["id"]) == []
    assert any(
        "No events yet. Add the first event when the competition schedule is ready."
        in info.value
        for info in app.info
    )
    assert not app.exception


def test_finish_day_keeps_data_read_only_and_offers_no_reopen(tmp_path, monkeypatch):
    path = tmp_path / "finish-day.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\nDING Max\tM1").records,
        "Carmine",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.request_help(event["id"], athlete["id"], "Carmine")

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()
    assert button_named(app, "Finish day only").disabled

    keyed(app.checkbox, "confirm_finish_").set_value(True).run()
    button_named(app, "Finish day only").click().run()

    archived = database.get_meet(meet["id"])
    retained = database.get_athlete(event["id"], athlete["id"])
    assert archived["status"] == "locked"
    assert archived["ended_at"]
    assert archived["ended_by"] == "Carmine"
    assert retained["name"] == "DING Max"
    assert retained["help_requested_at"]
    assert any("archive is read-only" in info.value for info in app.info)
    assert not any(button.label == "Reopen paused competition" for button in app.button)
    assert button_named(app, "Save settings").disabled
    assert not app.exception


def test_finished_day_without_successor_hides_old_live_data_from_coaches(
    tmp_path, monkeypatch
):
    path = tmp_path / "finished-waiting.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\nDING Max\tM1").records,
        "Carmine",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Sam", actor="Carmine"
    )
    database.finish_meet(meet["id"], "Carmine")

    app = open_board(meet["id"], meet["coach_token"], "Sam")

    assert any(
        "This competition day is closed. No new day has been prepared yet. "
        "This page checks automatically every 5 seconds."
        in info.value
        for info in app.info
    )
    assert not any("DING Max" in value for value in markdown_values(app))
    assert not app.get("button_group")
    assert query_value(app, "event") == meet["id"]
    assert query_value(app, "who") == "Sam"
    assert not app.exception


def test_explicit_admin_archive_view_does_not_roll_to_prepared_successor(
    tmp_path, monkeypatch
):
    path = tmp_path / "explicit-archive.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\nOLD ATHLETE\tM1").records,
        "Carmine",
    )
    successor = database.prepare_next_day(
        meet["id"],
        "October NAC Day 2",
        "2026-10-02",
        ["Junior Epee"],
        "Carmine",
    )

    app = open_board(
        meet["id"], meet["admin_token"], "Carmine", archive=True
    )

    assert query_value(app, "event") == meet["id"]
    assert query_value(app, "event") != successor["id"]
    assert query_value(app, "token") == meet["admin_token"]
    assert query_value(app, "archive") == "1"
    assert any("archive is read-only" in info.value for info in app.info)
    assert not any("October NAC Day 2" in value for value in markdown_values(app))
    assert database.list_athletes(event["id"])[0]["name"] == "OLD ATHLETE"
    assert nav_named(app, "admin_nav_").value == "My Group"
    assert not app.exception


def test_finish_and_prepare_next_day_creates_clean_successor_and_new_link(
    tmp_path, monkeypatch
):
    path = tmp_path / "prepare-next-day.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet = database.create_meet(
        "October NAC Day 1",
        ["Carmine", "Sam"],
        ["Irina"],
        first_event_name="Cadet Foil",
        competition_date="2026-10-01",
    )
    database.add_meet_event(meet["id"], "Junior Epee")
    old_events = database.list_meet_events(meet["id"])
    database.merge_import(
        old_events[0]["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\nDING Max\tG1\t8:00 AM\t4"
        ).records,
        "Carmine",
    )
    database.update_event_source_url(
        old_events[1]["id"], "https://example.test/source"
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()
    prepare_button = button_named(app, "Finish & prepare next day")
    assert not prepare_button.disabled
    prepare_button.click().run()
    assert any(
        "Confirm that the current day is finished" in warning.value
        for warning in app.warning
    )
    assert database.get_meet(meet["id"])["ended_at"] is None

    next_day_confirm = next(
        checkbox
        for checkbox in app.checkbox
        if checkbox.label
        == "I confirm that today is finished and the next day must start empty"
    )
    next_day_confirm.set_value(True).run()
    button_named(app, "Finish & prepare next day").click().run()

    old_meet = database.get_meet(meet["id"])
    successor = next(
        candidate
        for candidate in database.list_meets()
        if candidate.get("prepared_from_meet_id") == meet["id"]
    )
    new_events = database.list_meet_events(successor["id"])
    assert old_meet["ended_at"] and old_meet["status"] == "locked"
    assert successor["name"] == "October NAC Day 2"
    assert successor["competition_date"] == "2026-10-02"
    assert successor["status"] == "open"
    assert successor["active_coaches"] == old_meet["active_coaches"]
    assert successor["coordinators"] == old_meet["coordinators"]
    assert successor["timezone"] == old_meet["timezone"]
    assert successor["admin_token"] != old_meet["admin_token"]
    assert [event["name"] for event in new_events] == ["Cadet Foil", "Junior Epee"]
    assert all(event["athlete_count"] == 0 for event in new_events)
    assert all(event["source_url"] == "" for event in new_events)
    assert all(
        not database.get_phase_states(event["id"])[phase]["started"]
        for event in new_events
        for phase in ("pools", "de")
    )
    assert query_value(app, "event") == successor["id"]
    assert query_value(app, "token") == successor["admin_token"]
    assert query_value(app, "who") == "Carmine"
    assert not any(item.value == "Who are you?" for item in app.subheader)
    assert nav_named(app, "admin_nav_").value == "My Group"
    assert len(database.list_athletes(old_events[0]["id"])) == 1
    assert not app.exception


def test_old_nonadmin_links_roll_to_latest_day_and_preserve_valid_identity(
    tmp_path, monkeypatch
):
    path = tmp_path / "rollover-chain.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    day_one, _ = create_multi_event(database, ["Cadet Foil"])
    day_two = database.prepare_next_day(
        day_one["id"],
        "October NAC Day 2",
        "2026-10-02",
        ["Junior Epee"],
        "Carmine",
    )
    day_three = database.prepare_next_day(
        day_two["id"],
        "October NAC Day 3",
        "2026-10-03",
        ["Y14 Foil"],
        "Carmine",
    )

    for role, actor, nav_prefix in (
        ("coach", "Sam", "coach_nav_"),
        ("coordinator", "Irina", "coordinator_nav_"),
    ):
        app = open_board(day_one["id"], day_one[f"{role}_token"], actor)

        assert query_value(app, "event") == day_three["id"]
        assert query_value(app, "token") == day_three[f"{role}_token"]
        assert query_value(app, "who") == actor
        assert "archive" not in app.query_params
        assert nav_named(app, nav_prefix)
        assert any("October NAC Day 3" in value for value in markdown_values(app))
        assert not app.exception


def test_nonadmin_cannot_force_archive_and_removed_identity_is_cleared(
    tmp_path, monkeypatch
):
    path = tmp_path / "rollover-identity.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    day_one, _ = create_multi_event(database, ["Cadet Foil"])
    day_two = database.prepare_next_day(
        day_one["id"],
        "October NAC Day 2",
        "2026-10-02",
        [],
        "Carmine",
    )
    database.update_meet_staff(
        day_two["id"],
        name=day_two["name"],
        active_coaches=["Carmine"],
        coordinators=day_two["coordinators"],
        timezone_name=day_two["timezone"],
    )

    app = open_board(
        day_one["id"], day_one["coach_token"], "Sam", archive=True
    )

    assert query_value(app, "event") == day_two["id"]
    assert query_value(app, "token") == day_two["coach_token"]
    assert "archive" not in app.query_params
    assert "who" not in app.query_params
    assert any(item.value == "Who are you?" for item in app.subheader)
    assert not app.exception


def test_screenshot_preview_requires_review_and_imports_into_selected_event(
    tmp_path, monkeypatch
):
    path = tmp_path / "screenshot-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])
    target = events[1]
    fake_result = ScreenshotParseResult(
        phase="pools",
        rows=[
            {
                "name": "AGLIPAY Alyssa",
                "strip": "C3",
                "time": "",
                "pool": "7",
                "pod": "",
                "phase": "pools",
                "needs_review": False,
            }
        ],
        diagnostics={
            "errors": [],
            "warnings": [],
            "engine": "test OCR",
            "review_cells": [],
        },
    )
    monkeypatch.setattr(ocr_import, "parse_screenshot", lambda _payload: fake_result)

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    selectbox_named(app, "Event to manage").set_value(target["id"]).run()
    app.file_uploader[0].upload("table.png", tiny_png(), "image/png").run()
    button_named(app, "Read screenshot").click().run()

    assert any("1 row(s) detected with test OCR" in item.value for item in app.success)
    assert selectbox_named(app, "Confirm competition phase").value == "Pools"
    preview = app.dataframe[0].value
    assert preview.loc[0, "Name"] == "AGLIPAY Alyssa"
    assert preview.loc[0, "Strip #"] == "C3"
    assert preview.loc[0, "Pool #"] == "7"
    import_button = button_named(app, "Import 1 screenshot row(s)")
    assert import_button.disabled

    keyed(app.checkbox, "ocr_reviewed_").set_value(True).run()
    button_named(app, "Import 1 screenshot row(s)").click().run()

    assert database.list_athletes(events[0]["id"]) == []
    imported = database.list_athletes(target["id"])
    assert len(imported) == 1
    assert imported[0]["name"] == "AGLIPAY Alyssa"
    assert imported[0]["source_strip"] == "C3"
    assert imported[0]["pool_no"] == "7"
    assert imported[0]["phase"] == "pools"
    assert not app.exception


def test_new_screenshot_resets_phase_editor_and_review_approval(tmp_path, monkeypatch):
    path = tmp_path / "screenshot-reset.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\nOR Evan\tG2\t\t5"
        ).records,
        "Carmine",
    )
    pool_payload = tiny_png("white")
    de_payload = tiny_png("black")
    pool_result = ScreenshotParseResult(
        phase="pools",
        rows=[
            {
                "name": "OR Evan",
                "strip": "G2",
                "time": "",
                "pool": "5",
                "pod": "",
                "phase": "pools",
                "needs_review": False,
            }
        ],
        diagnostics={
            "errors": [],
            "warnings": [],
            "engine": "test OCR",
            "review_cells": [],
            "image": {"format": "PNG"},
        },
    )
    de_result = ScreenshotParseResult(
        phase="de",
        rows=[
            {
                "name": "DING Max",
                "strip": "M1",
                "time": "",
                "pool": "",
                "pod": "M",
                "phase": "de",
                "needs_review": False,
            }
        ],
        diagnostics={
            "errors": [],
            "warnings": [],
            "engine": "test OCR",
            "review_cells": [],
            "image": {"format": "PNG"},
        },
    )
    monkeypatch.setattr(
        ocr_import,
        "parse_screenshot",
        lambda payload: pool_result if payload == pool_payload else de_result,
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    app.file_uploader[0].upload("pools.png", pool_payload, "image/png").run()
    button_named(app, "Read screenshot").click().run()
    assert selectbox_named(app, "Confirm competition phase").value == "Pools"
    keyed(app.checkbox, "ocr_reviewed_").set_value(True).run()
    assert not button_named(app, "Import 1 screenshot row(s)").disabled

    app.file_uploader[0].upload("de.png", de_payload, "image/png").run()
    assert any("screenshot changed" in item.value for item in app.info)
    assert not any(
        widget.label == "Confirm competition phase" for widget in app.selectbox
    )
    assert not any(button.label.startswith("Import 1 screenshot") for button in app.button)

    button_named(app, "Read screenshot").click().run()
    assert selectbox_named(app, "Confirm competition phase").value == "Direct Elimination"
    assert keyed(app.checkbox, "ocr_reviewed_").value is False
    assert button_named(app, "Import 1 screenshot row(s)").disabled
    assert any("OR Evan" in value for value in markdown_values(app))

    keyed(app.checkbox, "ocr_reviewed_").set_value(True).run()
    app.file_uploader[0].upload("pools-again.png", pool_payload, "image/png").run()
    button_named(app, "Read screenshot").click().run()
    assert selectbox_named(app, "Confirm competition phase").value == "Pools"
    assert keyed(app.checkbox, "ocr_reviewed_").value is False
    assert not app.exception


def test_invalid_screenshot_is_validated_before_visual_preview(tmp_path, monkeypatch):
    path = tmp_path / "invalid-screenshot.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Junior Epee"])

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    app.file_uploader[0].upload("broken.png", b"not an image", "image/png").run()

    assert not app.exception
    button_named(app, "Read screenshot").click().run()
    assert any("not a readable screenshot" in item.value for item in app.error)
    assert not app.exception


def test_screenshot_libgl_failure_shows_codespace_recovery(tmp_path, monkeypatch):
    path = tmp_path / "screenshot-libgl.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Junior Epee"])
    fake_result = ScreenshotParseResult(
        phase="unknown",
        rows=[],
        diagnostics={
            "errors": [
                (
                    "The OCR engine cannot start because this server is missing "
                    "libGL.so.1."
                )
            ],
            "warnings": [],
            "engine": "",
            "review_cells": [],
            "engine_attempts": [
                {
                    "engine": "rapidocr",
                    "status": "failed",
                    "detail": (
                        "ImportError: libGL.so.1: cannot open shared object file"
                    ),
                }
            ],
        },
    )
    monkeypatch.setattr(ocr_import, "parse_screenshot", lambda _payload: fake_result)

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Import").run()
    app.file_uploader[0].upload("table.png", tiny_png(), "image/png").run()
    button_named(app, "Read screenshot").click().run()

    assert any("missing libGL.so.1" in item.value for item in app.error)
    assert any("Codespace recovery" in item.value for item in app.info)
    assert any("sudo apt-get install -y libgl1" in item.value for item in app.code)
    assert any(
        expander.label == "OCR technical details" for expander in app.expander
    )
    assert not app.exception


def test_legacy_child_link_opens_shared_meet_and_all_event_statuses(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy-routing.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])
    legacy_child = events[1]

    app = open_board(legacy_child["id"], legacy_child["coach_token"], "Sam")

    markup = "\n".join(markdown_values(app))
    assert "🤺 October NAC" in markup
    assert "Cadet Foil" in markup
    assert "Junior Epee" in markup
    assert nav_named(app, "coach_nav_").value == "My Group"
    assert query_value(app, "event") == legacy_child["id"]
    assert meet["id"] != legacy_child["id"]
    assert not app.exception


def test_stale_de_confirmation_is_cancelled_after_another_phone_update(
    tmp_path, monkeypatch
):
    path = tmp_path / "stale-de-confirmation.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    event = database.create_event("DE Test", ["Carmine"], ["Irina"])
    parsed = parse_pasted_table("Name\tStrip #\nDING Max\tM1")
    database.merge_import(event["id"], parsed.records, "Carmine")
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(
        event["id"], [athlete["id"]], main_coach="Carmine", actor="Carmine"
    )

    app = open_board(event["id"], event["coach_token"], "Carmine")
    button_named(app, "Won").click().run()
    assert any(button.label == "Confirm Won" for button in app.button)

    latest = database.get_athlete(event["id"], athlete["id"])
    database.mark_result(
        event["id"],
        athlete["id"],
        outcome="won",
        actor="Irina",
        expected_version=latest["version"],
    )
    app.run()

    assert not any(button.label == "Confirm Won" for button in app.button)
    assert database.get_athlete(event["id"], athlete["id"])["de_wins"] == 1
    assert any("changed on another phone" in info.value for info in app.info)
    assert not app.exception
