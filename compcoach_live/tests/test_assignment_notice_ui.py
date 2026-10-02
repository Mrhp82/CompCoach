"""New duties stay visible until their assigned coach explicitly accepts."""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import create_multi_event, keyed, markdown_values, nav_named, open_board


@pytest.fixture
def assignments(tmp_path, monkeypatch):
    path = tmp_path / "assignment-notices-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"])
    for event, name, strip in ((events[0], "EXAMPLE Existing", "P1"), (events[1], "EXAMPLE Added", "R1")):
        database.merge_import(event["id"], parse_pasted_table(f"Name\tStrip #\n{name}\t{strip}").records, "Carmine")
    athletes = {row["name"]: row for event in events for row in database.list_athletes(event["id"])}
    existing = athletes["EXAMPLE Existing"]
    database.assign_de_athletes(existing["event_id"], [existing["id"]], coaches=["Sam"], actor="Carmine")
    for notice in database.list_assignment_notices(meet["id"], "Sam"):
        database.accept_assignment_notice(meet["id"], notice["id"], "Sam", actor="Sam")
    yield database, meet, events, athletes
    st.cache_resource.clear()


def _coach(meet, who="Sam"):
    return open_board(meet["id"], meet["coach_token"], who)


def _new_assignment(database, meet, athlete):
    database.assign_de_athletes(athlete["event_id"], [athlete["id"]], coaches=["Sam"], actor="Irina")
    notices = database.list_assignment_notices(meet["id"], "Sam")
    assert len(notices) == 1
    return notices[0]


def _visible_text(app):
    return "\n".join([
        *markdown_values(app),
        *(item.value for item in app.caption),
        *(item.value for item in app.info),
        *(item.value for item in app.warning),
        *(item.value for item in app.success),
    ])


def test_new_cross_event_assignment_is_visible_and_requires_explicit_acceptance(assignments):
    database, meet, _events, athletes = assignments
    added = athletes["EXAMPLE Added"]
    app = _coach(meet)
    database.set_coach_availability(meet["id"], "Sam", True, "Sam")
    notice = _new_assignment(database, meet, added)
    app.run()

    accept = keyed(app.button, f"accept_assignment_{notice['id']}")
    assert accept.label == "Accept assignment"
    visible = _visible_text(app)
    assert added["name"] in visible and "Cadet Epee" in visible and "Irina" in visible
    # Opening or rerendering the board is not a receipt from the coach.
    app.run()
    assert database.list_assignment_notices(meet["id"], "Sam")[0]["accepted_at"] is None
    accept = keyed(app.button, f"accept_assignment_{notice['id']}")
    accept.click().run()

    assert not app.exception
    assert database.list_assignment_notices(meet["id"], "Sam") == []
    receipt = next(row for row in database.list_assignment_notices(meet["id"], "Sam", pending_only=False) if row["id"] == notice["id"])
    assert receipt["accepted_at"] and receipt["accepted_by"] == "Sam"
    saved = database.get_athlete(added["event_id"], added["id"])
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["takeover_coach"] == ""
    status = next(row for row in database.list_coach_availability(meet["id"]) if row["coach_name"] == "Sam")
    assert not status["is_available"] and not status["is_busy"]
    assert not any((button.key or "").startswith(f"accept_assignment_{notice['id']}") for button in app.button)


def test_pending_assignment_follows_coach_navigation_and_is_private_to_recipient(assignments):
    database, meet, _events, athletes = assignments
    notice = _new_assignment(database, meet, athletes["EXAMPLE Added"])
    app = _coach(meet)
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert keyed(app.button, f"accept_assignment_{notice['id']}")
    nav_named(app, "coach_nav_").set_value("My Group").run()
    assert keyed(app.button, f"accept_assignment_{notice['id']}")
    other = _coach(meet, "Carmine")
    assert not any((button.key or "").startswith(f"accept_assignment_{notice['id']}") for button in other.button)
    assert not app.exception and not other.exception


def test_coordinator_can_distinguish_pending_and_accepted_assignments(assignments):
    database, meet, _events, athletes = assignments
    added = athletes["EXAMPLE Added"]
    notice = _new_assignment(database, meet, added)
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    section = next(section for section in coordinator.expander if section.label == "Assignment confirmations")
    before = _visible_text(section)
    assert added["name"] in before and "Sam" in before
    assert "pending" in before.casefold() or "awaiting" in before.casefold()
    coach = _coach(meet)
    keyed(coach.button, f"accept_assignment_{notice['id']}").click().run()
    coordinator.run()
    section = next(section for section in coordinator.expander if section.label == "Assignment confirmations")
    after = _visible_text(section)
    assert added["name"] in after and "Sam" in after
    assert "accepted" in after.casefold()
    assert not coordinator.exception and not coach.exception


def test_stale_acceptance_after_reassignment_disappears_and_cannot_restore_old_coach(assignments):
    database, meet, _events, athletes = assignments
    added = athletes["EXAMPLE Added"]
    notice = _new_assignment(database, meet, added)
    app = _coach(meet)
    database.assign_de_athletes(added["event_id"], [added["id"]], coaches=["Carmine"], actor="Irina")
    keyed(app.button, f"accept_assignment_{notice['id']}").click().run()
    assert database.get_athlete(added["event_id"], added["id"])["de_coaches"] == ["Carmine"]
    assert database.list_assignment_notices(meet["id"], "Sam") == []
    assert not any((button.key or "").startswith(f"accept_assignment_{notice['id']}") for button in app.button)
    assert not app.exception


def test_new_pool_assignment_exposes_strip_time_and_pool_before_coach_accepts(assignments):
    database, meet, _events, _athletes = assignments
    event = database.add_meet_event(meet["id"], "Cadet Women")
    database.merge_import(
        event["id"],
        parse_pasted_table("Name\tStrip #\tTime\tPool #\nEXAMPLE Pool\tF3\t09:00\t7").records,
        "Carmine",
    )
    athlete = database.list_athletes(event["id"])[0]
    database.assign_athletes(event["id"], [athlete["id"]], main_coach="Sam", actor="Irina")
    notice = database.list_assignment_notices(meet["id"], "Sam")[0]
    app = _coach(meet)
    text = _visible_text(app)
    assert "EXAMPLE Pool" in text and "F3" in text and "09:00" in text and "Pool 7" in text
    keyed(app.button, f"accept_assignment_{notice['id']}").click().run()
    assert database.list_assignment_notices(meet["id"], "Sam") == []
    saved = database.get_athlete(event["id"], athlete["id"])
    assert saved["main_coach"] == "Sam" and saved["pool_wins"] is None
    assert saved["covered_by"] == ""
    assert not app.exception
