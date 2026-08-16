"""Fish Audio voiceover client (spec §1.3, §4 `standard`).

Alternative to ElevenLabs in the `tts` category (see `services.active_vendor`
-- both vendors are registered under that category_key and the admin panel
picks which one is active). Callers must go through
`services.consent.require_voice_for_narration` before calling `synthesize`,
same as the ElevenLabs client -- this client trusts that the voice id it is
given has already been consent-checked upstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx
from sqlmodel import Session

from .base import write_placeholder_file
from .credential_lookup import resolve_field

_REQUEST_TIMEOUT = 60.0  # TTS synthesis can take longer than a typical API call


class FishAudioError(RuntimeError):
    """Raised when the Fish Audio API rejects a synthesis request."""


class FishAudioClient(Protocol):
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        """Render `text` as speech in `voice_id` (a Fish Audio `reference_id`)
        and return the audio path."""
        ...


class StubFishAudioClient:
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        return write_placeholder_file(
            output_path,
            f"[stub Fish Audio audio]\nvoice_id={voice_id}\ntext={text}\n",
        )


class HttpFishAudioClient:
    """Confirmed against the official docs at docs.fish.audio (api.fish.audio is
    the confirmed base domain -- fishaudio.org, a different domain, publishes a
    conflicting endpoint/field-name doc and was not used):
    `POST /v1/tts` with `Authorization: Bearer <token>`, a JSON body of
    `{"text": ..., "reference_id": ...}`, and the response is the raw audio
    bytes themselves (not a JSON envelope, no separate poll step -- this is a
    synchronous request/response API, unlike ElevenLabs' identically-shaped
    synchronous call or Gemini Omni/Replicate's async generate-then-poll flow).

    NOT independently confirmed: `reference_id` accepting a bare voice ID
    string the way `voice_id` is used elsewhere in this codebase (vs. Fish
    Audio's own voice-model IDs, which may need to be created/uploaded
    through their voice-cloning flow first). Treat the first real call as
    the actual verification step -- check
    https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
    first if it fails.
    """

    def __init__(self, api_key: str, *, base_url: str = "https://api.fish.audio") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/tts",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"text": text, "reference_id": voice_id, "format": "mp3"},
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise FishAudioError(f"Fish Audio request failed: {exc}") from exc

        if resp.status_code != 200:
            raise FishAudioError(f"Fish Audio synthesis failed ({resp.status_code}): {resp.text[:500]}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path


def get_fish_audio_client(*, session: Session | None = None) -> FishAudioClient:
    """Resolve the Fish Audio client from stored/admin-panel credentials, falling
    back to a stub if no API key is configured anywhere (admin panel or env)."""
    api_key = resolve_field("fish_audio", "api_key", session=session)
    if api_key:
        base_url = resolve_field("fish_audio", "base_url", session=session) or "https://api.fish.audio"
        return HttpFishAudioClient(api_key=api_key, base_url=base_url)
    return StubFishAudioClient()
