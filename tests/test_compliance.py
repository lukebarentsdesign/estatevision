"""Tests for the §1 compliance layer.

These are the tests referenced in spec §9 Phase A item 2. They are not
decoration: if any of them fails, the build has a regulatory defect.
"""

from __future__ import annotations

import pytest

from app.models import AgentProfile, FeatureLevel, Photo, PropertyJob
from app.services import consent
from app.services.compliance import (
    AI_DISCLOSURE_BADGE_TEXT,
    ComplianceError,
    assert_price_free,
    find_price_mentions,
)
from app.services.render_contract import CaptionCue, build_render_props
from app.services.script_prompt import (
    JOB_FIELD_WHITELIST,
    ScriptJobContext,
    ScriptVariant,
    assert_whitelist_integrity,
    build_prompt,
    sanitize_source_sentences,
)

PRICE_SENTINEL = "£875,000"

BROCHURE = [
    "A detached four bedroom family home set back from the road.",
    "The kitchen has been extended to the rear with bi-fold doors.",
    "The garden is mainly laid to lawn with a paved terrace.",
    f"Guide price {PRICE_SENTINEL} for the freehold.",
]


@pytest.fixture
def agent() -> AgentProfile:
    return AgentProfile(id=1, agency_name="Thornes", staff_name="Luke")


@pytest.fixture
def job() -> PropertyJob:
    return PropertyJob(
        id=1,
        agent_id=1,
        address="32 Tregonwell Road",
        postcode="BH2 5NT",
        price_guide=PRICE_SENTINEL,
        garden_orientation="South-West",
        feature_level=FeatureLevel.CINEMATIC,
    )


# --- §1.2 price exclusion -------------------------------------------------


def test_context_dto_has_no_price_field() -> None:
    """The DTO must be structurally incapable of carrying a price."""
    assert "price_guide" not in ScriptJobContext.__dataclass_fields__


def test_whitelist_excludes_price() -> None:
    assert "price_guide" not in JOB_FIELD_WHITELIST
    assert_whitelist_integrity()


def test_context_from_job_drops_price(job: PropertyJob, agent: AgentProfile) -> None:
    ctx = ScriptJobContext.from_job(
        job,
        agency_name=agent.agency_name,
        staff_name=agent.staff_name,
        brochure_sentences=sanitize_source_sentences(BROCHURE),
    )
    assert not hasattr(ctx, "price_guide")
    assert PRICE_SENTINEL not in repr(ctx)


def test_context_is_frozen(job: PropertyJob, agent: AgentProfile) -> None:
    """Nothing may bolt a price onto the context after construction."""
    ctx = ScriptJobContext.from_job(
        job,
        agency_name=agent.agency_name,
        staff_name=agent.staff_name,
        brochure_sentences=sanitize_source_sentences(BROCHURE),
    )
    with pytest.raises(Exception):
        ctx.price_guide = PRICE_SENTINEL  # type: ignore[misc]


@pytest.mark.parametrize("variant", list(ScriptVariant))
def test_no_prompt_variant_leaks_price(
    job: PropertyJob, agent: AgentProfile, variant: ScriptVariant
) -> None:
    """Every prompt variant must be free of the price sentinel (§1.2)."""
    ctx = ScriptJobContext.from_job(
        job,
        agency_name=agent.agency_name,
        staff_name=agent.staff_name,
        brochure_sentences=sanitize_source_sentences(BROCHURE),
    )
    prompt = build_prompt(ctx, variant)

    assert PRICE_SENTINEL not in prompt
    assert "875" not in prompt

    # The fixed rule block necessarily names the forbidden terms in order to
    # forbid them, so scan only the property-specific payload that follows it.
    _, _, payload = prompt.partition("PROPERTY LOCATION:")
    assert not find_price_mentions(payload)


def test_price_bearing_brochure_sentence_is_stripped() -> None:
    """A price line lifted from the PDF must never reach the model."""
    cleaned = sanitize_source_sentences(BROCHURE)
    assert len(cleaned) == 3
    assert all(PRICE_SENTINEL not in s for s in cleaned)


def test_prompt_build_refuses_unsanitized_price_sentence(
    job: PropertyJob, agent: AgentProfile
) -> None:
    """If sanitization is skipped, prompt assembly must fail loudly."""
    ctx = ScriptJobContext.from_job(
        job,
        agency_name=agent.agency_name,
        staff_name=agent.staff_name,
        brochure_sentences=BROCHURE,  # deliberately not sanitized
    )
    with pytest.raises(ComplianceError):
        build_prompt(ctx, ScriptVariant.WALKTHROUGH)


@pytest.mark.parametrize(
    "text",
    [
        "Guide price £875,000",
        "Offers over 800,000",
        "OIRO £1,200,000",
        "Priced at 450k",
        "asking price on application",
        "$1,000,000",
        "€750,000",
    ],
)
def test_price_regex_catches_common_phrasings(text: str) -> None:
    assert find_price_mentions(text), f"failed to detect price in {text!r}"


def test_assert_price_free_passes_clean_narration() -> None:
    assert_price_free(
        "Welcome to this four bedroom home with a south-west facing garden.",
        context="test",
    )


# --- §1.4 AI disclosure badge --------------------------------------------


def test_sky_replaced_photo_always_gets_badge(job: PropertyJob, agent: AgentProfile) -> None:
    photos = [
        Photo(id=1, job_id=1, source_path="a.jpg", order_index=0, sky_replaced=True),
        Photo(id=2, job_id=1, source_path="b.jpg", order_index=1, sky_replaced=False),
    ]
    props = build_render_props(
        composition="Master16x9",
        job=job,
        agent=agent,
        photos=photos,
        clip_durations={1: 4.0, 2: 4.0},
    )
    assert props.clips[0].disclosure_badge == AI_DISCLOSURE_BADGE_TEXT
    assert props.clips[1].disclosure_badge is None


def test_badge_cannot_be_suppressed_by_caller() -> None:
    """There is no caller-facing switch to turn the badge off (§1.4)."""
    import inspect

    params = inspect.signature(build_render_props).parameters
    assert not any("badge" in p or "disclosure" in p for p in params)


def test_render_props_reject_price_in_captions(job: PropertyJob, agent: AgentProfile) -> None:
    photos = [Photo(id=1, job_id=1, source_path="a.jpg", order_index=0)]
    with pytest.raises(ComplianceError):
        build_render_props(
            composition="Reel9x16",
            job=job,
            agent=agent,
            photos=photos,
            clip_durations={1: 4.0},
            captions=[CaptionCue(text=f"Guide price {PRICE_SENTINEL}", start_sec=0, end_sec=1)],
        )


def test_render_props_reject_price_in_lower_third(job: PropertyJob, agent: AgentProfile) -> None:
    photos = [Photo(id=1, job_id=1, source_path="a.jpg", order_index=0)]
    with pytest.raises(ComplianceError):
        build_render_props(
            composition="Reel9x16",
            job=job,
            agent=agent,
            photos=photos,
            clip_durations={1: 4.0},
            lower_third=f"32 Tregonwell Road — {PRICE_SENTINEL}",
        )


# --- §1.3 consent ---------------------------------------------------------


def test_voice_id_requires_consent(agent: AgentProfile) -> None:
    with pytest.raises(consent.ConsentError):
        consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=False)
    assert agent.elevenlabs_voice_id is None


def test_voice_id_stored_with_consent(agent: AgentProfile) -> None:
    consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    assert agent.elevenlabs_voice_id == "voice_abc"
    assert agent.voice_consent_confirmed is True


def test_clearing_voice_resets_consent(agent: AgentProfile) -> None:
    consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    consent.set_elevenlabs_voice(agent, None, consent_confirmed=True)
    assert agent.elevenlabs_voice_id is None
    assert agent.voice_consent_confirmed is False


def test_narration_refused_without_consented_voice(agent: AgentProfile) -> None:
    agent.elevenlabs_voice_id = "sneaked_in"
    agent.voice_consent_confirmed = False
    with pytest.raises(consent.ConsentError):
        consent.require_voice_for_narration(agent)


def test_avatar_refused_without_heygen_id(agent: AgentProfile) -> None:
    with pytest.raises(consent.ConsentError):
        consent.require_avatar(agent)
