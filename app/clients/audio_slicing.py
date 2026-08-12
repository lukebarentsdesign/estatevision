"""Slice one continuous audio file into per-segment clips at known
boundaries.

Used by the sentence-photo linking workflow: narration is synthesized as one
continuous ElevenLabs take (for natural sentence-to-sentence intonation),
then sliced here at boundaries found by forced-aligning the already-known
segment texts against that audio via WhisperX. This module only does the
slicing -- alignment lives in `services.script_audio` (a later task).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment


@dataclass(frozen=True)
class AudioBoundary:
    start_sec: float
    end_sec: float


def slice_audio_file(
    source: Path, boundaries: list[AudioBoundary], *, out_dir: Path, stem: str, format: str = "wav"
) -> list[Path]:
    """Slice `source` into one file per boundary, named `{stem}_{i}.{format}`.

    Boundaries are given in seconds, must be non-empty, and are assumed
    valid and within the source audio's range -- the caller (WhisperX
    forced alignment, in a later task) is responsible for producing
    sane boundaries; this function does not validate `start_sec < end_sec`
    or range-check against the source's actual duration.

    Defaults to `"wav"` rather than `"mp3"` because `pydub`'s mp3 export
    shells out to `ffmpeg`, which is not guaranteed to be installed --
    WAV read/write needs no external binary. TODO: once this is wired into
    the real pipeline (a later task), prefer `format="mp3"` when
    `clients.local_tools.ffmpeg_available()` is true, since WAV is
    uncompressed and 5-10x larger than MP3 for narration-length clips.
    """
    if not boundaries:
        raise ValueError("boundaries must be non-empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    audio = AudioSegment.from_file(source)

    paths: list[Path] = []
    for i, boundary in enumerate(boundaries):
        start_ms = int(boundary.start_sec * 1000)
        end_ms = int(boundary.end_sec * 1000)
        clip = audio[start_ms:end_ms]

        dest = out_dir / f"{stem}_{i}.{format}"
        clip.export(dest, format=format)
        paths.append(dest)

    return paths
