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
    source: Path, boundaries: list[AudioBoundary], *, out_dir: Path, stem: str
) -> list[Path]:
    """Slice `source` into one file per boundary, named `{stem}_{i}.mp3`.

    Boundaries are given in seconds and must be non-empty.
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

        dest = out_dir / f"{stem}_{i}.wav"
        clip.export(dest, format="wav")
        paths.append(dest)

    return paths
