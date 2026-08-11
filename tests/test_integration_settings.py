"""Tests for the admin panel's credential storage, masking, and client wiring.

Covers: encryption round-trip, masking, env-var fallback, live client factory
resolution from stored credentials, and that the FastAPI admin endpoints never
return a raw secret.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.clients.elevenlabs import HttpElevenLabsClient, StubElevenLabsClient, get_elevenlabs_client
from app.clients.heygen import HttpHeyGenClient, StubHeyGenClient, get_heygen_client
from app.services.integration_settings import IntegrationSettings
from app.services.secrets_store import SecretsStore, mask


@pytest.fixture(autouse=True)
def secrets_store(tmp_path: Path, monkeypatch) -> SecretsStore:
    """Isolated store for every test in this file, including the default-store
    singleton (`get_secrets_store()`), which any code path called with no
    explicit store/session falls back to -- without this, those paths write a
    real `secret.key` into the project root."""
    import app.services.secrets_store as secrets_mod

    store = SecretsStore(key_path=tmp_path / "secret.key")
    monkeypatch.setattr(secrets_mod, "_default_store", store)
    monkeypatch.setattr(secrets_mod, "DEFAULT_KEY_PATH", tmp_path / "secret.key")
    return store


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --- encryption ------------------------------------------------------------


def test_encrypt_decrypt_round_trip(secrets_store: SecretsStore) -> None:
    ciphertext = secrets_store.encrypt("sk-super-secret-123")
    assert ciphertext != b"sk-super-secret-123"
    assert secrets_store.decrypt(ciphertext) == "sk-super-secret-123"


def test_key_file_is_reused_across_instances(tmp_path: Path) -> None:
    key_path = tmp_path / "secret.key"
    store1 = SecretsStore(key_path=key_path)
    ciphertext = store1.encrypt("hello")

    store2 = SecretsStore(key_path=key_path)
    assert store2.decrypt(ciphertext) == "hello"


def test_mask_shows_only_last_four_chars() -> None:
    assert mask("sk-abcdef1234") == "•" * 9 + "1234"  # 13 chars total
    assert mask("ab") == "••"


# --- IntegrationSettings ----------------------------------------------------


def test_set_and_get_field_round_trips(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    settings.set_field("heygen", "api_key", "hg_live_key_123")
    assert settings.get_field("heygen", "api_key") == "hg_live_key_123"


def test_set_field_rejects_unknown_field(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    with pytest.raises(ValueError):
        settings.set_field("heygen", "not_a_real_field", "x")


def test_set_field_overwrites_previous_value(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    settings.set_field("heygen", "api_key", "old")
    settings.set_field("heygen", "api_key", "new")
    assert settings.get_field("heygen", "api_key") == "new"


def test_clear_field_removes_stored_value(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    settings.set_field("heygen", "api_key", "hg_live_key_123")
    settings.clear_field("heygen", "api_key")
    assert settings.get_field("heygen", "api_key") is None


def test_env_var_fallback_used_when_nothing_stored(session, secrets_store, monkeypatch) -> None:
    monkeypatch.setenv("HEYGEN_API_KEY", "from_env")
    settings = IntegrationSettings(session, store=secrets_store)
    assert settings.get_field("heygen", "api_key") == "from_env"


def test_stored_value_takes_priority_over_env_var(session, secrets_store, monkeypatch) -> None:
    monkeypatch.setenv("HEYGEN_API_KEY", "from_env")
    settings = IntegrationSettings(session, store=secrets_store)
    settings.set_field("heygen", "api_key", "from_db")
    assert settings.get_field("heygen", "api_key") == "from_db"


def test_status_reports_masked_value_not_raw_secret(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    settings.set_field("heygen", "api_key", "hg_live_key_123456")
    status = settings.status_for("heygen")

    api_key_field = next(f for f in status.fields if f.field.key == "api_key")
    assert api_key_field.is_set is True
    assert "hg_live_key_123456" not in api_key_field.masked_value
    assert api_key_field.masked_value.endswith("3456")


def test_status_is_fully_configured_only_when_all_required_fields_set(session, secrets_store) -> None:
    settings = IntegrationSettings(session, store=secrets_store)
    status = settings.status_for("heygen")
    assert status.is_fully_configured is False

    settings.set_field("heygen", "api_key", "hg_live_key_123")
    status = settings.status_for("heygen")
    assert status.is_fully_configured is True  # base_url is not required


def test_all_statuses_covers_every_registered_integration(session, secrets_store) -> None:
    from app.services.integration_registry import list_integrations

    settings = IntegrationSettings(session, store=secrets_store)
    statuses = settings.all_statuses()
    assert len(statuses) == len(list_integrations())


# --- client factories --------------------------------------------------------


def test_heygen_factory_returns_stub_with_no_credentials(session, monkeypatch) -> None:
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    client = get_heygen_client(session=session)
    assert isinstance(client, StubHeyGenClient)


def test_heygen_factory_returns_http_client_once_key_stored(session, secrets_store, monkeypatch) -> None:
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    IntegrationSettings(session, store=secrets_store).set_field("heygen", "api_key", "hg_key")
    # Patch the module-level default store lookup used by resolve_field's session path
    import app.services.integration_settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_secrets_store", lambda: secrets_store)

    client = get_heygen_client(session=session)
    assert isinstance(client, HttpHeyGenClient)
    assert client.api_key == "hg_key"


def test_elevenlabs_factory_prefers_stored_over_env(session, secrets_store, monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env_key")
    import app.services.integration_settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_secrets_store", lambda: secrets_store)
    IntegrationSettings(session, store=secrets_store).set_field("elevenlabs", "api_key", "db_key")

    client = get_elevenlabs_client(session=session)
    assert isinstance(client, HttpElevenLabsClient)
    assert client.api_key == "db_key"


def test_elevenlabs_factory_falls_back_to_stub_with_nothing_configured(session, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = get_elevenlabs_client(session=session)
    assert isinstance(client, StubElevenLabsClient)


# --- FastAPI admin endpoints --------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)

    # Reset module-level singletons so the env vars above take effect --
    # `DEFAULT_KEY_PATH` is resolved once at import time, so setenv alone
    # does not move it; it must be patched directly.
    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def test_list_integrations_endpoint_returns_all_systems(api_client) -> None:
    resp = api_client.get("/api/integrations")
    assert resp.status_code == 200
    slugs = {item["slug"] for item in resp.json()}
    assert "heygen" in slugs
    assert "elevenlabs" in slugs
    assert "openai" in slugs


def test_list_integrations_includes_category_key_and_is_active(api_client) -> None:
    resp = api_client.get("/api/integrations")
    data = resp.json()
    heygen = next(item for item in data if item["slug"] == "heygen")
    assert heygen["category_key"] == "avatar"
    assert heygen["is_active"] is True


def test_set_field_endpoint_never_echoes_raw_secret(api_client) -> None:
    resp = api_client.put("/api/integrations/heygen/fields/api_key", json={"value": "hg_super_secret_999"})
    assert resp.status_code == 200
    body = resp.text
    assert "hg_super_secret_999" not in body


def test_set_then_status_shows_configured(api_client) -> None:
    api_client.put("/api/integrations/heygen/fields/api_key", json={"value": "hg_key_abc123"})
    resp = api_client.get("/api/integrations/heygen")
    data = resp.json()
    assert data["is_fully_configured"] is True
    field = next(f for f in data["fields"] if f["key"] == "api_key")
    assert field["is_set"] is True
    assert field["masked_value"].endswith("123")
    assert "hg_key_abc123" not in resp.text


def test_clear_field_endpoint(api_client) -> None:
    api_client.put("/api/integrations/heygen/fields/api_key", json={"value": "hg_key_abc123"})
    resp = api_client.delete("/api/integrations/heygen/fields/api_key")
    assert resp.status_code == 200
    assert resp.json()["is_fully_configured"] is False


def test_unknown_integration_returns_404(api_client) -> None:
    resp = api_client.get("/api/integrations/not_a_real_system")
    assert resp.status_code == 404


def test_test_connection_endpoint_reports_missing_key(api_client) -> None:
    resp = api_client.post("/api/integrations/heygen/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


def test_admin_page_is_served(api_client) -> None:
    resp = api_client.get("/admin/integrations")
    assert resp.status_code == 200
    assert "Integrations" in resp.text


def test_set_active_vendor_endpoint_updates_is_active(api_client) -> None:
    resp = api_client.put("/api/categories/avatar/active-vendor", json={"slug": "heygen"})
    assert resp.status_code == 200
    data = resp.json()
    heygen = next(item for item in data if item["slug"] == "heygen")
    assert heygen["is_active"] is True


def test_set_active_vendor_endpoint_rejects_wrong_category(api_client) -> None:
    resp = api_client.put("/api/categories/avatar/active-vendor", json={"slug": "elevenlabs"})
    assert resp.status_code == 400


def test_set_active_vendor_endpoint_rejects_unknown_category(api_client) -> None:
    resp = api_client.put("/api/categories/not_a_real_category/active-vendor", json={"slug": "heygen"})
    assert resp.status_code == 400
