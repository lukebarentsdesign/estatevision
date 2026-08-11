from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.clients.dispatch import (
    get_active_avatar_client,
    get_active_hero_shot_client,
    get_active_tts_client,
)
from app.clients.elevenlabs import StubElevenLabsClient
from app.clients.gemini_omni import StubGeminiOmniClient
from app.clients.heygen import StubHeyGenClient


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_active_avatar_client_returns_heygen_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    client = get_active_avatar_client(session=session)
    assert isinstance(client, StubHeyGenClient)


def test_get_active_tts_client_returns_elevenlabs_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = get_active_tts_client(session=session)
    assert isinstance(client, StubElevenLabsClient)


def test_get_active_hero_shot_client_returns_gemini_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = get_active_hero_shot_client(session=session)
    assert isinstance(client, StubGeminiOmniClient)
