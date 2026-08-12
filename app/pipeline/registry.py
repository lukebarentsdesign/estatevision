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
    # AvatarIntroStep must be declared (and therefore topologically sorted)
    # before RemotionAssemblyStep: for a segmented avatar-on job,
    # RemotionAssemblyStep reads ctx.artifacts["avatar_intro"]["avatar_clip_path"]
    # (see ScriptAndVoiceStep._run_segmented / RemotionAssemblyStep._run_segmented).
    # There is deliberately no `requires` edge for this -- AvatarIntroStep only
    # applies to avatar jobs, and adding "avatar_intro" to RemotionAssemblyStep's
    # `requires` would make it hard-skip every non-avatar job (whose
    # ctx.artifacts never gets an "avatar_intro" key, since that step's
    # `applies_to` is false and it never runs at all). Declaration order in
    # this tuple is what `_toposort` (contract.py) uses to break ties between
    # steps with no dependency relationship, so this ordering alone is
    # sufficient and correct.
    AvatarIntroStep,
    RemotionAssemblyStep,
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
        # Sentence-photo linking's segmented flow (app.pipeline.steps.script_
        # and_voice.ScriptAndVoiceStep._run_segmented) needs an ElevenLabs
        # voice even for avatar jobs: only the intro segment's audio comes
        # from HeyGen, every other segment is still voiced by ElevenLabs.
        # Populate it opportunistically -- an agent with `ScriptSegment` rows
        # but no consented voice yet will simply have this key absent, and
        # ScriptAndVoiceStep's own RuntimeError guard catches that case if
        # there are non-intro segments to voice; jobs with no ScriptSegment
        # rows at all (the legacy path) never read this key, so making it
        # optional here doesn't change legacy behavior.
        if agent.elevenlabs_voice_id and agent.voice_consent_confirmed:
            agent_snapshot["elevenlabs_voice_id"] = agent.elevenlabs_voice_id
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
