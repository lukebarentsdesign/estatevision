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
