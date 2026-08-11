"""Tests for real local CLI tool invocations (Real-ESRGAN, WhisperX, Demucs, DepthFlow).

`subprocess.run` is monkeypatched to a fake that writes the files the real
tool would produce, so these tests exercise the actual command construction
and output-path resolution logic without needing the tools installed.

Covers the Demucs output-path bug found while researching the real CLI: the
stub-era code assumed `no_vocals.wav`, but Demucs actually writes
`{output_dir}/{model}/{track_stem}/other.wav`.

Also covers DepthFlow, which has no built-in animation preset -- real motion
requires invoking a bundled custom `DepthScene` subclass script rather than
the bare `depthflow` command (see `app/depthflow_scenes/subtle_parallax.py`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.clients.local_tools import (
    ToolExecutionError,
    demucs_separate_music,
    depthflow_parallax,
    ffmpeg_mux,
    real_esrgan_upscale,
    whisperx_word_timestamps,
)


@pytest.fixture(autouse=True)
def _tools_available(monkeypatch):
    monkeypatch.setenv("LOCAL_TOOLS_AVAILABLE", "1")


def _fake_run_factory(side_effect):
    """Build a subprocess.run replacement that calls side_effect(cmd) then
    returns a successful CompletedProcess, unless side_effect raises/returns
    a nonzero code."""

    def fake_run(cmd, capture_output=True, text=True):
        returncode = side_effect(cmd) or 0
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="mock failure" if returncode else "")

    return fake_run


# --- Real-ESRGAN ---------------------------------------------------------


def test_real_esrgan_invokes_binary_with_expected_flags(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-upscaled")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    source = tmp_path / "in.jpg"
    source.write_bytes(b"fake-source")
    output = real_esrgan_upscale(source, tmp_path / "out.png")

    assert captured["cmd"][0] == "realesrgan-ncnn-vulkan"
    assert "-i" in captured["cmd"] and str(source) in captured["cmd"]
    assert "-n" in captured["cmd"]  # model flag must be present
    assert output.read_bytes() == b"fake-upscaled"


def test_real_esrgan_raises_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 1))
    with pytest.raises(ToolExecutionError):
        real_esrgan_upscale(tmp_path / "in.jpg", tmp_path / "out.png")


# --- WhisperX --------------------------------------------------------------


def test_whisperx_parses_word_level_json_output(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"fake-audio")

    def side_effect(cmd):
        out_dir = Path(cmd[cmd.index("--output_dir") + 1])
        payload = {
            "segments": [
                {
                    "words": [
                        {"word": "Welcome", "start": 0.0, "end": 0.4},
                        {"word": "home.", "start": 0.4, "end": 0.9},
                    ]
                }
            ]
        }
        (out_dir / "narration.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    words = whisperx_word_timestamps(audio, work_dir=tmp_path)

    assert [w.word for w in words] == ["Welcome", "home."]
    assert words[0].start_sec == 0.0
    assert words[1].end_sec == 0.9


def test_whisperx_skips_words_without_timing(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"fake-audio")

    def side_effect(cmd):
        out_dir = Path(cmd[cmd.index("--output_dir") + 1])
        payload = {"segments": [{"words": [{"word": "uh"}, {"word": "clear", "start": 1.0, "end": 1.2}]}]}
        (out_dir / "narration.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    words = whisperx_word_timestamps(audio, work_dir=tmp_path)

    assert [w.word for w in words] == ["clear"]


def test_whisperx_raises_if_output_json_missing(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 0))  # succeeds but writes nothing

    with pytest.raises(ToolExecutionError, match="did not produce"):
        whisperx_word_timestamps(audio, work_dir=tmp_path)


# --- Demucs ------------------------------------------------------------------


def test_demucs_returns_real_other_wav_path_not_no_vocals(tmp_path: Path, monkeypatch) -> None:
    """Regression test for the output-path bug found during API research:
    the old stub-derived code assumed `no_vocals.wav`, which Demucs never
    actually writes."""
    source = tmp_path / "background_music.mp3"
    source.write_bytes(b"fake-audio")
    output_dir = tmp_path / "music_out"

    def side_effect(cmd):
        stem_dir = output_dir / "htdemucs" / "background_music"
        stem_dir.mkdir(parents=True, exist_ok=True)
        (stem_dir / "other.wav").write_bytes(b"fake-instrumental")
        (stem_dir / "vocals.wav").write_bytes(b"fake-vocals")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    result = demucs_separate_music(source, output_dir)

    assert result.name == "other.wav"
    assert result.read_bytes() == b"fake-instrumental"
    assert "no_vocals" not in str(result)


def test_demucs_sends_two_stems_and_model_flags(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd
        stem_dir = tmp_path / "out" / "htdemucs" / "track"
        stem_dir.mkdir(parents=True, exist_ok=True)
        (stem_dir / "other.wav").write_bytes(b"x")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))
    demucs_separate_music(tmp_path / "track.mp3", tmp_path / "out")

    assert "--two-stems=vocals" in captured["cmd"]
    assert "-n" in captured["cmd"]


def test_demucs_raises_if_output_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 0))  # succeeds but writes nothing
    with pytest.raises(ToolExecutionError, match="did not produce"):
        demucs_separate_music(tmp_path / "track.mp3", tmp_path / "out")


# --- DepthFlow -----------------------------------------------------------


def test_depthflow_invokes_bundled_scene_script_not_bare_command(tmp_path: Path, monkeypatch) -> None:
    """DepthFlow's CLI has no built-in animation preset, so this must invoke
    the bundled custom scene script (`python subtle_parallax.py ...`), not a
    bare `depthflow` command with no scene logic behind it."""
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-parallax-clip")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    source = tmp_path / "room.jpg"
    source.write_bytes(b"fake-source")
    output = depthflow_parallax(source, tmp_path / "room_clip.mp4", duration_sec=5.0)

    cmd = captured["cmd"]
    assert cmd[0] == "python"
    assert cmd[1].endswith("subtle_parallax.py")
    assert "input" in cmd and "-i" in cmd and str(source) in cmd
    assert "main" in cmd and "-o" in cmd and "-t" in cmd
    assert cmd[cmd.index("-t") + 1] == "5.0"
    assert output.read_bytes() == b"fake-parallax-clip"


def test_depthflow_raises_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 1))
    with pytest.raises(ToolExecutionError):
        depthflow_parallax(tmp_path / "room.jpg", tmp_path / "out.mp4")


def test_depthflow_scene_script_exists_and_is_importable_shape() -> None:
    """Sanity check that the bundled scene script is a real DepthScene
    subclass, not just a stub file -- catches an empty/broken script even
    though the subprocess call itself is mocked in the tests above."""
    from app.clients.local_tools import _DEPTHFLOW_SCENE_SCRIPT

    assert _DEPTHFLOW_SCENE_SCRIPT.exists()
    source = _DEPTHFLOW_SCENE_SCRIPT.read_text(encoding="utf-8")
    assert "class SubtleParallax(DepthScene)" in source
    assert "def update(self)" in source
    assert "self.state.offset" in source
    assert "self.cycle" in source


# --- FFmpeg LUT path escaping ----------------------------------------------


def test_ffmpeg_mux_escapes_windows_drive_letter_colon(tmp_path: Path, monkeypatch) -> None:
    """Regression test: ffmpeg's filtergraph parser treats ':' as a
    key=value separator, which collides with a Windows drive letter
    (C:\\...) unless escaped -- found while researching the real lut3d
    filter syntax, not from a bug report."""
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-graded-video")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake-video")
    # Simulate a Windows-style absolute path regardless of host OS.
    lut = type(video)("C:/luts/architectural.cube")

    ffmpeg_mux(video_path=video, audio_path=None, output_path=tmp_path / "out.mp4", lut_path=lut)

    vf_arg = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf_arg == "lut3d=C\\:/luts/architectural.cube"
    assert ":" not in vf_arg.replace("\\:", "")  # no unescaped colon remains


def test_ffmpeg_mux_without_lut_has_no_vf_flag(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-video")

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake-video")
    ffmpeg_mux(video_path=video, audio_path=None, output_path=tmp_path / "out.mp4")

    assert "-vf" not in captured["cmd"]
