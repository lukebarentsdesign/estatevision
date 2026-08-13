"""Shared fixtures for pipeline/integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

BROCHURE_LINES = [
    "A detached four bedroom family home set back from the road.",
    "The kitchen has been extended to the rear with bi-fold doors.",
    "The garden is mainly laid to lawn with a paved terrace.",
    "There is off-street parking for two cars.",
]


@pytest.fixture
def brochure_pdf(tmp_path: Path) -> Path:
    """A minimal real PDF with brochure-style sentences, for pdfplumber."""
    path = tmp_path / "brochure.pdf"
    c = canvas.Canvas(str(path))
    y = 800
    for line in BROCHURE_LINES:
        c.drawString(50, y, line)
        y -= 20
    c.save()
    return path


@pytest.fixture
def sample_photo(tmp_path: Path) -> Path:
    """A tiny real image file for photo-processing steps."""
    import numpy as np

    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "photo.jpg"
    img = np.full((32, 32, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh in-memory DB and a throwaway session
    signing key, so each test gets an isolated app + auth state."""
    import app.db as db_mod
    import app.services.auth as auth_mod
    from app.main import app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_mod, "engine", engine)

    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    auth_mod._session_serializer = None

    with TestClient(app) as client:
        yield client


@pytest.fixture
def agency_client(api_client):
    """`api_client` with a logged-in agency session already attached to its
    cookie jar. Existing tests that create/read/update jobs need this now
    that those routes require an authenticated agency (spec: agency/admin
    auth design, 2026-08-13)."""
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AgentProfile
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        agent = AgentProfile(agency_name="Test Agency", email="test@agency.example", password_hash=hash_password("test-password"))
        session.add(agent)
        session.commit()

    resp = api_client.post("/api/login", json={"email": "test@agency.example", "password": "test-password"})
    assert resp.status_code == 200
    return api_client


@pytest.fixture
def admin_client(api_client):
    """`api_client` with a logged-in admin session already attached."""
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AdminAccount
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        session.add(AdminAccount(email="admin@platform.example", password_hash=hash_password("admin-password")))
        session.commit()

    resp = api_client.post("/api/login", json={"email": "admin@platform.example", "password": "admin-password"})
    assert resp.status_code == 200
    return api_client
