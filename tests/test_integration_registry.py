from __future__ import annotations

from app.services.integration_registry import list_integrations


def test_every_integration_has_a_category_key() -> None:
    for definition in list_integrations():
        assert definition.category_key, f"{definition.slug} has no category_key"


def test_avatar_and_tts_and_hero_shot_categories_have_expected_members() -> None:
    by_category: dict[str, list[str]] = {}
    for d in list_integrations():
        by_category.setdefault(d.category_key, []).append(d.slug)

    assert by_category["avatar"] == ["heygen"]
    assert by_category["tts"] == ["elevenlabs"]
    assert by_category["hero_shot_animation"] == ["gemini_omni"]
    assert by_category["script_generation"] == ["openai"]
    assert "aerial_flyover" not in by_category
    assert "schools_data" not in by_category
    assert "broadband_data" not in by_category


def test_openai_compatible_presets_exist() -> None:
    from app.services.integration_registry import OPENAI_COMPATIBLE_PRESETS

    names = {p["name"] for p in OPENAI_COMPATIBLE_PRESETS}
    assert "Groq" in names
    assert "OpenRouter" in names
    for preset in OPENAI_COMPATIBLE_PRESETS:
        assert preset["base_url"].startswith("https://")
