"""Voice-clone consent gate (§1.3).

ElevenLabs voice cloning has its own consent process, separate from and not
covered by HeyGen's identity verification. An `elevenlabs_voice_id` may only be
stored once explicit, specific consent for reusable AI cloning has been recorded
against the agent.

Enforced at the service layer, not just in the form, so an API client or a
future import script cannot bypass it.
"""

from __future__ import annotations

from ..models import AgentProfile, StaffMember


class ConsentError(Exception):
    """Raised when a voice clone is used or stored without recorded consent."""


def set_elevenlabs_voice(
    agent: AgentProfile,
    voice_id: str | None,
    *,
    consent_confirmed: bool,
) -> AgentProfile:
    """Attach an ElevenLabs voice ID, refusing without confirmed consent.

    Pass `voice_id=None` to clear the voice; that also clears the consent flag,
    so re-adding a voice later requires fresh confirmation.
    """
    if voice_id is None:
        agent.elevenlabs_voice_id = None
        agent.voice_consent_confirmed = False
        return agent

    if not consent_confirmed:
        raise ConsentError(
            "Cannot store an ElevenLabs voice ID without confirmed consent. "
            "Spec §1.3 requires explicit, specific consent for reusable AI voice "
            "cloning from the person being cloned."
        )

    agent.elevenlabs_voice_id = voice_id
    agent.voice_consent_confirmed = True
    return agent


def require_voice_for_narration(agent: AgentProfile) -> str:
    """Return the voice ID to narrate with, or refuse."""
    if not agent.elevenlabs_voice_id or not agent.voice_consent_confirmed:
        raise ConsentError(
            f"Agent {agent.id} has no consented ElevenLabs voice. "
            "Record consent (§1.3) before running a voice-only job."
        )
    return agent.elevenlabs_voice_id


def require_avatar(agent: AgentProfile) -> str:
    """Return the HeyGen avatar ID to render with, or refuse.

    HeyGen performs its own identity verification at clone-creation time, so a
    stored ID is itself the consent record. No open-source face-swap or
    LivePortrait-style fallback exists in this build by design (§1.3).
    """
    if not agent.heygen_avatar_id:
        raise ConsentError(
            f"Agent {agent.id} has no verified HeyGen avatar. Avatar jobs require "
            "a clone created through HeyGen's identity-verification workflow (§1.3)."
        )
    return agent.heygen_avatar_id


def set_elevenlabs_voice_for_staff(
    staff: StaffMember,
    voice_id: str | None,
    *,
    consent_confirmed: bool,
) -> StaffMember:
    """Attach an ElevenLabs voice ID to a staff member, refusing without
    confirmed consent. Same rule as `set_elevenlabs_voice`, re-targeted at a
    `StaffMember` instead of an `AgentProfile` -- consent concerns a specific
    person's voice, so it is tracked per staff member (spec: staff members
    phase 1 design, 2026-08-13).
    """
    if voice_id is None:
        staff.elevenlabs_voice_id = None
        staff.voice_consent_confirmed = False
        return staff

    if not consent_confirmed:
        raise ConsentError(
            "Cannot store an ElevenLabs voice ID without confirmed consent. "
            "Spec §1.3 requires explicit, specific consent for reusable AI voice "
            "cloning from the person being cloned."
        )

    staff.elevenlabs_voice_id = voice_id
    staff.voice_consent_confirmed = True
    return staff


def require_voice_for_staff(staff: StaffMember) -> str:
    """Return the voice ID to narrate with for this staff member, or refuse."""
    if not staff.elevenlabs_voice_id or not staff.voice_consent_confirmed:
        raise ConsentError(
            f"Staff member {staff.id} has no consented ElevenLabs voice. "
            "Record consent (§1.3) before running a voice-only job."
        )
    return staff.elevenlabs_voice_id


def require_avatar_for_staff(staff: StaffMember) -> str:
    """Return the HeyGen avatar ID for this staff member, or refuse."""
    if not staff.heygen_avatar_id:
        raise ConsentError(
            f"Staff member {staff.id} has no verified HeyGen avatar. Avatar jobs "
            "require a clone created through HeyGen's identity-verification "
            "workflow (§1.3)."
        )
    return staff.heygen_avatar_id
