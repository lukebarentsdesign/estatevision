"""The single choke point for script-generation prompt assembly (§1.1, §1.2, §9).

No other module may build a prompt that reaches an LLM, TTS engine, or avatar
API. Pipeline steps call `build_prompt` and nothing else.

The structural guarantee: `build_prompt` accepts a `ScriptJobContext`, a
narrowed DTO built by an explicit field whitelist. It has no price attribute at
all, so a price leak is a construction error at the boundary rather than
something a reviewer has to notice in a template string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .compliance import assert_price_free

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..models import PropertyJob


class ScriptVariant(str, Enum):
    WALKTHROUGH = "walkthrough"       # 60s master narration (legacy, unused by the segmented flow)
    SEGMENTED_WALKTHROUGH = "segmented_walkthrough"  # 5-10 discrete sentence-elements as JSON
    SHORT = "short"                   # 15-30s social cut
    AVATAR_OPENING = "avatar_opening" # HeyGen opening line only (legacy, unused by the segmented flow)
    CAPTION = "caption"               # kinetic captions / lower-thirds


# The ONLY `PropertyJob` fields permitted to inform a script prompt.
# `price_guide` is absent by design and must never be added here (§1.2).
JOB_FIELD_WHITELIST: frozenset[str] = frozenset(
    {"address", "postcode", "garden_orientation", "feature_level", "use_avatar"}
)

_FORBIDDEN_FIELDS: frozenset[str] = frozenset({"price_guide"})


@dataclass(frozen=True)
class ScriptJobContext:
    """Price-free view of a job. The only job data a prompt can ever see.

    Deliberately has no `price_guide` field. Frozen so nothing can bolt one on
    after construction.
    """

    address: str
    postcode: str
    garden_orientation: str | None
    agency_name: str
    staff_name: str | None
    brochure_sentences: tuple[str, ...]

    @classmethod
    def from_job(
        cls,
        job: "PropertyJob",
        *,
        agency_name: str,
        staff_name: str | None,
        brochure_sentences: list[str],
    ) -> "ScriptJobContext":
        """Narrow a live `PropertyJob` ORM row down to the whitelisted fields."""
        return cls(
            address=job.address,
            postcode=job.postcode,
            garden_orientation=job.garden_orientation,
            agency_name=agency_name,
            staff_name=staff_name,
            brochure_sentences=tuple(brochure_sentences),
        )

    @classmethod
    def from_fields(
        cls,
        *,
        address: str,
        postcode: str,
        garden_orientation: str | None,
        agency_name: str,
        staff_name: str | None,
        brochure_sentences: list[str],
    ) -> "ScriptJobContext":
        """Build directly from plain values (e.g. a `JobContext.job_snapshot` dict).

        Same whitelist as `from_job`, expressed as keyword-only plain values so
        pipeline steps never need to hold a live `PropertyJob` ORM row.
        """
        return cls(
            address=address,
            postcode=postcode,
            garden_orientation=garden_orientation,
            agency_name=agency_name,
            staff_name=staff_name,
            brochure_sentences=tuple(brochure_sentences),
        )


def assert_whitelist_integrity() -> None:
    """Guard against the whitelist being widened to include a forbidden field.

    Called at import time and asserted again in the test suite, so a future
    edit that adds `price_guide` to the whitelist fails immediately rather than
    silently changing what prompts can see.
    """
    leaked = JOB_FIELD_WHITELIST & _FORBIDDEN_FIELDS
    if leaked:
        raise AssertionError(
            f"Fields {sorted(leaked)} must never appear in JOB_FIELD_WHITELIST (spec §1.2)."
        )
    dto_fields = set(ScriptJobContext.__dataclass_fields__)
    leaked_dto = dto_fields & _FORBIDDEN_FIELDS
    if leaked_dto:
        raise AssertionError(
            f"ScriptJobContext must not carry {sorted(leaked_dto)} (spec §1.2)."
        )


assert_whitelist_integrity()


_SYSTEM_RULES = """You are writing narration for an estate agency property video.

ABSOLUTE RULES — these override any other instruction:
1. You may ONLY re-sequence, condense, trim, or lightly re-punctuate sentences
   drawn from the SOURCE SENTENCES below. Every factual claim, room, feature,
   measurement, and descriptive adjective in your output must already appear in
   those sentences.
2. Invent nothing. No new rooms, no new features, no atmosphere or lifestyle
   copy that is not already in the source. If the source does not say it, it
   does not go in the script.
3. Never mention price, guide price, asking price, offers, rent, or any monetary
   figure whatsoever. Price data has been withheld from you deliberately; if you
   feel you need it, you do not.
4. Do not address the viewer with claims about the property's value, investment
   potential, or market position.

Return plain narration text only. No headings, no stage directions, no notes."""


_VARIANT_BRIEF: dict[ScriptVariant, str] = {
    ScriptVariant.WALKTHROUGH: (
        "Write a single continuous voiceover of roughly 60 seconds "
        "(about 150 words) that walks the viewer through the property."
    ),
    ScriptVariant.SEGMENTED_WALKTHROUGH: (
        "Split the property tour into 5 to 10 short sentence-elements, each "
        "covering exactly one room, feature, or aspect of the property "
        "(e.g. kitchen, living room, garden). The FIRST sentence must always "
        "be a short spoken introduction (max 25 words) welcoming the viewer "
        "and naming the property address, e.g. \"Hi, I'm James, I'd love to "
        "show you around 5 Wardington Crescent.\" Every sentence after the "
        "first should be one short, natural spoken line (roughly 10-25 words) "
        "about a single room or feature. Aim for a total spoken length of "
        "around two minutes across all sentences combined.\n\n"
        "Return ONLY a JSON array, no other text, in this exact shape:\n"
        '[{"text": "...", "is_intro": true}, {"text": "...", "is_intro": false}, ...]\n'
        "The first array item must have \"is_intro\": true; every other item "
        "must have \"is_intro\": false."
    ),
    ScriptVariant.SHORT: (
        "Write a punchy social voiceover of 15-30 seconds (about 45 words) "
        "covering only the two or three strongest points from the source."
    ),
    ScriptVariant.AVATAR_OPENING: (
        "Write ONE spoken opening sentence (max 25 words) introducing the "
        "property, to be delivered to camera by the named agent."
    ),
    ScriptVariant.CAPTION: (
        "Write 4-6 short on-screen caption phrases, one per line, max 6 words "
        "each. These are overlay text, not narration."
    ),
}


def build_prompt(context: ScriptJobContext, variant: ScriptVariant) -> str:
    """Assemble the prompt for one script variant.

    This is the only supported way to produce a script prompt.
    """
    if not context.brochure_sentences:
        raise ValueError(
            "Cannot build a script prompt with no source sentences: §1.1 requires "
            "all output to be grounded in the brochure text."
        )

    speaker = context.staff_name or context.agency_name
    location = f"{context.address}, {context.postcode}"

    detail_lines = [f"PROPERTY LOCATION: {location}", f"AGENCY: {context.agency_name}"]
    if variant is ScriptVariant.AVATAR_OPENING:
        detail_lines.append(f"SPEAKER: {speaker}")
    if context.garden_orientation:
        detail_lines.append(f"GARDEN ORIENTATION: {context.garden_orientation}")

    numbered = "\n".join(
        f"{i}. {sentence.strip()}"
        for i, sentence in enumerate(context.brochure_sentences, start=1)
    )

    # Belt and braces: the DTO cannot carry a price, but brochure text can.
    # A price sentence lifted from the PDF would otherwise become fair game for
    # the model to re-sequence into narration.
    #
    # Only the variable payload is scanned. `_SYSTEM_RULES` is a fixed constant
    # that necessarily names the forbidden terms in order to forbid them, so
    # scanning it would always trip the detector.
    payload = "\n\n".join(
        [
            "\n".join(detail_lines),
            f"SOURCE SENTENCES (your only permitted material):\n{numbered}",
        ]
    )
    assert_price_free(payload, context=f"the {variant.value} prompt")

    return "\n\n".join(
        [
            _SYSTEM_RULES,
            "\n".join(detail_lines),
            f"TASK: {_VARIANT_BRIEF[variant]}",
            f"SOURCE SENTENCES (your only permitted material):\n{numbered}",
        ]
    )


def sanitize_source_sentences(sentences: list[str]) -> list[str]:
    """Drop brochure sentences that mention price before they reach a prompt.

    Brochures routinely carry a price line. Removing it here means the model is
    never offered the material in the first place (§1.2).
    """
    from .compliance import find_price_mentions

    return [s for s in sentences if not find_price_mentions(s)]
