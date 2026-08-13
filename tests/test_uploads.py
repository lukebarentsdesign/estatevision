from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    monkeypatch.setenv("PROPERTY_STUDIO_UPLOAD_DIR", str(tmp_path / "uploads"))

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job(agency_client) -> int:
    resp = agency_client.post(
        "/api/jobs",
        json={"address": "1 Test St", "postcode": "TE1 1ST"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_upload_brochure_sets_pdf_path(agency_client) -> None:
    job_id = _create_job(agency_client)
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    resp = agency_client.post(
        f"/api/jobs/{job_id}/brochure",
        files={"file": ("brochure.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_brochure_path"]

    job_resp = agency_client.get(f"/api/jobs/{job_id}")
    assert job_resp.json()["pdf_brochure_path"] == data["pdf_brochure_path"]


def test_upload_brochure_rejects_non_pdf(agency_client) -> None:
    job_id = _create_job(agency_client)
    resp = agency_client.post(
        f"/api/jobs/{job_id}/brochure",
        files={"file": ("brochure.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_photos_creates_photo_rows(agency_client) -> None:
    job_id = _create_job(agency_client)
    img_bytes = b"\xff\xd8\xff\xe0fake jpeg content"
    resp = agency_client.post(
        f"/api/jobs/{job_id}/photos",
        files=[
            ("files", ("kitchen.jpg", io.BytesIO(img_bytes), "image/jpeg")),
            ("files", ("garden.jpg", io.BytesIO(img_bytes), "image/jpeg")),
        ],
    )
    assert resp.status_code == 201
    photos = resp.json()
    assert len(photos) == 2
    assert len({p["source_path"] for p in photos}) == 2  # both rows have distinct file paths

    list_resp = agency_client.get(f"/api/jobs/{job_id}/photos")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 2


def test_upload_photos_unknown_job_returns_404(agency_client) -> None:
    resp = agency_client.post(
        "/api/jobs/999999/photos",
        files=[("files", ("x.jpg", io.BytesIO(b"x"), "image/jpeg"))],
    )
    assert resp.status_code == 404


def test_upload_photos_rejects_non_image_content_type(agency_client) -> None:
    job_id = _create_job(agency_client)
    resp = agency_client.post(
        f"/api/jobs/{job_id}/photos",
        files=[("files", ("virus.exe", io.BytesIO(b"MZ..."), "application/x-msdownload"))],
    )
    assert resp.status_code == 400


def test_upload_brochure_rejects_oversized_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    monkeypatch.setenv("PROPERTY_STUDIO_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("PROPERTY_STUDIO_MAX_UPLOAD_MB", "1")

    import importlib

    import app.db as db_mod
    import app.main as main_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        from app.models import AgentProfile
        from app.services.auth import hash_password
        from sqlmodel import Session

        with Session(db_mod.engine) as session:
            session.add(
                AgentProfile(
                    agency_name="Test Agency",
                    email="test@agency.example",
                    password_hash=hash_password("test-password"),
                )
            )
            session.commit()
        login_resp = client.post(
            "/api/login", json={"email": "test@agency.example", "password": "test-password"}
        )
        assert login_resp.status_code == 200

        job_id = _create_job(client)
        oversized = b"%PDF-1.4 " + (b"x" * (2 * 1024 * 1024))
        resp = client.post(
            f"/api/jobs/{job_id}/brochure",
            files={"file": ("brochure.pdf", io.BytesIO(oversized), "application/pdf")},
        )
        assert resp.status_code == 413

    importlib.reload(main_mod)  # restore default cap for subsequent tests in this process
