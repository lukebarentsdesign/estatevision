"""Gemini Omni client for hero-shot motion (spec §4 `standard`, motion pass).

Standing rule: subtle zoom/pan only, forward-facing, no-reveal prompting.
Large or reversing moves cause hallucinated furniture, so this client refuses
to accept a `motion_style` outside the allowed set -- the caller cannot ask for
a large or reversing pan even by mistake.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Literal, Protocol

import httpx
from sqlmodel import Session

from .base import write_placeholder_file
from .credential_lookup import resolve_field

MotionStyle = Literal["subtle_zoom_in", "subtle_zoom_out", "subtle_pan"]

_ALLOWED_STYLES: frozenset[str] = frozenset({"subtle_zoom_in", "subtle_zoom_out", "subtle_pan"})

# The spec's "no-reveal, forward-facing, subtle only" rule (§4) is a prompting
# constraint, not just an enum choice -- large or reversing moves cause
# hallucinated furniture, so the actual text sent to the model has to encode
# the restriction, not just pick from an allowed set of style names.
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
_POLL_TIMEOUT_SEC = 300.0
_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "veo-3.1-generate-preview"


class GeminiOmniError(RuntimeError):
    """Raised when the Veo API rejects a generation request or a render fails."""


class GeminiOmniClient(Protocol):
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


class StubGeminiOmniClient:
    def animate_hero_shot(
        self, *, image_path: Path, motion_style: MotionStyle, output_path: Path
    ) -> Path:
        _validate_style(motion_style)
        return write_placeholder_file(
            output_path,
            f"[stub Gemini Omni clip]\nsource={image_path}\nmotion={motion_style}\n",
        )


class HttpGeminiOmniClient:
    """Confirmed against live Google AI docs: `POST /v1beta/models/{model}:predictLongRunning`
    with `x-goog-api-key` auth, and `GET /v1beta/{operation_name}` for polling
    (ai.google.dev/gemini-api/docs/veo, fetched 2026-08-10).

    The image field uses `bytesBase64Encoded`/`mimeType`, not the
    `inlineData.data`/`inlineData.mimeType` shape shown in the docs as of
    2026-08-10 -- confirmed by a live call against `veo-3.1-generate-preview`
    on 2026-08-15: `inlineData` is rejected outright ("isn't supported by this
    model"), while `bytesBase64Encoded` with the same image passes validation
    and reaches quota/billing enforcement instead. Re-check this if the API
    starts rejecting `bytesBase64Encoded` too.

    NOT independently confirmed: the download step. Docs describe the
    completed response containing a video URI but don't document how to
    authenticate the download request. This implementation sends the same
    `x-goog-api-key` header used everywhere else in the API, which is the
    standard pattern for this API family, but treat the first real call as
    the actual verification step for that one detail.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def animate_hero_shot(
        self, *, image_path: Path, motion_style: MotionStyle, output_path: Path
    ) -> Path:
        _validate_style(motion_style)
        operation_name = self._start_generation(image_path=image_path, motion_style=motion_style)
        video_uri = self._poll_until_complete(operation_name)
        return self._download(video_uri, output_path)

    def _start_generation(self, *, image_path: Path, motion_style: MotionStyle) -> str:
        image_bytes = image_path.read_bytes()
        mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

        try:
            resp = httpx.post(
                f"{_API_BASE}/models/{_MODEL}:predictLongRunning",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "instances": [
                        {
                            "prompt": _MOTION_PROMPTS[motion_style],
                            "image": {
                                "bytesBase64Encoded": base64.b64encode(image_bytes).decode("ascii"),
                                "mimeType": mime_type,
                            },
                        }
                    ],
                    "parameters": {"aspectRatio": "16:9", "resolution": "720p"},
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise GeminiOmniError(f"Veo generation request failed: {exc}") from exc

        if resp.status_code != 200:
            raise GeminiOmniError(f"Veo generation rejected ({resp.status_code}): {resp.text[:500]}")

        data = resp.json()
        operation_name = data.get("name")
        if not operation_name:
            raise GeminiOmniError(f"Veo generation response had no operation name: {data!r}")
        return operation_name

    def _poll_until_complete(self, operation_name: str) -> str:
        deadline = time.monotonic() + _POLL_TIMEOUT_SEC
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(
                    f"{_API_BASE}/{operation_name}",
                    headers={"x-goog-api-key": self.api_key},
                    timeout=_REQUEST_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                raise GeminiOmniError(f"Veo status poll failed: {exc}") from exc

            if resp.status_code != 200:
                raise GeminiOmniError(f"Veo status poll rejected ({resp.status_code}): {resp.text[:500]}")

            data = resp.json()
            if data.get("done"):
                if "error" in data:
                    raise GeminiOmniError(f"Veo generation failed: {data['error']!r}")
                try:
                    samples = data["response"]["generateVideoResponse"]["generatedSamples"]
                    return samples[0]["video"]["uri"]
                except (KeyError, IndexError) as exc:
                    raise GeminiOmniError(
                        f"Veo reported done with no video URI in expected shape: {data!r}"
                    ) from exc

            time.sleep(_POLL_INTERVAL_SEC)

        raise GeminiOmniError(f"Veo operation {operation_name} did not complete within {_POLL_TIMEOUT_SEC}s")

    def _download(self, video_uri: str, output_path: Path) -> Path:
        try:
            resp = httpx.get(video_uri, headers={"x-goog-api-key": self.api_key}, timeout=_REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GeminiOmniError(f"Downloading Veo video failed: {exc}") from exc

        if resp.status_code != 200:
            raise GeminiOmniError(f"Downloading Veo video failed ({resp.status_code})")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path


def get_gemini_omni_client(*, session: Session | None = None) -> GeminiOmniClient:
    """Resolve the Gemini Omni client from stored/admin-panel credentials, falling
    back to a stub if no API key is configured anywhere (admin panel or env)."""
    api_key = resolve_field("gemini_omni", "api_key", session=session)
    if api_key:
        return HttpGeminiOmniClient(api_key=api_key)
    return StubGeminiOmniClient()
