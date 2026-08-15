"""Tests for the real HTTP client implementations (ElevenLabs, HeyGen, Gemini Omni/Veo).

All HTTP is mocked via `httpx.MockTransport` -- no network access. These tests
exercise the actual request construction and response parsing code, not just
the stub fallback path (covered separately in test_integration_settings.py).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.clients.elevenlabs import ElevenLabsError, HttpElevenLabsClient
from app.clients.gemini_omni import GeminiOmniError, HttpGeminiOmniClient
from app.clients.heygen import HeyGenError, HttpHeyGenClient


def _client_with_transport(cls, transport: httpx.MockTransport, monkeypatch, **kwargs):
    """Patch httpx.post/get at module level to route through a mock transport."""
    mock_client = httpx.Client(transport=transport)

    def fake_post(url, **kw):
        return mock_client.post(url, **{k: v for k, v in kw.items() if k != "timeout"})

    def fake_get(url, **kw):
        return mock_client.get(url, **{k: v for k, v in kw.items() if k != "timeout"})

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    return cls(**kwargs)


# --- ElevenLabs --------------------------------------------------------------


def test_elevenlabs_synthesize_writes_audio_bytes(tmp_path: Path, monkeypatch) -> None:
    fake_audio = b"\xff\xfb\x90\x00fake-mp3-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/text-to-speech/voice_abc"
        assert request.headers["xi-api-key"] == "test_key"
        return httpx.Response(200, content=fake_audio)

    client = _client_with_transport(
        HttpElevenLabsClient, httpx.MockTransport(handler), monkeypatch, api_key="test_key"
    )
    output = client.synthesize(voice_id="voice_abc", text="Hello", output_path=tmp_path / "out.mp3")

    assert output.read_bytes() == fake_audio


def test_elevenlabs_sends_text_and_model_id(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"audio")

    client = _client_with_transport(
        HttpElevenLabsClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    client.synthesize(voice_id="v1", text="Welcome home.", output_path=tmp_path / "out.mp3")

    assert captured["body"]["text"] == "Welcome home."
    assert "model_id" in captured["body"]


def test_elevenlabs_raises_on_error_status(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_api_key")

    client = _client_with_transport(
        HttpElevenLabsClient, httpx.MockTransport(handler), monkeypatch, api_key="bad_key"
    )
    with pytest.raises(ElevenLabsError, match="401"):
        client.synthesize(voice_id="v1", text="Hi", output_path=tmp_path / "out.mp3")


def test_elevenlabs_raises_on_network_error(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_transport(
        HttpElevenLabsClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    with pytest.raises(ElevenLabsError, match="request failed"):
        client.synthesize(voice_id="v1", text="Hi", output_path=tmp_path / "out.mp3")


# --- HeyGen --------------------------------------------------------------------


def test_heygen_full_flow_generate_poll_download(tmp_path: Path, monkeypatch) -> None:
    fake_video_bytes = b"fake-mp4-bytes"
    call_count = {"status_polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/video/generate":
            assert request.headers["X-Api-Key"] == "hg_key"
            return httpx.Response(200, json={"data": {"video_id": "vid_123"}})
        if request.url.path == "/v3/videos/vid_123":
            call_count["status_polls"] += 1
            if call_count["status_polls"] < 2:
                return httpx.Response(200, json={"status": "processing"})
            return httpx.Response(
                200, json={"status": "completed", "video_url": "https://cdn.heygen.com/vid_123.mp4"}
            )
        if str(request.url) == "https://cdn.heygen.com/vid_123.mp4":
            return httpx.Response(200, content=fake_video_bytes)
        raise AssertionError(f"unexpected request to {request.url}")

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    monkeypatch.setattr("app.clients.heygen._POLL_INTERVAL_SEC", 0)

    output = client.generate_avatar_clip(
        avatar_id="avatar_1", script_text="Welcome to this home.", output_path=tmp_path / "intro.mp4"
    )

    assert output.read_bytes() == fake_video_bytes
    assert call_count["status_polls"] == 2


def test_heygen_raises_on_generation_rejection(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid avatar_id")

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    with pytest.raises(HeyGenError, match="rejected"):
        client.generate_avatar_clip(avatar_id="bad", script_text="Hi", output_path=tmp_path / "out.mp4")


def test_heygen_raises_on_missing_video_id(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    with pytest.raises(HeyGenError, match="no video_id"):
        client.generate_avatar_clip(avatar_id="a1", script_text="Hi", output_path=tmp_path / "out.mp4")


def test_heygen_raises_on_render_failure(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/video/generate":
            return httpx.Response(200, json={"data": {"video_id": "vid_1"}})
        return httpx.Response(200, json={"status": "failed", "error": "avatar unavailable"})

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    monkeypatch.setattr("app.clients.heygen._POLL_INTERVAL_SEC", 0)

    with pytest.raises(HeyGenError, match="avatar unavailable"):
        client.generate_avatar_clip(avatar_id="a1", script_text="Hi", output_path=tmp_path / "out.mp4")


def test_heygen_raises_on_completed_with_no_video_url(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/video/generate":
            return httpx.Response(200, json={"data": {"video_id": "vid_1"}})
        return httpx.Response(200, json={"status": "completed"})

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    monkeypatch.setattr("app.clients.heygen._POLL_INTERVAL_SEC", 0)

    with pytest.raises(HeyGenError, match="no video_url"):
        client.generate_avatar_clip(avatar_id="a1", script_text="Hi", output_path=tmp_path / "out.mp4")


def test_heygen_poll_timeout_raises(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/video/generate":
            return httpx.Response(200, json={"data": {"video_id": "vid_1"}})
        return httpx.Response(200, json={"status": "processing"})

    client = _client_with_transport(
        HttpHeyGenClient, httpx.MockTransport(handler), monkeypatch, api_key="hg_key"
    )
    monkeypatch.setattr("app.clients.heygen._POLL_INTERVAL_SEC", 0)
    monkeypatch.setattr("app.clients.heygen._POLL_TIMEOUT_SEC", 0)

    with pytest.raises(HeyGenError, match="did not complete"):
        client.generate_avatar_clip(avatar_id="a1", script_text="Hi", output_path=tmp_path / "out.mp4")


# --- Gemini Omni / Veo --------------------------------------------------------

# Smallest possible valid PNG (1x1 transparent pixel), for real base64-encoding.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415478da6360000002000155f8fb190000000049454e44"
    "ae426082"
)


@pytest.fixture
def sample_hero_image(tmp_path: Path) -> Path:
    path = tmp_path / "hero.png"
    path.write_bytes(_TINY_PNG)
    return path


def test_gemini_omni_full_flow_generate_poll_download(sample_hero_image: Path, tmp_path: Path, monkeypatch) -> None:
    fake_video_bytes = b"fake-veo-mp4-bytes"
    call_count = {"status_polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models/veo-3.1-generate-preview:predictLongRunning":
            assert request.headers["x-goog-api-key"] == "gemini_key"
            return httpx.Response(200, json={"name": "operations/op_123"})
        if request.url.path == "/v1beta/operations/op_123":
            call_count["status_polls"] += 1
            if call_count["status_polls"] < 2:
                return httpx.Response(200, json={"done": False})
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "response": {
                        "generateVideoResponse": {
                            "generatedSamples": [{"video": {"uri": "https://veo.example/op_123.mp4"}}]
                        }
                    },
                },
            )
        if str(request.url) == "https://veo.example/op_123.mp4":
            return httpx.Response(200, content=fake_video_bytes)
        raise AssertionError(f"unexpected request to {request.url}")

    client = _client_with_transport(
        HttpGeminiOmniClient, httpx.MockTransport(handler), monkeypatch, api_key="gemini_key"
    )
    monkeypatch.setattr("app.clients.gemini_omni._POLL_INTERVAL_SEC", 0)

    output = client.animate_hero_shot(
        image_path=sample_hero_image,
        motion_style="subtle_zoom_in",
        output_path=tmp_path / "hero_clip.mp4",
    )

    assert output.read_bytes() == fake_video_bytes
    assert call_count["status_polls"] == 2


def test_gemini_omni_sends_motion_specific_no_reveal_prompt(sample_hero_image: Path, tmp_path: Path, monkeypatch) -> None:
    """§4 standing rule: large/reversing moves cause hallucinated furniture --
    the actual prompt text must encode the forward-facing/no-reveal constraint,
    not just the enum value."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if "predictLongRunning" in request.url.path:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"name": "operations/op_1"})
        return httpx.Response(
            200,
            json={
                "done": True,
                "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://x/1.mp4"}}]}},
            },
        )

    client = _client_with_transport(
        HttpGeminiOmniClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    monkeypatch.setattr("app.clients.gemini_omni._POLL_INTERVAL_SEC", 0)

    client.animate_hero_shot(
        image_path=sample_hero_image, motion_style="subtle_pan", output_path=tmp_path / "out.mp4"
    )

    prompt = captured["body"]["instances"][0]["prompt"]
    assert "no reveal" in prompt.lower()
    assert "forward-facing" in prompt.lower()
    assert captured["body"]["instances"][0]["image"]["mimeType"] == "image/png"


def test_gemini_omni_rejects_disallowed_motion_style(sample_hero_image: Path, tmp_path: Path) -> None:
    client = HttpGeminiOmniClient(api_key="k")
    with pytest.raises(ValueError, match="not allowed"):
        client.animate_hero_shot(
            image_path=sample_hero_image, motion_style="large_reveal_pan", output_path=tmp_path / "out.mp4"
        )


def test_gemini_omni_raises_on_generation_rejection(sample_hero_image: Path, tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid image")

    client = _client_with_transport(
        HttpGeminiOmniClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    with pytest.raises(GeminiOmniError, match="rejected"):
        client.animate_hero_shot(
            image_path=sample_hero_image, motion_style="subtle_zoom_in", output_path=tmp_path / "out.mp4"
        )


def test_gemini_omni_raises_on_operation_error(sample_hero_image: Path, tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "predictLongRunning" in request.url.path:
            return httpx.Response(200, json={"name": "operations/op_1"})
        return httpx.Response(200, json={"done": True, "error": {"message": "quota exceeded"}})

    client = _client_with_transport(
        HttpGeminiOmniClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    monkeypatch.setattr("app.clients.gemini_omni._POLL_INTERVAL_SEC", 0)

    with pytest.raises(GeminiOmniError, match="quota exceeded"):
        client.animate_hero_shot(
            image_path=sample_hero_image, motion_style="subtle_zoom_in", output_path=tmp_path / "out.mp4"
        )


def test_gemini_omni_poll_timeout_raises(sample_hero_image: Path, tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "predictLongRunning" in request.url.path:
            return httpx.Response(200, json={"name": "operations/op_1"})
        return httpx.Response(200, json={"done": False})

    client = _client_with_transport(
        HttpGeminiOmniClient, httpx.MockTransport(handler), monkeypatch, api_key="k"
    )
    monkeypatch.setattr("app.clients.gemini_omni._POLL_INTERVAL_SEC", 0)
    monkeypatch.setattr("app.clients.gemini_omni._POLL_TIMEOUT_SEC", 0)

    with pytest.raises(GeminiOmniError, match="did not complete"):
        client.animate_hero_shot(
            image_path=sample_hero_image, motion_style="subtle_zoom_in", output_path=tmp_path / "out.mp4"
        )
