"""Validated local or private Supabase image storage for competition assets.

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

Supabase requests follow the official Storage private-download/API key
contracts: https://supabase.com/docs/guides/storage/serving/downloads and
https://supabase.com/docs/guides/getting-started/api-keys. Remote credentials
remain in backend headers and downloads return validated bytes to the UI.
"""

from __future__ import annotations

import hashlib
import base64
import ipaddress
import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http.client import HTTPException
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


class _NoRedirects(HTTPRedirectHandler):
    """Never forward a backend credential to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SupabaseAssetStore:
    """Private Supabase Storage images accessed only by the Python backend.

    ``object_key`` is a durable ``supabase://bucket/key`` reference, never a
    public/signed URL. ``path`` is filename metadata for existing download UI;
    remote images are read through ``image.data``, not ``path.read_bytes()``.
    Buckets must exist and be private. Creation is an explicit installer step,
    never an automatic change to bucket permissions during an app request.
    Images have a 30-second, 32-MiB shared byte cache; bucket privacy checks
    expire after 60 seconds. Saves/deletes invalidate that logical asset.
    """

    def __init__(
        self,
        url: str,
        service_key: str,
        bucket: str = "compcoach-assets",
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        timeout: float = 15,
        image_cache_seconds: float = 30,
        max_cache_bytes: int = 32 * 1024 * 1024,
        bucket_cache_seconds: float = 60,
    ) -> None:
        self._url = _validated_supabase_url(url)
        self._key, self._legacy_key = _validated_service_key(service_key)
        self.bucket = _validated_id(bucket, "bucket")
        if max_bytes <= 0 or max_pixels <= 0:
            raise AssetStoreError("Image limits must be greater than zero")
        if not 0 < timeout <= 60:
            raise AssetStoreError("Storage timeout must be between zero and 60 seconds")
        if image_cache_seconds < 0 or max_cache_bytes < 0 or bucket_cache_seconds < 0:
            raise AssetStoreError("Storage cache limits cannot be negative")
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.timeout = timeout
        self._opener = build_opener(_NoRedirects())
        self.image_cache_seconds = image_cache_seconds
        self.max_cache_bytes = max_cache_bytes
        self.bucket_cache_seconds = bucket_cache_seconds
        self._lock = threading.RLock()
        self._bucket_verified_at = None
        self._images = OrderedDict()
        self._cache_bytes = 0
        self._generations = {}

    def save_logo(self, competition_id, payload, *, filename=None,
                  content_type=None, overwrite=True) -> StoredAsset:
        return self._save(competition_id, "logo", None, payload,
                          filename=filename, content_type=content_type,
                          overwrite=overwrite)

    def save_strip_map(self, competition_id, payload, *, map_id="main",
                       filename=None, content_type=None, overwrite=True) -> StoredAsset:
        return self._save(competition_id, "strip_map",
                          _validated_id(map_id, "map_id"), payload,
                          filename=filename, content_type=content_type,
                          overwrite=overwrite)

    def get_logo(self, competition_id) -> StoredAsset | None:
        return self._load(competition_id, "logo", None)

    def get_strip_map(self, competition_id, *, map_id="main") -> StoredAsset | None:
        return self._load(competition_id, "strip_map", _validated_id(map_id, "map_id"))

    def load_reference(self, reference: str, *, competition_id: str,
                       kind: str, map_id: str = "main") -> StoredAsset | None:
        """Load only a reference scoped to this bucket and logical asset."""
        current_map_id = _validated_id(map_id, "map_id") if kind == "strip_map" else None
        key = self._object_key(competition_id, kind, current_map_id)
        if reference != self._reference(key):
            raise AssetStoreError("Stored image reference does not match this competition")
        return self._load(competition_id, kind, current_map_id)

    def delete_logo(self, competition_id: str) -> None:
        self.delete_reference(self._reference(self._object_key(competition_id, "logo", None)))

    def delete_strip_map(self, competition_id: str, *, map_id="main") -> None:
        key = self._object_key(competition_id, "strip_map", _validated_id(map_id, "map_id"))
        self.delete_reference(self._reference(key))

    def delete_reference(self, reference: str) -> None:
        """Delete an owned reference; useful for failed migration cleanup."""
        key = self._parse_reference(reference)
        self._require_private_bucket()
        self._request("DELETE", f"object/{quote(self.bucket)}",
                      data=json.dumps({"prefixes": [key]}).encode(),
                      content_type="application/json")
        self._invalidate(key)

    def create_private_bucket(self) -> None:
        """Explicit installer action, safe to repeat for an existing private bucket."""
        metadata = self._bucket_info(allow_missing=True)
        if metadata is not None:
            self._assert_private(metadata)
            return
        self._request("POST", "bucket", data=json.dumps({
            "id": self.bucket, "name": self.bucket, "public": False,
            "file_size_limit": self.max_bytes,
            "allowed_mime_types": [mime for _extension, mime in _FORMAT_DETAILS.values()],
        }).encode(), content_type="application/json")
        self._require_private_bucket()

    def _save(self, competition_id, kind, map_id, payload, *, filename,
              content_type, overwrite) -> StoredAsset:
        key = self._object_key(competition_id, kind, map_id)
        image = validate_image(payload, filename=filename, content_type=content_type,
                               max_bytes=self.max_bytes, max_pixels=self.max_pixels)
        self._require_private_bucket()
        self._request("POST", f"object/{quote(self.bucket)}/{quote(key, safe='/')}",
                      data=image.data, content_type=image.content_type,
                      extra_headers={"x-upsert": "true" if overwrite else "false",
                                     "cache-control": "no-cache"})
        stored = self._stored_asset(competition_id, kind, map_id, key, image)
        generation = self._invalidate(key)
        self._cache_image(key, stored, generation)
        return stored

    def _load(self, competition_id, kind, map_id) -> StoredAsset | None:
        key = self._object_key(competition_id, kind, map_id)
        self._require_private_bucket()
        with self._lock:
            generation = self._generations.get(key, 0)
            cached = self._cached_image(key)
        if cached is not None:
            return cached
        data = self._request("GET", f"object/authenticated/{quote(self.bucket)}/{quote(key, safe='/')}",
                             allow_missing=True, response_limit=self.max_bytes)
        if data is None:
            return None
        image = validate_image(data, max_bytes=self.max_bytes, max_pixels=self.max_pixels)
        stored = self._stored_asset(competition_id, kind, map_id, key, image)
        self._cache_image(key, stored, generation)
        return stored

    def _cached_image(self, key):
        """Caller holds the cache lock; cache never exceeds a byte budget."""
        entry = self._images.get(key)
        if entry is None:
            return None
        stored, saved_at = entry
        if time.monotonic() - saved_at >= self.image_cache_seconds:
            self._images.pop(key)
            self._cache_bytes -= stored.image.byte_size
            return None
        self._images.move_to_end(key)
        return stored

    def _cache_image(self, key, stored, generation):
        with self._lock:
            if self._generations.get(key, 0) != generation:
                return  # A save/delete completed while this download was in flight.
            previous = self._images.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous[0].image.byte_size
            size = stored.image.byte_size
            if size > self.max_cache_bytes or self.image_cache_seconds == 0:
                return
            while self._images and self._cache_bytes + size > self.max_cache_bytes:
                _old_key, (old, _saved_at) = self._images.popitem(last=False)
                self._cache_bytes -= old.image.byte_size
            self._images[key] = (stored, time.monotonic())
            self._cache_bytes += size

    def _invalidate(self, key):
        with self._lock:
            previous = self._images.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous[0].image.byte_size
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
            return generation

    def _stored_asset(self, competition_id, kind, map_id, key, image) -> StoredAsset:
        return StoredAsset(competition_id=str(competition_id), kind=kind, map_id=map_id,
                           path=Path(key).with_suffix(f".{image.extension}"),
                           object_key=self._reference(key), image=image)

    def _object_key(self, competition_id, kind, map_id) -> str:
        competition_id = _validated_id(competition_id, "competition_id")
        if kind == "logo":
            return f"competitions/{competition_id}/logo"
        if kind == "strip_map" and map_id:
            return f"competitions/{competition_id}/strip-maps/{_validated_id(map_id, 'map_id')}"
        raise AssetStoreError("Unsupported competition image kind")

    def _reference(self, key: str) -> str:
        return f"supabase://{self.bucket}/{key}"

    def _parse_reference(self, reference: str) -> str:
        parsed = urlsplit(str(reference))
        parts = parsed.path.lstrip("/").split("/")
        valid = (parsed.scheme == "supabase" and parsed.netloc == self.bucket
                 and not parsed.query and not parsed.fragment
                 and len(parts) in (3, 4) and parts[0] == "competitions")
        if not valid:
            raise AssetStoreError("Invalid private competition image reference")
        kind = "logo" if len(parts) == 3 and parts[2] == "logo" else "strip_map"
        if kind == "strip_map" and (len(parts) != 4 or parts[2] != "strip-maps"):
            raise AssetStoreError("Invalid private competition image reference")
        key = self._object_key(parts[1], kind, parts[3] if kind == "strip_map" else None)
        if reference != self._reference(key):
            raise AssetStoreError("Invalid private competition image reference")
        return key

    def _bucket_info(self, *, allow_missing=False):
        data = self._request("GET", f"bucket/{quote(self.bucket)}", allow_missing=allow_missing)
        if data is None:
            return None
        try:
            metadata = json.loads(data)
        except (ValueError, TypeError):
            raise AssetStoreError("Storage returned invalid bucket metadata") from None
        if not isinstance(metadata, dict) or metadata.get("id") != self.bucket:
            raise AssetStoreError("Storage returned invalid bucket metadata")
        return metadata

    @staticmethod
    def _assert_private(metadata):
        if metadata.get("public") is not False:
            raise AssetStoreError("CompCoach images require a private Storage bucket")

    def _require_private_bucket(self):
        # Shared app resource: images and bucket metadata have short, bounded
        # caches. Live competition records use a separate uncached database.
        with self._lock:
            now = time.monotonic()
            if (self._bucket_verified_at is not None and
                    now - self._bucket_verified_at < self.bucket_cache_seconds):
                return
            self._assert_private(self._bucket_info())
            self._bucket_verified_at = time.monotonic()

    def _request(self, method, resource, *, data=None, content_type=None,
                 extra_headers=None, allow_missing=False, response_limit=128 * 1024):
        headers = {"apikey": self._key, "User-Agent": "CompCoach-server/1"}
        if self._legacy_key:
            headers["Authorization"] = f"Bearer {self._key}"
        if content_type:
            headers["Content-Type"] = content_type
        headers.update(extra_headers or {})
        request = Request(f"{self._url}/storage/v1/{resource}", data=data,
                          headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(response_limit + 1)
            if len(body) > response_limit:
                raise AssetStoreError("Storage response exceeds the allowed size")
            return body
        except HTTPError as exc:
            status = exc.code
            # Storage sometimes reports object/bucket not-found as HTTP 400.
            # Inspect only the bounded status fields; never expose raw JSON.
            if allow_missing and status in (400, 404):
                try:
                    details = json.loads(exc.read(8192))
                except (ValueError, OSError):
                    details = {}
                if status == 404 or (isinstance(details, dict) and
                        (str(details.get("statusCode")) == "404" or
                         details.get("error") in ("not_found", "Not Found"))):
                    return None
            raise AssetStoreError(f"Storage request failed (HTTP {status})") from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise AssetStoreError("Storage is temporarily unavailable; retry shortly") from None


def _validated_supabase_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        hostname = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or "." not in hostname
                or not re.fullmatch(r"[a-z0-9.-]+", hostname, re.IGNORECASE)
                or any(not part or part.startswith("-") or part.endswith("-")
                       for part in hostname.split("."))
                or hostname.lower().endswith((".localhost", ".local", ".internal"))):
            raise ValueError
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError
    except (ValueError, TypeError):
        raise AssetStoreError("Storage URL must be the HTTPS project endpoint") from None
    return f"https://{hostname.lower()}"


def _validated_service_key(value: str) -> tuple[str, bool]:
    key = str(value or "").strip()
    if re.fullmatch(r"sb_secret_[A-Za-z0-9_-]{17,}", key):
        return key, False
    # Decode only to reject anon/user JWTs, not to verify a token. The Storage
    # server performs authentication. Never include a supplied key in errors.
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", key):
            raise ValueError
        parts = key.split(".")
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        if len(parts) == 3 and claims.get("role") == "service_role":
            return key, True
    except (ValueError, IndexError, AttributeError):
        pass
    raise AssetStoreError("Storage requires a server secret or legacy service-role key")


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
