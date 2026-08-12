from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentProfile, Photo, PropertyJob, ScriptSegment
from app.services.compliance import ComplianceError
from app.services.script_segments import (
    create_segments_from_llm_json,
    estimate_total_duration_sec,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def job(session) -> PropertyJob:
    agent = AgentProfile(agency_name="Test Agency")
    session.add(agent)
    session.commit()
    session.refresh(agent)
    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_create_segments_from_llm_json_persists_ordered_rows(session, job) -> None:
    llm_output = (
        '[{"text": "Hi, I am James.", "is_intro": true}, '
        '{"text": "The kitchen is bright.", "is_intro": false}, '
        '{"text": "The garden faces south.", "is_intro": false}]'
    )
    segments = create_segments_from_llm_json(session, job_id=job.id, llm_json_text=llm_output)

    assert len(segments) == 3
    assert segments[0].is_intro is True
    assert segments[0].order_index == 0
    assert segments[1].order_index == 1
    assert segments[2].is_intro is False


def test_create_segments_rejects_price_bearing_text(session, job) -> None:
    llm_output = '[{"text": "Yours for £450,000.", "is_intro": true}]'
    with pytest.raises(ComplianceError):
        create_segments_from_llm_json(session, job_id=job.id, llm_json_text=llm_output)


def test_create_segments_rejects_malformed_json(session, job) -> None:
    with pytest.raises(ValueError):
        create_segments_from_llm_json(session, job_id=job.id, llm_json_text="not json")


def test_estimate_total_duration_sec_uses_words_per_second_heuristic() -> None:
    # ~150 words/min speech rate => 2.5 words/sec => 1 word ~= 0.4s
    texts = ["one two three four five"] * 4  # 20 words total
    seconds = estimate_total_duration_sec(texts)
    assert 6.0 < seconds < 10.0


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    monkeypatch.setenv("PROPERTY_STUDIO_UPLOAD_DIR", str(tmp_path / "uploads"))

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job_via_api(api_client) -> int:
    resp = api_client.post("/api/jobs", json={"address": "1 Test St", "postcode": "TE1 1ST"})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_create_and_list_segments_via_api(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A lovely hallway."})
    assert resp.status_code == 201
    assert resp.json()["order_index"] == 0

    list_resp = api_client.get(f"/api/jobs/{job_id}/segments")
    assert len(list_resp.json()) == 1


def test_create_segment_rejects_price_text(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "Offers over £300,000."})
    assert resp.status_code == 400


def test_update_segment_text_rejects_price(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A nice garden."})
    segment_id = create_resp.json()["id"]

    resp = api_client.put(f"/api/segments/{segment_id}", json={"text": "Guide price £500,000."})
    assert resp.status_code == 400


def test_update_segment_photo_id_must_belong_to_same_job(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    other_job_id = _create_job_via_api(api_client)

    import io
    photo_resp = api_client.post(
        f"/api/jobs/{other_job_id}/photos",
        files=[("files", ("x.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fake jpeg"), "image/jpeg"))],
    )
    other_photo_id = photo_resp.json()[0]["id"]

    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "The kitchen."})
    segment_id = create_resp.json()["id"]

    resp = api_client.put(f"/api/segments/{segment_id}", json={"photo_id": other_photo_id})
    assert resp.status_code == 400


def test_delete_segment(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A spare room."})
    segment_id = create_resp.json()["id"]

    resp = api_client.delete(f"/api/segments/{segment_id}")
    assert resp.status_code == 200

    list_resp = api_client.get(f"/api/jobs/{job_id}/segments")
    assert list_resp.json() == []
