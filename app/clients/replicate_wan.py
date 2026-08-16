"""Replicate-hosted Wan 2.2 client for hero-shot motion (spec §4 `standard`, motion pass).

Alternative to Gemini Omni/Veo in the `hero_shot_animation` category (see
`services.active_vendor` -- both vendors are registered under that
category_key and the admin panel picks which one is active). Runs the Wan 2.2
image-to-video model on Replicate's hosted infrastructure rather than a local
GPU, since this app's target machines are not assumed to have one.

NOT independently confirmed against live Replicate docs (the model's own API
page is JS-rendered and the input schema requires an authenticated call to
`GET /v1/models/wan-video/{model}` to inspect): the exact input field names
below (`image`, `prompt`, `resolution`, `go_fast`) are Replicate's
conventional field names for Wan image-to-video models, not verified against
this specific model's OpenAPI schema. The prediction create/poll/download
flow (`POST /v1/predictions`, `GET /v1/predictions/{id}`, `Bearer` auth) is
confirmed against Replicate's general HTTP API reference
(replicate.com/docs/reference/http, fetched 2026-08-16). Treat the first real
call as the actual verification step for the input field names -- check
https://replicate.com/wan-video/wan-2.2-i2v-fast/api first if it fails.

Standing rule mirrored from gemini_omni.py: subtle, forward-facing motion
only for hero shots -- large or reversing moves risk the same hallucinated-
furniture problem regardless of which model renders it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal, Protocol

import httpx
from sqlmodel import Session

from .base import write_placeholder_file
from .credential_lookup import resolve_field

MotionStyle = Literal["subtle_zoom_in", "subtle_zoom_out", "subtle_pan"]

_ALLOWED_STYLES: frozenset[str] = frozenset({"subtle_zoom_in", "subtle_zoom_out", "subtle_pan"})

_MOTION_PROMPTS: dict[str, str] = {
    "subtle_zoom_in": (
        "Subtle, slow forward zoom into the scene. Camera moves forward only, "
        "no panning, no reveal of areas outside the current frame, no new "
        "objects or furniture. Minimal, cinematic, real-estate photography style."
    ),
    "subtle_zoom_out": (
        "Subtle, slow zoom out from the scene. Camera moves backward only "
        "within the existing frame, no panning, no reveal of areas outside "
        "the current frame, no new objects or furniture. Minimal, cinematic, "
        "real-estate photography style."
    ),
    "subtle_pan": (
        "Subtle, slow horizontal pan across the scene, staying forward-facing "
        "and within the bounds of the existing frame. No reveal of new areas, "
        "no new objects or furniture. Minimal, cinematic, real-estate "
        "photography style."
    ),
}

_REQUEST_TIMEOUT = 30.0
_POLL_INTERVAL_SEC = 5.0
_POLL_TIMEOUT_SEC = 600.0  # Wan 2.2 720p generations can take several minutes
_API_BASE = "https://api.replicate.com/v1"
_MODEL = "wan-video/wan-2.2-i2v-fast"


class ReplicateWanError(RuntimeError):
    """Raised when the Replicate API rejects a prediction request or a render fails."""


class ReplicateWanClient(Protocol):
    def animate_hero_shot(
        self, *, image_path: Path, motion_style: MotionStyle, output_path: Path
    ) -> Path:
        ...


def _validate_style(motion_style: str) -> None:
    if motion_style not in _ALLOWED_STYLES:
        raise ValueError(
            f"motion_style {motion_style!r} is not allowed for hero shots. "
            f"Only subtle, forward-facing moves are permitted: {sorted(_ALLOWED_STYLES)}. "
            "Large or reversing moves cause hallucinated furniture (spec §4)."
        )


class StubReplicateWanClient:
    def animate_hero_shot(
        self, *, image_path: Path, motion_style: MotionStyle, output_path: Path
    ) -> Path:
        _validate_style(motion_style)
        return write_placeholder_file(
            output_path,
            f"[stub Replicate Wan clip]\nsource={image_path}\nmotion={motion_style}\n",
        )


class HttpReplicateWanClient:
    """See module docstring for what is and isn't independently confirmed."""

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    def animate_hero_shot(
        self, *, image_path: Path, motion_style: MotionStyle, output_path: Path
    ) -> Path:
        _validate_style(motion_style)
        image_data_uri = self._to_data_uri(image_path)
        prediction = self._create_prediction(image_data_uri, motion_style)
        video_url = self._poll_until_complete(prediction)
        return self._download(video_url, output_path)

    def _to_data_uri(self, image_path: Path) -> str:
        import base64

        mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{data}"

    def _create_prediction(self, image_data_uri: str, motion_style: MotionStyle) -> dict:
        try:
            resp = httpx.post(
                f"{_API_BASE}/models/{_MODEL}/predictions",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=1",
                },
                json={
                    "input": {
                        "image": image_data_uri,
                        "prompt": _MOTION_PROMPTS[motion_style],
                    }
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ReplicateWanError(f"Replicate prediction request failed: {exc}") from exc

        if resp.status_code not in (200, 201):
            raise ReplicateWanError(f"Replicate prediction rejected ({resp.status_code}): {resp.text[:500]}")

        return resp.json()

    def _poll_until_complete(self, prediction: dict) -> str:
        get_url = prediction.get("urls", {}).get("get")
        if not get_url:
            raise ReplicateWanError(f"Replicate prediction response had no poll URL: {prediction!r}")

        deadline = time.monotonic() + _POLL_TIMEOUT_SEC
        while time.monotonic() < deadline:
            status = prediction.get("status")
            if status == "succeeded":
                output = prediction.get("output")
                if isinstance(output, str):
                    return output
                if isinstance(output, list) and output:
                    return output[0]
                raise ReplicateWanError(f"Replicate prediction succeeded with no output URL: {prediction!r}")
            if status in ("failed", "canceled"):
                raise ReplicateWanError(f"Replicate prediction {status}: {prediction.get('error')!r}")

            time.sleep(_POLL_INTERVAL_SEC)
            try:
                resp = httpx.get(
                    get_url, headers={"Authorization": f"Bearer {self.api_token}"}, timeout=_REQUEST_TIMEOUT
                )
            except httpx.HTTPError as exc:
                raise ReplicateWanError(f"Replicate status poll failed: {exc}") from exc

            if resp.status_code != 200:
                raise ReplicateWanError(f"Replicate status poll rejected ({resp.status_code}): {resp.text[:500]}")
            prediction = resp.json()

        raise ReplicateWanError(f"Replicate prediction did not complete within {_POLL_TIMEOUT_SEC}s")

    def _download(self, video_url: str, output_path: Path) -> Path:
        try:
            resp = httpx.get(video_url, timeout=_REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise ReplicateWanError(f"Downloading Wan video failed: {exc}") from exc

        if resp.status_code != 200:
            raise ReplicateWanError(f"Downloading Wan video failed ({resp.status_code})")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path


def get_replicate_wan_client(*, session: Session | None = None) -> ReplicateWanClient:
    """Resolve the Replicate Wan client from stored/admin-panel credentials, falling
    back to a stub if no API token is configured anywhere (admin panel or env)."""
    api_token = resolve_field("replicate_wan", "api_key", session=session)
    if api_token:
        return HttpReplicateWanClient(api_token=api_token)
    return StubReplicateWanClient()
