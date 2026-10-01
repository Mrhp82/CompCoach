"""Competition profile and branding UI for CompCoach Live.

This module intentionally has no dependency on :mod:`compcoach_live.app` so it
can be imported by the Streamlit entry point without creating an import cycle.
The two public render functions only depend on the storage API and the local
asset adapter.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import streamlit as st

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
        strip_map = _load_asset(store, competition, "strip_map")
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
            st.image(logo.image.data, width=72)
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

    if strip_map is not None:
        with st.expander("🗺️ View strip map"):
            st.image(strip_map.image.data, width="stretch")
            st.download_button(
                "Download full-resolution map",
                data=strip_map.image.data,
                file_name=strip_map.path.name,
                mime=strip_map.image.content_type,
                key=f"header_strip_map_download_{competition_id}",
                width="stretch",
                on_click="ignore",
            )


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
    with st.expander(title, expanded=expanded):
        try:
            current = _load_asset(store, competition, kind)
        except (AssetStoreError, ValueError) as exc:
            current = None
            st.warning(f"The current {title.lower()} could not be opened: {exc}")

        if current is not None:
            if kind == "logo":
                st.image(current.image.data, width=160)
            else:
                st.image(current.image.data, width="stretch")
            st.download_button(
                f"Download original {title.lower()}",
                data=current.image.data,
                file_name=current.path.name,
                mime=current.image.content_type,
                key=f"download_{kind}_{competition_id}",
                width="stretch",
                on_click="ignore",
            )
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
