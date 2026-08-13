from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _create_agency_and_login(client: TestClient, *, email: str, password: str, agency_name: str) -> int:
    """Creates an agency directly via the DB (no public signup endpoint --
    matches the spec's manual-account-creation decision) and logs in,
    leaving the session cookie set on `client`."""
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AgentProfile
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        agent = AgentProfile(agency_name=agency_name, email=email, password_hash=hash_password(password))
        session.add(agent)
        session.commit()
        session.refresh(agent)
        agent_id = agent.id

    resp = client.post("/api/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return agent_id


def test_login_sets_cookie_and_redirects_agency(api_client):
    _create_agency_and_login(api_client, email="a@thornes.org.uk", password="hunter2", agency_name="Thornes")
    assert "ps_session" in api_client.cookies


def test_login_rejects_wrong_password(api_client):
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AgentProfile
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        session.add(AgentProfile(agency_name="Thornes", email="a@thornes.org.uk", password_hash=hash_password("hunter2")))
        session.commit()

    resp = api_client.post("/api/login", json={"email": "a@thornes.org.uk", "password": "wrong"})
    assert resp.status_code == 401


def test_jobs_route_requires_login(api_client):
    resp = api_client.get("/api/jobs")
    assert resp.status_code == 401


def test_agency_cannot_read_another_agencys_job(api_client):
    _create_agency_and_login(api_client, email="a@agency-a.com", password="pw-a", agency_name="Agency A")
    resp = api_client.post("/api/jobs", json={"address": "1 A St", "postcode": "AA1 1AA", "feature_level": "plus"})
    job_id = resp.json()["id"]
    api_client.post("/api/logout")

    _create_agency_and_login(api_client, email="b@agency-b.com", password="pw-b", agency_name="Agency B")
    resp = api_client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 404


def test_agency_sees_only_own_jobs_in_list(api_client):
    _create_agency_and_login(api_client, email="a@agency-a.com", password="pw-a", agency_name="Agency A")
    api_client.post("/api/jobs", json={"address": "1 A St", "postcode": "AA1 1AA", "feature_level": "plus"})
    api_client.post("/api/logout")

    _create_agency_and_login(api_client, email="b@agency-b.com", password="pw-b", agency_name="Agency B")
    api_client.post("/api/jobs", json={"address": "1 B St", "postcode": "BB1 1BB", "feature_level": "plus"})

    resp = api_client.get("/api/jobs")
    addresses = [j["address"] for j in resp.json()]
    assert addresses == ["1 B St"]


def test_admin_route_rejects_agency_session(api_client):
    _create_agency_and_login(api_client, email="a@agency-a.com", password="pw-a", agency_name="Agency A")
    resp = api_client.get("/api/integrations")
    assert resp.status_code == 401
