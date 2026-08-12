from __future__ import annotations

import pytest

from app.services.script_prompt import ScriptJobContext, ScriptVariant, build_prompt


@pytest.fixture
def context() -> ScriptJobContext:
    return ScriptJobContext.from_fields(
        address="5 Wardington Crescent",
        postcode="SW1A 2AA",
        garden_orientation="South-West",
        agency_name="Test Agency",
        staff_name="James",
        brochure_sentences=[
            "A detached four bedroom family home set back from the road.",
            "The kitchen has been extended to the rear with bi-fold doors.",
            "The garden is mainly laid to lawn with a paved terrace.",
        ],
    )


def test_segmented_walkthrough_variant_exists() -> None:
    assert ScriptVariant.SEGMENTED_WALKTHROUGH == "segmented_walkthrough"


def test_segmented_walkthrough_prompt_asks_for_json_list(context) -> None:
    prompt = build_prompt(context, ScriptVariant.SEGMENTED_WALKTHROUGH)
    assert "JSON" in prompt
    assert "5" in prompt and "10" in prompt  # sentence count range mentioned
    assert "is_intro" in prompt
    assert "SOURCE SENTENCES" in prompt


def test_segmented_walkthrough_prompt_is_price_free(context) -> None:
    # build_prompt already runs assert_price_free internally; this just
    # confirms it doesn't raise for a clean context.
    prompt = build_prompt(context, ScriptVariant.SEGMENTED_WALKTHROUGH)
    assert "price" not in prompt.lower().split("absolute rules")[0]  # sanity smoke check


def test_existing_walkthrough_variant_unchanged(context) -> None:
    prompt = build_prompt(context, ScriptVariant.WALKTHROUGH)
    assert "roughly 60 seconds" in prompt
