"""Deployment must not split one tournament into cloud and local copies."""

import sys
import types
from unittest.mock import Mock

import pytest

from compcoach_live.backend_config import (
    ConfigurationError,
    create_asset_store,
    create_database,
    validate_settings,
)


def cloud_settings():
    return {
        "COMPCOACH_REQUIRE_CLOUD": "true",
        "COMPCOACH_DATABASE_URL": "postgresql://coach:private-db-secret@example.test/compcoach",
        "COMPCOACH_ADMIN_PIN": "private-pin",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_private-storage-secret",
    }


def test_cloud_missing_database_never_opens_sqlite(monkeypatch):
    settings = cloud_settings()
    settings.pop("COMPCOACH_DATABASE_URL")
    local = Mock()
    monkeypatch.setattr("compcoach_live.backend_config.CompCoachDB", local)
    with pytest.raises(ConfigurationError, match="COMPCOACH_DATABASE_URL") as caught:
        create_database(settings)
    local.assert_not_called()
    assert "private" not in str(caught.value)


def test_cloud_missing_assets_never_opens_a_database(monkeypatch):
    settings = cloud_settings()
    settings.pop("SUPABASE_SECRET_KEY")
    local = Mock()
    monkeypatch.setattr("compcoach_live.backend_config.CompCoachDB", local)
    with pytest.raises(ConfigurationError, match="SUPABASE_SECRET_KEY"):
        create_database(settings)
    local.assert_not_called()


def test_postgres_error_is_not_retried_in_sqlite(monkeypatch):
    cloud = Mock(side_effect=ConfigurationError("Cloud connection unavailable."))
    monkeypatch.setitem(
        sys.modules, "compcoach_live.postgres_storage",
        types.SimpleNamespace(PostgresCompCoachDB=cloud),
    )
    local = Mock()
    monkeypatch.setattr("compcoach_live.backend_config.CompCoachDB", local)
    with pytest.raises(ConfigurationError, match="unavailable"):
        create_database(cloud_settings())
    local.assert_not_called()
    cloud.assert_called_once()


def test_storage_secret_names_and_private_bucket(monkeypatch):
    cloud = Mock()
    monkeypatch.setattr("compcoach_live.asset_store.SupabaseAssetStore", cloud)
    settings = cloud_settings()
    settings["SUPABASE_SERVICE_ROLE_KEY"] = "legacy-key"
    create_asset_store(settings)
    cloud.assert_called_once_with(
        "https://project.supabase.co", "sb_secret_private-storage-secret",
        bucket="compcoach-assets",
    )


def test_partial_storage_configuration_never_falls_back(monkeypatch):
    local = Mock()
    monkeypatch.setattr("compcoach_live.asset_store.LocalAssetStore", local)
    with pytest.raises(ConfigurationError, match="Storage setup is incomplete"):
        create_asset_store({"SUPABASE_URL": "https://project.supabase.co"})
    local.assert_not_called()


def test_local_database_path_is_explicit(tmp_path):
    database = create_database({"COMPCOACH_DB_PATH": str(tmp_path / "local.db")})
    assert database.path == tmp_path / "local.db"
    assert database.list_competitions() == []


@pytest.mark.parametrize("flag", ["truu", "yesplease"])
def test_invalid_cloud_flag_cannot_disable_guard(flag):
    with pytest.raises(ConfigurationError, match="true or false"):
        validate_settings({"COMPCOACH_REQUIRE_CLOUD": flag})
