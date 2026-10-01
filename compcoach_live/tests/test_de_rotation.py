"""Direct-elimination wheel order is persistent and independent of readiness."""

import pytest

from compcoach_live.de_rotation import (
    de_queue_sort_key,
    de_round_label,
    de_rounds_passed,
    ordered_de_athletes,
)
from compcoach_live.storage import CompCoachDB, ConcurrentUpdateError


def row(key, *, wins=0, byes=0, stamp=None, **extra):
    return {
        "id": key, "name": key.upper(), "phase": "de", "de_wins": wins,
        "de_byes": byes, "last_de_result_at": stamp,
        "last_de_result": "won" if wins else "bye" if byes else "",
        **extra,
    }


def keys(athletes):
    return [athlete["id"] for athlete in ordered_de_athletes(athletes)]


def test_initial_order_is_retained_until_a_result_is_reported():
    rows = [row("c"), row("a"), row("b")]
    assert keys(rows) == ["c", "a", "b"]
    assert keys(rows) == ["c", "a", "b"]
    assert all(de_round_label(athlete) == "First DE bout" for athlete in rows)


def test_latest_winner_rotates_to_tail_then_rises_as_other_results_arrive():
    rows = [row("a"), row("b"), row("c")]
    rows[0].update(de_wins=1, last_de_result="won", last_de_result_at="2026-10-01T12:00:00.100001+00:00")
    assert keys(rows) == ["b", "c", "a"]
    rows[1].update(de_wins=1, last_de_result="won", last_de_result_at="2026-10-01T12:00:00.100002+00:00")
    assert keys(rows) == ["c", "a", "b"]
    rows[2].update(de_wins=1, last_de_result="won", last_de_result_at="2026-10-01T12:00:00.100003+00:00")
    assert keys(rows) == ["a", "b", "c"]
    rows[0].update(de_wins=2, last_de_result_at="2026-10-01T12:00:00.100004+00:00")
    assert keys(rows) == ["b", "c", "a"]


def test_overlapping_rounds_follow_the_wheel_without_a_global_round_gate():
    rows = [
        row("a", wins=2, stamp="2026-10-01T12:00:02+00:00"),
        row("b", wins=1, stamp="2026-10-01T12:00:03+00:00"),
        row("c", wins=1, stamp="2026-10-01T12:00:01+00:00"),
    ]
    assert keys(rows) == ["c", "a", "b"]
    assert de_round_label(rows[0]) == "Next bout · 2 rounds passed"
    assert de_round_label(rows[1]) == "Next bout · 1 round passed"


def test_a_call_or_claim_highlights_an_athlete_without_resetting_rotation():
    early = row("a", wins=1, stamp="2026-10-01T12:00:01+00:00", de_awaiting_next=1)
    late = row("b", wins=1, stamp="2026-10-01T12:00:02+00:00", de_awaiting_next=0,
               call_status="now", live_location="J4", covered_by="Alex")
    assert keys([late, early]) == ["a", "b"]
    before = de_queue_sort_key(late)
    late.update(de_awaiting_next=1, call_status="waiting", covered_by="", updated_at="2027-01-01T00:00:00+00:00")
    assert de_queue_sort_key(late) == before


def test_timezone_offsets_and_precise_timestamps_sort_by_real_time():
    rows = [
        row("later", wins=1, stamp="2026-10-01T05:00:00.100003-07:00"),
        row("early", wins=1, stamp="2026-10-01T12:00:00.100001Z"),
        row("middle", wins=1, stamp="2026-10-01T12:00:00.100002"),
    ]
    assert keys(rows) == ["early", "middle", "later"]


def test_legacy_timestamp_ties_are_stable_and_bad_dates_do_not_hide_rows():
    rows = [row("new"), row("broken", wins=1, stamp="bad-date"),
            row("b", wins=1, stamp="2026-10-01T12:00:00+00:00"),
            row("a", wins=1, stamp="2026-10-01T12:00:00+00:00")]
    assert keys(rows) == ["new", "broken", "b", "a"]


@pytest.mark.parametrize("wins,byes,expected", [(1, 2, 3), (0, 1, 1), ("2", "1", 3), (-1, None, 0), ("bad", 2, 2)])
def test_progress_labels_include_byes_without_inventing_wins(wins, byes, expected):
    athlete = row("a", wins=wins, byes=byes)
    assert de_rounds_passed(athlete) == expected


@pytest.fixture
def context(tmp_path):
    db = CompCoachDB(tmp_path / "wheel.db")
    event = db.create_event("DE Wheel", ["Alex", "Taylor"], ["Jordan"])
    records = [{"athlete_id": name, "name": name.upper(), "phase": "de", "pod": "B", "strip": "B1", "time": ""} for name in ("a", "b", "c")]
    db.merge_import(event["id"], records, "Jordan")
    by_name = {athlete["name"].lower(): athlete["id"] for athlete in db.list_athletes(event["id"])}
    return db, event, records, by_name


def db_keys(db, event):
    return [athlete["name"].lower() for athlete in ordered_de_athletes(db.list_athletes(event["id"])) if athlete["active_state"] == "active"]


def test_reimports_and_reopening_preserve_the_wheel_and_correction_restores_order(context, monkeypatch):
    db, event, records, ids = context
    stamps = iter(["2026-10-01T12:00:00.100001+00:00", "2026-10-01T12:00:00.100002+00:00", "2026-10-01T12:00:00.100003+00:00", "2026-10-01T12:00:00.100004+00:00"])
    monkeypatch.setattr("compcoach_live.storage.de_result_now", lambda: next(stamps))
    a_initial = db.get_athlete(event["id"], ids["a"])
    db.mark_result(event["id"], ids["a"], outcome="won", actor="Alex", expected_version=a_initial["version"])
    assert db_keys(db, event) == ["b", "c", "a"]
    b = db.get_athlete(event["id"], ids["b"])
    db.mark_bye(event["id"], ids["b"], actor="Alex", expected_version=b["version"])
    assert db_keys(db, event) == ["c", "a", "b"]
    db.merge_import(event["id"], records, "Jordan")
    assert db_keys(CompCoachDB(db.path), event) == ["c", "a", "b"]
    # No Ready operation is necessary before a subsequent win.
    a = db.get_athlete(event["id"], ids["a"])
    db.mark_result(event["id"], ids["a"], outcome="won", actor="Alex", expected_version=a["version"])
    assert db_keys(db, event) == ["c", "b", "a"]
    latest = db.get_athlete(event["id"], ids["a"])
    db.correct_de_result(event["id"], ids["a"], "Jordan", expected_version=latest["version"])
    assert db_keys(db, event) == ["c", "a", "b"]
    with pytest.raises(ConcurrentUpdateError):
        db.mark_result(event["id"], ids["a"], outcome="won", actor="Taylor", expected_version=a_initial["version"])


def test_live_strip_and_assignment_updates_do_not_rotate_a_winner_again(context, monkeypatch):
    db, event, _, ids = context
    stamps = iter(["2026-10-01T12:00:00.100001+00:00", "2026-10-01T12:00:00.100002+00:00"])
    monkeypatch.setattr("compcoach_live.storage.de_result_now", lambda: next(stamps))
    db.mark_result(event["id"], ids["a"], outcome="won", actor="Alex")
    db.mark_result(event["id"], ids["b"], outcome="won", actor="Alex")
    db.report_call(event["id"], ids["b"], status="now", location="J4", actor="Jordan")
    db.assign_de_athletes(event["id"], [ids["b"]], coaches=["Taylor"], actor="Jordan")
    assert db_keys(db, event) == ["c", "a", "b"]
    b = db.get_athlete(event["id"], ids["b"])
    assert b["pod"] == "B" and b["source_strip"] == "B1" and b["live_location"] == "J4"
