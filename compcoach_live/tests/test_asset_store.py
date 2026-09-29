from io import BytesIO

import pytest
from PIL import Image, features

from compcoach_live.asset_store import (
    ImageTooLargeError,
    InvalidAssetIdError,
    InvalidImageError,
    LocalAssetStore,
    UnsupportedImageTypeError,
    validate_image,
)


def image_bytes(image_format="PNG", *, size=(24, 16), color="navy"):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=image_format)
    return output.getvalue()


def test_logo_uses_deterministic_storage_key_and_preserves_original(tmp_path):
    payload = image_bytes("PNG")
    store = LocalAssetStore(tmp_path)

    stored = store.save_logo(
        "abc123",
        payload,
        filename="afm-logo.png",
        content_type="image/png",
    )

    assert stored.object_key == "competitions/abc123/logo.png"
    assert stored.path == tmp_path / stored.object_key
    assert stored.path.read_bytes() == payload
    assert stored.image.format == "PNG"
    assert stored.image.content_type == "image/png"
    assert (stored.image.width, stored.image.height) == (24, 16)
    assert len(stored.image.sha256) == 64


def test_strip_maps_are_named_by_competition_and_map_id(tmp_path):
    payload = image_bytes("JPEG")
    store = LocalAssetStore(tmp_path)

    first = store.save_strip_map(
        "october_nac",
        payload,
        map_id="hall-a",
        filename="map.jpeg",
        content_type="image/jpg",
    )
    second = store.save_strip_map(
        "october_nac",
        payload,
        map_id="hall-b",
    )

    assert first.object_key == "competitions/october_nac/strip-maps/hall-a.jpg"
    assert second.object_key == "competitions/october_nac/strip-maps/hall-b.jpg"
    assert first.path.read_bytes() == payload
    assert second.path.read_bytes() == payload


def test_saved_assets_can_be_loaded_after_store_is_recreated(tmp_path):
    payload = image_bytes("PNG")
    LocalAssetStore(tmp_path).save_logo("meet1", payload)
    LocalAssetStore(tmp_path).save_strip_map("meet1", payload, map_id="hall-a")

    reopened = LocalAssetStore(tmp_path)
    logo = reopened.get_logo("meet1")
    strip_map = reopened.get_strip_map("meet1", map_id="hall-a")

    assert logo is not None
    assert logo.image.data == payload
    assert logo.object_key == "competitions/meet1/logo.png"
    assert strip_map is not None
    assert strip_map.image.data == payload
    assert strip_map.object_key == "competitions/meet1/strip-maps/hall-a.png"
    assert reopened.get_strip_map("meet1", map_id="missing") is None


def test_replacing_asset_with_new_format_removes_stale_variant(tmp_path):
    store = LocalAssetStore(tmp_path)
    old_asset = store.save_logo("meet1", image_bytes("PNG"))
    new_payload = image_bytes("JPEG", color="orange")

    new_asset = store.save_logo("meet1", new_payload)

    assert old_asset.path.exists() is False
    assert new_asset.path.read_bytes() == new_payload
    assert new_asset.object_key == "competitions/meet1/logo.jpg"


@pytest.mark.skipif(not features.check("webp"), reason="Pillow lacks WebP support")
def test_webp_is_accepted_and_detected_from_bytes(tmp_path):
    payload = image_bytes("WEBP")

    stored = LocalAssetStore(tmp_path).save_strip_map("meet1", payload)

    assert stored.object_key == "competitions/meet1/strip-maps/main.webp"
    assert stored.image.content_type == "image/webp"


@pytest.mark.parametrize("bad_id", ["", "../other", "a/b", "has space", ".hidden"])
def test_identifiers_cannot_escape_asset_root(tmp_path, bad_id):
    store = LocalAssetStore(tmp_path)

    with pytest.raises(InvalidAssetIdError):
        store.save_logo(bad_id, image_bytes())


def test_map_id_is_path_safe(tmp_path):
    with pytest.raises(InvalidAssetIdError):
        LocalAssetStore(tmp_path).save_strip_map(
            "meet1",
            image_bytes(),
            map_id="../private",
        )


def test_invalid_or_spoofed_images_are_rejected():
    with pytest.raises(InvalidImageError):
        validate_image(b"not an image")

    png = image_bytes("PNG")
    with pytest.raises(InvalidImageError, match="Filename extension"):
        validate_image(png, filename="actually-jpeg.jpg")
    with pytest.raises(InvalidImageError, match="content type"):
        validate_image(png, content_type="image/webp")
    with pytest.raises(UnsupportedImageTypeError):
        validate_image(png, filename="logo.gif")


def test_byte_and_pixel_limits_are_enforced():
    png = image_bytes("PNG", size=(11, 10))

    with pytest.raises(ImageTooLargeError, match="bytes"):
        validate_image(png, max_bytes=len(png) - 1)
    with pytest.raises(ImageTooLargeError, match="pixels"):
        validate_image(png, max_pixels=100)


def test_constructor_rejects_non_positive_limits(tmp_path):
    with pytest.raises(ValueError, match="max_bytes"):
        LocalAssetStore(tmp_path, max_bytes=0)
    with pytest.raises(ValueError, match="max_pixels"):
        LocalAssetStore(tmp_path, max_pixels=0)
