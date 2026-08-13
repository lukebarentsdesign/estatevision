# tests/test_staff_members.py
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentProfile, StaffMember


def test_staff_member_belongs_to_agency():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        agent = AgentProfile(agency_name="Thornes")
        session.add(agent)
        session.commit()
        session.refresh(agent)

        staff = StaffMember(agent_id=agent.id, staff_name="Jane Doe")
        session.add(staff)
        session.commit()
        session.refresh(staff)

        assert staff.id is not None
        assert staff.agent_id == agent.id
        assert staff.staff_name == "Jane Doe"
        assert staff.heygen_avatar_id is None
        assert staff.elevenlabs_voice_id is None
        assert staff.voice_consent_confirmed is False


import pytest

from app.models import StaffMember
from app.services.consent import (
    ConsentError,
    require_avatar_for_staff,
    require_voice_for_staff,
    set_elevenlabs_voice_for_staff,
)


def test_set_elevenlabs_voice_for_staff_requires_consent():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    with pytest.raises(ConsentError):
        set_elevenlabs_voice_for_staff(staff, "voice-123", consent_confirmed=False)


def test_set_elevenlabs_voice_for_staff_succeeds_with_consent():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    set_elevenlabs_voice_for_staff(staff, "voice-123", consent_confirmed=True)
    assert staff.elevenlabs_voice_id == "voice-123"
    assert staff.voice_consent_confirmed is True


def test_set_elevenlabs_voice_for_staff_clearing_resets_consent():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    set_elevenlabs_voice_for_staff(staff, "voice-123", consent_confirmed=True)
    set_elevenlabs_voice_for_staff(staff, None, consent_confirmed=False)
    assert staff.elevenlabs_voice_id is None
    assert staff.voice_consent_confirmed is False


def test_require_voice_for_staff_refuses_without_consent():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    with pytest.raises(ConsentError):
        require_voice_for_staff(staff)


def test_require_voice_for_staff_returns_id_when_consented():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    set_elevenlabs_voice_for_staff(staff, "voice-123", consent_confirmed=True)
    assert require_voice_for_staff(staff) == "voice-123"


def test_require_avatar_for_staff_refuses_without_id():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe")
    with pytest.raises(ConsentError):
        require_avatar_for_staff(staff)


def test_require_avatar_for_staff_returns_id_when_set():
    staff = StaffMember(agent_id=1, staff_name="Jane Doe", heygen_avatar_id="avatar-456")
    assert require_avatar_for_staff(staff) == "avatar-456"
