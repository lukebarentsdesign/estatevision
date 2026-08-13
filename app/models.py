"""Core data models (spec §3).

Compliance note: `PropertyJob.price_guide` is CRM/microsite only. It must never
reach a script-generation prompt. That is enforced structurally by
`services.script_prompt.ScriptJobContext` -- see §1.2 and §9 Phase A.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, Relationship, SQLModel


class FeatureLevel(str, Enum):
    STANDARD = "standard"
    PLUS = "plus"
    CINEMATIC = "cinematic"
    CUSTOM = "custom"


class JobStatus(str, Enum):
    INGESTION = "ingestion"
    PROCESSING = "processing"
    REVIEW = "review"
    COMPLETED = "completed"


class AgentProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agency_name: str

    # Login identity (spec: agency/admin auth design, 2026-08-13). One shared
    # login per agency -- not per staff member.
    email: Optional[str] = Field(default=None, index=True, unique=True)
    password_hash: str = Field(default="")
    is_active: bool = True

    primary_color: str = "#111827"
    secondary_color: str = "#6b7280"
    logo_path: Optional[str] = None
    staff_name: Optional[str] = None
    staff_headshot_path: Optional[str] = None

    # Avatar pipeline. HeyGen runs its own identity verification, so the
    # presence of an ID is itself the consent record (§1.3).
    heygen_avatar_id: Optional[str] = None

    # Voice-only pipeline. ElevenLabs consent is separate and NOT covered by
    # HeyGen verification, so this ID is gated on `voice_consent_confirmed`
    # by `services.consent` -- never set it directly.
    elevenlabs_voice_id: Optional[str] = None
    voice_consent_confirmed: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    jobs: List["PropertyJob"] = Relationship(back_populates="agent")


class PropertyJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agentprofile.id")

    address: str
    postcode: str

    # CRM & microsite ONLY. Never passed into script generation, voiceover,
    # avatar dialogue, or caption/lower-third text. See §1.2.
    price_guide: Optional[str] = None

    garden_orientation: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    feature_level: FeatureLevel = FeatureLevel.STANDARD
    use_avatar: bool = False
    status: JobStatus = JobStatus.INGESTION

    pdf_brochure_path: Optional[str] = None
    raw_photos_dir: Optional[str] = None

    script_json: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    location_data_json: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    agent: Optional[AgentProfile] = Relationship(back_populates="jobs")
    photos: List["Photo"] = Relationship(back_populates="job")


class Photo(SQLModel, table=True):
    """A single source photo and the per-shot decisions made about it.

    `sky_replaced` is the trigger for the mandatory §1.4 disclosure badge. It is
    set by the sky-replacement step and read by the render layer; no step may
    clear it.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="propertyjob.id")

    source_path: str
    processed_path: Optional[str] = None
    clip_path: Optional[str] = None

    is_hero: bool = False
    sky_replaced: bool = False
    order_index: int = 0

    job: Optional[PropertyJob] = Relationship(back_populates="photos")


class ScriptSegment(SQLModel, table=True):
    """One sentence of narration, paired with the one photo that illustrates
    it and the one audio clip that voices it (spec: sentence-photo linking
    design, 2026-08-12).

    `photo_id` and `audio_path` start null and are filled in during the
    arrange step / generate step respectively. A `Photo` may be referenced by
    more than one `ScriptSegment` -- reuse across sentences is allowed by
    design, so there is no uniqueness constraint on `photo_id`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="propertyjob.id")

    order_index: int = 0
    text: str
    is_intro: bool = False

    photo_id: Optional[int] = Field(default=None, foreign_key="photo.id")
    audio_path: Optional[str] = None
    duration_sec: Optional[float] = None

    job: Optional[PropertyJob] = Relationship()
    photo: Optional[Photo] = Relationship()


class IntegrationCredential(SQLModel, table=True):
    """One field's stored value for one integration (see `services.integration_registry`).

    `value_ciphertext` is always encrypted at rest via `services.secrets_store`
    -- nothing in this table is ever plaintext on disk, secret or not, to keep
    the storage path uniform regardless of field kind.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    integration_slug: str = Field(index=True)
    field_key: str
    value_ciphertext: bytes
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("integration_slug", "field_key"),)


class ActiveVendorChoice(SQLModel, table=True):
    """Which registered vendor is active for a given category_key.

    Absence of a row means "use the first-registered vendor in that
    category" -- see `services.active_vendor.get_active_vendor`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    category_key: str = Field(index=True, unique=True)
    vendor_slug: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminAccount(SQLModel, table=True):
    """The platform owner's login (spec: agency/admin auth design,
    2026-08-13). Modeled as a table rather than an env-var credential so it
    can be changed without a redeploy; a single row is expected in practice.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
