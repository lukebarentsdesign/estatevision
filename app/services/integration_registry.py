"""Registry of external systems the app integrates with.

Single source of truth for the admin panel: what systems exist, what fields
each one needs (API key, base URL, extra tokens), and how to verify a
credential actually works. Adding a new integration (a new upscaler, a
different avatar vendor) means adding one entry here -- the admin UI, storage,
and client factories all key off this list rather than hardcoding fields
per-system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FieldKind(str, Enum):
    API_KEY = "api_key"       # secret, always masked
    TOKEN = "token"            # secret, always masked
    BASE_URL = "base_url"      # not secret, shown in plain text
    TEXT = "text"               # not secret, shown in plain text


@dataclass(frozen=True)
class CredentialField:
    key: str                    # storage key, e.g. "api_key"
    label: str                  # UI label, e.g. "API Key"
    kind: FieldKind
    required: bool = True
    placeholder: str = ""
    help_text: str = ""

    @property
    def is_secret(self) -> bool:
        return self.kind in (FieldKind.API_KEY, FieldKind.TOKEN)


class ConnectionTestMode(str, Enum):
    LIVE = "live"                # makes a real, cheap read-only API call
    FORMAT_ONLY = "format_only"  # no safe cheap endpoint exists; checks shape only
    NONE = "none"                 # nothing to test (e.g. a local CLI tool path)


@dataclass(frozen=True)
class IntegrationDefinition:
    slug: str                    # storage key, e.g. "heygen"
    name: str                    # display name, e.g. "HeyGen"
    category: str                # groups the admin UI list (display label)
    category_key: str            # stable machine id; multiple vendors can share one
    description: str
    fields: tuple[CredentialField, ...]
    docs_url: str = ""
    test_mode: ConnectionTestMode = ConnectionTestMode.FORMAT_ONLY
    used_by: tuple[str, ...] = field(default_factory=tuple)  # spec sections / pipeline steps


INTEGRATIONS: tuple[IntegrationDefinition, ...] = (
    IntegrationDefinition(
        slug="heygen",
        name="HeyGen",
        category="Avatar & Voice",
        category_key="avatar",
        description="Verified avatar clones for the avatar pipeline (§1.3, §4 plus).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, help_text="From HeyGen account settings."),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://api.heygen.com"),
        ),
        docs_url="https://docs.heygen.com",
        test_mode=ConnectionTestMode.LIVE,
        used_by=("avatar_intro pipeline step", "§1.3 avatar consent"),
    ),
    IntegrationDefinition(
        slug="elevenlabs",
        name="ElevenLabs",
        category="Avatar & Voice",
        category_key="tts",
        description="Voice cloning and TTS for voice-only narration jobs (§1.3, §4 standard).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, help_text="From ElevenLabs profile settings."),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://api.elevenlabs.io"),
        ),
        docs_url="https://elevenlabs.io/docs",
        test_mode=ConnectionTestMode.LIVE,
        used_by=("script_and_voice pipeline step",),
    ),
    IntegrationDefinition(
        slug="gemini_omni",
        name="Gemini Omni (Google)",
        category="Video & Image Generation",
        category_key="hero_shot_animation",
        description="Hero-shot subtle zoom/pan animation for the exterior photo (§4 standard).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, help_text="Google AI Studio / Vertex API key."),
        ),
        docs_url="https://ai.google.dev/",
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("motion_pass pipeline step",),
    ),
    IntegrationDefinition(
        slug="openai",
        name="OpenAI (or compatible)",
        category="Script Generation",
        category_key="script_generation",
        description="LLM used to write property scripts, strictly grounded in brochure text (§1.1, §1.2).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://api.openai.com/v1",
                             help_text="Change this to point at a different OpenAI-compatible provider."),
            CredentialField("model", "Model", FieldKind.TEXT, required=False, placeholder="gpt-4.1"),
        ),
        docs_url="https://platform.openai.com/docs",
        test_mode=ConnectionTestMode.LIVE,
        used_by=("script_and_voice pipeline step",),
    ),
    IntegrationDefinition(
        slug="google_3d_tiles",
        name="Google Photorealistic 3D Tiles",
        category="Video & Image Generation",
        category_key="aerial_flyover",
        description="Aerial orbiting flyover render for cinematic-level jobs (§4 cinematic).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY),
        ),
        docs_url="https://developers.google.com/maps/documentation/tile",
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("aerial_flyover pipeline step",),
    ),
    IntegrationDefinition(
        slug="schools_api",
        name="DfE / Ofsted Schools API",
        category="Location Data",
        category_key="schools_data",
        description="Nearest Outstanding/Good schools for the location insights panel (§5.1).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, required=False,
                             help_text="Leave blank if using the public endpoint without a key."),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://get-information-schools.service.gov.uk/api"),
        ),
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("services.uk_location.get_nearby_schools",),
    ),
    IntegrationDefinition(
        slug="ofcom_broadband",
        name="Ofcom Broadband Checker",
        category="Location Data",
        category_key="broadband_data",
        description="Broadband/mobile coverage for the location insights panel (§5.3).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, required=False),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://api.checker.ofcom.org.uk"),
        ),
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("services.uk_location.get_broadband_info",),
    ),
)

_BY_SLUG: dict[str, IntegrationDefinition] = {i.slug: i for i in INTEGRATIONS}


def get_integration(slug: str) -> IntegrationDefinition:
    try:
        return _BY_SLUG[slug]
    except KeyError:
        raise ValueError(f"Unknown integration {slug!r}") from None


def list_integrations() -> tuple[IntegrationDefinition, ...]:
    return INTEGRATIONS
