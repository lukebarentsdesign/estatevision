from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.models import PropertyJob, AgentProfile
from app.db import get_session
from app.services.auth import hash_password

def test_export_pack_endpoint(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        client = TestClient(app)
        agent = AgentProfile(
            agency_name="Premier Properties",
            primary_color="#112233",
            secondary_color="#445566",
            staff_name="Jane Doe",
            email="premier@agency.example",
            password_hash=hash_password("test-password"),
        )
        db_session.add(agent)
        db_session.commit()

        job = PropertyJob(
            agent_id=agent.id,
            address="10 Downing Street",
            postcode="SW1A 2AA",
            price_guide="£2,500,000",
            garden_orientation="South-West",
            feature_level="standard",
        )
        db_session.add(job)
        db_session.commit()

        login_resp = client.post(
            "/api/login", json={"email": "premier@agency.example", "password": "test-password"}
        )
        assert login_resp.status_code == 200

        res = client.get(f"/api/jobs/{job.id}/export")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"
        assert len(res.content) > 100
    finally:
        app.dependency_overrides.clear()
