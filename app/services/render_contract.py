"""The Python -> Remotion boundary (§9 Phase A item 4).

Everything Remotion needs to render a composition is described here. Two
properties matter more than the rest:

* Disclosure badges are derived, not passed. `build_render_props` inspects the
  photos and sets the badge itself; there is no caller-supplied "show badge"
  flag to forget or switch off (§1.4).
* All narration and on-screen text is re-checked for price at this boundary,
  which is the last point before pixels and audio are produced (§1.2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import AgentProfile, Photo, PropertyJob
from .compliance import AI_DISCLOSURE_BADGE_TEXT, assert_price_free


@dataclass(frozen=True)
class Branding:
    agency_name: str
    primary_color: str
    secondary_color: str
    logo_path: str | None
    staff_name: str | None


@dataclass(frozen=True)
class CaptionCue:
    """A word- or phrase-level caption cue, timed by WhisperX."""

    text: str
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class Clip:
    """One shot in the timeline."""

    source_path: str
    duration_sec: float
    # Set by the render layer from `Photo.sky_replaced`. Never caller-supplied.
    disclosure_badge: str | None = None


@dataclass(frozen=True)
class Segment:
    """One self-contained (visual, audio, captions, duration) unit in a
    segmented timeline. Unlike the legacy `Clip`, a `Segment` carries its own
    audio and duration rather than sharing one global voiceover track --
    this is what structurally guarantees a photo is on screen for exactly as
    long as its matching sentence is spoken (spec: sentence-photo linking
    design, 2026-08-12, §5).
    """

    text: str
    visual_path: str          # a photo's clip_path, or an avatar clip's path when is_avatar
    audio_path: str | None    # None only when is_avatar (avatar clip carries its own audio)
    duration_sec: float
    captions: tuple[CaptionCue, ...]
    is_avatar: bool
    disclosure_badge: str | None


@dataclass(frozen=True)
class SegmentedRenderProps:
    """The segmented-timeline counterpart to `RenderProps`, used for jobs
    with `ScriptSegment` rows. Structurally distinct from `RenderProps`
    (rather than an optional field bolted onto it) so a caller cannot
    accidentally mix a shared `voiceover_path` with per-segment audio."""

    composition: str
    width: int
    height: int
    fps: int
    branding: Branding
    segments: tuple[Segment, ...]
    music_path: str | None = None
    music_duck_db: float = -14.0
    lower_third: str | None = None

    def to_props(self) -> dict[str, Any]:
        return asdict(self)


def build_segmented_render_props(
    *,
    composition: str,
    job: PropertyJob,
    agent: AgentProfile,
    segments: list[Segment],
    music_path: str | None = None,
    lower_third: str | None = None,
    fps: int = 30,
) -> SegmentedRenderProps:
    """Assemble segmented render props. Mirrors `build_render_props`'s
    compliance checkpoint: every segment's text and every caption cue is
    re-checked for price here, the last point before pixels/audio are
    produced (§1.2). Also the last point to catch malformed segment data
    (non-positive duration, an empty segment list, or a caption cue that
    outlives its own segment) before it reaches Remotion, where the same
    problem surfaces only as a silent blank/glitched render instead of a
    clear error here.
    """
    if composition not in ASPECTS:
        raise ValueError(f"Unknown composition {composition!r}; expected one of {sorted(ASPECTS)}")
    if not segments:
        raise ValueError("segments must be non-empty")

    for segment in segments:
        if segment.duration_sec <= 0:
            raise ValueError(
                f"segment {segment.text!r} has non-positive duration_sec={segment.duration_sec!r}"
            )
        assert_price_free(segment.text, context="a segment's narration text")
        for cue in segment.captions:
            if cue.end_sec > segment.duration_sec:
                raise ValueError(
                    f"caption cue {cue.text!r} (end_sec={cue.end_sec!r}) exceeds its "
                    f"segment's duration_sec={segment.duration_sec!r}"
                )
            assert_price_free(cue.text, context="a segment caption cue")
    if lower_third:
        assert_price_free(lower_third, context="the lower-third")

    width, height = ASPECTS[composition]
    return SegmentedRenderProps(
        composition=composition,
        width=width,
        height=height,
        fps=fps,
        branding=Branding(
            agency_name=agent.agency_name,
            primary_color=agent.primary_color,
            secondary_color=agent.secondary_color,
            logo_path=agent.logo_path,
            staff_name=agent.staff_name,
        ),
        segments=tuple(segments),
        music_path=music_path,
        lower_third=lower_third,
    )


@dataclass(frozen=True)
class RenderProps:
    """The complete prop payload handed to a Remotion composition."""

    composition: str            # e.g. "Master16x9", "Reel9x16"
    width: int
    height: int
    fps: int
    branding: Branding
    clips: tuple[Clip, ...]
    captions: tuple[CaptionCue, ...] = ()
    voiceover_path: str | None = None
    music_path: str | None = None
    music_duck_db: float = -14.0
    avatar_clip_path: str | None = None
    lower_third: str | None = None

    def to_props(self) -> dict[str, Any]:
        """Serialize for `remotion render --props`."""
        return asdict(self)


ASPECTS: dict[str, tuple[int, int]] = {
    "Master16x9": (3840, 2160),
    "Reel9x16": (1080, 1920),
}


def build_render_props(
    *,
    composition: str,
    job: PropertyJob,
    agent: AgentProfile,
    photos: list[Photo],
    clip_durations: dict[int, float],
    captions: list[CaptionCue] | None = None,
    voiceover_path: str | None = None,
    music_path: str | None = None,
    avatar_clip_path: str | None = None,
    lower_third: str | None = None,
    fps: int = 30,
) -> RenderProps:
    """Assemble render props, applying the §1.4 badge unconditionally.

    Any photo carrying `sky_replaced` gets the disclosure badge composited. The
    caller has no say in it -- that is the point.
    """
    if composition not in ASPECTS:
        raise ValueError(f"Unknown composition {composition!r}; expected one of {sorted(ASPECTS)}")

    clips: list[Clip] = []
    for photo in sorted(photos, key=lambda p: p.order_index):
        path = photo.clip_path or photo.processed_path or photo.source_path
        clips.append(
            Clip(
                source_path=path,
                duration_sec=clip_durations.get(photo.id, 4.0),
                # Mandatory, derived from the photo record (§1.4).
                disclosure_badge=AI_DISCLOSURE_BADGE_TEXT if photo.sky_replaced else None,
            )
        )

    caption_cues = tuple(captions or ())

    # Last checkpoint before render: nothing spoken or shown may carry price.
    for cue in caption_cues:
        assert_price_free(cue.text, context="a caption cue")
    if lower_third:
        assert_price_free(lower_third, context="the lower-third")

    width, height = ASPECTS[composition]
    return RenderProps(
        composition=composition,
        width=width,
        height=height,
        fps=fps,
        branding=Branding(
            agency_name=agent.agency_name,
            primary_color=agent.primary_color,
            secondary_color=agent.secondary_color,
            logo_path=agent.logo_path,
            staff_name=agent.staff_name,
        ),
        clips=tuple(clips),
        captions=caption_cues,
        voiceover_path=voiceover_path,
        music_path=music_path,
        avatar_clip_path=avatar_clip_path,
        lower_third=lower_third,
    )
