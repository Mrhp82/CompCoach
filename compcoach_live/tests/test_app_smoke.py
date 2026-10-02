import io
from pathlib import Path

import pytest
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
        "Team situation",
        "Setup",
        "Share",
    ]
    assert any("You are Carmine" in value for value in markdown_values(app))
    assert not app.exception


@pytest.mark.parametrize(
    "role,actor,expected_nav",
    [
        ("admin", "Carmine", ["My Group", "Team situation", "Setup", "Share"]),
        ("coach", "Sam", ["My Group", "Team situation"]),
        ("coordinator", "Irina", ["Team situation", "Activity"]),
    ],
)
def test_unified_live_retains_shared_features_with_one_operational_athlete_list(
    tmp_path, monkeypatch, role, actor, expected_nav
):
    path = tmp_path / "unified-live.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database, ["Cadet Epee", "Junior Epee"],
        coaches=("Carmine", "Sam", "Taylor"),
    )
    database.merge_import(
        events[0]["id"],
        parse_pasted_table("Name\tStrip #\tPool #\nEXAMPLE Pool\tA1\t1").records,
        "Carmine",
    )
    pool = database.list_athletes(events[0]["id"])[0]
    database.assign_athletes(
        events[0]["id"], [pool["id"]], main_coach="Sam", actor="Carmine",
    )
    database.set_pool_result(
        events[0]["id"], pool["id"], wins=3, losses=3, actor="Sam",
    )
    database.merge_import(
        events[1]["id"],
        parse_pasted_table("Name\tStrip #\nEXAMPLE Waiting\tP1\nEXAMPLE Called\tQ1").records,
        "Carmine",
    )
    de_rows = database.list_athletes(events[1]["id"])
    database.assign_de_pods(
        events[1]["id"], pods=["P", "Q"], coaches=["Sam"], actor="Carmine",
    )
    called = next(row for row in de_rows if row["name"] == "EXAMPLE Called")
    database.report_call(
        events[1]["id"], called["id"], status="on_deck", location="C3", actor="Irina",
    )
    database.set_coach_availability(meet["id"], "Taylor", True, "Taylor")

    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    assert nav_named(app, f"{role}_nav_").options == expected_nav
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    assert not any("situation_view_" in str(group.key) for group in app.get("button_group"))
    assert nav_named(app, "live_view_").options == [
        "Uncovered", "Needs Coach", "Covered Now", "No Current Call", "Out",
    ]
    sections = {section.label for section in app.expander}
    assert {"Coaches", "DE sector load · all assignments", "All assignments", "Pool results"} <= sections
    markup = "\n".join(markdown_values(app))
    assert "1 coach available" in markup and "2 still in DE" in markup
    assert "Sector P" in markup and "Sector Q" in markup and "C3" in markup
    assert "3 W · 3 L" in markup
    plan = "\n".join(value for value in markdown_values(app) if "cc-plan-group" in value)
    assert "EXAMPLE Waiting" in plan and "EXAMPLE Called" in plan and "EXAMPLE Pool" in plan

    def assert_one_copy():
        for row in de_rows:
            assert len([button for button in app.button if (button.key or "").startswith(f"won_{row['id']}")]) == 1
            assert len([button for button in app.button if (button.key or "").startswith(f"lost_{row['id']}")]) == 1
            cards = [value for value in markdown_values(app) if "class='cc-athlete'" in value and row["name"] in value]
            assert len(cards) == 1

    assert_one_copy()
    assert not app.exception
    app.run()
    assert nav_named(app, f"{role}_nav_").value == "Live"
    assert_one_copy()
    assert not app.exception


@pytest.mark.parametrize("role,actor", [("admin", "Carmine"), ("coach", "Sam"), ("coordinator", "Irina")])
@pytest.mark.parametrize("old_section,expanded_label", [("Assignments", "All assignments"), ("Pool Results", "Pool results")])
def test_saved_situation_navigation_is_migrated_to_live(
    tmp_path, monkeypatch, role, actor, old_section, expanded_label
):
    path = tmp_path / "migrate-situation.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Junior Epee"])
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_key = f"{role}_nav_{meet['id']}"
    app.session_state[nav_key] = "Situation"
    app.session_state[f"nav_choice_{nav_key}"] = "Situation"
    old_section_key = f"situation_view_{meet['id']}_{role}"
    app.session_state[old_section_key] = old_section
    app.session_state[f"nav_choice_{old_section_key}"] = old_section
    app.run()
    assert nav_named(app, f"{role}_nav_").value == "Live"
    assert "Situation" not in nav_named(app, f"{role}_nav_").options
    section = next(section for section in app.expander if section.label == expanded_label)
    assert section.proto.expanded
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
    nav_named(app, "coordinator_nav_").set_value("Live").run()
    keyed(app.get("button_group"), "call_status_").set_value("Now")
    selectbox_named(app, "Coverage").set_value("Sam").run()
    keyed(app.text_input, "call_location_").set_value("M1")
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
    nav_named(app, "coordinator_nav_").set_value("Live").run()
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


def test_assignment_coach_selectors_use_meet_wide_usage_without_blocking_reuse(
    tmp_path, monkeypatch
):
    path = tmp_path / "meet-wide-coach-order.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(
        database,
        ["Cadet Men's Epee", "Cadet Women's Epee"],
        coaches=("Igor", "Carmine", "Sam", "Vivien"),
    )
    men, women = events
    database.merge_import(
        men["id"],
        parse_pasted_table(
            "Name\tStrip #\tPool #\n"
            "M ONE\tB1\t1\n"
            "M TWO\tB2\t2\n"
            "M TEN\tB10\t10\n"
            "M NINE\tB9\t9\n"
            "M TWENTY\tB20\t20\n"
            "M AA TWO\tAA2\t21\n"
            "M AA TEN\tAA10\t22\n"
            "M MISSING\t\t23\n"
        ).records,
        "Carmine",
    )
    database.merge_import(
        men["id"],
        parse_pasted_table("Name\tStrip #\nM DE\tQ3").records,
        "Carmine",
    )
    database.merge_import(
        women["id"],
        parse_pasted_table(
            "Name\tStrip #\tPool #\nW POOL\tC1\t1"
        ).records,
        "Carmine",
    )
    database.merge_import(
        women["id"],
        parse_pasted_table("Name\tStrip #\nW DE\tP3").records,
        "Carmine",
    )
    men_rows = database.list_athletes(men["id"])
    assigned_men = [
        row
        for row in men_rows
        if row["phase"] == "pools" and row["source_strip"] in {"B2", "B10"}
    ]
    database.assign_athletes(
        men["id"],
        [row["id"] for row in assigned_men],
        main_coach="Igor",
        actor="Carmine",
    )
    women_rows = database.list_athletes(women["id"])
    pool_row = next(row for row in women_rows if row["phase"] == "pools")
    de_row = next(row for row in women_rows if row["phase"] == "de")
    database.assign_athletes(
        women["id"], [pool_row["id"]], main_coach="Igor", actor="Carmine"
    )
    database.assign_pod(
        women["id"],
        phase="de",
        pod="P",
        main_coach="Carmine",
        side_coach="",
        actor="Carmine",
    )
    database.assign_pod(
        women["id"],
        phase="de",
        pod="EMPTY",
        main_coach="Vivien",
        side_coach="",
        actor="Carmine",
    )
    database.report_call(
        women["id"],
        de_row["id"],
        status="now",
        location="P3",
        actor="Irina",
        covered_by="Sam",
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()

    main = keyed(app.selectbox, f"main_{men['id']}_pools")
    expected = [
        "No change",
        "None",
        "Vivien",
        "✓ Igor · already assigned",
        "✓ Carmine · already assigned",
        "✓ Sam · already assigned",
    ]
    assert main.options == expected
    main.set_value("Igor").run()
    assert keyed(app.selectbox, f"main_{men['id']}_pools").value == "Igor"

    athlete_options = keyed(app.multiselect, "selected_assign_").options
    assert [option.split(" · ", 1)[0] for option in athlete_options] == [
        "M AA TWO",
        "M AA TEN",
        "M ONE",
        "M NINE",
        "M TWENTY",
        "M MISSING",
        "✓ M TWO",
        "✓ M TEN",
    ]

    keyed(app.get("button_group"), f"assignment_phase_{men['id']}").set_value(
        "Direct Elimination"
    ).run()
    assert keyed(app.multiselect, "de_assignment_pod_coaches_").options == [
        "Vivien",
        "✓ Igor · already assigned",
        "✓ Carmine · already assigned",
        "✓ Sam · already assigned",
    ]


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
    assert "◆ *POD M*" in message
    assert "Coaches: Carmine\n• DING Max" in message
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
    carmine = open_board(meet["id"], meet["coach_token"], "Carmine")
    assert any(
        expander.label == "Completed pool results · 1"
        for expander in carmine.expander
    )
    assert any("0 active work · 1 completed pool" in item.value for item in carmine.caption)
    assert any("Cadet Foil" in value for value in markdown_values(carmine))
    assert not any(button.label == "🚨 Need help now" for button in carmine.button)

    edit_wins = button_group_named(carmine, "Wins")
    edit_losses = button_group_named(carmine, "Losses")
    assert edit_wins.value == 3
    assert edit_losses.value == 3
    edit_wins.set_value(4)
    edit_losses.set_value(2)
    button_named(carmine, "Save pool result").click().run()
    edited = database.get_athlete(foil["id"], max_row["id"])
    assert edited["pool_wins"] == 4
    assert edited["pool_losses"] == 2

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


def test_team_plan_groups_same_named_de_athletes_by_event_with_equal_roles(
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
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert any(section.label == "All assignments" for section in app.expander)

    labels = [expander.label for expander in app.expander]
    assert "⭐ Cadet Foil · 1 active" in labels
    assert "Junior Foil · 1 active" in labels
    plan_markup = "\n".join(
        value for value in markdown_values(app) if "cc-plan-group" in value
    )
    assert "🤺 Carmine · 1" in plan_markup
    assert "🤺 Sam · 1" in plan_markup
    assert plan_markup.count("◆ DING Max") == 2
    assert "🔹 <b>DING Max</b>" not in plan_markup
    assert "Pod B" in plan_markup and "Pod P" in plan_markup
    assert plan_markup.count("Actual strip TBD") == 2
    assert "Actual strip B1" not in plan_markup and "Actual strip P3" not in plan_markup
    assert len(database.list_athletes(events[0]["id"])) == 1
    assert len(database.list_athletes(events[1]["id"])) == 1
    assert not app.exception


def test_coach_without_assignments_can_declare_meet_wide_availability(
    tmp_path, monkeypatch
):
    path = tmp_path / "coach-availability.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, _events = create_multi_event(database, ["Cadet Foil", "Junior Epee"])

    app = open_board(meet["id"], meet["coach_token"], "Sam")
    assert button_named(app, "I’m available to help")
    button_named(app, "I’m available to help").click().run()

    status = next(
        row
        for row in database.list_coach_availability(meet["id"])
        if row["coach_name"] == "Sam"
    )
    assert status["is_available"] is True
    assert status["available_since"]
    assert button_named(app, "I’m no longer available")
    assert not app.exception


def test_coordinator_live_adds_available_equal_coach_to_de_sector(
    tmp_path, monkeypatch
):
    path = tmp_path / "live-deploy.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\nDING Max\tP1\nNG Alexander\tP3"
        ).records,
        "Carmine",
    )
    database.assign_pod(
        event["id"],
        phase="de",
        pod="P",
        main_coach="Carmine",
        side_coach="",
        actor="Carmine",
    )
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")

    app = open_board(meet["id"], meet["coordinator_token"], "Irina")

    assert nav_named(app, "coordinator_nav_").value == "Live"
    assert any(section.label == "Coaches" for section in app.expander)
    markup = "\n".join(markdown_values(app))
    assert "Sector P" in markup
    assert "2 still in" in markup
    assert "Coaches: Carmine" in markup
    assert "Sam" in markup
    button_named(app, "Send Sam to Sector P").click().run()

    saved = database.list_athletes(event["id"])
    assert all(row["de_coaches"] == ["Carmine", "Sam"] for row in saved)
    availability = next(
        row
        for row in database.list_coach_availability(meet["id"])
        if row["coach_name"] == "Sam"
    )
    assert availability["is_available"] is False
    assert nav_named(app, "coordinator_nav_").value == "Live"
    # AppTest can retain deleted deployment widgets after a fragment rerun.
    # A newly loaded phone should see the saved plan and no expired shortcut.
    app = open_board(meet["id"], meet["coordinator_token"], "Irina")
    assert nav_named(app, "coordinator_nav_").value == "Live"
    markup = "\n".join(markdown_values(app))
    assert "Coaches: Carmine / Sam" in markup
    assert not any(button.label == "Send Sam to Sector P" for button in app.button)
    assert not app.exception


def test_live_pool_results_include_advanced_and_out_athletes(
    tmp_path, monkeypatch
):
    path = tmp_path / "live-pool-results.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Foil"])
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
    by_name = {row["name"]: row for row in database.list_athletes(event["id"])}
    database.set_pool_result(
        event["id"], by_name["DING Max"]["id"], wins=6, losses=0, actor="Carmine"
    )
    database.set_pool_result(
        event["id"], by_name["OR Evan"]["id"], wins=0, losses=6, actor="Carmine"
    )
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\nDING Max\tM1").records,
        "Carmine",
    )
    evan = next(row for row in database.list_athletes(event["id"]) if row["name"] == "OR Evan")
    database.mark_out(event["id"], evan["id"], "Carmine")

    app = open_board(meet["id"], meet["coach_token"], "Sam")
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert any(section.label == "Pool results" for section in app.expander)

    markup = "\n".join(markdown_values(app))
    assert "DING Max" in markup and "6 W · 0 L" in markup
    assert "OR Evan" in markup and "0 W · 6 L" in markup
    assert "Out" in markup
    assert not app.exception


def test_de_result_is_saved_with_one_tap_and_keeps_live_controls_accessible(tmp_path, monkeypatch):
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
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["de_wins"] == 1
    assert saved["last_de_result"] == "won"
    assert saved["active_state"] == "active"
    assert saved["de_awaiting_next"] == 1
    assert not any(button.label == "Confirm Won" for button in app.button)
    cards = "\n".join(value for value in markdown_values(app) if "class='cc-athlete'" in value)
    assert athlete["name"] in cards
    assert not any("Ready for next bout" in button.label for button in app.button)
    assert keyed(app.button, f"won_{athlete['id']}")
    assert keyed(app.button, f"lost_{athlete['id']}")
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

    nav_named(app, "admin_nav_").set_value("Live").run()
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
    assert "event" not in app.query_params
    assert "token" not in app.query_params
    assert any(item.value == "⏸️ No active competition" for item in app.subheader)
    # Use a fresh app session: AppTest retains widgets from completed timed
    # fragments after app-level navigation even though their state was cleared.
    app = open_board(meet["id"], meet["admin_token"], "Carmine", archive=True)
    assert any("archive is read-only" in info.value for info in app.info)
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Settings").run()
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
        "This competition day is closed. No competition day is active. "
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


def test_stale_de_tap_does_not_repeat_a_win_saved_on_another_phone(
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
    latest = database.get_athlete(event["id"], athlete["id"])
    database.mark_result(
        event["id"],
        athlete["id"],
        outcome="won",
        actor="Irina",
        expected_version=latest["version"],
    )
    button_named(app, "Won").click().run()

    assert not any(button.label == "Confirm Won" for button in app.button)
    assert database.get_athlete(event["id"], athlete["id"])["de_wins"] == 1
    assert database.get_athlete(event["id"], athlete["id"])["de_awaiting_next"] == 1
    assert not app.exception


def test_admin_attendance_hides_absent_and_withdrawn_then_restores(
    tmp_path, monkeypatch
):
    path = tmp_path / "attendance-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Cadet Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\n"
            "DING Max\tB1\t\t1\n"
            "OR Evan\tB2\t\t2"
        ).records,
        "Carmine",
    )
    athletes = {row["name"]: row for row in database.list_athletes(event["id"])}
    database.assign_athletes(
        event["id"],
        [row["id"] for row in athletes.values()],
        main_coach="Carmine",
        actor="Carmine",
    )

    app = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    keyed(app.multiselect, "attendance_selected_").set_value(
        [athletes["DING Max"]["id"]]
    ).run()
    button_named(app, "Mark absent").click().run()

    absent = database.get_athlete(event["id"], athletes["DING Max"]["id"])
    assert absent["participation_status"] == "absent"
    assert all(
        "DING Max" not in option
        for option in keyed(app.multiselect, "selected_assign_").options
    )
    assert any(expander.label == "Absent · 1" for expander in app.expander)

    nav_named(app, "admin_nav_").set_value("My Group").run()
    active_cards = "\n".join(
        value for value in markdown_values(app) if "<div class='cc-athlete'>" in value
    )
    assert "OR Evan" in active_cards
    assert "DING Max" not in active_cards
    assert any(
        expander.label == "Absent pool athletes · 1" for expander in app.expander
    )

    nav_named(app, "admin_nav_").set_value("Setup").run()
    nav_named(app, "admin_setup_nav_").set_value("Assign").run()
    keyed(app.button, "restore_attendance_").click().run()
    restored = database.get_athlete(event["id"], athletes["DING Max"]["id"])
    assert restored["participation_status"] == "active"
    assert any(
        "DING Max" in option
        for option in keyed(app.multiselect, "selected_assign_").options
    )

    keyed(app.multiselect, "attendance_selected_").set_value(
        [athletes["DING Max"]["id"]]
    ).run()
    button_named(app, "Mark withdrawn").click().run()
    withdrawn = database.get_athlete(event["id"], athletes["DING Max"]["id"])
    assert withdrawn["participation_status"] == "withdrawn"
    assert any(expander.label == "Withdrawn · 1" for expander in app.expander)
    assert all(
        "DING Max" not in option
        for option in keyed(app.multiselect, "selected_assign_").options
    )

    keyed(app.button, "restore_attendance_").click().run()
    restored_again = database.get_athlete(
        event["id"], athletes["DING Max"]["id"]
    )
    assert restored_again["participation_status"] == "active"
    assert restored_again["main_coach"] == "Carmine"
    assert not app.exception


@pytest.mark.parametrize(
    "wave_times",
    [("8:00 AM", "10:00 AM"), ("8:00 AM", "10:00 AM", "1:00 PM")],
)
def test_pool_waves_assign_all_then_show_and_activate_one_wave(
    tmp_path, monkeypatch, wave_times
):
    path = tmp_path / f"waves-{len(wave_times)}.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    names = [f"WAVE ATHLETE {index}" for index in range(1, len(wave_times) + 1)]
    table_rows = ["Name\tStrip #\tTime\tPool #"]
    table_rows.extend(
        f"{name}\tB{index}\t{start_time}\t{index}"
        for index, (name, start_time) in enumerate(zip(names, wave_times), start=1)
    )
    table_rows.append("UNTIMED ATHLETE\tB20\t\t20")
    database.merge_import(
        event["id"], parse_pasted_table("\n".join(table_rows)).records, "Carmine"
    )

    waves = database.list_pool_waves(event["id"])
    assert [wave["label"] for wave in waves] == list(wave_times)
    assert waves[0]["is_active"] is True
    assert all(not wave["is_active"] for wave in waves[1:])

    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Setup").run()
    nav_named(admin, "admin_setup_nav_").set_value("Assign").run()
    assert selectbox_named(admin, "Start time to assign").value == "All start times"
    assignment_picker = keyed(admin.multiselect, "selected_assign_")
    assert len(assignment_picker.options) == len(wave_times) + 1
    all_ids = [row["id"] for row in database.list_athletes(event["id"])]
    assignment_picker.set_value(all_ids).run()
    keyed(admin.selectbox, "main_").set_value("Carmine").run()
    button_named(admin, f"Apply to {len(all_ids)} athletes").click().run()
    assert all(
        row["main_coach"] == "Carmine"
        for row in database.list_athletes(event["id"])
    )

    coach = open_board(meet["id"], meet["coach_token"], "Carmine")
    first_markup = "\n".join(markdown_values(coach))
    assert names[0] in first_markup
    assert "UNTIMED ATHLETE" in first_markup
    assert all(name not in first_markup for name in names[1:])

    share = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(share, "admin_nav_").set_value("Share").run()
    first_message = share.code[2].value
    assert names[0] in first_message
    assert "UNTIMED ATHLETE" in first_message
    assert all(name not in first_message for name in names[1:])

    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Setup").run()
    nav_named(admin, "admin_setup_nav_").set_value("Assign").run()
    keyed(
        admin.button,
        f"activate_wave_{event['id']}_{waves[1]['wave_key']}",
    ).click().run()
    activated = database.list_pool_waves(event["id"])
    assert activated[1]["is_active"] is True
    assert activated[1]["is_visible"] is True
    assert activated[0]["is_active"] is False

    coach = open_board(meet["id"], meet["coach_token"], "Carmine")
    second_markup = "\n".join(markdown_values(coach))
    assert names[1] in second_markup
    assert "UNTIMED ATHLETE" in second_markup
    assert names[0] not in second_markup
    if len(names) == 3:
        assert names[2] not in second_markup

    admin = open_board(meet["id"], meet["admin_token"], "Carmine")
    nav_named(admin, "admin_nav_").set_value("Setup").run()
    nav_named(admin, "admin_setup_nav_").set_value("Assign").run()
    selectbox_named(admin, "Start time to assign").set_value(wave_times[1]).run()
    filtered = keyed(admin.multiselect, "selected_assign_").options
    assert len(filtered) == 1
    assert names[1] in filtered[0]
    assert not admin.exception


def test_completed_pool_result_remains_in_collapsed_history_after_wave_change(
    tmp_path, monkeypatch
):
    path = tmp_path / "completed-wave-history.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee"])
    event = events[0]
    database.merge_import(
        event["id"],
        parse_pasted_table(
            "Name\tStrip #\tTime\tPool #\n"
            "FIRST WAVE\tB1\t8:00 AM\t1\n"
            "SECOND WAVE\tB2\t10:00 AM\t2"
        ).records,
        "Carmine",
    )
    rows = {row["name"]: row for row in database.list_athletes(event["id"])}
    database.assign_athletes(
        event["id"],
        [row["id"] for row in rows.values()],
        main_coach="Carmine",
        actor="Carmine",
    )
    database.set_pool_result(
        event["id"],
        rows["FIRST WAVE"]["id"],
        wins=4,
        losses=2,
        actor="Carmine",
    )
    second_wave = database.list_pool_waves(event["id"])[1]
    database.activate_pool_wave(
        event["id"],
        second_wave["wave_key"],
        "Carmine",
        expected_version=second_wave["version"],
    )

    coach = open_board(meet["id"], meet["coach_token"], "Carmine")

    markup = "\n".join(markdown_values(coach))
    assert "SECOND WAVE" in markup
    assert "FIRST WAVE" in markup
    assert any(
        expander.label == "Completed pool results · 1"
        for expander in coach.expander
    )
    assert any(
        "1 active work · 1 completed pool" in item.value
        for item in coach.caption
    )
    assert not coach.exception
