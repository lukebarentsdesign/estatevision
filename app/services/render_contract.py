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
