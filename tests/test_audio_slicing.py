from __future__ import annotations

import wave
from pathlib import Path

import pytest
from pydub import AudioSegment

from app.clients.audio_slicing import AudioBoundary, slice_audio_file


@pytest.fixture
def silent_wav(tmp_path: Path) -> Path:
    """A 3-second silent mono WAV fixture -- no ffmpeg required to read it."""
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 3)  # 3 seconds of silence
    return path


def test_slice_audio_file_produces_one_file_per_boundary(silent_wav: Path, tmp_path: Path) -> None:
    boundaries = [
        AudioBoundary(start_sec=0.0, end_sec=1.0),
        AudioBoundary(start_sec=1.0, end_sec=2.5),
        AudioBoundary(start_sec=2.5, end_sec=3.0),
    ]
    out_dir = tmp_path / "slices"
    paths = slice_audio_file(silent_wav, boundaries, out_dir=out_dir, stem="segment")

    assert len(paths) == 3
    assert all(p.exists() for p in paths)
    assert paths[0].name == "segment_0.wav"


def test_slice_audio_file_slice_durations_match_boundaries(silent_wav: Path, tmp_path: Path) -> None:
    boundaries = [AudioBoundary(start_sec=0.0, end_sec=1.5), AudioBoundary(start_sec=1.5, end_sec=3.0)]
    out_dir = tmp_path / "slices2"
    paths = slice_audio_file(silent_wav, boundaries, out_dir=out_dir, stem="seg")

    first = AudioSegment.from_file(paths[0])
    assert 1400 <= len(first) <= 1600  # ~1.5s in milliseconds, small tolerance


def test_slice_audio_file_rejects_empty_boundaries(silent_wav: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        slice_audio_file(silent_wav, [], out_dir=tmp_path / "slices3", stem="seg")


def test_slice_audio_file_preserves_boundary_order(silent_wav: Path, tmp_path: Path) -> None:
    """Slices must correspond to boundaries by position, not be reordered --
    downstream photo-linking code depends on paths[i] matching boundaries[i]."""
    boundaries = [
        AudioBoundary(start_sec=2.0, end_sec=3.0),  # deliberately out of chronological order
        AudioBoundary(start_sec=0.0, end_sec=0.5),
    ]
    out_dir = tmp_path / "slices4"
    paths = slice_audio_file(silent_wav, boundaries, out_dir=out_dir, stem="seg")

    assert paths[0].name == "seg_0.wav"
    assert paths[1].name == "seg_1.wav"
    first = AudioSegment.from_file(paths[0])
    second = AudioSegment.from_file(paths[1])
    assert 900 <= len(first) <= 1100  # boundaries[0] is 1.0s long
    assert 400 <= len(second) <= 600  # boundaries[1] is 0.5s long
