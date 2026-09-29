"""Import helpers for CompCoach Live.

The public entry point is :func:`parse_pasted_table`.  It deliberately accepts
the untidy text a phone/browser clipboard tends to produce: tab-separated
rows, Markdown pipe tables, and simple HTML tables.  The parser only retains
the operational fields CompCoach needs and ignores the descriptive FTL
columns (club, division, country, and any future extras).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

Phase = Literal["pools", "de", "unknown"]


@dataclass
class ParseResult:
    """Result returned by :func:`parse_pasted_table`.

    ``records`` are plain dictionaries so they can be fed directly to a
    dataframe, JSON response, or database upsert.  ``diagnostics`` is intended
    for a compact import preview in the UI.
    """

    phase: Phase
    records: list[dict[str, str]]
    diagnostics: dict[str, Any]

    @property
    def ok(self) -> bool:
        """Whether a recognizable table was parsed (an empty DE table is OK)."""

        return self.phase != "unknown" and not self.diagnostics.get("errors")

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "records": self.records,
            "diagnostics": self.diagnostics,
        }


_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TR_END_RE = re.compile(r"<\s*/\s*tr\s*>", re.IGNORECASE)
_CELL_END_RE = re.compile(r"<\s*/\s*t[dh]\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_HTML_TABLE_RE = re.compile(r"<\s*(?:table|tr|t[dh])\b", re.IGNORECASE)
_MARKDOWN_DECORATION_RE = re.compile(r"(?:\*\*|__|`)")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_TIME_VALUE_RE = re.compile(
    r"^(?:\d{1,2}(?::\d{2})\s*(?:a\.?m\.?|p\.?m\.?)?|tbd)$",
    re.IGNORECASE,
)
_POOL_VALUE_RE = re.compile(r"^(?:pool|poule)?\s*#?\s*\d+(?:\.0+)?$", re.IGNORECASE)
_STRIP_VALUE_RE = re.compile(
    r"^(?:(?:strip|piste|pod|sector)\s*[:#-]?\s*)?(?:[A-Za-z]{1,4}\s*-?\s*\d*|#?\s*\d+(?:\.0+)?)$",
    re.IGNORECASE,
)


_HEADER_ALIASES: dict[str, set[str]] = {
    "name": {
        "name",
        "athlete",
        "athlete name",
        "fencer",
        "fencer name",
        "competitor",
        "competitor name",
    },
    "strip": {
        "strip",
        "strip no",
        "strip num",
        "strip number",
        "piste",
        "piste no",
        "piste number",
    },
    "time": {
        "time",
        "start",
        "start time",
        "scheduled time",
        "call time",
    },
    "pool": {
        "pool",
        "pool no",
        "pool num",
        "pool number",
        "poule",
        "poule no",
        "poule number",
    },
}


def _clean_text(value: object) -> str:
    """Decode HTML-ish content and collapse visual whitespace."""

    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\ufeff", "")
    text = _BR_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_display_name(value: object) -> str:
    """Clean a name without guessing whether it is LAST/FIRST or FIRST/LAST.

    Fencing Time commonly uses mixed forms such as ``AGLIPAY Alyssa``.  The
    casing and token order therefore remain untouched; only clipboard/HTML
    noise and redundant whitespace are removed.
    """

    return _clean_text(value)


def canonical_athlete_key(name: object) -> str:
    """Return a comparison key stable across case, accents, and punctuation."""

    cleaned = normalize_display_name(name)
    decomposed = unicodedata.normalize("NFKD", cleaned).casefold()
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    alphanumeric_tokens = re.findall(r"[^\W_]+", without_marks, flags=re.UNICODE)
    return " ".join(alphanumeric_tokens)


def stable_athlete_id(name: object) -> str:
    """Create a deterministic merge key that does not depend on strip/phase.

    A strip, pool, pod, or competition phase can change several times during
    the day, while the athlete ID must remain constant for database upserts.
    """

    key = canonical_athlete_key(name)
    if not key:
        return ""
    return f"ath_{sha256(key.encode('utf-8')).hexdigest()[:16]}"


# A descriptive alias for callers that prefer the term "identity".
stable_athlete_identity = stable_athlete_id


def derive_pod(strip: object) -> str:
    """Derive the alphabetic pod/sector prefix from a strip value.

    Examples: ``C3 -> C``, ``P -> P``, ``M-12 -> M``.  A purely numeric strip
    intentionally returns an empty pod because no sector can be inferred.
    """

    value = _clean_text(strip)
    if not value:
        return ""
    value = re.sub(
        r"^(?:strip|piste|pod|sector)\s*[:#-]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    if re.fullmatch(r"#?\s*\d+(?:\.0+)?", value):
        return ""
    match = re.match(r"([A-Za-z]+)", value)
    return match.group(1).upper() if match else ""


def _normalize_strip(value: object) -> str:
    strip = _clean_text(value)
    if not strip:
        return ""
    strip = re.sub(r"^(?:strip|piste)\s*[:#-]?\s*", "", strip, flags=re.IGNORECASE)
    # Normalize the compact codes used by FTL, while leaving unusual values
    # intact so the app never destroys information supplied by a coordinator.
    compact = re.fullmatch(r"([A-Za-z]+)\s*-?\s*(\d*)", strip)
    if compact:
        return f"{compact.group(1).upper()}{compact.group(2)}"
    if re.fullmatch(r"\d+(?:\.0+)?", strip):
        return strip.removesuffix(".0")
    return strip


def _normalize_pool(value: object) -> str:
    pool = _clean_text(value)
    if not pool:
        return ""
    pool = re.sub(r"^(?:pool|poule)\s*[:#-]?\s*", "", pool, flags=re.IGNORECASE)
    pool = pool.strip().lstrip("#").strip()
    if re.fullmatch(r"\d+\.0+", pool):
        return pool.split(".", 1)[0]
    return pool


def _canonical_header(value: object) -> str:
    header = _clean_text(value)
    header = _MARKDOWN_DECORATION_RE.sub("", header)
    header = header.casefold().replace("#", " no ")
    header = re.sub(r"[^\w]+", " ", header, flags=re.UNICODE)
    return re.sub(r"\s+", " ", header).strip()


def _recognized_header(cells: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(cells):
        canonical = _canonical_header(cell)
        for field, aliases in _HEADER_ALIASES.items():
            if field not in mapping and canonical in aliases:
                mapping[field] = index
                break
    return mapping


def _split_pipe_row(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped.removeprefix("|")
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    # FTL values do not normally contain pipes, but honoring Markdown's \|
    # escape costs little and avoids shifting later columns.
    placeholder = "\x00PIPE\x00"
    stripped = stripped.replace(r"\|", placeholder)
    return [cell.replace(placeholder, "|") for cell in stripped.split("|")]


def _split_candidate(line: str, row_format: str | None = None) -> tuple[list[str], str]:
    if row_format in {"tsv", "html"}:
        return line.split("\t"), row_format
    if row_format == "markdown":
        return _split_pipe_row(line), row_format
    if "\t" in line:
        return line.split("\t"), "tsv"
    if "|" in line:
        return _split_pipe_row(line), "markdown"
    # Some browser/phone clipboards replace table tabs with aligned runs of
    # spaces.  Split those runs, but never split on a single space: names,
    # clubs, and divisions legitimately contain spaces.
    spaced = re.split(r"\s{2,}", line.strip())
    if len(spaced) > 1:
        return spaced, "spaced"
    return [line], "plain"


def _looks_like_separator(cells: list[str]) -> bool:
    meaningful = [_clean_text(cell) for cell in cells if _clean_text(cell)]
    return bool(meaningful) and all(_SEPARATOR_CELL_RE.fullmatch(c) for c in meaningful)


def _field_value(header_map: dict[str, int], cells: list[str], name: str) -> str:
    index = header_map.get(name)
    return cells[index] if index is not None and index < len(cells) else ""


def _prepare_source(raw_text: str) -> tuple[str, str | None]:
    """Convert a simple HTML table to TSV-like text before line parsing."""

    decoded = html.unescape(raw_text).replace("\r\n", "\n").replace("\r", "\n")
    if not _HTML_TABLE_RE.search(decoded):
        # Still clean BR elements inside Markdown cells without creating rows.
        return _BR_RE.sub(" ", decoded), None

    prepared = _BR_RE.sub(" ", decoded)
    prepared = _CELL_END_RE.sub("\t", prepared)
    prepared = _TR_END_RE.sub("\n", prepared)
    prepared = _TAG_RE.sub("", prepared)
    return prepared, "html"


def _normalize_phase_hint(phase_hint: str | None) -> Phase:
    if not phase_hint:
        return "unknown"
    canonical = re.sub(r"[^a-z]", "", phase_hint.casefold())
    if canonical in {"pool", "pools", "poule", "poules", "gironi", "girone"}:
        return "pools"
    if canonical in {
        "de",
        "directelimination",
        "directeliminations",
        "elimination",
        "eliminations",
        "dirette",
        "diretta",
    }:
        return "de"
    return "unknown"


def _looks_like_time(value: object) -> bool:
    cleaned = _clean_text(value)
    return not cleaned or bool(_TIME_VALUE_RE.fullmatch(cleaned))


def _looks_like_pool(value: object) -> bool:
    return bool(_POOL_VALUE_RE.fullmatch(_clean_text(value)))


def _looks_like_strip(value: object) -> bool:
    cleaned = _clean_text(value)
    return bool(cleaned and _STRIP_VALUE_RE.fullmatch(cleaned))


def _looks_like_name(value: object) -> bool:
    cleaned = _clean_text(value)
    if not cleaned or cleaned.casefold() == "no matching records found":
        return False
    return bool(re.search(r"[^\W\d_]", cleaned, flags=re.UNICODE))


def _infer_headerless_schema(
    lines: list[str],
    forced_format: str | None,
    phase_hint: str | None,
) -> tuple[dict[str, int], Phase, str, str] | None:
    """Infer the fixed leading columns used by Fencing Time Live.

    Descriptive columns are deliberately ignored, so missing Country,
    Division, or Club cells at the right edge never invalidate a row.
    Inference is only attempted for genuinely columnar text (tabs, pipes, or
    runs of two or more spaces); ordinary notes such as ``Max on deck at M1``
    therefore remain rejected.
    """

    candidates: list[tuple[list[str], str]] = []
    empty_marker_format = ""
    for line in lines:
        if not line.strip():
            continue
        cells, candidate_format = _split_candidate(line, forced_format)
        cleaned = [_clean_text(cell) for cell in cells]
        if _looks_like_separator(cleaned):
            continue
        first = cleaned[0] if cleaned else ""
        if first.casefold() == "no matching records found":
            empty_marker_format = candidate_format
            continue
        if len(cleaned) >= 2 and _looks_like_name(first):
            candidates.append((cleaned, candidate_format))

    hinted_phase = _normalize_phase_hint(phase_hint)
    if not candidates:
        if empty_marker_format:
            return {"name": 0, "strip": 1}, "de", empty_marker_format, "de"
        return None

    full_pool_rows = 0
    compact_pool_rows = 0
    de_rows = 0
    format_counts: dict[str, int] = {}
    for cells, candidate_format in candidates:
        format_counts[candidate_format] = format_counts.get(candidate_format, 0) + 1
        strip_ok = not cells[1] or _looks_like_strip(cells[1])
        if not strip_ok:
            continue
        if len(cells) >= 4 and _looks_like_time(cells[2]) and _looks_like_pool(cells[3]):
            full_pool_rows += 1
            continue
        if len(cells) >= 3 and _looks_like_pool(cells[2]):
            compact_pool_rows += 1
            continue
        # A DE copy starts Name, Strip, Club...; the strip may still be blank
        # before FTL assigns a piste.  Explicit columns keep that case safe.
        if _looks_like_strip(cells[1]) or (not cells[1] and len(cells) >= 3):
            de_rows += 1

    row_format = max(format_counts, key=format_counts.get)
    if row_format == "plain":
        return None

    if hinted_phase == "pools":
        layout = "pools_full" if full_pool_rows >= compact_pool_rows else "pools_compact"
    elif hinted_phase == "de":
        layout = "de"
    elif full_pool_rows:
        layout = "pools_full"
    elif compact_pool_rows:
        layout = "pools_compact"
    elif de_rows:
        layout = "de"
    else:
        return None

    if layout == "pools_full":
        return {"name": 0, "strip": 1, "time": 2, "pool": 3}, "pools", row_format, layout
    if layout == "pools_compact":
        return {"name": 0, "strip": 1, "pool": 2}, "pools", row_format, layout
    return {"name": 0, "strip": 1}, "de", row_format, layout


def parse_pasted_table(text: object, phase_hint: str | None = None) -> ParseResult:
    """Parse a pasted FTL-style roster/strip table.

    Headers are preferred when present.  Without them, the fixed leading
    columns used by Fencing Time Live are inferred conservatively from
    columnar clipboard text.  ``phase_hint`` is only a fallback and never
    overrides an unambiguous header.
    """

    raw_text = "" if text is None else str(text)
    prepared, forced_format = _prepare_source(raw_text)
    lines = prepared.splitlines()

    diagnostics: dict[str, Any] = {
        "source_format": forced_format or "unknown",
        "format": forced_format or "unknown",  # convenient backwards alias
        "header_row": None,
        "recognized_columns": {},
        "schema_source": "unknown",
        "inferred_layout": "",
        "rows_seen": 0,
        "records_imported": 0,
        "ignored_rows": {
            "before_header": 0,
            "empty": 0,
            "separator": 0,
            "empty_marker": 0,
            "missing_name": 0,
            "malformed": 0,
            "repeated_header": 0,
            "duplicate_identity": 0,
        },
        "empty_marker_found": False,
        "warnings": [],
        "errors": [],
    }

    header_cells: list[str] | None = None
    header_map: dict[str, int] = {}
    row_format: str | None = forced_format
    header_line_index = -1

    for line_index, line in enumerate(lines):
        if not line.strip():
            diagnostics["ignored_rows"]["empty"] += 1
            continue
        cells, candidate_format = _split_candidate(line, row_format)
        candidate_map = _recognized_header(cells)
        # Name + either strip or pool is enough to identify the table.  This
        # also tolerates a pools export where the time column is omitted.
        if "name" in candidate_map and ({"strip", "pool"} & candidate_map.keys()):
            header_cells = cells
            header_map = candidate_map
            row_format = candidate_format
            header_line_index = line_index
            diagnostics["source_format"] = candidate_format
            diagnostics["format"] = candidate_format
            diagnostics["header_row"] = line_index + 1
            diagnostics["recognized_columns"] = dict(candidate_map)
            break
        diagnostics["ignored_rows"]["before_header"] += 1

    inferred_layout = ""
    if header_cells is None:
        inference = _infer_headerless_schema(lines, forced_format, phase_hint)
        if inference is None:
            diagnostics["errors"].append(
                "The pasted text does not look like a Fencing Time table. Copy at least the Name and Strip columns; headers are optional."
            )
            return ParseResult("unknown", [], diagnostics)
        header_map, detected_phase, row_format, inferred_layout = inference
        header_line_index = -1
        expected_width = max(header_map.values()) + 1
        diagnostics["source_format"] = row_format
        diagnostics["format"] = row_format
        diagnostics["recognized_columns"] = dict(header_map)
        diagnostics["schema_source"] = "inferred"
        diagnostics["inferred_layout"] = inferred_layout
        diagnostics["ignored_rows"]["before_header"] = 0
        label = "Pools" if detected_phase == "pools" else "Direct Elimination"
        diagnostics["warnings"].append(
            f"Headers were not included; the pasted rows were interpreted as {label}."
        )
    else:
        detected_phase = "pools" if "pool" in header_map else "de"
        expected_width = len(header_cells)
        diagnostics["schema_source"] = "header"

    hinted_phase = _normalize_phase_hint(phase_hint)
    if hinted_phase != "unknown" and hinted_phase != detected_phase:
        diagnostics["warnings"].append(
            f"The {hinted_phase!r} phase hint was ignored because the headers indicate {detected_phase!r}."
        )

    records_by_id: dict[str, dict[str, str]] = {}
    record_order: list[str] = []

    for line in lines[header_line_index + 1 :]:
        if not line.strip():
            diagnostics["ignored_rows"]["empty"] += 1
            continue

        cells, _ = _split_candidate(line, row_format)
        cleaned_cells = [_clean_text(cell) for cell in cells]

        if _looks_like_separator(cleaned_cells):
            diagnostics["ignored_rows"]["separator"] += 1
            continue

        repeated_map = _recognized_header(cleaned_cells)
        if "name" in repeated_map and ({"strip", "pool"} & repeated_map.keys()):
            diagnostics["ignored_rows"]["repeated_header"] += 1
            continue

        possible_name = normalize_display_name(_field_value(header_map, cleaned_cells, "name"))
        if possible_name.casefold() == "no matching records found":
            diagnostics["ignored_rows"]["empty_marker"] += 1
            diagnostics["empty_marker_found"] = True
            continue

        if len(cleaned_cells) == 1 and expected_width > 1:
            diagnostics["ignored_rows"]["malformed"] += 1
            continue

        diagnostics["rows_seen"] += 1
        compact_row_in_full_layout = (
            inferred_layout == "pools_full"
            and len(cleaned_cells) == 3
            and _looks_like_pool(cleaned_cells[2])
        )
        if len(cleaned_cells) < expected_width:
            cleaned_cells.extend([""] * (expected_width - len(cleaned_cells)))

        name = normalize_display_name(_field_value(header_map, cleaned_cells, "name"))
        if not name:
            diagnostics["ignored_rows"]["missing_name"] += 1
            continue

        athlete_id = stable_athlete_id(name)
        strip = _normalize_strip(_field_value(header_map, cleaned_cells, "strip"))
        time_value = _clean_text(_field_value(header_map, cleaned_cells, "time"))
        pool_value = _normalize_pool(_field_value(header_map, cleaned_cells, "pool"))
        if compact_row_in_full_layout:
            time_value = ""
            pool_value = _normalize_pool(cleaned_cells[2])
        record = {
            "athlete_id": athlete_id,
            "name": name,
            "strip": strip,
            "time": time_value,
            "pool": pool_value,
            "pod": derive_pod(strip),
            "phase": detected_phase,
        }

        if athlete_id in records_by_id:
            # A repeated browser row should not create a second athlete.  New
            # non-empty values win, allowing a later row to fill a blank strip.
            existing = records_by_id[athlete_id]
            for key, value in record.items():
                if value:
                    existing[key] = value
            diagnostics["ignored_rows"]["duplicate_identity"] += 1
        else:
            records_by_id[athlete_id] = record
            record_order.append(athlete_id)

    records = [records_by_id[athlete_id] for athlete_id in record_order]
    diagnostics["records_imported"] = len(records)
    if not records and not diagnostics["empty_marker_found"]:
        diagnostics["warnings"].append("The table was recognized, but no athlete rows were found.")

    return ParseResult(detected_phase, records, diagnostics)


# Integration-friendly aliases.  Keeping a single implementation avoids two
# parsers gradually behaving differently as import formats evolve.
parse_import = parse_pasted_table
parse_pasted_data = parse_pasted_table


__all__ = [
    "ParseResult",
    "canonical_athlete_key",
    "derive_pod",
    "normalize_display_name",
    "parse_import",
    "parse_pasted_data",
    "parse_pasted_table",
    "stable_athlete_id",
    "stable_athlete_identity",
]
