from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AgentProfile, Photo, PropertyJob, ScriptSegment


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_job(session: Session) -> PropertyJob:
    agent = AgentProfile(agency_name="Test Agency")
    session.add(agent)
    session.commit()
    session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_script_segment_round_trips(session) -> None:
    job = _make_job(session)
    segment = ScriptSegment(job_id=job.id, order_index=0, text="Hi, I'm James.", is_intro=True)
    session.add(segment)
    session.commit()
    session.refresh(segment)

    assert segment.id is not None
    assert segment.photo_id is None
    assert segment.audio_path is None
    assert segment.duration_sec is None


def test_script_segment_can_reference_a_photo(session) -> None:
    job = _make_job(session)
    photo = Photo(job_id=job.id, source_path="/photos/kitchen.jpg", order_index=0)
    session.add(photo)
    session.commit()
    session.refresh(photo)

    segment = ScriptSegment(job_id=job.id, order_index=1, text="The kitchen has bi-fold doors.", photo_id=photo.id)
    session.add(segment)
    session.commit()
    session.refresh(segment)

    assert segment.photo_id == photo.id


def test_same_photo_can_back_two_segments(session) -> None:
    job = _make_job(session)
    photo = Photo(job_id=job.id, source_path="/photos/exterior.jpg", order_index=0)
    session.add(photo)
    session.commit()
    session.refresh(photo)

    seg_a = ScriptSegment(job_id=job.id, order_index=0, text="Welcome to the property.", photo_id=photo.id, is_intro=True)
    seg_b = ScriptSegment(job_id=job.id, order_index=4, text="And that's the view from the front.", photo_id=photo.id)
    session.add(seg_a)
    session.add(seg_b)
    session.commit()

    stmt_count = session.exec(
        select(ScriptSegment).where(ScriptSegment.photo_id == photo.id)
    ).all()
    assert len(stmt_count) == 2
