"""ElevenLabs voiceover client (spec §1.3, §4 `standard`).

Callers must go through `services.consent.require_voice_for_narration` before
calling `synthesize` -- this client trusts that the voice_id it is given has
already been consent-checked upstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx
from sqlmodel import Session

from .base import write_placeholder_file
from .credential_lookup import resolve_field

_REQUEST_TIMEOUT = 60.0  # TTS synthesis can take longer than a typical API call


class ElevenLabsError(RuntimeError):
    """Raised when the ElevenLabs API rejects a synthesis request."""


class ElevenLabsClient(Protocol):
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        """Render `text` as speech in `voice_id` and return the audio path."""
        ...


class StubElevenLabsClient:
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        return write_placeholder_file(
            output_path,
            f"[stub ElevenLabs audio]\nvoice_id={voice_id}\ntext={text}\n",
        )


class HttpElevenLabsClient:
    """Confirmed against live ElevenLabs API docs (POST /v1/text-to-speech/{voice_id})."""

    def __init__(self, api_key: str, *, base_url: str = "https://api.elevenlabs.io") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_multilingual_v2"},
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc

        if resp.status_code != 200:
            raise ElevenLabsError(
                f"ElevenLabs synthesis failed ({resp.status_code}): {resp.text[:500]}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path


def get_elevenlabs_client(*, session: Session | None = None) -> ElevenLabsClient:
    """Resolve the ElevenLabs client from stored/admin-panel credentials, falling
    back to a stub if no API key is configured anywhere (admin panel or env)."""
    api_key = resolve_field("elevenlabs", "api_key", session=session)
    if api_key:
        base_url = resolve_field("elevenlabs", "base_url", session=session) or "https://api.elevenlabs.io"
        return HttpElevenLabsClient(api_key=api_key, base_url=base_url)
    return StubElevenLabsClient()
