"""Shared coach groups keep equal DE membership and cross-event coverage."""

from copy import deepcopy

from compcoach_live.team_groups import group_team_athletes_by_coach


def _row(athlete_id, phase="pools", **fields):
    return {"id": athlete_id, "name": f"Athlete {athlete_id}", "phase": phase, **fields}


def test_empty_input_and_generator_are_supported():
    assert group_team_athletes_by_coach([]) == []
    row = _row("one", main_coach="Alex")
    assert group_team_athletes_by_coach(iter([row])) == [
        {"label": "Coach Alex", "coaches": ["Alex"], "athletes": [row]},
    ]


def test_pool_main_owns_row_side_is_fallback_and_side_details_stay_in_original_row():
    main = _row("main", main_coach="Sam", side_coach="Alex", event_id="event1")
    fallback = _row("side", main_coach="  ", side_coach="Sam", event_id="event2")
    unrelated = _row("other", main_coach="Alex", side_coach="Sam")
    groups = group_team_athletes_by_coach([main, fallback, unrelated])
    assert [group["label"] for group in groups] == ["Coach Alex", "Coach Sam"]
    assert groups[1]["athletes"] == [main, fallback]
    assert groups[1]["athletes"][0] is main
    assert main["side_coach"] == "Alex"


def test_coach_groups_sort_alphabetically_and_embedded_numbers_naturally():
    names = ["Zoe", "Coach 10", "Coach 2", "alex", "Coach 1"]
    rows = [_row(str(index), main_coach=name) for index, name in enumerate(names)]
    groups = group_team_athletes_by_coach(rows)
    assert [group["coaches"] for group in groups] == [["alex"], ["Coach 1"], ["Coach 2"], ["Coach 10"], ["Zoe"]]


def test_de_equal_coach_names_use_one_shared_bucket_independent_of_order_and_case():
    first = _row("first", "de", de_coaches=["Zoe", "Coach 10", "Coach 2"])
    second = _row("second", "de", de_coaches=["coach 2", "Zoe", "Coach 10", "ZOE"])
    groups = group_team_athletes_by_coach([first, second])
    assert groups == [{
        "label": "Coaches Coach 2 · Coach 10 · Zoe",
        "coaches": ["Coach 2", "Coach 10", "Zoe"], "athletes": [first, second],
    }]
    assert sum(len(group["athletes"]) for group in groups) == 2


def test_enriched_de_assignment_takes_precedence_over_json_and_legacy_slots():
    row = _row(
        "de", "de", de_coaches=["Sam", "Alex"], de_coaches_json='["Wrong"]',
        main_coach="Wrong Main", side_coach="Wrong Side",
    )
    group = group_team_athletes_by_coach([row])[0]
    assert group["coaches"] == ["Alex", "Sam"]
    empty = {**row, "id": "empty", "de_coaches": []}
    assert group_team_athletes_by_coach([empty])[0]["label"] == "Unassigned"


def test_persisted_de_json_and_legacy_assignments_follow_the_existing_helper():
    json_row = _row("json", "de", de_coaches_json='["Sam", "Alex"]', main_coach="Other")
    legacy_row = _row("legacy", "de", main_coach="Alex", side_coach="Sam")
    group = group_team_athletes_by_coach([json_row, legacy_row])[0]
    assert group["label"] == "Coaches Alex · Sam"
    assert group["athletes"] == [json_row, legacy_row]


def test_missing_or_invalid_assignment_is_unassigned_and_sorted_last():
    unknown = _row("unknown", coach="not_an_assignment_field")
    empty = _row("empty", "de", de_coaches=None, main_coach="Ignored")
    invalid = _row("invalid", "de", de_coaches_json="invalid json", side_coach="Ignored")
    assigned = _row("assigned", main_coach="Zoe")
    groups = group_team_athletes_by_coach([unknown, assigned, empty, invalid])
    assert [group["label"] for group in groups] == ["Coach Zoe", "Unassigned"]
    assert groups[-1]["coaches"] == []
    assert groups[-1]["athletes"] == [unknown, empty, invalid]


def test_groups_span_events_and_phases_without_duplicating_rows_or_reordering_work():
    first = _row("1", main_coach="Sam", event_id="junior", source_strip="B10")
    shared = _row("2", "de", de_coaches=["Sam", "Alex"], event_id="cadet", call_status="now")
    third = _row("3", "de", de_coaches=["sam"], event_id="cadet", call_status="now")
    fourth = _row("4", side_coach="Sam", event_id="women", source_strip="B2")
    groups = group_team_athletes_by_coach([first, shared, third, fourth])
    single = next(group for group in groups if group["coaches"] == ["Sam"])
    assert single["athletes"] == [first, third, fourth]
    assert sum(len(group["athletes"]) for group in groups) == 4
    assert sorted(row["id"] for group in groups for row in group["athletes"]) == ["1", "2", "3", "4"]


def test_duplicate_storage_id_keeps_first_row_but_equal_names_with_distinct_ids_survive():
    first = _row("same", "de", de_coaches=["Alex", "Sam"], name="Equal athlete")
    duplicate = {**first, "de_coaches": ["Other"]}
    separate = {**first, "id": "different"}
    group = group_team_athletes_by_coach([first, duplicate, separate])[0]
    assert group["athletes"] == [first, separate]
    assert group["coaches"] == ["Alex", "Sam"]


def test_rows_without_storage_id_are_not_deduplicated_by_name():
    first = {"phase": "pools", "name": "Equal athlete", "main_coach": "Sam"}
    second = {**first, "event_id": "another event"}
    assert group_team_athletes_by_coach([first, second])[0]["athletes"] == [first, second]


def test_group_labels_use_normalized_first_spelling_and_preserve_original_assignments():
    rows = [
        _row("1", "de", de_coaches=["  Coach   Sam  ", "Alex"]),
        _row("2", main_coach="coach sam", side_coach="Alex"),
    ]
    before = deepcopy(rows)
    groups = group_team_athletes_by_coach(rows)
    assert [group["label"] for group in groups] == ["Coaches Alex · Coach Sam", "Coach Coach Sam"]
    assert rows == before
    assert all(any(row is original for original in rows) for group in groups for row in group["athletes"])


def test_non_directory_coach_names_are_not_discarded_by_a_roster_free_data_helper():
    row = _row("1", main_coach="Visiting Coach")
    assert group_team_athletes_by_coach([row])[0]["label"] == "Coach Visiting Coach"
