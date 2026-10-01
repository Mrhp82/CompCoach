"""Coverage warnings represent real occupations, not just assignment plans."""

from datetime import datetime, timezone

from streamlit.testing.v1 import AppTest

from compcoach_live.busy_board import build_busy_coach_board


NOW = datetime(2026, 10, 1, 11, 10, tzinfo=timezone.utc)
EVENTS = {
    "men": {"id": "men", "name": "Men's Epee", "status": "open"},
    "women": {"id": "women", "name": "Women's Epee", "status": "open"},
}


def athlete(identifier, name, **changes):
    return {
        "id": identifier, "name": name, "event_id": "men", "phase": "de",
        "active_state": "active", "participation_status": "active",
        "de_coaches": ["Alex"], "source_strip": "B1", "pod": "B",
        "live_location": "", "call_status": "waiting", "covered_by": "",
        **changes,
    }


def test_other_assigned_athletes_are_global_even_before_calls_and_after_advance():
    rows = [
        athlete("nathan", "NATHAN Athlete", covered_by="Alex", live_location="J4", covered_at="2026-10-01T11:00:00Z"),
        athlete("noah", "NOAH Athlete", event_id="women"),
        athlete("daniel", "DANIEL Athlete", last_de_result="won", de_awaiting_next=1),
    ]
    projected = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert projected["busy"][0]["actual_strip"] == "Actual strip J4"
    assert projected["busy"][0]["busy_elapsed"] == "10 min"
    assert {row["athlete_id"] for row in projected["needs_cover"]} == {"noah", "daniel"}
    noah = next(row for row in projected["needs_cover"] if row["athlete_id"] == "noah")
    assert noah["event_name"] == "Women's Epee"
    assert noah["actual_strip"] == "Actual strip TBD"
    assert noah["pod"] == "B"
    assert noah["needs_alternate"]
    assert not noah["urgent"]


def test_absent_out_already_covered_and_ocr_noise_do_not_create_warnings():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex"),
        athlete("out", "OUT Athlete", active_state="eliminated"),
        athlete("absent", "ABSENT Athlete", participation_status="absent"),
        athlete("retired", "RETIRED Athlete", participation_status="withdrawn"),
        athlete("covered", "COVERED Athlete", covered_by="Taylor"),
        athlete("noise", "fe melive.com"),
    ]
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert {row["coach"] for row in projection["busy"]} == {"Alex", "Taylor"}
    assert not projection["needs_cover"]


def test_unoccupied_equal_peer_is_shown_without_claiming_manual_availability():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex"),
        athlete("sibling", "SIBLING Athlete", de_coaches=["Alex", "Taylor", "Jordan"], call_status="now", live_location="C3", reported_at="2026-10-01T11:09:00Z"),
    ]
    sibling = build_busy_coach_board(rows, EVENTS, now=NOW)["needs_cover"][0]
    assert sibling["busy_coaches"] == ["Alex"]
    assert sibling["unoccupied_peers"] == ["Taylor", "Jordan"]
    assert not sibling["needs_alternate"]
    assert not sibling["urgent"]


def test_all_busy_peers_and_calls_rank_by_operational_urgency():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex"),
        athlete("other_live", "OTHER Athlete", covered_by="Taylor", event_id="women"),
        athlete("waiting", "A WAITING Athlete"),
        athlete("stale", "A STALE Athlete", call_status="now", reported_at="2026-10-01T10:30:00Z"),
        athlete("deck", "B DECK Athlete", call_status="on_deck", reported_at="2026-10-01T11:09:00Z"),
        athlete("hole", "C HOLE Athlete", call_status="in_hole", reported_at="2026-10-01T11:09:00Z"),
        athlete("now", "Z NOW Athlete", call_status="now", reported_at="2026-10-01T11:09:00Z", de_coaches=["Alex", "Taylor"]),
    ]
    warnings = build_busy_coach_board(rows, EVENTS, now=NOW)["needs_cover"]
    assert [row["athlete_id"] for row in warnings] == ["now", "deck", "hole", "waiting", "stale"]
    assert warnings[0]["busy_coaches"] == ["Alex", "Taylor"]
    assert warnings[0]["urgent"]
    assert warnings[-1]["stale"]
    assert not warnings[-1]["urgent"]


def test_result_releases_coach_and_removes_sibling_conflicts():
    rows = [
        athlete("winner", "WINNER Athlete", covered_by="Alex"),
        athlete("waiting", "WAITING Athlete"),
    ]
    assert build_busy_coach_board(rows, EVENTS, now=NOW)["needs_cover"]
    rows[0].update(covered_by="", covered_at=None, last_de_result="won", de_awaiting_next=1)
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert not projection["busy"]
    assert not projection["needs_cover"]


def test_pool_legacy_roles_and_completed_pool_do_not_make_unfinished_conflicts():
    rows = [
        athlete("live", "LIVE Athlete", phase="pools", covered_by="Taylor", de_coaches=None, main_coach="Alex", side_coach="Taylor", source_strip="A2"),
        athlete("unfinished", "UNFINISHED Athlete", phase="pools", de_coaches=None, main_coach="Taylor", side_coach="Jordan", pool_wins=None, pool_losses=None),
        athlete("complete", "COMPLETE Athlete", phase="pools", de_coaches=None, main_coach="Taylor", pool_wins=3, pool_losses=3),
    ]
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert projection["busy"][0]["actual_strip"] == "Actual strip A2"
    assert [row["athlete_id"] for row in projection["needs_cover"]] == ["unfinished"]
    assert projection["needs_cover"][0]["unoccupied_peers"] == ["Jordan"]


def test_missing_or_future_timer_and_json_coaches_are_safe():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex", covered_at="2026-10-01T11:12:00"),
        athlete("json", "JSON Athlete", de_coaches=None, de_coaches_json='["Alex", "Alex"]'),
    ]
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert projection["busy"][0]["busy_elapsed"] == "just started"
    assert projection["needs_cover"][0]["busy_coaches"] == ["Alex"]
    rows[0]["covered_at"] = "not a date"
    assert build_busy_coach_board(rows, EVENTS, now=NOW)["busy"][0]["busy_elapsed"] == "Start time unavailable"


def test_closed_events_do_not_keep_coach_busy_in_current_day():
    events = {**EVENTS, "old": {"id": "old", "status": "closed", "name": "Yesterday"}}
    rows = [athlete("old", "OLD Athlete", event_id="old", covered_by="Alex"), athlete("live", "LIVE Athlete")]
    assert build_busy_coach_board(rows, events, now=NOW) == {
        "busy": [], "needs_cover": [], "accepted_cover": [],
    }


def test_takeover_resolves_warning_without_claiming_physical_coverage():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex", live_location="D4"),
        athlete("waiting", "WAITING Athlete", takeover_coach="Taylor", takeover_at="2026-10-01T11:07:00Z"),
    ]
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert [row["coach"] for row in projection["busy"]] == ["Alex"]
    assert not projection["needs_cover"]
    accepted = projection["accepted_cover"][0]
    assert accepted["athlete_id"] == "waiting" and accepted["takeover_coach"] == "Taylor"
    assert accepted["actual_strip"] == "Actual strip TBD"
    assert accepted["call_label"] == "Not called yet"
    assert accepted["accepted_elapsed"] == "3 min"
    assert "busy_elapsed" not in accepted


def test_takeover_owner_busy_with_someone_else_reopens_coverage_warning():
    rows = [
        athlete("first", "FIRST Athlete", covered_by="Alex", live_location="D4"),
        athlete("second", "SECOND Athlete", covered_by="Taylor", event_id="women"),
        athlete("waiting", "WAITING Athlete", takeover_coach="Taylor"),
    ]
    projection = build_busy_coach_board(rows, EVENTS, now=NOW)
    assert not projection["accepted_cover"]
    warning = projection["needs_cover"][0]
    assert warning["athlete_id"] == "waiting"
    assert warning["takeover_coach"] == "Taylor"
    assert warning["busy_coaches"] == ["Alex", "Taylor"]
    assert warning["needs_alternate"]


def test_read_only_renderer_shows_accepted_takeover_even_when_original_coach_is_free():
    rows = [athlete("waiting", "WAITING Athlete", takeover_coach="Taylor")]
    source = f'''\
from compcoach_live.busy_board import render_busy_coach_board
render_busy_coach_board(None, {{'id': 'meet'}}, {EVENTS!r}, {rows!r}, 'coach', 'Jordan', key_prefix='test')
'''
    app = AppTest.from_string(source).run()
    assert not app.exception and not app.button
    assert app.success[0].value.startswith("WAITING Athlete · Taken by Taylor")
    assert "Not called yet · Actual strip TBD" in app.caption[0].value
    assert not any("Coaches busy now" in item.value for item in app.markdown)


def test_renderer_is_read_only_and_uses_actual_strip_and_not_called_label():
    rows = [
        athlete("live", "LIVE Athlete", covered_by="Alex", live_location="D4", covered_at="2026-10-01T11:00:00Z"),
        athlete("waiting", "WAITING Athlete", event_id="women"),
    ]
    source = f'''\
from compcoach_live.busy_board import render_busy_coach_board
render_busy_coach_board(None, {{'id': 'meet'}}, {EVENTS!r}, {rows!r}, 'coordinator', 'Jordan', key_prefix='test')
'''
    app = AppTest.from_string(source).run()
    assert not app.exception
    assert not app.button
    assert any("Coaches busy now" in item.value for item in app.markdown)
    assert any("Actual strip D4" in item.value for item in app.caption)
    assert "Not called yet" in app.info[0].value
    assert "Actual strip TBD" in app.info[0].value
    assert "Women's Epee" in app.info[0].value
    assert "Alternate coverage needed if called" in app.info[0].value
    assert "Actual strip B1" not in app.info[0].value
