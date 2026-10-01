import pytest

from compcoach_live.parsers import (
    canonical_athlete_key,
    derive_pod,
    parse_pasted_table,
    stable_athlete_id,
)

POOLS_MARKDOWN = """
| **Name**       | **Strip #** | **Time** | **Pool #** | **Club**                         | **Division**       | **Country** |
| -------------- | ----------- | -------- | ---------- | -------------------------------- | ------------------ | ----------- |
| AGLIPAY Alyssa | C3          |          | 7          | Academy Of Fencing Masters (AFM) | Central California | &#x20;USA   |
| DEFENSOR Ella  | C2          | 8:00 AM  | 6          | Academy Of Fencing Masters (AFM) | Central California | USA         |
| YU Elise       | B4          |          | 3          | Academy Of Fencing Masters (AFM) | Central California | USA<br><br> |
"""


def test_markdown_pools_detects_headers_and_ignores_extra_columns():
    result = parse_pasted_table(POOLS_MARKDOWN)

    assert result.ok
    assert result.phase == "pools"
    assert len(result.records) == 3
    assert result.records[0] == {
        "athlete_id": stable_athlete_id("AGLIPAY Alyssa"),
        "name": "AGLIPAY Alyssa",
        "strip": "C3",
        "time": "",
        "pool": "7",
        "pod": "C",
        "phase": "pools",
    }
    assert result.records[1]["time"] == "8:00 AM"
    assert result.records[2]["name"] == "YU Elise"
    assert result.diagnostics["source_format"] == "markdown"
    assert result.diagnostics["ignored_rows"]["separator"] == 1


def test_tsv_preserves_blank_time_and_column_positions():
    pasted = (
        "Name\tStrip #\tTime\tPool #\tClub\n"
        "CHAN Nathan\tA1\t\t1\tAFM\n"
        "DING Max\t12\t9:15 AM\t4\tAFM\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert result.records[0]["time"] == ""
    assert result.records[0]["pool"] == "1"
    assert result.records[0]["pod"] == "A"
    assert result.records[1]["strip"] == "12"
    assert result.records[1]["pod"] == ""


def test_headerless_pools_tsv_is_inferred_from_operational_columns():
    pasted = (
        "LEE Lizzie\tB2\t\t4\tAcademy Of Fencing Masters (AFM)\tCentral California\tUSA\n"
        "LUO Xinyue\tC1\t\t1\tAcademy Of Fencing Masters (AFM)\tCentral California\tUSA\n"
        "YU Elise\tB4\t\t3\tAcademy Of Fencing Masters (AFM)\tCentral California\tUSA\n"
    )

    result = parse_pasted_table(pasted)

    assert result.ok
    assert result.phase == "pools"
    assert [(r["name"], r["strip"], r["time"], r["pool"], r["pod"]) for r in result.records] == [
        ("LEE Lizzie", "B2", "", "4", "B"),
        ("LUO Xinyue", "C1", "", "1", "C"),
        ("YU Elise", "B4", "", "3", "B"),
    ]
    assert result.diagnostics["schema_source"] == "inferred"
    assert result.diagnostics["inferred_layout"] == "pools_full"


def test_headerless_pools_accepts_missing_trailing_cells_and_compact_rows():
    pasted = (
        "| LEE Lizzie | B2 | | 4 | AFM | Central California | USA |\n"
        "| YU Elise | B4 | 3"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert [(r["name"], r["strip"], r["time"], r["pool"]) for r in result.records] == [
        ("LEE Lizzie", "B2", "", "4"),
        ("YU Elise", "B4", "", "3"),
    ]


def test_headerless_pools_detects_collapsed_blank_time_column():
    pasted = (
        "LEE Lizzie\tB2\t4\tAFM\tCentral California\tUSA\n"
        "YU Elise\tB4\t3\tAFM\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert [r["time"] for r in result.records] == ["", ""]
    assert [r["pool"] for r in result.records] == ["4", "3"]
    assert result.diagnostics["inferred_layout"] == "pools_compact"


def test_headerless_de_accepts_short_final_row():
    pasted = (
        "DING Max\tm1\tAFM\tCentral California\tUSA\n"
        "EVANS Evan\tP\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "de"
    assert [(r["name"], r["strip"], r["pod"]) for r in result.records] == [
        ("DING Max", "M1", "M"),
        ("EVANS Evan", "P", "P"),
    ]


def test_headerless_aligned_spaces_are_supported():
    pasted = (
        "LEE Lizzie    B2        4    Academy Of Fencing Masters (AFM)    Central California    USA\n"
        "YU Elise      B4        3    Academy Of Fencing Masters (AFM)    Central California\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert [(r["name"], r["strip"], r["pool"]) for r in result.records] == [
        ("LEE Lizzie", "B2", "4"),
        ("YU Elise", "B4", "3"),
    ]


def test_headerless_empty_marker_is_valid_de_table():
    result = parse_pasted_table("No matching records found\t\t\t\t")

    assert result.ok
    assert result.phase == "de"
    assert result.records == []
    assert result.diagnostics["empty_marker_found"] is True


def test_empty_de_table_is_valid_and_does_not_import_marker():
    pasted = """
| **Name**                  | **Strip #** | **Club** | **Division** | **Country** |
| ------------------------- | ----------- | -------- | ------------ | ----------- |
| No matching records found |             |          |              |             |
"""

    result = parse_pasted_table(pasted)

    assert result.ok
    assert result.phase == "de"
    assert result.records == []
    assert result.diagnostics["empty_marker_found"] is True
    assert result.diagnostics["ignored_rows"]["empty_marker"] == 1
    assert not result.diagnostics["warnings"]


def test_de_rows_derive_sector_and_accept_sector_only_strip():
    pasted = (
        "Name\tStrip #\tClub\tDivision\tCountry\n"
        "DING Max\tm1\tAFM\tCentral California\tUSA\n"
        "EVANS Evan\tP\tAFM\tCentral California\tUSA\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "de"
    assert [(r["strip"], r["pod"]) for r in result.records] == [
        ("M1", "M"),
        ("P", "P"),
    ]
    assert all(r["pool"] == "" and r["time"] == "" for r in result.records)


def test_simple_html_table_and_entities_are_supported():
    pasted = """
<table>
  <tr><th>Name</th><th>Strip #</th><th>Time</th><th>Pool #</th><th>Country</th></tr>
  <tr><td>O&#39;NEIL&nbsp;Zoë</td><td>D 4</td><td></td><td>Pool #9</td><td>USA<br>West</td></tr>
</table>
"""

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert result.diagnostics["source_format"] == "html"
    assert len(result.records) == 1
    assert result.records[0]["name"] == "O'NEIL Zoë"
    assert result.records[0]["strip"] == "D4"
    assert result.records[0]["pool"] == "9"
    assert result.records[0]["pod"] == "D"


def test_stable_identity_ignores_case_accents_spacing_and_punctuation():
    variants = ["José  O'Neil", "JOSE O NEIL", "  josé-o’neil "]

    assert len({canonical_athlete_key(v) for v in variants}) == 1
    assert len({stable_athlete_id(v) for v in variants}) == 1
    assert stable_athlete_id("José O'Neil").startswith("ath_")


def test_stable_identity_does_not_change_with_phase_or_strip():
    pools = parse_pasted_table("Name\tStrip #\tPool #\nDING Max\tC3\t4")
    de = parse_pasted_table("Name\tStrip #\nDING Max\tM1")

    assert pools.records[0]["athlete_id"] == de.records[0]["athlete_id"]


def test_duplicate_athlete_is_coalesced_with_latest_nonblank_values():
    pasted = (
        "Name\tStrip #\tPool #\n"
        "DING Max\t\t4\n"
        "ding max\tM1\t4\n"
    )

    result = parse_pasted_table(pasted)

    assert len(result.records) == 1
    assert result.records[0]["name"] == "ding max"
    assert result.records[0]["strip"] == "M1"
    assert result.diagnostics["ignored_rows"]["duplicate_identity"] == 1


def test_header_detection_handles_aliases_and_unrelated_leading_text():
    pasted = (
        "Copied from browser\n"
        "Fencer Name\tPiste No.\tScheduled Time\tPoule Number\tRanking\n"
        "HSU Audrey\tb1\t\t2\t99\n"
    )

    result = parse_pasted_table(pasted)

    assert result.phase == "pools"
    assert result.records[0]["name"] == "HSU Audrey"
    assert result.records[0]["strip"] == "B1"
    assert result.records[0]["pool"] == "2"
    assert result.diagnostics["ignored_rows"]["before_header"] == 1


def test_conflicting_phase_hint_does_not_override_detected_headers():
    result = parse_pasted_table("Name\tStrip #\tPool #\nLEE Lizzie\tB2\t4", "de")

    assert result.phase == "pools"
    assert result.diagnostics["warnings"]


def test_unrecognized_text_returns_diagnostic_instead_of_raising():
    result = parse_pasted_table("Max on deck at M1")

    assert not result.ok
    assert result.phase == "unknown"
    assert result.records == []
    assert result.diagnostics["errors"]


def test_derive_pod_examples():
    assert derive_pod("C3") == "C"
    assert derive_pod("P") == "P"
    assert derive_pod("Strip M-12") == "M"
    assert derive_pod("12") == ""
    assert derive_pod(7) == ""
    assert derive_pod("") == ""


@pytest.mark.parametrize("headers", [True, False])
def test_import_rejects_ocr_website_residue_without_rejecting_short_final_rows(headers):
    pasted = "Name\tStrip #\tClub\tDivision\tCountry\n" if headers else ""
    pasted += (
        "CHAN Nathan\tJ2\tAFM\tCentral California\tUSA\n"
        "fe melive.com\tVE\n"
        "PARK Sangwook\tM1\n"
    )

    result = parse_pasted_table(pasted)

    assert result.ok
    assert result.phase == "de"
    assert [(row["name"], row["strip"]) for row in result.records] == [
        ("CHAN Nathan", "J2"),
        ("PARK Sangwook", "M1"),
    ]
    assert result.diagnostics["ignored_rows"]["page_noise"] == 1


@pytest.mark.parametrize(
    "debris",
    [
        "www.fencingtimelive.com",
        "https://www.fencingtimelive.com/rounds/strips/event/round",
        "fencing time live . com",
        "fe melive . com · VE",
        "fe melive.c0m",
        "fe melive.corn",
        "Fencing T1me L1ve",
        "Fencing Time Live",
        "Copyright 2026",
        "Privacy Policy",
        "Terms of Use",
        "Showing 1 to 10 of 20 entries",
        "Show 10 entries",
        "Search:",
        "Previous",
        "Next",
        "Direct Elimination",
    ],
)
def test_browser_navigation_and_footer_text_are_not_athletes(debris):
    result = parse_pasted_table(f"Name\tStrip #\nDING Max\tB1\n{debris}\tVE")

    assert [row["name"] for row in result.records] == ["DING Max"]
    assert result.diagnostics["ignored_rows"]["page_noise"] == 1


def test_name_noise_filter_preserves_initials_unicode_hyphens_and_navigation_surnames():
    names = [
        "LEE J. P.", "O'NEIL Zoë", "José García-López", "王小明", "ALICE",
        "LIVE Jennifer", "PAGE Nicholas", "NEXT Alex", "SHOW William", "STRIP Jonathan",
        "Max VE", "NG Jo", "O Jo", "JO COM",
    ]
    result = parse_pasted_table("Name\tStrip #\n" + "\n".join(f"{name}\tB1" for name in names))

    assert result.ok
    assert [row["name"] for row in result.records] == names
    assert result.diagnostics["ignored_rows"]["page_noise"] == 0
