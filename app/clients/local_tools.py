"""Wrappers for local CLI/ML tools (spec §2, §4, §7).

Real-ESRGAN, Zero-DCE, DepthFlow, WhisperX, Demucs, and FFmpeg all run as local
subprocesses rather than hosted APIs. Each function here is stubbed to copy or
touch a placeholder file so the pipeline runs without the tools installed;
`LOCAL_TOOLS_AVAILABLE=1` switches to real subprocess calls once they are.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .base import write_placeholder_file


def _tools_available() -> bool:
    return os.environ.get("LOCAL_TOOLS_AVAILABLE") == "1"


class ToolExecutionError(RuntimeError):
    """Raised when a local CLI tool exits non-zero."""


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolExecutionError(f"{cmd[0]} failed: {result.stderr}")


def real_esrgan_upscale(source: Path, output_path: Path, *, model: str = "realesrgan-x4plus") -> Path:
    """4K upscale + JPEG artifact removal via the ncnn-vulkan portable binary.

    CLI shape confirmed against github.com/xinntao/Real-ESRGAN README.
    """
    if not _tools_available():
        return write_placeholder_file(output_path, f"[stub Real-ESRGAN]\nsource={source}\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["realesrgan-ncnn-vulkan", "-i", str(source), "-o", str(output_path), "-n", model])
    return output_path


def zero_dce_shadow_lift(source: Path, output_path: Path) -> Path:
    """Adaptive shadow lift.

    SCOPING NOTE (confirmed against github.com/Li-Chongyi/Zero-DCE): the
    reference implementation is not a usable CLI tool. Its inference script
    (`lowlight_test.py`) has no argparse despite importing it, hardcodes
    `.cuda()` with no CPU fallback, and hardcodes both the input directory
    (`data/test_data/`) and weights path (`snapshots/Epoch99.pth`) -- it only
    batch-processes a fixed folder, with no per-file input/output flags at
    all. There is no `zero-dce` console command; the `--input`/`--output`
    flags this function previously assumed don't exist anywhere in the
    project. Wiring this up for real means forking and rewriting their
    script into an actual CLI, not calling one -- a materially larger task
    than the other local-tool integrations here, so it stays stubbed
    regardless of `LOCAL_TOOLS_AVAILABLE` until that's worth doing. See the
    README for the same treatment given to the §4 `cinematic` aerial flyover.
    """
    return write_placeholder_file(output_path, f"[stub Zero-DCE -- see docstring]\nsource={source}\n")


def gray_world_white_balance(source: Path, output_path: Path) -> Path:
    """OpenCV Gray-World auto white-balance. Runs in-process, not a subprocess."""
    if not _tools_available():
        return write_placeholder_file(output_path, f"[stub white-balance]\nsource={source}\n")

    import cv2
    import numpy as np

    img = cv2.imread(str(source))
    if img is None:
        raise ToolExecutionError(f"could not read image {source}")
    result = img.astype(np.float32)
    for channel in range(3):
        mean = result[:, :, channel].mean()
        if mean > 0:
            result[:, :, channel] *= 128.0 / mean
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), np.clip(result, 0, 255).astype(np.uint8))
    return output_path


_DEPTHFLOW_SCENE_SCRIPT = Path(__file__).resolve().parents[1] / "depthflow_scenes" / "subtle_parallax.py"


def depthflow_parallax(source: Path, output_path: Path, *, duration_sec: float = 4.0) -> Path:
    """Depth-Anything-V2-based depth parallax motion for interior shots.

    DepthFlow's CLI has no built-in animation preset (confirmed against its
    own docs, which describe presets as "a future release"), so this invokes
    the bundled custom scene at `app/depthflow_scenes/subtle_parallax.py`
    rather than the bare `depthflow` command. Invocation pattern
    (`python script.py input -i ... main -o ... -t ...`) confirmed against
    DepthFlow's own `examples/presets.py`.
    """
    if not _tools_available():
        return write_placeholder_file(
            output_path, f"[stub DepthFlow]\nsource={source}\nduration={duration_sec}\n"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "python", str(_DEPTHFLOW_SCENE_SCRIPT),
            "input", "-i", str(source),
            "main", "-o", str(output_path), "-t", str(duration_sec),
        ]
    )
    return output_path


@dataclass(frozen=True)
class WordTimestamp:
    word: str
    start_sec: float
    end_sec: float


def whisperx_word_timestamps(audio_path: Path, *, work_dir: Path | None = None) -> list[WordTimestamp]:
    """Word-level timestamps for kinetic captions (spec §4 `plus`).

    Runs the WhisperX CLI, which writes a `{stem}.json` file alongside its
    `.srt`/`.vtt` output containing `segments[].words[]` with `word`/`start`/
    `end` fields. CLI invocation confirmed against github.com/m-bain/whisperX
    README; exact JSON field names are WhisperX's well-established, widely
    used output schema.
    """
    if not _tools_available():
        return [
            WordTimestamp("stub", 0.0, 0.5),
            WordTimestamp("timestamps", 0.5, 1.2),
        ]

    import json

    out_dir = work_dir or audio_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([
        "whisperx", str(audio_path),
        "--output_dir", str(out_dir),
        "--output_format", "json",
    ])

    json_path = out_dir / f"{audio_path.stem}.json"
    if not json_path.exists():
        raise ToolExecutionError(f"WhisperX did not produce expected output {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    words: list[WordTimestamp] = []
    for segment in data.get("segments", []):
        for w in segment.get("words", []):
            if "start" not in w or "end" not in w:
                continue  # WhisperX omits timing for words it couldn't align
            words.append(WordTimestamp(w["word"], w["start"], w["end"]))
    return words


def demucs_separate_music(source: Path, output_dir: Path, *, model: str = "htdemucs") -> Path:
    """Background-music stem separation for auto-ducking (spec §4 `cinematic`).

    `demucs --two-stems=vocals` writes `{output_dir}/{model}/{track_stem}/other.wav`
    for the non-vocal stem (confirmed against github.com/facebookresearch/demucs
    README). The previous version of this function returned a
    `{output_dir}/no_vocals.wav` path that Demucs never actually produces --
    caught while researching the real CLI output shape, not by a bug report.
    """
    track_stem = source.stem
    stem_path = output_dir / model / track_stem / "other.wav"

    if not _tools_available():
        return write_placeholder_file(stem_path, f"[stub Demucs stem]\nsource={source}\n")

    _run(["demucs", "--two-stems=vocals", "-n", model, "-o", str(output_dir), str(source)])
    if not stem_path.exists():
        raise ToolExecutionError(f"Demucs did not produce expected output {stem_path}")
    return stem_path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for embedding inside an ffmpeg filtergraph value.

    ffmpeg's filtergraph parser treats `:` as a key=value separator within a
    filter string, which collides with a Windows drive letter (`C:\\...`).
    Without escaping, `-vf lut3d=C:\\luts\\x.cube` is misparsed. The documented
    fix is backslash-escaping the colon; also normalize to forward slashes,
    which ffmpeg accepts on Windows and sidesteps backslash-escaping entirely.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def ffmpeg_mux(
    *,
    video_path: Path,
    audio_path: Path | None,
    output_path: Path,
    lut_path: Path | None = None,
) -> Path:
    """Multiplex video + audio, optionally applying a 3D LUT (spec §4 `cinematic`)."""
    if not _tools_available():
        return write_placeholder_file(
            output_path,
            f"[stub ffmpeg mux]\nvideo={video_path}\naudio={audio_path}\nlut={lut_path}\n",
        )

    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    if audio_path is not None:
        cmd += ["-i", str(audio_path)]
    if lut_path is not None:
        cmd += ["-vf", f"lut3d={_escape_ffmpeg_filter_path(lut_path)}"]
    cmd += [str(output_path)]
    _run(cmd)
    return output_path
