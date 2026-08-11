"""Pipeline composition and job snapshot construction (§9 Phase A + Phase B glue).

`build_job_snapshot` is the ONLY place that reads `AgentProfile`/`PropertyJob`
ORM rows and turns them into the plain-dict `job_snapshot` steps consume. This
is where the consent gates (§1.3) are enforced -- a job snapshot is simply not
constructable for an avatar/voice job whose agent lacks consent, so no step
downstream needs to re-check it.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from ..models import AgentProfile, PropertyJob
from ..services import consent
from .contract import PipelineRunner, PipelineStep
from .steps.assembly import RemotionAssemblyStep
from .steps.avatar_and_captions import AvatarIntroStep, CaptionTimingStep
from .steps.cinematic import (
    AerialFlyoverStep,
    LutAndGrainStep,
    MicrositeBuilderStep,
    MusicDuckingStep,
    SkyReplacementStep,
)
from .steps.ingestion import IngestBrochureStep
from .steps.motion import MotionPassStep
from .steps.restoration import RestorationPassStep
from .steps.script_and_voice import ScriptAndVoiceStep

ALL_STEPS: tuple[type[PipelineStep], ...] = (
    IngestBrochureStep,
    RestorationPassStep,
    MotionPassStep,
    ScriptAndVoiceStep,
    RemotionAssemblyStep,
    AvatarIntroStep,
    CaptionTimingStep,
    SkyReplacementStep,
    MusicDuckingStep,
    LutAndGrainStep,
    AerialFlyoverStep,
    MicrositeBuilderStep,
)


def build_runner() -> PipelineRunner:
    """A runner wired with every known step; `applies_to` filters per job."""
    return PipelineRunner(step_cls() for step_cls in ALL_STEPS)


def build_job_snapshot(session: Session, job: PropertyJob) -> dict[str, Any]:
    """Build the read-only snapshot steps operate on.

    Enforces consent (§1.3) at construction time: if the job needs a voice or
    avatar the agent hasn't consented to, this raises before any step runs.
    """
    agent = session.get(AgentProfile, job.agent_id)
    if agent is None:
        raise ValueError(f"PropertyJob {job.id} has no agent {job.agent_id}")

    agent_snapshot: dict[str, Any] = {
        "id": agent.id,
        "agency_name": agent.agency_name,
        "primary_color": agent.primary_color,
        "secondary_color": agent.secondary_color,
        "logo_path": agent.logo_path,
        "staff_name": agent.staff_name,
        "staff_headshot_path": agent.staff_headshot_path,
    }

    if job.use_avatar:
        agent_snapshot["heygen_avatar_id"] = consent.require_avatar(agent)
    else:
        agent_snapshot["elevenlabs_voice_id"] = consent.require_voice_for_narration(agent)

    photos_snapshot = [
        {
            "id": photo.id,
            "job_id": photo.job_id,
            "source_path": photo.source_path,
            "processed_path": photo.processed_path,
            "is_hero": photo.is_hero,
            "sky_replaced": photo.sky_replaced,
            "order_index": photo.order_index,
        }
        for photo in job.photos
    ]

    return {
        "id": job.id,
        "address": job.address,
        "postcode": job.postcode,
        "garden_orientation": job.garden_orientation,
        "latitude": job.latitude,
        "longitude": job.longitude,
        "feature_level": job.feature_level.value if hasattr(job.feature_level, "value") else job.feature_level,
        "pdf_brochure_path": job.pdf_brochure_path,
        "raw_photos_dir": job.raw_photos_dir,
        "location_data": job.location_data_json,
        "photos": photos_snapshot,
        "agent": agent_snapshot,
        # Deliberately NOT whitelisted for script/voice/avatar prompts (§1.2) --
        # present here only because `microsite_builder` legitimately needs it.
        "price_guide": job.price_guide,
    }
