"""Category -> active-vendor client dispatch.

Pipeline steps call these instead of naming a vendor's client factory
directly (e.g. `get_heygen_client()`), so switching the active vendor in the
admin panel (`services.active_vendor`) takes effect without editing pipeline
code. Adding a new vendor to a category means writing a client module that
implements the same Protocol as the existing vendor(s) in that category,
registering it in `integration_registry.py` with a matching `category_key`,
and adding one branch below.
"""

from __future__ import annotations

from typing import Union

from sqlmodel import Session

from ..services.active_vendor import get_active_vendor
from .elevenlabs import ElevenLabsClient, get_elevenlabs_client
from .fish_audio import FishAudioClient, get_fish_audio_client
from .gemini_omni import GeminiOmniClient, get_gemini_omni_client
from .heygen import HeyGenClient, get_heygen_client
from .replicate_wan import ReplicateWanClient, get_replicate_wan_client

HeroShotClient = Union[GeminiOmniClient, ReplicateWanClient]
TtsClient = Union[ElevenLabsClient, FishAudioClient]


def _resolve_session(session: Session | None) -> Session:
    if session is not None:
        return session
    from ..db import engine

    return Session(engine)


def get_active_avatar_client(*, session: Session | None = None) -> HeyGenClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "avatar")
        if vendor == "heygen":
            return get_heygen_client(session=s)
        raise ValueError(f"No client dispatch registered for avatar vendor {vendor!r}")
    finally:
        if owned:
            s.close()


def get_active_tts_client(*, session: Session | None = None) -> TtsClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "tts")
        if vendor == "elevenlabs":
            return get_elevenlabs_client(session=s)
        if vendor == "fish_audio":
            return get_fish_audio_client(session=s)
        raise ValueError(f"No client dispatch registered for tts vendor {vendor!r}")
    finally:
        if owned:
            s.close()


def get_active_hero_shot_client(*, session: Session | None = None) -> HeroShotClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "hero_shot_animation")
        if vendor == "gemini_omni":
            return get_gemini_omni_client(session=s)
        if vendor == "replicate_wan":
            return get_replicate_wan_client(session=s)
        raise ValueError(f"No client dispatch registered for hero_shot_animation vendor {vendor!r}")
    finally:
        if owned:
            s.close()
