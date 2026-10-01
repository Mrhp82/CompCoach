"""Choose persistence explicitly; a broken cloud setup never becomes local data.

Settings are supplied by the entry point so this module also works in the
migration CLI and tests without importing Streamlit or reading secret files.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

try:
    from compcoach_live.storage import CompCoachDB, CompCoachError
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from storage import CompCoachDB, CompCoachError


class ConfigurationError(CompCoachError):
    """An incomplete deployment configuration, with no credential values."""


SETTING_NAMES = (
    "COMPCOACH_DATABASE_URL",
    "COMPCOACH_DB_PATH",
    "COMPCOACH_REQUIRE_CLOUD",
    "COMPCOACH_ADMIN_PIN",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "COMPCOACH_STORAGE_BUCKET",
)


def _value(settings: Mapping[str, object], name: str) -> str:
    return str(settings.get(name) or "").strip()


def require_cloud(settings: Mapping[str, object]) -> bool:
    value = _value(settings, "COMPCOACH_REQUIRE_CLOUD").casefold()
    if value not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
        raise ConfigurationError("COMPCOACH_REQUIRE_CLOUD must be true or false.")
    return value in {"1", "true", "yes", "on"}


def validate_settings(settings: Mapping[str, object]) -> None:
    if not require_cloud(settings):
        return
    missing = [
        name
        for name in ("COMPCOACH_DATABASE_URL", "SUPABASE_URL", "COMPCOACH_ADMIN_PIN")
        if not _value(settings, name)
    ]
    if not (_value(settings, "SUPABASE_SECRET_KEY") or _value(settings, "SUPABASE_SERVICE_ROLE_KEY")):
        missing.append("SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY)")
    if missing:
        raise ConfigurationError(
            "Cloud setup is incomplete. Set " + ", ".join(missing)
            + " in Secrets, then restart the app. No local database was opened."
        )


def create_database(settings: Mapping[str, object]) -> CompCoachDB:
    validate_settings(settings)
    database_url = _value(settings, "COMPCOACH_DATABASE_URL")
    if database_url:
        try:
            from compcoach_live.postgres_storage import PostgresCompCoachDB
        except ModuleNotFoundError:  # pragma: no cover - direct script fallback
            from postgres_storage import PostgresCompCoachDB
        return PostgresCompCoachDB(database_url)
    default_path = Path(__file__).resolve().parent / "data" / "compcoach.db"
    return CompCoachDB(_value(settings, "COMPCOACH_DB_PATH") or default_path)


def create_asset_store(settings: Mapping[str, object]):
    validate_settings(settings)
    try:
        from compcoach_live.asset_store import LocalAssetStore, SupabaseAssetStore
    except ModuleNotFoundError:  # pragma: no cover - direct script fallback
        from asset_store import LocalAssetStore, SupabaseAssetStore
    project_url = _value(settings, "SUPABASE_URL")
    secret_key = _value(settings, "SUPABASE_SECRET_KEY") or _value(settings, "SUPABASE_SERVICE_ROLE_KEY")
    if project_url or secret_key:
        if not project_url or not secret_key:
            raise ConfigurationError(
                "Storage setup is incomplete. Set SUPABASE_URL and SUPABASE_SECRET_KEY "
                "(or SUPABASE_SERVICE_ROLE_KEY) in Secrets."
            )
        return SupabaseAssetStore(
            project_url,
            secret_key,
            bucket=_value(settings, "COMPCOACH_STORAGE_BUCKET") or "compcoach-assets",
        )
    return LocalAssetStore(Path(__file__).resolve().parent / "data" / "assets")
