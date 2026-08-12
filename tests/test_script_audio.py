from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.clients.elevenlabs import StubElevenLabsClient
from app.clients.local_tools import WordTimestamp
from app.services.script_audio import _find_segment_boundaries, synthesize_and_slice_segments


def _write_silent_wav(path: Path, *, seconds: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


class _FakeElevenLabsClient(StubElevenLabsClient):
    """Records the exact text it was asked to synthesize, so tests can assert
    the segments were concatenated into ONE call rather than N calls.

    Rather than writing the real StubElevenLabsClient's placeholder text file,
    this writes a real (silent) WAV file at output_path, since the audio this
    module produces gets fed into pydub's AudioSegment.from_file for slicing --
    a placeholder text file would make that raise. This keeps the test focused
    on orchestration (one call, correct boundaries, correct output count)
    without needing a real ElevenLabs call or real speech content.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        self.calls.append(text)
        return _write_silent_wav(output_path)


def _fake_whisperx(audio_path: Path, *, work_dir: Path | None = None) -> list[WordTimestamp]:
    """Deterministic word timings covering 'Hi there. The kitchen is bright.'
    at a fixed rate, standing in for a real forced-alignment call."""
    words = ["Hi", "there.", "The", "kitchen", "is", "bright."]
    timings = []
    t = 0.0
    for w in words:
        timings.append(WordTimestamp(w, t, t + 0.5))
        t += 0.5
    return timings


def test_synthesize_and_slice_makes_exactly_one_elevenlabs_call(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    segments_text = ["Hi there.", "The kitchen is bright."]

    synthesize_and_slice_segments(
        segments_text,
        voice_id="voice-123",
        elevenlabs_client=client,
        whisperx_fn=_fake_whisperx,
        out_dir=tmp_path / "audio",
        stem="walkthrough",
    )

    assert len(client.calls) == 1
    assert client.calls[0] == "Hi there. The kitchen is bright."


def test_synthesize_and_slice_returns_one_path_per_segment(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    segments_text = ["Hi there.", "The kitchen is bright."]

    result = synthesize_and_slice_segments(
        segments_text,
        voice_id="voice-123",
        elevenlabs_client=client,
        whisperx_fn=_fake_whisperx,
        out_dir=tmp_path / "audio2",
        stem="walkthrough",
    )

    assert len(result) == 2
    assert all(p.exists() for p in result)


def test_synthesize_and_slice_rejects_empty_segment_list(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    with pytest.raises(ValueError):
        synthesize_and_slice_segments(
            [],
            voice_id="voice-123",
            elevenlabs_client=client,
            whisperx_fn=_fake_whisperx,
            out_dir=tmp_path / "audio3",
            stem="walkthrough",
        )


def test_find_segment_boundaries_falls_back_to_zero_duration_when_words_run_short() -> None:
    """If WhisperX aligns fewer words than a segment's word count implies
    (e.g. it dropped a word), that segment gets a zero-duration boundary
    at the previous segment's end, rather than crashing or misreading
    words that belong to a later segment."""
    segments_text = ["Hi there.", "The kitchen is bright.", "A lovely garden."]
    # Only 3 words total (should be 2 + 4 + 3 = 9) -- WhisperX under-produced.
    words = [
        WordTimestamp("Hi", 0.0, 0.3),
        WordTimestamp("there.", 0.3, 0.6),
        WordTimestamp("The", 0.6, 0.9),
    ]

    boundaries = _find_segment_boundaries(segments_text, words)

    assert len(boundaries) == 3
    # First segment consumed its 2 words normally.
    assert boundaries[0].start_sec == 0.0
    assert boundaries[0].end_sec == 0.6
    # Second segment needs 4 words (cursor 2:6) but only "The" (index 2)
    # remains within that slice -- non-empty, so it uses that single word's
    # own start/end rather than falling back.
    assert boundaries[1].start_sec == 0.6
    assert boundaries[1].end_sec == 0.9
    # Third segment needs 3 words (cursor 6:9), but `words` only has 3
    # entries total -- that slice is empty, triggering the fallback to
    # segment 2's end (a zero-duration boundary).
    assert boundaries[2].start_sec == boundaries[2].end_sec == boundaries[1].end_sec
