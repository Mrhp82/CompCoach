"""Competition profile and branding UI for CompCoach Live.

This module intentionally has no dependency on :mod:`compcoach_live.app` so it
can be imported by the Streamlit entry point without creating an import cycle.
The two public render functions only depend on the storage API and the local
asset adapter.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import streamlit as st
from PIL import Image, ImageOps

try:
    from compcoach_live.asset_store import (
        AssetStoreError,
        LocalAssetStore,
        SupabaseAssetStore,
        StoredAsset,
        validate_image,
    )
    from compcoach_live.storage import CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from asset_store import (
        AssetStoreError,
        LocalAssetStore,
        SupabaseAssetStore,
        StoredAsset,
        validate_image,
    )
    from storage import CompCoachError


DEFAULT_ASSET_ROOT = Path(__file__).resolve().parent / "data" / "assets"
COMMON_TIMEZONES = (
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Phoenix",
    "Pacific/Honolulu",
    "Europe/Rome",
)

AssetKind = Literal["logo", "strip_map"]

# Deliver the validated original in the existing app session. Preview, open and
# save all use the same bytes, without another HTTP request for a media file.
_VIEWER_HTML = """
<div class="viewer">
  <p role="status">Opening image…</p>
  <img hidden>
  <div class="actions" hidden>
    <a class="open" target="_blank" rel="noopener">Open original</a>
    <a class="save">Download original</a>
  </div>
</div>
"""
_VIEWER_CSS = """
.viewer {max-width:100%; font-family:inherit; color:inherit}
.viewer [hidden] {display:none!important}
.viewer p {margin:0 0 .4rem}
.viewer img {display:block; max-width:100%; height:auto}
.viewer .map {width:100%}
.viewer .logo {width:auto; max-width:min(160px,100%); max-height:160px; object-fit:contain}
.actions {display:flex; gap:.5rem; margin-top:.5rem; flex-wrap:wrap}
.actions a {display:inline-flex; align-items:center; justify-content:center;
  min-height:44px; padding:0 .75rem; box-sizing:border-box; border:1px solid currentColor;
  border-radius:.5rem; color:inherit; text-decoration:underline; cursor:pointer}
"""
_VIEWER_JS = """
export default function ({data, parentElement}) {
  const root = parentElement.querySelector(".viewer");
  const img = root.querySelector("img"), note = root.querySelector("p");
  const actions = root.querySelector(".actions");
  const open = root.querySelector(".open"), save = root.querySelector(".save");
  let url, active = true, inline = false;
  const hide = () => {
    actions.hidden = true; img.hidden = true; note.hidden = false;
    open.removeAttribute("href"); save.removeAttribute("href");
  };
  hide();
  note.textContent = "Opening image…";
  const fail = () => {
    hide();
    note.textContent = "This image could not be opened. Close and reopen this section to retry.";
  };
  try {
    const ext = {"image/jpeg":"jpg", "image/png":"png", "image/webp":"webp"}[data.mime];
    const logo = data.kind === "logo";
    if (!ext || (!logo && data.kind !== "strip_map")) throw Error();
    const raw = atob(data.body);
    if (!raw.length || raw.length !== data.size) throw Error();
    const magicOK = ext === "jpg" ? raw.startsWith("\\xFF\\xD8\\xFF")
      : ext === "png" ? raw.startsWith("\\x89PNG\\r\\n\\x1A\\n")
      : raw.startsWith("RIFF") && raw.slice(8,12) === "WEBP";
    if (!magicOK) throw Error();
    const bytes = new Uint8Array(raw.length);
    for (let i=0; i<raw.length; i++) bytes[i] = raw.charCodeAt(i);
    url = URL.createObjectURL(new Blob([bytes], {type:data.mime}));
    img.alt = logo ? "Competition logo" : "Strip map";
    img.className = logo ? "logo" : "map";
    save.download = (logo ? "competition-logo." : "strip-map.") + ext;
    save.textContent = logo ? "Download original competition logo" : "Download original strip map";
    img.onload = () => {
      if (!active) return;
      img.hidden = false; note.hidden = true;
      open.href = url; save.href = url; actions.hidden = false;
    };
    img.onerror = () => {
      if (!active) return;
      if (inline) { fail(); return; }
      inline = true;
      img.src = "data:" + data.mime + ";base64," + data.body;
    };
    img.src = url;
  } catch { fail(); }
  return () => {
    active = false;
    img.onload = img.onerror = null;
    if (url) URL.revokeObjectURL(url);
  };
}
"""


def _show_original(asset: StoredAsset, *, key: str) -> None:
    image = asset.image
    viewer = st.components.v2.component(
        "compcoach_competition_image", html=_VIEWER_HTML, css=_VIEWER_CSS, js=_VIEWER_JS
    )
    viewer(data={
        "kind": asset.kind, "mime": image.content_type, "size": image.byte_size,
        "body": b64encode(image.data).decode("ascii"),
    }, key=key)


@st.cache_data(max_entries=32, ttl=300, show_spinner=False)
def _logo_badge(digest: str, _data: bytes) -> str:
    # Hash the validated original by its SHA-256, not by a competition's name.
    with Image.open(BytesIO(_data)) as src:
        src.thumbnail((144, 144), Image.Resampling.LANCZOS)
        thumb = ImageOps.exif_transpose(src).convert("RGBA")
        out = BytesIO()
        thumb.save(out, format="PNG")
    return "data:image/png;base64," + b64encode(out.getvalue()).decode("ascii")


def render_competition_profile(
    db: Any,
    meet: dict[str, Any],
    actor: str,
    *,
    asset_root: str | Path | None = None,
    asset_store: LocalAssetStore | SupabaseAssetStore | None = None,
) -> None:
    """Render the admin editor for parent competition details and images.

    Metadata and the two assets are deliberately saved independently.  Every
    write reloads the current row first so a metadata-only save keeps both
    image paths, while an image-only save keeps all competition details.
    """

    competition_id = _competition_id(meet)
    if not competition_id:
        st.error("This competition day is not linked to a competition profile.")
        return

    try:
        competition = _require_competition(db, competition_id)
        store = asset_store or LocalAssetStore(asset_root or DEFAULT_ASSET_ROOT)
    except (CompCoachError, AssetStoreError, ValueError) as exc:
        st.error(str(exc))
        return

    st.subheader("Competition profile")
    if actor:
        st.caption(f"Editing as {actor}")

    form_key = f"competition_profile_{competition_id}"
    with st.form(form_key):
        name = st.text_input(
            "Competition name",
            value=str(competition.get("name") or "AFM Competition"),
        )
        location = st.text_input(
            "Location",
            value=str(competition.get("location") or ""),
            placeholder="Example: Salt Palace, Salt Lake City",
        )
        date_columns = st.columns(2)
        with date_columns[0]:
            start_date = st.date_input(
                "Start date",
                value=_date_value(competition.get("start_date")),
                format="MM/DD/YYYY",
            )
        with date_columns[1]:
            end_date = st.date_input(
                "End date",
                value=_date_value(competition.get("end_date")),
                format="MM/DD/YYYY",
            )
        timezone_name = str(
            competition.get("timezone") or meet.get("timezone") or COMMON_TIMEZONES[0]
        )
        timezone_options = list(dict.fromkeys([timezone_name, *COMMON_TIMEZONES]))
        selected_timezone = st.selectbox(
            "Competition timezone",
            timezone_options,
            index=0,
        )
        save_profile = st.form_submit_button(
            "Save competition profile",
            type="primary",
            width="stretch",
        )

    if save_profile:
        try:
            _save_profile_metadata(
                db,
                competition_id,
                name=name,
                location=location,
                start_date=_iso_date(start_date),
                end_date=_iso_date(end_date),
                timezone_name=selected_timezone,
            )
        except (CompCoachError, AssetStoreError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("Competition profile saved.")
            st.rerun()

    st.subheader("Competition images")
    st.caption("PNG, JPEG or WebP. The original full-resolution image is kept.")

    _render_asset_editor(
        db,
        store,
        competition_id,
        competition,
        kind="logo",
        title="Competition logo",
        upload_label="Choose a logo",
        save_label="Save logo",
    )
    _render_asset_editor(
        db,
        store,
        competition_id,
        competition,
        kind="strip_map",
        title="Strip map",
        upload_label="Choose a strip-map screenshot",
        save_label="Save strip map",
    )


def render_competition_branding(
    db: Any,
    meet: dict[str, Any],
    *,
    asset_root: str | Path | None = None,
    asset_store: LocalAssetStore | SupabaseAssetStore | None = None,
) -> None:
    """Render compact competition branding plus an optional strip-map viewer."""

    competition_id = _competition_id(meet)
    if not competition_id:
        return

    try:
        competition = _require_competition(db, competition_id)
        store = asset_store or LocalAssetStore(asset_root or DEFAULT_ASSET_ROOT)
        logo = _load_asset(store, competition, "logo")
    except (CompCoachError, AssetStoreError, ValueError) as exc:
        st.warning(f"Competition details are temporarily unavailable: {exc}")
        return

    location = str(competition.get("location") or "").strip()
    dates = _date_range_label(
        str(competition.get("start_date") or ""),
        str(competition.get("end_date") or ""),
    )
    name = str(competition.get("name") or "").strip()
    detail_line = " · ".join(value for value in (location, dates) if value)

    if logo is not None:
        logo_column, details_column = st.columns([1, 4], vertical_alignment="center")
        with logo_column:
            # A small inline image keeps the dashboard light on a phone.
            badge = _logo_badge(logo.image.sha256, logo.image.data)
            st.html(f'<img src="{badge}" alt="Competition logo" width="72" '
                    'style="max-width:100%;height:auto;display:block">')
        with details_column:
            if name and name != str(meet.get("name") or "").strip():
                st.markdown(f"**{name}**")
            if detail_line:
                st.caption(detail_line)
    else:
        if name and name != str(meet.get("name") or "").strip():
            st.markdown(f"**{name}**")
        if detail_line:
            st.caption(detail_line)

    has_map = bool(competition.get("strip_map_path"))
    # Older local installations may have an image without a stored reference.
    legacy_map = None
    if not has_map and isinstance(store, LocalAssetStore):
        try:
            legacy_map = _load_asset(store, competition, "strip_map")
        except (AssetStoreError, ValueError) as exc:
            st.warning(f"The strip map could not be opened: {exc}")
    if has_map or legacy_map is not None:
        panel = st.expander("🗺️ View strip map", key=f"view_map_{competition_id}", on_change="rerun")
        if panel.open:
            with panel:
                try:
                    strip_map = legacy_map or _load_asset(store, competition, "strip_map")
                except (AssetStoreError, ValueError) as exc:
                    st.warning(f"The strip map could not be opened: {exc}")
                else:
                    if strip_map is not None:
                        _show_original(strip_map, key=f"map_original_{competition_id}")
                    else:
                        st.warning("The saved strip map could not be found.")


def _render_asset_editor(
    db: Any,
    store: LocalAssetStore | SupabaseAssetStore,
    competition_id: str,
    competition: dict[str, Any],
    *,
    kind: AssetKind,
    title: str,
    upload_label: str,
    save_label: str,
) -> None:
    expanded = kind == "logo" and not competition.get("logo_path")
    panel = st.expander(title, expanded=expanded, key=f"edit_image_{kind}_{competition_id}",
                        on_change="rerun")
    if not panel.open:
        return
    with panel:
        try:
            current = _load_asset(store, competition, kind)
        except (AssetStoreError, ValueError) as exc:
            st.warning(f"The current {title.lower()} could not be opened: {exc}")
        else:
            if current is not None:
                _show_original(current, key=f"edit_original_{kind}_{competition_id}")
            else:
                st.caption(f"No {title.lower()} saved yet.")

        with st.form(f"upload_{kind}_{competition_id}"):
            uploaded = st.file_uploader(
                upload_label,
                type=["png", "jpg", "jpeg", "webp"],
                key=f"file_{kind}_{competition_id}",
            )
            save_asset = st.form_submit_button(save_label, width="stretch")

        if not save_asset:
            return
        if uploaded is None:
            st.warning("Choose an image first.")
            return

        try:
            _save_uploaded_asset(
                db,
                store,
                competition_id,
                kind=kind,
                payload=uploaded.getvalue(),
                filename=uploaded.name,
                content_type=uploaded.type,
            )
        except (CompCoachError, AssetStoreError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"{title} saved.")
            st.rerun()


def _save_profile_metadata(
    db: Any,
    competition_id: str,
    *,
    name: str,
    location: str,
    start_date: str,
    end_date: str,
    timezone_name: str,
) -> dict[str, Any]:
    """Update metadata without clearing independently managed asset paths."""

    current = _require_competition(db, competition_id)
    return db.update_competition(
        competition_id,
        name=name,
        location=location,
        start_date=start_date,
        end_date=end_date,
        timezone_name=timezone_name,
        logo_path=str(current.get("logo_path") or ""),
        strip_map_path=str(current.get("strip_map_path") or ""),
    )


def _save_uploaded_asset(
    db: Any,
    store: LocalAssetStore | SupabaseAssetStore,
    competition_id: str,
    *,
    kind: AssetKind,
    payload: bytes,
    filename: str | None,
    content_type: str | None,
) -> dict[str, Any]:
    """Persist one image and update only its path on the current profile row."""

    if kind == "logo":
        stored = store.save_logo(
            competition_id,
            payload,
            filename=filename,
            content_type=content_type,
        )
    elif kind == "strip_map":
        stored = store.save_strip_map(
            competition_id,
            payload,
            map_id="main",
            filename=filename,
            content_type=content_type,
        )
    else:  # pragma: no cover - guarded by the AssetKind call sites
        raise ValueError(f"Unsupported asset kind: {kind}")

    current = _require_competition(db, competition_id)
    logo_path = str(current.get("logo_path") or "")
    strip_map_path = str(current.get("strip_map_path") or "")
    if kind == "logo":
        logo_path = stored.object_key
    else:
        strip_map_path = stored.object_key

    return db.update_competition(
        competition_id,
        name=str(current.get("name") or "AFM Competition"),
        location=str(current.get("location") or ""),
        start_date=str(current.get("start_date") or ""),
        end_date=str(current.get("end_date") or ""),
        timezone_name=str(current.get("timezone") or "America/Los_Angeles"),
        logo_path=logo_path,
        strip_map_path=strip_map_path,
    )


def _load_asset(
    store: LocalAssetStore | SupabaseAssetStore,
    competition: dict[str, Any],
    kind: AssetKind,
) -> StoredAsset | None:
    """Load a deterministic asset, with a safe legacy-path fallback."""

    competition_id = str(competition.get("id") or "").strip()
    if not competition_id:
        return None
    if isinstance(store, SupabaseAssetStore):
        reference = str(competition.get(f"{kind}_path") or "").strip()
        if not reference:
            return None
        return store.load_reference(reference, competition_id=competition_id,
                                    kind=kind, map_id="main")
    deterministic = (
        store.get_logo(competition_id)
        if kind == "logo"
        else store.get_strip_map(competition_id, map_id="main")
    )
    if deterministic is not None:
        return deterministic

    path_value = str(competition.get(f"{kind}_path") or "").strip()
    if not path_value:
        return None
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = store.root / candidate
    root = store.root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AssetStoreError("Stored asset path is outside the asset directory") from exc
    if not candidate.is_file():
        return None

    image = validate_image(candidate.read_bytes(), filename=candidate.name)
    return StoredAsset(
        competition_id=competition_id,
        kind=kind,
        map_id="main" if kind == "strip_map" else None,
        path=candidate,
        object_key=candidate.relative_to(root).as_posix(),
        image=image,
    )


def _require_competition(db: Any, competition_id: str) -> dict[str, Any]:
    competition = db.get_competition(competition_id)
    if competition is None:
        raise CompCoachError("Competition profile not found.")
    return competition


def _competition_id(meet: dict[str, Any]) -> str:
    return str(meet.get("competition_id") or "").strip()


def _date_value(value: Any) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return date.fromisoformat(clean)
    except ValueError:
        return None


def _iso_date(value: date | tuple[date, ...] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return value[0].isoformat() if value else ""
    return value.isoformat()


def _date_range_label(start_value: str, end_value: str) -> str:
    start = _date_value(start_value)
    end = _date_value(end_value)
    if start is None and end is None:
        return ""
    if start is None:
        return _friendly_date(end)
    if end is None or end == start:
        return _friendly_date(start)
    return f"{_friendly_date(start)} – {_friendly_date(end)}"


def _friendly_date(value: date | None) -> str:
    if value is None:
        return ""
    # Avoid platform-dependent %-d while still keeping the mobile label short.
    return value.strftime("%b %d, %Y").replace(" 0", " ")
