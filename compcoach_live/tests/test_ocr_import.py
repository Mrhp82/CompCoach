import io
import struct
import zlib

from PIL import Image

from compcoach_live import ocr_import
from compcoach_live.ocr_import import (
    MAX_IMAGE_PIXELS,
    MAX_UPLOAD_BYTES,
    OCRBackendError,
    OCRToken,
    _RapidOCRBackend,
    parse_screenshot,
)


def token(text, confidence, left, top, right, bottom):
    return OCRToken(text, confidence, left, top, right, bottom)


def screenshot_bytes(width=1000, height=400, image_format="PNG"):
    image = Image.new("RGB", (width, height), "white")
    payload = io.BytesIO()
    image.save(payload, format=image_format)
    return payload.getvalue()


def table_tokens(*, pool=True, strip_values=("C3", "B1"), pool_values=("7", "2")):
    values = [
        token("Name", 0.99, 10, 100, 65, 120),
        token("Strip #", 0.99, 300, 100, 365, 120),
        token("Time", 0.99, 450, 100, 500, 120),
        token("Club", 0.99, 760, 100, 810, 120),
        token("AGLIPAY Alyssa", 0.98, 10, 140, 170, 160),
        token(strip_values[0], 0.97, 310, 140, 340, 160),
        token("9:00 AM", 0.96, 460, 140, 530, 160),
        token("HSU Audrey", 0.98, 10, 180, 130, 200),
        token(strip_values[1], 0.97, 310, 180, 340, 200),
    ]
    if pool:
        values.insert(3, token("Pool #", 0.99, 600, 100, 660, 120))
        values.extend(
            [
                token(pool_values[0], 0.99, 610, 140, 625, 160),
                token(pool_values[1], 0.99, 610, 180, 625, 200),
            ]
        )
    return values


class StaticBackend:
    name = "fake"

    def __init__(self, tokens):
        self.tokens = tokens
        self.calls = []

    def scan(self, image, *, layout="sparse", whitelist=None):
        self.calls.append((image.size, layout, whitelist))
        return self.tokens if len(self.calls) == 1 else []


def test_pool_screenshot_postprocessing_returns_only_operational_fields():
    backend = StaticBackend(table_tokens())

    result = parse_screenshot(screenshot_bytes(), backend=backend)

    assert result.ok
    assert result.phase == "pools"
    assert result.rows == [
        {
            "name": "AGLIPAY Alyssa",
            "strip": "C3",
            "time": "9:00 AM",
            "pool": "7",
            "pod": "",
            "phase": "pools",
            "needs_review": False,
        },
        {
            "name": "HSU Audrey",
            "strip": "B1",
            "time": "",
            "pool": "2",
            "pod": "",
            "phase": "pools",
            "needs_review": False,
        },
    ]
    assert result.diagnostics["engine"] == "fake"
    assert result.diagnostics["image"]["decoded"] is True
    assert result.diagnostics["phase_source"] == "Pool table header"
    assert result.diagnostics["rows_detected"] == 2
    assert "club" not in result.rows[0]


def test_navigation_pools_label_does_not_change_de_phase():
    tokens = table_tokens(pool=False)
    tokens.append(token("POOLS", 0.99, 100, 20, 160, 40))
    backend = StaticBackend(tokens)

    result = parse_screenshot(screenshot_bytes(), backend=backend)

    assert result.ok
    assert result.phase == "de"
    assert [row["strip"] for row in result.rows] == ["C3", "B1"]
    assert all(row["pool"] == "" for row in result.rows)


def test_ambiguous_code_is_retained_and_flagged_instead_of_corrected():
    backend = StaticBackend(table_tokens(strip_values=("BI", "B2")))

    result = parse_screenshot(screenshot_bytes(), backend=backend)

    assert result.rows[0]["strip"] == "BI"
    assert result.rows[0]["needs_review"] is True
    assert {
        "row": 0,
        "field": "strip",
        "value": "BI",
        "reason": "possible_letter_digit_confusion",
    } in result.diagnostics["review_cells"]


class RereadBackend:
    name = "reread-fake"

    def __init__(self):
        self.calls = []
        self.initial = [
            token("Name", 0.99, 10, 100, 65, 120),
            token("Strip #", 0.99, 300, 100, 365, 120),
            token("Time", 0.99, 450, 100, 500, 120),
            token("Pool #", 0.99, 600, 100, 660, 120),
            token("Club", 0.99, 760, 100, 810, 120),
            token("AGLIPAY Alyssa", 0.98, 10, 140, 170, 160),
            token("C3", 0.98, 310, 140, 340, 160),
        ]

    def scan(self, image, *, layout="sparse", whitelist=None):
        self.calls.append((image.size, layout, whitelist))
        if len(self.calls) == 1:
            return self.initial
        if whitelist == "0123456789#":
            # With a 2x reread, this maps back to the first row at y=150.
            return [token("7", 0.99, 10, 50, 30, 70)]
        return []


def test_missing_tiny_pool_digit_is_filled_by_scaled_column_reread():
    backend = RereadBackend()

    result = parse_screenshot(screenshot_bytes(), backend=backend)

    assert result.rows[0]["pool"] == "7"
    assert result.rows[0]["needs_review"] is False
    assert "pool" in result.diagnostics["column_rereads"]
    assert any(layout == "block" for _size, layout, _whitelist in backend.calls)


def test_empty_de_marker_is_not_imported_as_an_athlete():
    backend = StaticBackend(
        [
            token("Name", 0.99, 10, 100, 65, 120),
            token("Strip #", 0.99, 300, 100, 365, 120),
            token("Club", 0.99, 760, 100, 810, 120),
            token("No matching records found", 0.99, 10, 140, 245, 160),
        ]
    )

    result = parse_screenshot(screenshot_bytes(), backend=backend)

    assert result.ok
    assert result.phase == "de"
    assert result.rows == []
    assert result.diagnostics["empty_marker_found"] is True


def test_oversized_upload_is_rejected_before_ocr():
    backend = StaticBackend([])

    result = parse_screenshot(b"x" * (MAX_UPLOAD_BYTES + 1), backend=backend)

    assert not result.ok
    assert "larger than" in result.diagnostics["errors"][0]
    assert backend.calls == []


def _png_header(width, height):
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )


def test_excessive_pixel_count_is_rejected_before_image_decompression():
    backend = StaticBackend([])
    width = 5001
    height = MAX_IMAGE_PIXELS // width + 1

    result = parse_screenshot(_png_header(width, height), backend=backend)

    assert not result.ok
    assert "pixel safety limit" in result.diagnostics["errors"][0]
    assert backend.calls == []


def test_invalid_or_unsupported_files_return_diagnostics_not_exceptions():
    invalid = parse_screenshot(b"not an image", backend=StaticBackend([]))
    gif = parse_screenshot(screenshot_bytes(image_format="GIF"), backend=StaticBackend([]))

    assert not invalid.ok
    assert "not a readable screenshot" in invalid.diagnostics["errors"][0]
    assert not invalid.diagnostics["image"].get("decoded", False)
    assert not gif.ok
    assert "Unsupported screenshot format" in gif.diagnostics["errors"][0]


class FailingBackend:
    name = "broken-primary"

    def scan(self, image, *, layout="sparse", whitelist=None):
        raise OCRBackendError("model could not start")


class MissingLibGLBackend:
    name = "rapidocr"

    def scan(self, image, *, layout="sparse", whitelist=None):
        raise OCRBackendError(
            "ImportError: libGL.so.1: cannot open shared object file"
        )


def test_backend_failure_falls_back_without_crashing(monkeypatch):
    fallback = StaticBackend(table_tokens())
    monkeypatch.setattr(
        ocr_import,
        "_available_backends",
        lambda: [FailingBackend(), fallback],
    )

    result = parse_screenshot(screenshot_bytes())

    assert result.ok
    assert result.diagnostics["engine"] == "fake"
    assert [attempt["status"] for attempt in result.diagnostics["engine_attempts"]] == [
        "failed",
        "recognized",
    ]
    assert result.diagnostics["warnings"]


def test_missing_libgl_backend_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(
        ocr_import,
        "_available_backends",
        lambda: [MissingLibGLBackend()],
    )

    result = parse_screenshot(screenshot_bytes())

    assert not result.ok
    assert "missing libGL.so.1" in result.diagnostics["errors"][0]
    assert "install the libgl1 system package" in result.diagnostics["errors"][0]
    assert result.diagnostics["engine_attempts"] == [
        {
            "engine": "rapidocr",
            "status": "failed",
            "detail": "ImportError: libGL.so.1: cannot open shared object file",
        }
    ]


def test_missing_libgl_is_not_hidden_by_unrecognized_fallback(monkeypatch):
    monkeypatch.setattr(
        ocr_import,
        "_available_backends",
        lambda: [MissingLibGLBackend(), StaticBackend([])],
    )

    result = parse_screenshot(screenshot_bytes())

    assert not result.ok
    assert "missing libGL.so.1" in result.diagnostics["errors"][0]
    assert [attempt["status"] for attempt in result.diagnostics["engine_attempts"]] == [
        "failed",
        "unrecognized",
    ]


def test_no_engine_available_is_a_recoverable_error(monkeypatch):
    monkeypatch.setattr(ocr_import, "_available_backends", list)

    result = parse_screenshot(screenshot_bytes())

    assert not result.ok
    assert result.phase == "unknown"
    assert "No local OCR engine" in result.diagnostics["errors"][0]


def test_current_rapidocr_output_object_is_converted_to_tokens(monkeypatch):
    class CurrentOutput:
        boxes = (
            [[10, 20], [50, 20], [50, 40], [10, 40]],
            [[60, 20], [90, 20], [90, 40], [60, 40]],
        )
        txts = ("Name", "C3")
        scores = (0.99, 0.95)

    class CurrentEngine:
        def __call__(self, _image, **_kwargs):
            return CurrentOutput()

    monkeypatch.setattr(ocr_import, "_rapid_engine", CurrentEngine)
    backend = ocr_import._RapidOCRBackend()

    tokens = backend.scan(Image.new("RGB", (100, 60), "white"))

    assert [(item.text, item.confidence) for item in tokens] == [
        ("Name", 0.99),
        ("C3", 0.95),
    ]
    assert (tokens[0].left, tokens[0].top, tokens[0].right, tokens[0].bottom) == (
        10,
        20,
        50,
        40,
    )


def test_pinned_rapidocr_native_stack_initializes_headlessly():
    """Catch missing native libraries in the actual deployment environment."""

    backend = _RapidOCRBackend()

    tokens = backend.scan(Image.new("RGB", (32, 32), "white"))

    assert tokens == []
