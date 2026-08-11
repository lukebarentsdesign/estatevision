"""HeyGen avatar client (spec §1.3, §4 `plus`).

Only verified, consented clones (an existing `heygen_avatar_id`) may be used.
Callers must go through `services.consent.require_avatar` before calling
`generate_avatar_clip` -- this client does not re-check consent itself, since
that is `consent.py`'s job, not the HTTP client's.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

import httpx
from sqlmodel import Session

from .base import write_placeholder_file
from .credential_lookup import resolve_field

_REQUEST_TIMEOUT = 30.0
_POLL_INTERVAL_SEC = 5.0
_POLL_TIMEOUT_SEC = 600.0  # HeyGen renders can take several minutes


class HeyGenError(RuntimeError):
    """Raised when the HeyGen API rejects a generation request or a render fails."""


class HeyGenClient(Protocol):
    def generate_avatar_clip(
        self, *, avatar_id: str, script_text: str, output_path: Path
    ) -> Path:
        """Render a talking-head clip of `script_text` and return its path."""
        ...


class StubHeyGenClient:
    """Writes a placeholder file instead of calling the HeyGen API."""

    def generate_avatar_clip(
        self, *, avatar_id: str, script_text: str, output_path: Path
    ) -> Path:
        return write_placeholder_file(
            output_path,
            f"[stub HeyGen clip]\navatar_id={avatar_id}\nscript={script_text}\n",
        )


class HttpHeyGenClient:
    """Real client. Constructed only when an API key is configured (admin panel or env fallback).

    Confirmed against HeyGen docs: `GET /v3/videos/{video_id}` for status
    polling, with `X-Api-Key` auth and `status`/`video_url` response fields
    (developers.heygen.com/docs/quick-start, fetched 2026-08-10).

    NOT independently confirmed: the exact request body field names for the
    generation call below (`POST /v2/video/generate`). HeyGen's full API
    reference is behind a JS-rendered docs site this session couldn't fetch
    past the page shell. The `video_inputs[].character`/`voice` shape used
    here is the well-established HeyGen v2 avatar-video schema, but treat the
    first real call against this as the actual verification step -- if it
    400s, check the field names here against the live reference at
    https://docs.heygen.com/reference/create-an-avatar-video-v2 first.
    """

    def __init__(self, api_key: str, *, base_url: str = "https://api.heygen.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate_avatar_clip(
        self, *, avatar_id: str, script_text: str, output_path: Path
    ) -> Path:
        video_id = self._start_generation(avatar_id=avatar_id, script_text=script_text)
        video_url = self._poll_until_complete(video_id)
        return self._download(video_url, output_path)

    def _start_generation(self, *, avatar_id: str, script_text: str) -> str:
        try:
            resp = httpx.post(
                f"{self.base_url}/v2/video/generate",
                headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                json={
                    "video_inputs": [
                        {
                            "character": {"type": "avatar", "avatar_id": avatar_id, "avatar_style": "normal"},
                            "voice": {"type": "text", "input_text": script_text},
                        }
                    ],
                    "dimension": {"width": 1280, "height": 720},
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise HeyGenError(f"HeyGen generation request failed: {exc}") from exc

        if resp.status_code != 200:
            raise HeyGenError(f"HeyGen generation rejected ({resp.status_code}): {resp.text[:500]}")

        data = resp.json()
        video_id = (data.get("data") or {}).get("video_id")
        if not video_id:
            raise HeyGenError(f"HeyGen generation response had no video_id: {data!r}")
        return video_id

    def _poll_until_complete(self, video_id: str) -> str:
        deadline = time.monotonic() + _POLL_TIMEOUT_SEC
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(
                    f"{self.base_url}/v3/videos/{video_id}",
                    headers={"X-Api-Key": self.api_key},
                    timeout=_REQUEST_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                raise HeyGenError(f"HeyGen status poll failed: {exc}") from exc

            if resp.status_code != 200:
                raise HeyGenError(f"HeyGen status poll rejected ({resp.status_code}): {resp.text[:500]}")

            data = resp.json()
            status = data.get("status")
            if status == "completed":
                video_url = data.get("video_url")
                if not video_url:
                    raise HeyGenError(f"HeyGen reported completed with no video_url: {data!r}")
                return video_url
            if status == "failed":
                raise HeyGenError(f"HeyGen render failed: {data.get('error') or data!r}")

            time.sleep(_POLL_INTERVAL_SEC)

        raise HeyGenError(f"HeyGen video {video_id} did not complete within {_POLL_TIMEOUT_SEC}s")

    def _download(self, video_url: str, output_path: Path) -> Path:
        try:
            resp = httpx.get(video_url, timeout=_REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise HeyGenError(f"Downloading HeyGen video failed: {exc}") from exc

        if resp.status_code != 200:
            raise HeyGenError(f"Downloading HeyGen video failed ({resp.status_code})")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path


def get_heygen_client(*, session: Session | None = None) -> HeyGenClient:
    """Resolve the HeyGen client from stored/admin-panel credentials, falling
    back to a stub if no API key is configured anywhere (admin panel or env)."""
    api_key = resolve_field("heygen", "api_key", session=session)
    if api_key:
        base_url = resolve_field("heygen", "base_url", session=session) or "https://api.heygen.com"
        return HttpHeyGenClient(api_key=api_key, base_url=base_url)
    return StubHeyGenClient()
