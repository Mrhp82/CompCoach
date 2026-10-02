"""Snapshots distinguish recorded coverage, reservations and unknown availability."""

from copy import deepcopy
import json

from compcoach_live.missed_coaching import build_coach_status_snapshot


def athlete(key="a", **changes):
    return {
        "id": key,
        "name": f"ATHLETE {key}",
        "event_id": "event-a",
        "phase": "de",
        "pod": "B",
        "source_strip": "B1",
        "live_location": "C3",
        "call_status": "now",
        "active_state": "active",
        "participation_status": "active",
        "covered_by": "",
        "covered_at": None,
        "takeover_coach": "",
        "takeover_at": None,
        "version": 7,
        **changes,
    }


def test_all_active_coaches_are_partitioned_without_inferred_availability():
    result = build_coach_status_snapshot(
        ["Alex", "Jordan", "Taylor", "Casey", "Morgan"],
        [
            athlete("covered", covered_by="Alex", covered_at="2026-10-02T16:00:00Z"),
            athlete("reserved", takeover_coach="Jordan", takeover_at="2026-10-02T16:01:00Z"),
            athlete("planned", main_coach="Casey", de_coaches=["Morgan"]),
        ],
        [
            {"coach_name": "Alex", "is_available": 1, "version": 4},
            {"coach_name": "Jordan", "is_available": 1, "version": 5},
            {"coach_name": "Taylor", "is_available": 1, "version": 6,
             "available_since": "2026-10-02T15:59:00Z", "updated_by": "Taylor"},
            {"coach_name": "Morgan", "is_available": 0},
        ],
        {"event-a": "Cadet ME"},
    )
    coaches = {row["coach_name"]: row for row in result["coaches"]}
    assert [row["status"] for row in result["coaches"]] == [
        "busy", "reserved", "available", "unconfirmed", "unconfirmed"
    ]
    assert (result["busy_count"], result["reserved_count"], result["available_count"],
            result["unconfirmed_count"], result["total_coaches"]) == (1, 1, 1, 2, 5)
    assert not result["all_committed"]
    assert not coaches["Alex"]["is_declared_available"]
    assert coaches["Alex"]["stored_is_available"]
    assert coaches["Taylor"]["is_declared_available"]
    assert coaches["Taylor"]["availability_version"] == 6
    assert coaches["Taylor"]["available_since"] == "2026-10-02T15:59:00Z"
    assert coaches["Taylor"]["availability_updated_by"] == "Taylor"
    assert "2 availability unconfirmed" in result["summary_label"]
    assert result["status_note"] == "Status at reporting time"


def test_cross_event_busy_and_reservations_are_both_kept_for_same_coach():
    result = build_coach_status_snapshot(
        ["Alex"],
        [
            athlete("first", covered_by="Alex", covered_at="2026-10-02T16:00:00Z"),
            athlete("second", event_id="event-b", live_location="P4", pod="F",
                    takeover_coach="Alex", takeover_at="2026-10-02T16:02:00Z"),
        ],
        [{"coach_name": "Alex", "is_available": True}],
        {"event-a": "Cadet ME", "event-b": "Junior WE"},
    )
    row = result["coaches"][0]
    assert row["status"] == "busy"
    assert row["busy_with"][0] == {
        "athlete_id": "first", "athlete_name": "ATHLETE first", "athlete_version": 7,
        "event_id": "event-a", "event_name": "Cadet ME", "phase": "de",
        "actual_strip": "C3", "source_pod": "B", "source_strip": "B1", "call_status": "now",
        "covered_since": "2026-10-02T16:00:00Z", "reserved_since": None,
    }
    assert row["reserved_for"][0]["event_name"] == "Junior WE"
    assert row["reserved_for"][0]["actual_strip"] == "P4"
    assert row["reserved_for"][0]["source_pod"] == "F"
    assert row["reserved_for"][0]["reserved_since"] == "2026-10-02T16:02:00Z"
    assert result["busy_count"] == 1 and result["reserved_count"] == 0
    assert result["all_committed"]


def test_inactive_athletes_and_coaches_do_not_claim_busy_or_available_status():
    result = build_coach_status_snapshot(
        ["Alex", "Jordan"],
        [
            athlete("out", covered_by="Alex", active_state="eliminated"),
            athlete("absent", takeover_coach="Alex", participation_status="absent"),
            athlete("withdrawn", covered_by="Jordan", participation_status="withdrawn"),
            athlete("external", covered_by="Former coach"),
        ],
        [{"coach_name": "Former coach", "is_available": 1}],
        {},
    )
    assert [row["status"] for row in result["coaches"]] == ["unconfirmed", "unconfirmed"]
    assert all(not row["busy_with"] and not row["reserved_for"] for row in result["coaches"])
    assert result["available_count"] == 0
    assert not result["all_committed"]


def test_names_are_deduplicated_and_matched_without_changing_inputs():
    names = ["  Alex   Rivera ", "alex rivera", "", "Jordan", " JORDAN "]
    athletes = [athlete(covered_by="ALEX  RIVERA", live_location="", source_strip="B1")]
    availability = [{"coach_name": " jordan ", "is_available": "1", "version": 3}]
    event_names = {"event-a": "Cadet ME"}
    original = deepcopy((names, athletes, availability, event_names))
    result = build_coach_status_snapshot(names, athletes, availability, event_names)
    assert [row["coach_name"] for row in result["coaches"]] == ["Alex Rivera", "Jordan"]
    assert result["coaches"][0]["status"] == "busy"
    assert result["coaches"][0]["busy_with"][0]["actual_strip"] == ""
    assert result["coaches"][0]["busy_with"][0]["source_strip"] == "B1"
    assert result["coaches"][1]["is_declared_available"]
    assert (names, athletes, availability, event_names) == original
    json.dumps(result)


def test_explicit_false_and_missing_availability_are_unconfirmed():
    result = build_coach_status_snapshot(
        ["Alex", "Jordan", "Taylor"], [],
        [{"coach_name": "Alex", "is_available": "0"},
         {"coach_name": "Jordan", "is_available": False}], {},
    )
    assert result["unconfirmed_count"] == 3
    assert result["available_count"] == 0
    assert not result["all_committed"]


def test_all_committed_includes_reservations_but_not_an_empty_roster():
    result = build_coach_status_snapshot(
        ["Alex", "Jordan"],
        [athlete("a", covered_by="Alex"), athlete("b", takeover_coach="Jordan")], [], {},
    )
    assert result["all_committed"]
    assert result["busy_count"] == result["reserved_count"] == 1
    empty = build_coach_status_snapshot([], [], [], {})
    assert empty["total_coaches"] == 0 and not empty["all_committed"]
    assert empty["summary_label"] == "No active coaches recorded"
