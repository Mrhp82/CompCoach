"""Validated local image storage for competition assets.

The local layout deliberately mirrors object-storage keys so the UI can later
swap this adapter for Supabase Storage without changing how assets are named::

    competitions/<competition_id>/logo.<ext>
    competitions/<competition_id>/strip-maps/<map_id>.<ext>

PNG, JPEG, and WebP uploads are accepted.  The image type is detected from its
bytes (not trusted from the filename), uploads are size/pixel limited, and
writes are atomic.  Originals are retained at full resolution; thumbnails can
be added as separate derivatives if they become necessary.

Mobile display integration
--------------------------
Logos may use a responsive thumbnail, but strip maps must never be cropped.
Render a map with ``object-fit: contain`` and ``height: auto`` and provide an
obvious tap target that opens the full-resolution image in a pan/zoom view (or
a new browser tab).  A responsive ``st.image`` preview alone is not enough:
pinch zoom inside an embedded Streamlit image is inconsistent on iOS, and a
large piste map becomes unreadable after it is fitted to phone width.  Keep a
download/open-original fallback, preserve the source dimensions, and make the
open/close controls at least 44 CSS pixels tall.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_PIXELS = 25_000_000

_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_FORMAT_DETAILS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}
_MIME_ALIASES = {
    "image/png": "image/png",
    "image/x-png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/webp": "image/webp",
}
_SUFFIX_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}


class AssetStoreError(ValueError):
    """Base class for asset validation and persistence failures."""


class InvalidAssetIdError(AssetStoreError):
    """Raised when an identifier could escape or destabilize the asset path."""


class ImageTooLargeError(AssetStoreError):
    """Raised when an upload exceeds the configured byte or pixel limit."""


class UnsupportedImageTypeError(AssetStoreError):
    """Raised when an upload is not PNG, JPEG, or WebP."""


class InvalidImageError(AssetStoreError):
    """Raised when image bytes are empty, corrupt, or otherwise unsafe."""


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """Image data and metadata established from the decoded upload."""

    data: bytes
    format: str
    extension: str
    content_type: str
    width: int
    height: int
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredAsset:
    """A stored image plus its future object-storage key."""

    competition_id: str
    kind: str
    map_id: str | None
    path: Path
    object_key: str
    image: ValidatedImage


class LocalAssetStore:
    """Store validated competition images below one explicit local root."""

    def __init__(
        self,
        root: str | Path = "data/assets",
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        if max_pixels <= 0:
            raise ValueError("max_pixels must be greater than zero")
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    def save_logo(
        self,
        competition_id: str,
        payload: bytes | bytearray | memoryview,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> StoredAsset:
        """Validate and atomically replace a competition's single logo."""

        return self._save(
            competition_id,
            "logo",
            None,
            payload,
            filename=filename,
            content_type=content_type,
        )

    def save_strip_map(
        self,
        competition_id: str,
        payload: bytes | bytearray | memoryview,
        *,
        map_id: str = "main",
        filename: str | None = None,
        content_type: str | None = None,
    ) -> StoredAsset:
        """Validate and atomically replace one named strip-map screenshot."""

        return self._save(
            competition_id,
            "strip_map",
            _validated_id(map_id, "map_id"),
            payload,
            filename=filename,
            content_type=content_type,
        )

    def get_logo(self, competition_id: str) -> StoredAsset | None:
        """Return the persisted logo, or ``None`` when none has been saved."""

        competition_id = _validated_id(competition_id, "competition_id")
        return self._load(competition_id, "logo", None)

    def get_strip_map(
        self,
        competition_id: str,
        *,
        map_id: str = "main",
    ) -> StoredAsset | None:
        """Return one persisted strip map, or ``None`` when it is absent."""

        competition_id = _validated_id(competition_id, "competition_id")
        map_id = _validated_id(map_id, "map_id")
        return self._load(competition_id, "strip_map", map_id)

    def _save(
        self,
        competition_id: str,
        kind: str,
        map_id: str | None,
        payload: bytes | bytearray | memoryview,
        *,
        filename: str | None,
        content_type: str | None,
    ) -> StoredAsset:
        competition_id = _validated_id(competition_id, "competition_id")
        image = validate_image(
            payload,
            filename=filename,
            content_type=content_type,
            max_bytes=self.max_bytes,
            max_pixels=self.max_pixels,
        )
        stem = self._asset_stem(competition_id, kind, map_id)
        target = stem.with_suffix(f".{image.extension}")
        _atomic_write(target, image.data)

        # Replacing a PNG with a JPEG (or similar) must not leave two current
        # objects for the same logical asset.
        for extension, _mime in _FORMAT_DETAILS.values():
            stale = stem.with_suffix(f".{extension}")
            if stale != target:
                stale.unlink(missing_ok=True)

        return StoredAsset(
            competition_id=competition_id,
            kind=kind,
            map_id=map_id,
            path=target,
            object_key=target.relative_to(self.root).as_posix(),
            image=image,
        )

    def _load(
        self,
        competition_id: str,
        kind: str,
        map_id: str | None,
    ) -> StoredAsset | None:
        stem = self._asset_stem(competition_id, kind, map_id)
        candidates = [
            stem.with_suffix(f".{extension}")
            for extension, _mime in _FORMAT_DETAILS.values()
            if stem.with_suffix(f".{extension}").is_file()
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise AssetStoreError(
                f"Multiple files exist for logical asset {stem.relative_to(self.root)}"
            )
        path = candidates[0]
        image = validate_image(
            path.read_bytes(),
            filename=path.name,
            max_bytes=self.max_bytes,
            max_pixels=self.max_pixels,
        )
        return StoredAsset(
            competition_id=competition_id,
            kind=kind,
            map_id=map_id,
            path=path,
            object_key=path.relative_to(self.root).as_posix(),
            image=image,
        )

    def _asset_stem(
        self,
        competition_id: str,
        kind: str,
        map_id: str | None,
    ) -> Path:
        base = self.root / "competitions" / competition_id
        if kind == "logo":
            return base / "logo"
        if kind == "strip_map" and map_id:
            return base / "strip-maps" / map_id
        raise AssetStoreError(f"Unsupported asset kind: {kind!r}")


def validate_image(
    payload: bytes | bytearray | memoryview,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> ValidatedImage:
    """Validate one upload using decoded bytes as the source of truth."""

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise InvalidImageError("Image payload must be bytes-like")
    data = bytes(payload)
    if not data:
        raise InvalidImageError("Image payload is empty")
    if len(data) > max_bytes:
        raise ImageTooLargeError(
            f"Image is {len(data)} bytes; maximum is {max_bytes} bytes"
        )

    try:
        with Image.open(BytesIO(data)) as opened:
            image_format = str(opened.format or "").upper()
            if image_format not in _FORMAT_DETAILS:
                raise UnsupportedImageTypeError(
                    "Only PNG, JPEG, and WebP images are supported"
                )
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise InvalidImageError("Image dimensions must be positive")
            if width * height > max_pixels:
                raise ImageTooLargeError(
                    f"Image has {width * height} pixels; maximum is {max_pixels}"
                )
            if getattr(opened, "n_frames", 1) != 1:
                raise InvalidImageError("Animated images are not supported")
            opened.verify()

        # Pillow's verify() checks the container; a second pass forces pixel
        # decoding so truncated payloads are rejected before persistence.
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
    except (UnsupportedImageTypeError, ImageTooLargeError, InvalidImageError):
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImageError("The upload is not a valid complete image") from exc

    extension, detected_mime = _FORMAT_DETAILS[image_format]
    _check_filename(filename, image_format)
    _check_content_type(content_type, detected_mime)
    return ValidatedImage(
        data=data,
        format=image_format,
        extension=extension,
        content_type=detected_mime,
        width=width,
        height=height,
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _validated_id(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _SAFE_ID.fullmatch(clean):
        raise InvalidAssetIdError(
            f"{label} must use 1-128 letters, numbers, underscores, or hyphens"
        )
    return clean


def _check_filename(filename: str | None, image_format: str) -> None:
    if filename is None:
        return
    suffix = Path(filename).suffix.lower()
    declared_format = _SUFFIX_FORMATS.get(suffix)
    if declared_format is None:
        raise UnsupportedImageTypeError(
            "Filename must end in .png, .jpg, .jpeg, or .webp"
        )
    if declared_format != image_format:
        raise InvalidImageError(
            f"Filename extension does not match detected {image_format} image"
        )


def _check_content_type(content_type: str | None, detected_mime: str) -> None:
    if content_type is None:
        return
    supplied = content_type.partition(";")[0].strip().lower()
    declared_mime = _MIME_ALIASES.get(supplied)
    if declared_mime is None:
        raise UnsupportedImageTypeError(f"Unsupported content type: {content_type!r}")
    if declared_mime != detected_mime:
        raise InvalidImageError(
            f"Declared content type does not match detected {detected_mime} image"
        )


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
