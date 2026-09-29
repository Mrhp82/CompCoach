"""Local screenshot OCR for Fencing Time Live tables.

The module deliberately has no dependency on Streamlit or the database.  It
turns an uploaded screenshot into a conservative, editable preview; callers
remain responsible for passing confirmed rows through the normal text import
parser before saving them.

RapidOCR is preferred because it is a pip-installable, local OCR engine.  Its
model is imported and initialized only on the first OCR request.  A local
Tesseract executable is used as a fallback when RapidOCR is unavailable or
cannot recognize a table.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import re
import shutil
import subprocess
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from statistics import median
from typing import Any, Literal, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

Phase = Literal["pools", "de", "unknown"]

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MIN_CELL_CONFIDENCE = 0.80
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass(frozen=True)
class OCRToken:
    """One OCR text box in original-image coordinates."""

    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass
class ScreenshotParseResult:
    """Result returned by :func:`parse_screenshot`."""

    phase: Phase
    rows: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.phase != "unknown" and not self.diagnostics.get("errors")

    @property
    def records(self) -> list[dict[str, Any]]:
        """Compatibility alias for import-preview code that says records."""

        return self.rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "rows": self.rows,
            "diagnostics": self.diagnostics,
        }


class OCRBackend(Protocol):
    """Small interface used by the real engines and lightweight test fakes."""

    name: str

    def scan(
        self,
        image: Image.Image,
        *,
        layout: Literal["sparse", "block"] = "sparse",
        whitelist: str | None = None,
    ) -> list[OCRToken]: ...


class OCRBackendError(RuntimeError):
    """A local OCR engine could not process the image."""


_RAPID_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _rapid_engine() -> Any:
    # Importing onnxruntime and loading the three OCR models is expensive.  Do
    # it only after the user explicitly asks to read a screenshot.
    from rapidocr import RapidOCR

    return RapidOCR()


class _RapidOCRBackend:
    name = "rapidocr"

    def scan(
        self,
        image: Image.Image,
        *,
        layout: Literal["sparse", "block"] = "sparse",
        whitelist: str | None = None,
    ) -> list[OCRToken]:
        del layout, whitelist  # RapidOCR detects layout and has no whitelist.
        try:
            import numpy as np

            with _RAPID_LOCK:
                result = _rapid_engine()(
                    np.asarray(image),
                    use_det=True,
                    use_cls=False,
                    use_rec=True,
                )
        except Exception as exc:  # pragma: no cover - native-runtime failures
            raise OCRBackendError(f"RapidOCR failed: {exc}") from exc

        tokens: list[OCRToken] = []
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None or texts is None or scores is None:
            return tokens
        for box, text, confidence in zip(boxes, texts, scores, strict=False):
            try:
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                cleaned = _clean_cell(text)
                if not cleaned:
                    continue
                tokens.append(
                    OCRToken(
                        cleaned,
                        _clamp_confidence(float(confidence)),
                        min(xs),
                        min(ys),
                        max(xs),
                        max(ys),
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        return tokens


class _TesseractBackend:
    name = "tesseract"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def scan(
        self,
        image: Image.Image,
        *,
        layout: Literal["sparse", "block"] = "sparse",
        whitelist: str | None = None,
    ) -> list[OCRToken]:
        tokens = self._scan_tsv(
            image,
            page_segmentation="11" if layout == "sparse" else "6",
            whitelist=whitelist,
        )
        if layout == "sparse":
            # Fencing Time renders yellow header text on a blue band.  A
            # normal Tesseract pass can treat that entire band as one shape,
            # so add focused high-contrast passes over blue horizontal bands.
            for top, bottom in _blue_header_bands(image):
                crop = image.crop((0, top, image.width, bottom)).convert("L")
                scale = max(2, min(5, round(100 / max(crop.height, 1))))
                crop = crop.resize(
                    (crop.width * scale, crop.height * scale),
                    Image.Resampling.LANCZOS,
                )
                crop = crop.point(lambda value: 255 if value >= 204 else 0)
                header_tokens = self._scan_tsv(
                    crop,
                    page_segmentation="6",
                    whitelist=None,
                )
                tokens.extend(
                    OCRToken(
                        token.text,
                        token.confidence,
                        token.left / scale,
                        top + token.top / scale,
                        token.right / scale,
                        top + token.bottom / scale,
                    )
                    for token in header_tokens
                )
        return tokens

    def _scan_tsv(
        self,
        image: Image.Image,
        *,
        page_segmentation: str,
        whitelist: str | None,
    ) -> list[OCRToken]:
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        command = [
            self.executable,
            "stdin",
            "stdout",
            "-l",
            "eng",
            "--psm",
            page_segmentation,
        ]
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        command.append("tsv")

        try:
            completed = subprocess.run(
                command,
                input=payload.getvalue(),
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCRBackendError(f"Tesseract failed: {exc}") from exc
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise OCRBackendError(
                f"Tesseract exited with code {completed.returncode}: {message[:240]}"
            )

        decoded = completed.stdout.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(decoded), delimiter="\t")
        tokens: list[OCRToken] = []
        for row in reader:
            text = _clean_cell(row.get("text", ""))
            if not text:
                continue
            try:
                confidence = float(row.get("conf", "-1"))
                if confidence < 0:
                    continue
                left = float(row["left"])
                top = float(row["top"])
                width = float(row["width"])
                height = float(row["height"])
            except (KeyError, TypeError, ValueError):
                continue
            tokens.append(
                OCRToken(
                    text,
                    _clamp_confidence(confidence / 100),
                    left,
                    top,
                    left + width,
                    top + height,
                )
            )
        return tokens


def _blue_header_bands(image: Image.Image) -> list[tuple[int, int]]:
    """Locate broad blue bands without importing OpenCV or NumPy."""

    sample_width = min(256, image.width)
    sample_height = max(1, round(image.height * sample_width / image.width))
    sample = image.resize((sample_width, sample_height), Image.Resampling.BILINEAR)
    pixels = sample.load()
    matching_rows: list[int] = []
    for y in range(sample_height):
        blue_pixels = 0
        for x in range(sample_width):
            red, green, blue = pixels[x, y][:3]
            if blue > red + 20 and blue > green + 10 and blue > 80:
                blue_pixels += 1
        if blue_pixels / sample_width >= 0.45:
            matching_rows.append(y)

    sampled_bands: list[list[int]] = []
    for y in matching_rows:
        if not sampled_bands or y > sampled_bands[-1][1] + 1:
            sampled_bands.append([y, y])
        else:
            sampled_bands[-1][1] = y

    scale_y = image.height / sample_height
    bands: list[tuple[int, int]] = []
    for start, end in sampled_bands:
        top = max(0, round(start * scale_y) - 2)
        bottom = min(image.height, round((end + 1) * scale_y) + 2)
        if bottom - top >= 8:
            bands.append((top, bottom))
    return bands


def _available_backends() -> list[OCRBackend]:
    backends: list[OCRBackend] = []
    try:
        rapid_available = importlib.util.find_spec("rapidocr") is not None
    except (ImportError, AttributeError, ValueError):
        rapid_available = False
    if rapid_available:
        backends.append(_RapidOCRBackend())

    executable = shutil.which("tesseract")
    if executable:
        backends.append(_TesseractBackend(executable))
    return backends


def _base_diagnostics(byte_count: int) -> dict[str, Any]:
    return {
        "engine": "",
        "engine_attempts": [],
        "image": {"bytes": byte_count},
        "headers": {},
        "phase_source": "",
        "rows_detected": 0,
        "empty_marker_found": False,
        "column_rereads": [],
        "alternatives": [],
        "cell_confidences": [],
        "review_cells": [],
        "warnings": [],
        "errors": [],
    }


def _error_result(byte_count: int, message: str) -> ScreenshotParseResult:
    diagnostics = _base_diagnostics(byte_count)
    diagnostics["errors"].append(message)
    return ScreenshotParseResult("unknown", [], diagnostics)


def _backend_failure_message(attempts: list[dict[str, str]]) -> str:
    """Turn a known server dependency failure into an actionable error."""

    details = " ".join(str(attempt.get("detail") or "") for attempt in attempts)
    if "libGL.so.1" in details:
        return (
            "The OCR engine cannot start because this server is missing "
            "libGL.so.1. In GitHub Codespaces, install the libgl1 system package "
            "and restart the app."
        )
    return "The local OCR engines could not read this screenshot."


def _decode_image(
    image_bytes: bytes | bytearray | memoryview,
) -> tuple[Image.Image | None, dict[str, Any], str | None]:
    try:
        payload = bytes(image_bytes)
    except (TypeError, ValueError):
        return None, {"bytes": 0}, "Screenshot data must be bytes."

    info: dict[str, Any] = {"bytes": len(payload)}
    if not payload:
        return None, info, "The screenshot is empty."
    if len(payload) > MAX_UPLOAD_BYTES:
        return (
            None,
            info,
            f"The screenshot is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            image_format = (opened.format or "").upper()
            width, height = opened.size
            info.update(
                {"format": image_format, "width": width, "height": height}
            )
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                formats = ", ".join(sorted(SUPPORTED_IMAGE_FORMATS))
                return None, info, f"Unsupported screenshot format. Use {formats}."
            if width <= 0 or height <= 0:
                return None, info, "The screenshot dimensions are invalid."
            if width * height > MAX_IMAGE_PIXELS:
                return (
                    None,
                    info,
                    f"The screenshot exceeds the {MAX_IMAGE_PIXELS:,}-pixel safety limit.",
                )
            transposed = ImageOps.exif_transpose(opened)
            transposed.load()
            image = transposed.convert("RGB")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return None, info, "The uploaded file is not a readable screenshot."

    info["width"], info["height"] = image.size
    info["decoded"] = True
    return image, info, None


def parse_screenshot(
    image_bytes: bytes | bytearray | memoryview,
    *,
    backend: OCRBackend | None = None,
) -> ScreenshotParseResult:
    """Read a Fencing Time table screenshot into a reviewable preview.

    Values are never repaired through character substitution.  Ambiguous or
    low-confidence cells retain the OCR text and appear in ``review_cells``.
    The optional ``backend`` parameter is primarily useful for deterministic
    tests and controlled deployments.
    """

    try:
        byte_count = len(image_bytes)
    except TypeError:
        byte_count = 0
    image, image_info, decode_error = _decode_image(image_bytes)
    if decode_error or image is None:
        result = _error_result(byte_count, decode_error or "Invalid screenshot.")
        result.diagnostics["image"].update(image_info)
        return result

    backends = [backend] if backend is not None else _available_backends()
    if not backends:
        result = _error_result(
            byte_count,
            "No local OCR engine is available. Install RapidOCR or Tesseract.",
        )
        result.diagnostics["image"].update(image_info)
        return result

    attempts: list[dict[str, str]] = []
    best_result: ScreenshotParseResult | None = None
    for selected in backends:
        try:
            candidate = _parse_with_backend(image, image_info, selected)
        # A native OCR package can surface backend-specific exception classes;
        # no engine failure should take down the live competition application.
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                {"engine": selected.name, "status": "failed", "detail": str(exc)[:300]}
            )
            continue

        recognized = candidate.phase != "unknown"
        has_content = bool(candidate.rows) or candidate.diagnostics.get(
            "empty_marker_found"
        )
        attempts.append(
            {
                "engine": selected.name,
                "status": "recognized" if recognized and has_content else "unrecognized",
                "detail": "",
            }
        )
        if best_result is None or _result_score(candidate) > _result_score(best_result):
            best_result = candidate
        if recognized and has_content:
            candidate.diagnostics["engine_attempts"] = attempts
            if len(attempts) > 1:
                candidate.diagnostics["warnings"].append(
                    f"Used {selected.name} after the preferred OCR engine did not recognize the table."
                )
            return candidate

    if best_result is not None:
        best_result.diagnostics["engine_attempts"] = attempts
        backend_failure = _backend_failure_message(attempts)
        if "libGL.so.1" in backend_failure:
            # A secondary engine may run but fail to recognize the table. Keep
            # its useful parsing diagnostics while still exposing the real
            # server failure that disabled the preferred OCR engine.
            errors = best_result.diagnostics.setdefault("errors", [])
            if backend_failure not in errors:
                errors.insert(0, backend_failure)
        return best_result

    result = _error_result(byte_count, _backend_failure_message(attempts))
    result.diagnostics["image"].update(image_info)
    result.diagnostics["engine_attempts"] = attempts
    return result


def _result_score(result: ScreenshotParseResult) -> int:
    return (
        (1000 if result.phase != "unknown" else 0)
        + len(result.rows) * 10
        + (1 if result.diagnostics.get("empty_marker_found") else 0)
    )


_HEADER_ALIASES: dict[str, set[str]] = {
    "name": {"NAME", "ATHLETE", "FENCER", "COMPETITOR"},
    "strip": {"STRIP", "STRIPNO", "STRIPNUM", "STRIPNUMBER", "PISTE"},
    "time": {"TIME", "START", "STARTTIME", "CALLTIME"},
    "pool": {"POOL", "POOLNO", "POOLNUM", "POOLNUMBER", "POULE"},
    "pod": {"POD", "SECTOR", "ZONE"},
    "club": {"CLUB"},
    "division": {"DIVISION"},
    "country": {"COUNTRY", "NATION"},
}
_OPERATIONAL_FIELDS = ("name", "strip", "time", "pool", "pod")
_SUPPORT_FIELDS = ("club", "division", "country")
_EMPTY_MARKER = "NOMATCHINGRECORDSFOUND"


def _header_field(text: str) -> str | None:
    normalized = _normalized_code(text)
    if not normalized:
        return None
    for field, aliases in _HEADER_ALIASES.items():
        if normalized in aliases:
            return field
        # OCR often retains the visual # in Strip#/Pool# or joins "No.".
        if field in {"strip", "pool"} and any(
            normalized.startswith(alias) and len(normalized) <= len(alias) + 3
            for alias in aliases
        ):
            return field
    return None


def _locate_headers(tokens: list[OCRToken]) -> dict[str, OCRToken]:
    name_candidates = [token for token in tokens if _header_field(token.text) == "name"]
    best: dict[str, OCRToken] = {}
    best_score = -1.0
    for name in name_candidates:
        tolerance = max(14.0, name.height * 1.6)
        candidate: dict[str, OCRToken] = {"name": name}
        for token in tokens:
            field = _header_field(token.text)
            if not field or field == "name" or abs(token.center_y - name.center_y) > tolerance:
                continue
            current = candidate.get(field)
            if current is None or abs(token.center_y - name.center_y) < abs(
                current.center_y - name.center_y
            ):
                candidate[field] = token

        operational = {"strip", "pool", "pod"} & candidate.keys()
        ordered = sorted(candidate.values(), key=lambda item: item.center_x)
        if not operational or not ordered or ordered[0] is not name:
            continue
        score = len(candidate) * 10 + sum(token.confidence for token in candidate.values())
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _column_bounds(
    headers: dict[str, OCRToken], image_width: int
) -> dict[str, tuple[int, int]]:
    ordered = sorted(headers.items(), key=lambda item: item[1].left)
    typical_height = median(token.height for token in headers.values())
    padding = max(2, round(typical_height * 0.25))
    bounds: dict[str, tuple[int, int]] = {}
    for index, (field, token) in enumerate(ordered):
        left = 0 if index == 0 else max(0, round(token.left - padding))
        if index + 1 < len(ordered):
            right = min(image_width, round(ordered[index + 1][1].left - padding / 2))
        else:
            right = image_width
        if right > left:
            bounds[field] = (left, right)
    return bounds


def _cluster_by_y(tokens: list[OCRToken], tolerance: float) -> list[list[OCRToken]]:
    clusters: list[list[OCRToken]] = []
    for token in sorted(tokens, key=lambda item: (item.center_y, item.left)):
        if not clusters:
            clusters.append([token])
            continue
        center = sum(item.center_y for item in clusters[-1]) / len(clusters[-1])
        if abs(token.center_y - center) <= tolerance:
            clusters[-1].append(token)
        else:
            clusters.append([token])
    for cluster in clusters:
        cluster.sort(key=lambda item: item.left)
    return clusters


def _joined_tokens(tokens: list[OCRToken]) -> tuple[str, float]:
    text = _clean_cell(" ".join(token.text for token in sorted(tokens, key=lambda x: x.left)))
    confidence = min((token.confidence for token in tokens), default=0.0)
    return text, confidence


def _parse_with_backend(
    image: Image.Image,
    image_info: dict[str, Any],
    backend: OCRBackend,
) -> ScreenshotParseResult:
    diagnostics = _base_diagnostics(int(image_info.get("bytes", 0)))
    diagnostics["image"].update(image_info)
    diagnostics["engine"] = backend.name

    tokens = backend.scan(image, layout="sparse")
    headers = _locate_headers(tokens)
    if not headers:
        diagnostics["errors"].append(
            "Could not find a table header containing Name and Strip, Pool, or Pod."
        )
        return ScreenshotParseResult("unknown", [], diagnostics)

    diagnostics["headers"] = {
        field: {
            "text": token.text,
            "confidence": round(token.confidence, 3),
            "x": round(token.center_x),
            "y": round(token.center_y),
        }
        for field, token in headers.items()
    }
    if "pool" in headers:
        phase: Phase = "pools"
        diagnostics["phase_source"] = "Pool table header"
    elif "strip" in headers or "pod" in headers:
        phase = "de"
        diagnostics["phase_source"] = "table header without Pool"
    else:  # protected by _locate_headers, retained for type safety
        phase = "unknown"
        diagnostics["errors"].append("The competition phase could not be detected.")
        return ScreenshotParseResult(phase, [], diagnostics)

    bounds = _column_bounds(headers, image.width)
    name_left, name_right = bounds["name"]
    header_bottom = max(token.bottom for token in headers.values())
    name_tokens = [
        token
        for token in tokens
        if token.center_y > header_bottom + 1
        and name_left <= token.center_x < name_right
        and (
            _letter_count(token.text) >= 2
            or _normalized_code(token.text) == _EMPTY_MARKER
        )
    ]
    header_height = max(6.0, median(token.height for token in headers.values()))
    name_clusters = _cluster_by_y(name_tokens, max(3.0, header_height * 0.55))

    row_anchors: list[dict[str, Any]] = []
    for cluster in name_clusters:
        text, confidence = _joined_tokens(cluster)
        normalized = _normalized_code(text)
        if normalized == _EMPTY_MARKER:
            diagnostics["empty_marker_found"] = True
            continue
        if _letter_count(text) < 2 or _header_field(text):
            continue
        row_anchors.append(
            {
                "text": text,
                "confidence": confidence,
                "center_y": sum(token.center_y for token in cluster) / len(cluster),
                "bottom": max(token.bottom for token in cluster),
            }
        )

    if not row_anchors:
        diagnostics["rows_detected"] = 0
        if not diagnostics["empty_marker_found"]:
            diagnostics["warnings"].append(
                "The table header was recognized, but no athlete rows were found."
            )
        return ScreenshotParseResult(phase, [], diagnostics)

    row_centers = [float(row["center_y"]) for row in row_anchors]
    positive_gaps = [
        current - previous
        for previous, current in pairwise(row_centers)
        if current - previous > 3
    ]
    row_gap = median(positive_gaps) if positive_gaps else header_height * 1.6
    row_tolerance = max(3.0, min(header_height * 0.8, row_gap * 0.42))
    table_top = max(0, round(header_bottom + 1))
    table_bottom = min(
        image.height,
        round(max(row["bottom"] for row in row_anchors) + max(row_gap / 2, 5)),
    )

    fields = [field for field in _OPERATIONAL_FIELDS if field in headers]
    values: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        values[field] = _values_for_rows(
            tokens,
            bounds[field],
            row_centers,
            row_tolerance,
            table_top,
            table_bottom,
        )
    # The clustered Name column is more reliable than repeating the generic
    # lookup, especially for word-level Tesseract output.
    values["name"] = [
        {"value": row["text"], "confidence": row["confidence"]}
        for row in row_anchors
    ]

    for field in fields:
        if not _column_needs_reread(field, values[field]):
            continue
        reread_tokens = _reread_column(
            backend,
            image,
            bounds[field],
            table_top,
            table_bottom,
            header_height,
            field,
        )
        reread_values = _values_for_rows(
            reread_tokens,
            bounds[field],
            row_centers,
            row_tolerance,
            table_top,
            table_bottom,
        )
        diagnostics["column_rereads"].append(field)
        _merge_reread_values(field, values[field], reread_values, diagnostics)

    rows: list[dict[str, Any]] = []
    confidence_rows: list[dict[str, float]] = []
    for row_index in range(len(row_anchors)):
        row: dict[str, Any] = {
            "name": "",
            "strip": "",
            "time": "",
            "pool": "",
            "pod": "",
            "phase": phase,
            "needs_review": False,
        }
        row_confidences: dict[str, float] = {}
        for field in _OPERATIONAL_FIELDS:
            cell = values.get(field, [])
            if row_index < len(cell):
                row[field] = cell[row_index]["value"]
                row_confidences[field] = round(float(cell[row_index]["confidence"]), 3)

        reviews = _cell_reviews(row_index, row, row_confidences, phase)
        for alternative in diagnostics["alternatives"]:
            if alternative["row"] != row_index:
                continue
            reviews.append(
                {
                    "row": row_index,
                    "field": alternative["field"],
                    "value": row.get(alternative["field"], ""),
                    "reason": "conflicting_ocr_reads",
                }
            )
        if reviews:
            row["needs_review"] = True
            diagnostics["review_cells"].extend(reviews)
        rows.append(row)
        confidence_rows.append(row_confidences)

    diagnostics["rows_detected"] = len(rows)
    diagnostics["cell_confidences"] = confidence_rows
    if diagnostics["review_cells"]:
        diagnostics["warnings"].append(
            f"Review {len(diagnostics['review_cells'])} highlighted OCR cell(s) before importing."
        )
    return ScreenshotParseResult(phase, rows, diagnostics)


def _values_for_rows(
    tokens: list[OCRToken],
    bounds: tuple[int, int],
    row_centers: list[float],
    row_tolerance: float,
    table_top: int,
    table_bottom: int,
) -> list[dict[str, Any]]:
    left, right = bounds
    column_tokens = [
        token
        for token in tokens
        if left <= token.center_x < right and table_top <= token.center_y <= table_bottom
    ]
    values: list[dict[str, Any]] = []
    for center in row_centers:
        nearby = [
            token
            for token in column_tokens
            if abs(token.center_y - center) <= row_tolerance
        ]
        text, confidence = _joined_tokens(nearby)
        values.append({"value": text, "confidence": confidence})
    return values


_FIELD_WHITELISTS = {
    "strip": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-#",
    "time": "0123456789:AMPamp. ",
    "pool": "0123456789#",
    "pod": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-#",
}


def _reread_column(
    backend: OCRBackend,
    image: Image.Image,
    bounds: tuple[int, int],
    table_top: int,
    table_bottom: int,
    text_height: float,
    field: str,
) -> list[OCRToken]:
    left, right = bounds
    if right <= left or table_bottom <= table_top:
        return []
    crop = image.crop((left, table_top, right, table_bottom))
    scale = max(1, min(4, round(48 / max(text_height, 1))))
    if scale > 1:
        crop = crop.resize(
            (crop.width * scale, crop.height * scale),
            Image.Resampling.LANCZOS,
        )
    local_tokens = backend.scan(
        crop,
        layout="block",
        whitelist=_FIELD_WHITELISTS.get(field),
    )
    return [
        OCRToken(
            token.text,
            token.confidence,
            left + token.left / scale,
            table_top + token.top / scale,
            left + token.right / scale,
            table_top + token.bottom / scale,
        )
        for token in local_tokens
    ]


def _column_needs_reread(field: str, cells: list[dict[str, Any]]) -> bool:
    if not cells:
        return True
    for cell in cells:
        value = str(cell.get("value", ""))
        confidence = float(cell.get("confidence", 0))
        if (
            not value
            or not _valid_field(field, value)
            or confidence < _minimum_confidence(field)
        ):
            return True
    return False


def _merge_reread_values(
    field: str,
    current: list[dict[str, Any]],
    reread: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> None:
    for row_index, cell in enumerate(current):
        if row_index >= len(reread):
            continue
        old_value = _clean_cell(cell.get("value", ""))
        new_value = _clean_cell(reread[row_index].get("value", ""))
        old_confidence = float(cell.get("confidence", 0))
        new_confidence = float(reread[row_index].get("confidence", 0))
        if not new_value:
            continue
        old_valid = _valid_field(field, old_value) if old_value else False
        new_valid = _valid_field(field, new_value)
        if not old_value:
            cell.update(value=new_value, confidence=new_confidence)
        elif not old_valid and new_valid:
            diagnostics["alternatives"].append(
                {
                    "row": row_index,
                    "field": field,
                    "first_read": old_value,
                    "reread": new_value,
                }
            )
            cell.update(value=new_value, confidence=new_confidence)
        elif _normalized_code(old_value) != _normalized_code(new_value):
            diagnostics["alternatives"].append(
                {
                    "row": row_index,
                    "field": field,
                    "first_read": old_value,
                    "reread": new_value,
                }
            )
            if old_confidence < _minimum_confidence(field) and new_confidence > old_confidence:
                cell.update(value=new_value, confidence=new_confidence)
        elif new_confidence > old_confidence:
            # Same characters with improved spacing/case are safe to prefer.
            cell.update(value=new_value, confidence=new_confidence)


def _cell_reviews(
    row_index: int,
    row: dict[str, Any],
    confidences: dict[str, float],
    phase: Phase,
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for field in ("name", "strip", "time", "pool", "pod"):
        value = _clean_cell(row.get(field, ""))
        if not value:
            continue
        if not _valid_field(field, value):
            reviews.append(
                {"row": row_index, "field": field, "value": value, "reason": "invalid_format"}
            )
            continue
        confidence = confidences.get(field, 0.0)
        if confidence < _minimum_confidence(field):
            reviews.append(
                {
                    "row": row_index,
                    "field": field,
                    "value": value,
                    "reason": "low_confidence",
                }
            )
        if field in {"strip", "pod"} and _ambiguous_location_code(value, phase):
            reviews.append(
                {
                    "row": row_index,
                    "field": field,
                    "value": value,
                    "reason": "possible_letter_digit_confusion",
                }
            )
    if not _clean_cell(row.get("name", "")):
        reviews.append(
            {"row": row_index, "field": "name", "value": "", "reason": "missing_name"}
        )
    if phase == "pools" and not _clean_cell(row.get("pool", "")):
        reviews.append(
            {"row": row_index, "field": "pool", "value": "", "reason": "missing_pool"}
        )
    return reviews


def _minimum_confidence(field: str) -> float:
    # One wrong character changes the physical location, so short operational
    # codes require stronger evidence than names and times.
    if field in {"strip", "pool", "pod"}:
        return 0.90
    return MIN_CELL_CONFIDENCE


_TIME_RE = re.compile(
    r"^(?:\d{1,2}(?::\d{2})\s*(?:A\.?M\.?|P\.?M\.?)?|TBD)$",
    re.IGNORECASE,
)
_POOL_RE = re.compile(
    r"^(?:(?:POOL|POULE)\s*#?\s*)?\d{1,3}$",
    re.IGNORECASE,
)
_LOCATION_RE = re.compile(
    r"^(?:(?:STRIP|PISTE|POD|SECTOR)\s*[:#-]?\s*)?(?:[A-Z]{1,4}\s*-?\s*\d{0,3}|#?\s*\d{1,3})$",
    re.IGNORECASE,
)


def _valid_field(field: str, value: str) -> bool:
    cleaned = _clean_cell(value)
    if not cleaned:
        return field in {"strip", "time", "pool", "pod"}
    if field == "name":
        return _letter_count(cleaned) >= 2
    if field == "time":
        return bool(_TIME_RE.fullmatch(cleaned))
    if field == "pool":
        return bool(_POOL_RE.fullmatch(cleaned))
    if field in {"strip", "pod"}:
        return bool(_LOCATION_RE.fullmatch(cleaned))
    return True


def _ambiguous_location_code(value: str, phase: Phase) -> bool:
    normalized = _normalized_code(value)
    # A single letter is a legitimate DE sector.  Two or more letters with no
    # digit are often OCR readings such as BI/Bl for B1; retain and flag them.
    return bool(re.fullmatch(r"[A-Z]{2,4}", normalized)) and phase in {"pools", "de"}


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()


def _normalized_code(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean_cell(value))
    ascii_text = text.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", ascii_text.upper())


def _letter_count(value: object) -> int:
    return sum(character.isalpha() for character in _clean_cell(value))


def _clamp_confidence(value: float) -> float:
    return min(1.0, max(0.0, value))


__all__ = [
    "MAX_IMAGE_PIXELS",
    "MAX_UPLOAD_BYTES",
    "OCRBackend",
    "OCRBackendError",
    "OCRToken",
    "ScreenshotParseResult",
    "parse_screenshot",
]
