"""Emergency invitations reserve one free coach without changing the pod plan."""

import pytest
import streamlit as st

from compcoach_live.parsers import parse_pasted_table
from compcoach_live.storage import CompCoachDB
from compcoach_live.tests.test_app_smoke import button_named, create_multi_event, keyed, markdown_values, nav_named, open_board


@pytest.fixture
def requests(tmp_path, monkeypatch):
    path = tmp_path / "coverage-requests-ui.db"
    monkeypatch.setenv("COMPCOACH_DB_PATH", str(path))
    monkeypatch.setenv("COMPCOACH_ADMIN_PIN", "4321")
    monkeypatch.delenv("COMPCOACH_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPCOACH_REQUIRE_CLOUD", raising=False)
    st.cache_resource.clear()
    database = CompCoachDB(path)
    meet, events = create_multi_event(database, ["Junior Epee", "Cadet Epee"], coaches=("Carmine", "Sam", "Vivien"))
    for event, name, strip in ((events[0], "EXAMPLE Existing", "P1"), (events[1], "EXAMPLE Emergency", "R1")):
        database.merge_import(event["id"], parse_pasted_table(f"Name\tStrip #\n{name}\t{strip}").records, "Carmine")
    athletes = {row["name"]: row for event in events for row in database.list_athletes(event["id"])}
    existing, emergency = athletes["EXAMPLE Existing"], athletes["EXAMPLE Emergency"]
    database.assign_de_athletes(existing["event_id"], [existing["id"]], coaches=["Sam"], actor="Carmine")
    database.assign_de_athletes(emergency["event_id"], [emergency["id"]], coaches=["Vivien"], actor="Carmine")
    database.report_call(emergency["event_id"], emergency["id"], status="now", location="D3", actor="Irina")
    for coach in ("Sam", "Carmine"):
        database.set_coach_availability(meet["id"], coach, True, coach)
    yield database, meet, events, athletes
    st.cache_resource.clear()


def _coach(meet, who="Sam"):
    return open_board(meet["id"], meet["coach_token"], who)


def _request(database, meet, athlete):
    return database.create_coverage_request(
        meet["id"], athlete["event_id"], athlete["id"], ["Sam", "Carmine"], "Irina",
    )


def _visible_text(app):
    return "\n".join([
        *markdown_values(app),
        *(item.value for item in app.caption),
        *(item.value for item in app.info),
        *(item.value for item in app.warning),
        *(item.value for item in app.success),
    ])


def _accept(app, request):
    return keyed(app.button, f"accept_coverage_request_{request['id']}")


def test_ordinary_pool_and_de_assignments_need_no_acceptance(requests):
    database, meet, _events, athletes = requests
    emergency = athletes["EXAMPLE Emergency"]
    database.assign_de_athletes(emergency["event_id"], [emergency["id"]], coaches=["Sam"], actor="Irina")
    pool_event = database.add_meet_event(meet["id"], "Cadet Women")
    database.merge_import(pool_event["id"], parse_pasted_table("Name\tStrip #\tPool #\nEXAMPLE Pool\tF3\t7").records, "Carmine")
    pool = database.list_athletes(pool_event["id"])[0]
    database.assign_athletes(pool_event["id"], [pool["id"]], main_coach="Sam", actor="Irina")
    app = _coach(meet)
    text = _visible_text(app)
    assert emergency["name"] in text and pool["name"] in text
    assert not any((button.key or "").startswith(("accept_assignment_", "accept_coverage_request_")) for button in app.button)
    assert not any(button.label == "Accept assignment" for button in app.button)
    assert database.list_coverage_requests(meet["id"]) == []
    assert not app.exception


@pytest.mark.parametrize("role,actor", [("admin", "Carmine"), ("coordinator", "Irina")])
def test_admin_and_coordinator_can_invite_multiple_available_coaches(requests, role, actor):
    database, meet, _events, athletes = requests
    emergency = athletes["EXAMPLE Emergency"]
    app = open_board(meet["id"], meet[f"{role}_token"], actor)
    nav_named(app, f"{role}_nav_").set_value("Live").run()
    selector = keyed(app.selectbox, "emergency_request_athlete_")
    selector.set_value(emergency["id"]).run()
    coaches = keyed(app.multiselect, "emergency_request_coaches_")
    assert all("Vivien" not in option for option in coaches.options)
    assert any("Sam" in option for option in coaches.options) and any("Carmine" in option for option in coaches.options)
    # The multiselect is outside a form: selecting coaches first reruns the
    # page and binds the send callback to this selection/version snapshot.
    coaches.set_value(["Sam", "Carmine"]).run()
    button_named(app, "Request coverage").click().run()
    assert not app.exception
    pending = database.list_coverage_requests(meet["id"])
    assert len(pending) == 1
    assert pending[0]["athlete_id"] == emergency["id"]
    assert {row["coach_name"] for row in pending[0]["recipients"]} == {"Sam", "Carmine"}
    assert database.get_athlete(emergency["event_id"], emergency["id"])["de_coaches"] == ["Vivien"]
    states = {row["coach_name"]: row for row in database.list_coach_availability(meet["id"])}
    assert states["Sam"]["is_available"] and states["Carmine"]["is_available"]


def test_request_is_visible_to_recipients_and_follows_coach_navigation(requests):
    database, meet, _events, athletes = requests
    emergency = athletes["EXAMPLE Emergency"]
    request = _request(database, meet, emergency)
    app = _coach(meet)
    assert _accept(app, request).label.replace("’", "'") == "I'll cover this bout"
    text = _visible_text(app)
    assert "Coverage request" in text and emergency["name"] in text
    assert "Cadet Epee" in text and "D3" in text and "Irina" in text
    nav_named(app, "coach_nav_").set_value("Live").run()
    assert _accept(app, request)
    nav_named(app, "coach_nav_").set_value("My Group").run()
    assert _accept(app, request)
    assert not database.list_coverage_requests(meet["id"], "Sam")[0].get("accepted_by")
    uninvited = _coach(meet, "Vivien")
    assert not any((button.key or "").startswith(f"accept_coverage_request_{request['id']}") for button in uninvited.button)
    assert not app.exception and not uninvited.exception


def test_first_acceptance_reserves_only_winner_and_removes_request_for_everyone(requests):
    database, meet, _events, athletes = requests
    emergency = athletes["EXAMPLE Emergency"]
    request = _request(database, meet, emergency)
    first, second = _coach(meet, "Sam"), _coach(meet, "Carmine")
    assert _accept(first, request) and _accept(second, request)
    _accept(first, request).click().run()
    saved = database.get_athlete(emergency["event_id"], emergency["id"])
    assert saved["takeover_coach"] == "Sam" and saved["takeover_at"]
    assert saved["covered_by"] == "" and saved["covered_at"] is None
    assert saved["de_coaches"] == ["Vivien"]
    states = {row["coach_name"]: row for row in database.list_coach_availability(meet["id"])}
    assert not states["Sam"]["is_available"] and not states["Sam"]["is_busy"]
    assert states["Carmine"]["is_available"]
    assert not database.list_coverage_requests(meet["id"], "Sam")
    assert not database.list_coverage_requests(meet["id"], "Carmine")
    receipt = next(row for row in database.list_coverage_requests(meet["id"], pending_only=False) if row["id"] == request["id"])
    assert receipt["accepted_by"] == "Sam" and receipt["accepted_at"]
    assert not any((button.key or "").startswith(f"accept_coverage_request_{request['id']}") for button in first.button)
    second.run()
    assert not any((button.key or "").startswith(f"accept_coverage_request_{request['id']}") for button in second.button)
    assert not first.exception and not second.exception


def test_stale_loser_cannot_take_over_or_cancel_already_accepted_request(requests):
    database, meet, _events, athletes = requests
    emergency = athletes["EXAMPLE Emergency"]
    request = _request(database, meet, emergency)
    first, stale = _coach(meet, "Sam"), _coach(meet, "Carmine")
    coordinator = open_board(meet["id"], meet["coordinator_token"], "Irina")
    responses = next(section for section in coordinator.expander if section.label == "Coverage requests · responses")
    assert "Awaiting response" in _visible_text(responses)
    cancel = keyed(coordinator.button, f"cancel_coverage_request_{request['id']}")
    _accept(first, request).click().run()
    _accept(stale, request).click().run()
    assert database.get_athlete(emergency["event_id"], emergency["id"])["takeover_coach"] == "Sam"
    assert not any((button.key or "").startswith(f"accept_coverage_request_{request['id']}") for button in stale.button)
    cancel.click().run()
    responses = next(section for section in coordinator.expander if section.label == "Coverage requests · responses")
    assert "Taken by Sam" in _visible_text(responses)
    assert not any((button.key or "").startswith(f"cancel_coverage_request_{request['id']}") for button in coordinator.button)
    assert database.get_athlete(emergency["event_id"], emergency["id"])["de_coaches"] == ["Vivien"]
    assert not first.exception and not stale.exception and not coordinator.exception
