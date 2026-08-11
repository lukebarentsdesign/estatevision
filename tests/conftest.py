"""Shared fixtures for pipeline/integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas
from sqlmodel import Session, SQLModel, create_engine

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
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
