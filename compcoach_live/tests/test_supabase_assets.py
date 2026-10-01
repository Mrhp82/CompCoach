"""Storage contract/security tests: all HTTP calls stay inside the fake server."""

import base64
import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest
from PIL import Image

from compcoach_live.asset_store import (
    AssetStoreError, ImageTooLargeError, InvalidAssetIdError,
    InvalidImageError, SupabaseAssetStore, _NoRedirects,
)
from compcoach_live.competition_setup import _load_asset, _save_uploaded_asset


SECRET = "sb_secret_" + "example_private_key_1234567890"


def image_bytes(fmt="PNG"):
    output = BytesIO()
    Image.new("RGB", (15, 10), "navy").save(output, format=fmt)
    return output.getvalue()


class Response(BytesIO):
    pass


class StorageServer:
    def __init__(self, *, exists=True, public=False):
        self.exists = exists
        self.public = public
        self.objects = {}
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        route = urlsplit(request.full_url).path.removeprefix("/storage/v1/")
        method = request.get_method()
        if route == "bucket/compcoach-assets":
            if not self.exists:
                self.fail(request, 404, {"message": "missing bucket"})
            return Response(json.dumps({"id": "compcoach-assets", "public": self.public}).encode())
        if route == "bucket" and method == "POST":
            values = json.loads(request.data)
            assert values["public"] is False
            assert values["allowed_mime_types"] == ["image/png", "image/jpeg", "image/webp"]
            self.exists = True
            return Response(b"{}")
        if route.startswith("object/authenticated/compcoach-assets/"):
            key = route.removeprefix("object/authenticated/compcoach-assets/")
            if key not in self.objects:
                self.fail(request, 400, {"statusCode": "404", "error": "not_found"})
            return Response(self.objects[key])
        if route.startswith("object/compcoach-assets/") and method == "POST":
            key = route.removeprefix("object/compcoach-assets/")
            if request.get_header("X-upsert") == "false" and key in self.objects:
                self.fail(request, 409, {"message": "Object already exists"})
            self.objects[key] = request.data
            return Response(b"{}")
        if route == "object/compcoach-assets" and method == "DELETE":
            for key in json.loads(request.data)["prefixes"]:
                self.objects.pop(key, None)
            return Response(b"[]")
        raise AssertionError(f"Unexpected test request: {method} {route}")

    @staticmethod
    def fail(request, status, body):
        raise HTTPError(request.full_url, status, "failure", {},
                        BytesIO(json.dumps(body).encode()))


def store_and_server(**kwargs):
    server = StorageServer(**kwargs)
    store = SupabaseAssetStore("https://example.supabase.co", SECRET, timeout=7)
    store._opener = server
    return store, server


def test_private_logo_map_lifecycle_and_format_replace():
    store, server = store_and_server()
    first = store.save_logo("october", image_bytes(), filename="logo.png")
    map_asset = store.save_strip_map("october", image_bytes(), map_id="hall-a")
    assert first.object_key == "supabase://compcoach-assets/competitions/october/logo"
    assert map_asset.object_key.endswith("/strip-maps/hall-a")
    assert first.path.name == "logo.png"
    assert SECRET not in first.object_key
    second = store.save_logo("october", image_bytes("JPEG"), filename="logo.jpg")
    assert first.object_key == second.object_key
    assert second.path.name == "logo.jpg"
    loaded = store.get_logo("october")
    assert loaded.image.format == "JPEG"
    assert loaded.image.data == second.image.data
    assert len(server.objects) == 2
    post = [r for r, _ in server.calls if r.method == "POST"][-1]
    assert post.get_header("Content-type") == "image/jpeg"
    assert post.get_header("Apikey") == SECRET
    assert post.get_header("Authorization") is None
    assert all(timeout == 7 for _r, timeout in server.calls)
    store.delete_logo("october")
    assert store.get_logo("october") is None
    assert store.get_strip_map("october", map_id="hall-a").image.data == map_asset.image.data
    store.delete_reference(map_asset.object_key)
    assert server.objects == {}


def test_migration_upload_does_not_overwrite_and_can_cleanup_owned_object():
    store, server = store_and_server()
    first = store.save_logo("october", image_bytes(), overwrite=False)
    with pytest.raises(AssetStoreError, match="HTTP 409"):
        store.save_logo("october", image_bytes("JPEG"), overwrite=False)
    assert store.get_logo("october").image.data == first.image.data
    store.delete_reference(first.object_key)
    assert server.objects == {}


def test_bucket_creation_is_explicit_and_never_changes_public_bucket():
    store, server = store_and_server(exists=False)
    with pytest.raises(AssetStoreError, match="HTTP 404"):
        store.save_logo("october", image_bytes())
    assert server.exists is False
    store.create_private_bucket()
    assert server.exists is True
    store.save_logo("october", image_bytes())
    before = len([r for r, _ in server.calls if r.method == "POST"])
    store.create_private_bucket()
    assert len([r for r, _ in server.calls if r.method == "POST"]) == before
    server.public = True
    for action in (store.create_private_bucket,
                   lambda: store.save_logo("october", image_bytes()),
                   lambda: store.get_logo("october"),
                   lambda: store.delete_logo("october")):
        store._bucket_verified_at = None
        with pytest.raises(AssetStoreError, match="private Storage bucket"):
            action()
    assert server.public is True


@pytest.mark.parametrize("url", [
    "http://example.supabase.co", "https://user:pass@example.supabase.co",
    "https://example.supabase.co/redirect", "https://example.supabase.co?key=secret",
    "https://example.supabase.co#frag", "https://example.supabase.co:1234",
    "https://127.0.0.1", "https://localhost", "https://storage.local",
    "https://bad..host", "https://example.supabase.co\n/evil",
])
def test_unsafe_endpoint_is_rejected_without_echoing_values(url):
    with pytest.raises(AssetStoreError) as error:
        SupabaseAssetStore(url, SECRET)
    assert url not in str(error.value)


def test_custom_https_project_hostname_supported_and_no_redirects():
    store = SupabaseAssetStore("https://storage.my-club.example/", SECRET)
    assert store._url == "https://storage.my-club.example"
    assert any(isinstance(handler, _NoRedirects) for handler in store._opener.handlers)
    handler = _NoRedirects()
    assert handler.redirect_request(None, None, 302, "", {}, "https://other.example") is None


def jwt_key(role):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


@pytest.mark.parametrize("key", ["", "anon-key", "sb_publishable_abcdef12345", jwt_key("anon"),
                                  jwt_key("authenticated"), SECRET + "\nX-Evil: yes"])
def test_only_backend_credentials_accepted_and_never_echoed(key):
    with pytest.raises(AssetStoreError) as error:
        SupabaseAssetStore("https://example.supabase.co", key)
    if key:
        assert key not in str(error.value)


def test_legacy_service_role_uses_bearer_header():
    key = jwt_key("service_role")
    store, server = store_and_server()
    store = SupabaseAssetStore("https://example.supabase.co", key)
    store._opener = server
    store.get_logo("october")
    assert all(r.get_header("Authorization") == "Bearer " + key for r, _ in server.calls)


def test_validation_blocks_network_and_scopes_references():
    store, server = store_and_server()
    for reference in ("supabase://other/competitions/october/logo",
                      "supabase://compcoach-assets/competitions/../logo",
                      "supabase://compcoach-assets/competitions/october/logo?x=1",
                      "https://example.supabase.co/file.png"):
        with pytest.raises(AssetStoreError):
            store.delete_reference(reference)
    with pytest.raises(InvalidAssetIdError):
        store.save_strip_map("october", image_bytes(), map_id="../../escape")
    with pytest.raises(InvalidImageError):
        store.save_logo("october", b"not an image")
    with pytest.raises(AssetStoreError):
        store.load_reference("supabase://compcoach-assets/competitions/other/logo",
                             competition_id="october", kind="logo")
    assert server.calls == []


def test_raw_network_failures_and_server_bodies_are_masked():
    store, server = store_and_server()
    class FailingServer:
        def open(self, request, *, timeout):
            StorageServer.fail(request, 403, {"message": SECRET, "url": request.full_url})
    store._opener = FailingServer()
    with pytest.raises(AssetStoreError) as error:
        store.get_logo("october")
    assert str(error.value) == "Storage request failed (HTTP 403)"
    assert SECRET not in str(error.value)
    class Offline:
        def open(self, request, *, timeout):
            raise URLError("https://private-url.example?secret=" + SECRET)
    store._opener = Offline()
    with pytest.raises(AssetStoreError, match="temporarily unavailable") as error:
        store.get_logo("october")
    assert SECRET not in str(error.value)


def test_missing_handling_does_not_hide_auth_error():
    store, server = store_and_server()
    assert store.get_logo("october") is None
    class AuthFailure(StorageServer):
        def open(self, request, *, timeout):
            if "object/authenticated" in request.full_url:
                self.fail(request, 400, {"error": "InvalidJWT", "statusCode": "400"})
            return super().open(request, timeout=timeout)
    store._opener = AuthFailure()
    with pytest.raises(AssetStoreError, match="HTTP 400"):
        store.get_logo("october")


def test_untrusted_download_is_validated_and_bounded():
    store, server = store_and_server()
    key = "competitions/october/logo"
    server.objects[key] = b"invalid image"
    with pytest.raises(InvalidImageError):
        store.get_logo("october")
    store.max_bytes = 20
    server.objects[key] = b"x" * 21
    with pytest.raises(AssetStoreError, match="allowed size"):
        store.get_logo("october")
    with pytest.raises(ImageTooLargeError):
        store.save_logo("october", image_bytes())


def test_competition_ui_persists_cloud_reference_and_reads_bytes():
    store, server = store_and_server()
    class DB:
        row = {"id": "october", "name": "October", "logo_path": "", "strip_map_path": ""}
        def get_competition(self, competition_id):
            assert competition_id == "october"
            return dict(self.row)
        def update_competition(self, competition_id, **values):
            self.row.update(values)
            return dict(self.row)
    db = DB()
    _save_uploaded_asset(db, store, "october", kind="logo", payload=image_bytes(),
                         filename="logo.png", content_type="image/png")
    assert db.row["logo_path"].startswith("supabase://")
    assert _load_asset(store, db.row, "logo").image.data == image_bytes()
    assert _load_asset(store, db.row, "strip_map") is None


def test_image_and_bucket_ttl_caches_refresh_and_invalidate(monkeypatch):
    current_time = [100.0]
    monkeypatch.setattr("compcoach_live.asset_store.time.monotonic", lambda: current_time[0])
    store, server = store_and_server()
    stored = store.save_logo("october", image_bytes())
    initial_calls = len(server.calls)
    assert store.get_logo("october").image.data == stored.image.data
    assert len(server.calls) == initial_calls
    current_time[0] += 31
    server.objects["competitions/october/logo"] = image_bytes("JPEG")
    assert store.get_logo("october").image.format == "JPEG"
    assert len(server.calls) == initial_calls + 1  # Bucket still cached.
    current_time[0] += 30
    assert store.get_logo("october").image.format == "JPEG"
    assert len(server.calls) == initial_calls + 3  # Both caches expired.
    store.save_logo("october", image_bytes())
    assert store.get_logo("october").image.format == "PNG"
    store.delete_logo("october")
    assert store.get_logo("october") is None
    assert store._cache_bytes == 0


def test_memory_budget_eviction_and_large_images_not_cached():
    store, server = store_and_server()
    size = len(image_bytes())
    store.max_cache_bytes = size * 2
    store.save_logo("first", image_bytes())
    store.save_logo("second", image_bytes())
    store.get_logo("first")  # First now most recently used.
    store.save_logo("third", image_bytes())
    assert list(store._images) == ["competitions/first/logo", "competitions/third/logo"]
    assert store._cache_bytes == size * 2
    store.max_cache_bytes = 1
    store.save_logo("large", image_bytes())
    assert "competitions/large/logo" not in store._images


def test_inflight_download_does_not_recache_deleted_image():
    store, server = store_and_server()
    stored = store.save_logo("october", image_bytes())
    old_generation = store._generations["competitions/october/logo"]
    store.delete_logo("october")
    store._cache_image("competitions/october/logo", stored, old_generation)
    assert store._images == {}
    assert store._cache_bytes == 0
