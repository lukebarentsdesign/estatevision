"""Continuous-synthesis-then-slice audio production for script segments.

Rationale (spec: sentence-photo linking design, 2026-08-12, §4): synthesizing
each segment as its own isolated ElevenLabs call makes the narration sound
like a list of disconnected sentences rather than one continuous tour, because
each call defaults to sentence-level intonation. Instead, ALL segment text is
sent to ElevenLabs as one concatenated take, preserving natural prosody
across the whole passage. WhisperX then force-aligns that single audio file's
words against the already-known segment texts (not free-form inference --
the exact texts and order are known going in) to find per-segment boundaries,
and the file is sliced at those boundaries into one clip per segment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from ..clients.audio_slicing import AudioBoundary, slice_audio_file
from ..clients.local_tools import WordTimestamp


class _SynthesizesSpeech(Protocol):
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        ...


def _find_segment_boundaries(
    segments_text: list[str], words: list[WordTimestamp]
) -> list[AudioBoundary]:
    """Walk the word-timing stream in lockstep with each segment's own word
    count to find that segment's [start_sec, end_sec) window.

    This is forced alignment, not inference: the segment texts and their
    word counts are already known, so this only needs to consume that many
    words off the front of the timing stream for each segment in turn.
    """
    boundaries: list[AudioBoundary] = []
    cursor = 0
    for text in segments_text:
        word_count = len(text.split())
        segment_words = words[cursor : cursor + word_count]
        if not segment_words:
            # WhisperX produced fewer aligned words than expected (e.g. it
            # dropped a word it couldn't align) -- fall back to the last
            # known timestamp so slicing doesn't crash on a short transcript.
            start = boundaries[-1].end_sec if boundaries else 0.0
            end = start
        else:
            start = segment_words[0].start_sec
            end = segment_words[-1].end_sec
        boundaries.append(AudioBoundary(start_sec=start, end_sec=end))
        cursor += word_count
    return boundaries


def synthesize_and_slice_segments(
    segments_text: list[str],
    *,
    voice_id: str,
    elevenlabs_client: _SynthesizesSpeech,
    whisperx_fn: Callable[..., list[WordTimestamp]],
    out_dir: Path,
    stem: str,
) -> list[Path]:
    """Synthesize `segments_text` as one continuous take, then slice it into
    one audio file per segment, in order. Returns one path per input segment.
    """
    if not segments_text:
        raise ValueError("segments_text must be non-empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    continuous_text = " ".join(segments_text)

    # WAV, not MP3: slice_audio_file loads this via pydub's AudioSegment.from_file,
    # which needs ffmpeg to decode MP3 but can read WAV via the pure-Python
    # stdlib `wave` module (see app/clients/audio_slicing.py's own docstring
    # for the same reasoning). ffmpeg is not guaranteed to be present in this
    # environment, so the continuous take is written as WAV to keep this
    # service usable without it.
    continuous_audio_path = out_dir / f"{stem}_continuous.wav"
    elevenlabs_client.synthesize(voice_id=voice_id, text=continuous_text, output_path=continuous_audio_path)

    words = whisperx_fn(continuous_audio_path, work_dir=out_dir)
    boundaries = _find_segment_boundaries(segments_text, words)

    return slice_audio_file(continuous_audio_path, boundaries, out_dir=out_dir, stem=stem)
