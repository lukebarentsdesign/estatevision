from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.active_vendor import get_active_vendor, set_active_vendor


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_active_vendor_defaults_to_first_registered(session) -> None:
    assert get_active_vendor(session, "avatar") == "heygen"


def test_set_then_get_active_vendor_round_trips(session) -> None:
    set_active_vendor(session, "avatar", "heygen")
    assert get_active_vendor(session, "avatar") == "heygen"


def test_set_active_vendor_rejects_slug_from_wrong_category(session) -> None:
    with pytest.raises(ValueError):
        set_active_vendor(session, "avatar", "elevenlabs")


def test_set_active_vendor_rejects_unknown_slug(session) -> None:
    with pytest.raises(ValueError):
        set_active_vendor(session, "avatar", "not_a_real_vendor")


def test_get_active_vendor_rejects_unknown_category(session) -> None:
    with pytest.raises(ValueError):
        get_active_vendor(session, "not_a_real_category")
