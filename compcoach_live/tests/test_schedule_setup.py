from datetime import date

import pytest

from compcoach_live.schedule_setup import _clean_event_names, _suggested_day_date


def test_suggested_day_date_uses_day_after_latest_and_never_past_today():
    days = [
        {"competition_date": "2026-10-09"},
        {"competition_date": ""},
        {"competition_date": "not-a-date"},
        {"competition_date": "2026-10-11"},
    ]

    assert _suggested_day_date(days, today=date(2026, 10, 1)) == date(2026, 10, 12)
    assert _suggested_day_date(days, today=date(2026, 10, 20)) == date(2026, 10, 20)


def test_clean_event_names_allows_empty_day_and_normalizes_spacing():
    assert _clean_event_names(["", "  "]) == []
    assert _clean_event_names([" Cadet   Men ", "Cadet Women", "", " "]) == [
        "Cadet Men",
        "Cadet Women",
    ]


def test_clean_event_names_rejects_case_insensitive_duplicates_and_fifth_event():
    with pytest.raises(ValueError, match="unique"):
        _clean_event_names(["Cadet Men", " cadet men "])

    with pytest.raises(ValueError, match="at most 4"):
        _clean_event_names(["One", "Two", "Three", "Four", "Five"])
