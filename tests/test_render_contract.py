from __future__ import annotations

import pytest

from app.models import AgentProfile, Photo, PropertyJob
from app.services.render_contract import CaptionCue, Segment, build_segmented_render_props


def test_build_segmented_render_props_one_segment_per_input() -> None:
    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")
    photo = Photo(id=1, job_id=1, source_path="/photos/kitchen.jpg", processed_path="/photos/kitchen_p.jpg")

    segments_input = [
        Segment(
            text="Hi there.",
            visual_path="/avatar/intro.mp4",
            audio_path=None,  # avatar clip carries its own audio
            duration_sec=3.2,
            captions=(),
            is_avatar=True,
            disclosure_badge=None,
        ),
        Segment(
            text="The kitchen is bright.",
            visual_path=photo.clip_path or photo.processed_path or photo.source_path,
            audio_path="/audio/segment_1.mp3",
            duration_sec=2.8,
            captions=(),
            is_avatar=False,
            disclosure_badge=None,
        ),
    ]

    props = build_segmented_render_props(
        composition="Master16x9",
        job=job,
        agent=agent,
        segments=segments_input,
    )

    assert len(props.segments) == 2
    assert props.segments[0].is_avatar is True
    assert props.segments[1].duration_sec == 2.8
    assert props.composition == "Master16x9"


def test_build_segmented_render_props_rejects_price_in_captions() -> None:
    import pytest
    from app.models import AgentProfile, PropertyJob
    from app.services.render_contract import CaptionCue, Segment, build_segmented_render_props

    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    segments_input = [
        Segment(
            text="Yours for £400,000.",
            visual_path="/photos/x.jpg",
            audio_path="/audio/x.mp3",
            duration_sec=2.0,
            captions=(CaptionCue(text="£400,000", start_sec=0.0, end_sec=1.0),),
            is_avatar=False,
            disclosure_badge=None,
        )
    ]

    with pytest.raises(Exception):  # ComplianceError or whatever assert_price_free raises
        build_segmented_render_props(composition="Master16x9", job=job, agent=agent, segments=segments_input)


def test_build_segmented_render_props_unknown_composition_raises() -> None:
    import pytest
    from app.models import AgentProfile, PropertyJob
    from app.services.render_contract import build_segmented_render_props

    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    with pytest.raises(ValueError):
        build_segmented_render_props(composition="NotReal", job=job, agent=agent, segments=[])


def test_build_segmented_render_props_rejects_empty_segments() -> None:
    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    with pytest.raises(ValueError):
        build_segmented_render_props(composition="Master16x9", job=job, agent=agent, segments=[])


def test_build_segmented_render_props_rejects_non_positive_duration() -> None:
    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    segments_input = [
        Segment(
            text="The kitchen is bright.",
            visual_path="/photos/kitchen.jpg",
            audio_path="/audio/segment_0.mp3",
            duration_sec=0.0,
            captions=(),
            is_avatar=False,
            disclosure_badge=None,
        )
    ]

    with pytest.raises(ValueError):
        build_segmented_render_props(composition="Master16x9", job=job, agent=agent, segments=segments_input)


def test_build_segmented_render_props_rejects_caption_exceeding_segment_duration() -> None:
    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    segments_input = [
        Segment(
            text="The kitchen is bright.",
            visual_path="/photos/kitchen.jpg",
            audio_path="/audio/segment_0.mp3",
            duration_sec=2.0,
            captions=(CaptionCue(text="bright", start_sec=1.5, end_sec=2.5),),  # 2.5 > 2.0
            is_avatar=False,
            disclosure_badge=None,
        )
    ]

    with pytest.raises(ValueError):
        build_segmented_render_props(composition="Master16x9", job=job, agent=agent, segments=segments_input)
