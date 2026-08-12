from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    monkeypatch.setenv("PROPERTY_STUDIO_UPLOAD_DIR", str(tmp_path / "uploads"))

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job(api_client) -> int:
    resp = api_client.post(
        "/api/jobs",
        json={"address": "1 Test St", "postcode": "TE1 1ST", "feature_level": "plus"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_patch_job_updates_use_avatar(api_client) -> None:
    job_id = _create_job(api_client)

    resp = api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})
    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is True

    get_resp = api_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["use_avatar"] is True


def test_patch_job_can_toggle_back_to_false(api_client) -> None:
    job_id = _create_job(api_client)

    api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})
    resp = api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": False})

    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is False


def test_patch_job_unknown_job_returns_404(api_client) -> None:
    resp = api_client.patch("/api/jobs/999999", json={"use_avatar": True})
    assert resp.status_code == 404


def test_patch_job_ignores_absent_fields(api_client) -> None:
    """A PATCH body with use_avatar omitted (None) must not overwrite the
    existing value -- this is a partial update, not a replace."""
    job_id = _create_job(api_client)
    api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})

    resp = api_client.patch(f"/api/jobs/{job_id}", json={})
    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is True
