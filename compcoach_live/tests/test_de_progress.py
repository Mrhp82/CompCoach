"""Derived DE round labels count byes separately from actual fenced bouts."""

from copy import deepcopy

import pytest

from compcoach_live.de_progress import (
    de_progress,
    describe_de_progress,
    validate_de_start_tableau,
)


def athlete(**extra):
    return {
        "active_state": "active",
        "de_wins": 0,
        "de_byes": 0,
        "call_status": "waiting",
        "covered_by": "",
        **extra,
    }


def test_unknown_tableau_keeps_counts_and_actual_next_bout_without_inventing_round():
    progress = describe_de_progress(athlete(de_wins=2, de_byes=1))
    assert progress["progress_summary"] == "1 bye · 2 DE wins · Waiting for DE bout 3"
    assert progress["bye_count"] == 1 and progress["passed_rounds"] == 3
    assert progress["next_de_bout_number"] == 3
    assert progress["initial_tableau"] is progress["current_tableau"] is None
    assert progress["round_label"] == ""
    assert not any(progress[key] for key in ("completed", "out", "inconsistent"))


def test_empty_progress_is_explicit_and_alias_has_same_api():
    assert de_progress is describe_de_progress
    progress = de_progress(athlete())
    assert progress["progress_summary"] == "0 DE wins · Waiting for DE bout 1"
    assert progress["next_de_bout_number"] == 1


@pytest.mark.parametrize(
    "byes,wins,current,bout",
    [(0, 0, 256, 1), (1, 0, 128, 1), (1, 1, 64, 2), (1, 2, 32, 3)],
)
def test_configured_tableau_counts_byes_as_rounds_not_fenced_wins(byes, wins, current, bout):
    progress = describe_de_progress(
        athlete(de_wins=wins, de_byes=byes), {"de_start_tableau": 256}
    )
    assert progress["current_tableau"] == current
    assert progress["round_label"] == f"T{current}"
    assert progress["next_de_bout_number"] == bout
    assert progress["passed_rounds"] == byes + wins
    assert progress["status_label"] == f"Waiting for DE bout {bout}"
    assert not progress["completed"] and not progress["inconsistent"]


def test_exact_waiting_summary_includes_optional_tableau_last():
    progress = describe_de_progress(athlete(de_wins=2, de_byes=1), {"de_start_tableau": 256})
    assert progress["progress_summary"] == "1 bye · 2 DE wins · Waiting for DE bout 3 · T32"


@pytest.mark.parametrize("call_status", ["now", "on_deck", "in_hole"])
def test_called_bouts_do_not_claim_to_be_waiting(call_status):
    progress = describe_de_progress(
        athlete(de_wins=2, de_byes=1, call_status=call_status),
        {"de_start_tableau": 256},
    )
    assert progress["status_label"] == "DE bout 3"
    assert progress["progress_summary"] == "1 bye · 2 DE wins · DE bout 3 · T32"


def test_coverage_removes_waiting_label_but_reservation_does_not():
    covered = describe_de_progress(athlete(de_wins=1, covered_by="Jordan"))
    reserved = describe_de_progress(athlete(de_wins=1, takeover_coach="Jordan"))
    assert covered["status_label"] == "DE bout 2"
    assert reserved["status_label"] == "Waiting for DE bout 2"


@pytest.mark.parametrize(
    "extra,expected",
    [({"de_wins": 0}, "Out at T256"), ({"de_wins": 2, "de_byes": 1}, "Out at T32")],
)
def test_loss_stays_at_bout_tableau_instead_of_advancing(extra, expected):
    progress = describe_de_progress(
        athlete(last_de_result="lost", active_state="eliminated", **extra),
        {"de_start_tableau": 256},
    )
    assert progress["out"] and not progress["completed"]
    assert progress["status_label"] == expected
    assert progress["next_de_bout_number"] is None
    assert not progress["inconsistent"]


def test_unconfigured_out_has_no_guessed_tableau_and_plural_counts_are_clear():
    progress = describe_de_progress(athlete(de_wins=1, de_byes=2, active_state="eliminated"))
    assert progress["progress_summary"] == "2 byes · 1 DE win · Out"
    assert progress["current_tableau"] is None and progress["round_label"] == ""
    assert progress["next_de_bout_number"] is None and progress["out"]


@pytest.mark.parametrize("start,wins,byes", [(2, 1, 0), (256, 7, 1), (4096, 10, 2)])
def test_final_result_is_completed_without_t1_or_a_next_bout(start, wins, byes):
    progress = describe_de_progress(
        athlete(de_wins=wins, de_byes=byes, last_de_result="won"),
        initial_tableau=start,
    )
    assert progress["completed"] and not progress["out"]
    assert not progress["inconsistent"]
    assert progress["current_tableau"] is progress["next_de_bout_number"] is None
    assert progress["round_label"] == "" and progress["status_label"] == "Completed"
    assert "T1" not in progress["progress_summary"]


def test_unknown_start_never_assumes_an_athlete_has_won_the_final():
    progress = describe_de_progress(athlete(de_wins=12, last_de_result="won"))
    assert not progress["completed"] and not progress["inconsistent"]
    assert progress["next_de_bout_number"] == 13


def test_results_past_configured_final_are_flagged_without_negative_or_fictitious_round():
    progress = describe_de_progress(athlete(de_wins=999999), initial_tableau=256)
    assert progress["completed"] and progress["inconsistent"]
    assert progress["current_tableau"] is progress["next_de_bout_number"] is None
    assert progress["round_label"] == "" and progress["status_label"] == "Completed"


def test_impossible_loss_after_final_is_out_and_flagged_without_t1():
    progress = describe_de_progress(athlete(de_wins=8, last_de_result="lost"), initial_tableau=256)
    assert progress["out"] and progress["inconsistent"] and not progress["completed"]
    assert progress["status_label"] == "Out" and progress["round_label"] == ""
    assert progress["current_tableau"] is progress["next_de_bout_number"] is None


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("  ", None), (2, 2), (4096, 4096), (" 256 ", 256)])
def test_start_tableau_validation_accepts_optional_settings_and_integer_form_values(value, expected):
    assert validate_de_start_tableau(value) == expected


@pytest.mark.parametrize("value", [True, False, 0, 1, -2, 3, 255, 4097, 8192, 256.0, "256.0", "T256", "abc", [], {}])
def test_start_tableau_validation_rejects_invalid_configuration(value):
    with pytest.raises(ValueError, match="power of two"):
        validate_de_start_tableau(value)


@pytest.mark.parametrize("wins,byes", [("bad", 1), (-2, 1), (1.5, 0), (True, 0)])
def test_bad_legacy_counts_are_safe_and_marked_for_review(wins, byes):
    progress = describe_de_progress(athlete(de_wins=wins, de_byes=byes))
    assert progress["inconsistent"]
    assert progress["de_wins"] == 0 and progress["passed_rounds"] == byes
    assert progress["next_de_bout_number"] == 1


def test_bad_legacy_configuration_does_not_break_the_operational_summary():
    progress = describe_de_progress(athlete(de_wins=2), {"de_start_tableau": 63})
    assert progress["inconsistent"] and progress["initial_tableau"] is None
    assert progress["progress_summary"] == "2 DE wins · Waiting for DE bout 3"


def test_integer_counts_from_legacy_json_are_read_without_changing_sources():
    source = athlete(de_wins="2", de_byes="1", round_label="T16")
    event = {"de_start_tableau": "256", "round_label": "T8", "other": ["untouched"]}
    before = deepcopy((source, event))
    progress = describe_de_progress(source, event)
    assert progress["round_label"] == "T32"
    assert progress["initial_tableau"] == 256 and not progress["inconsistent"]
    assert (source, event) == before


def test_explicit_start_overrides_event_setting():
    progress = describe_de_progress(athlete(de_wins=1), {"de_start_tableau": 256}, initial_tableau=128)
    assert progress["initial_tableau"] == 128 and progress["current_tableau"] == 64
