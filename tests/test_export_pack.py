from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.models import PropertyJob, AgentProfile
from app.db import get_session

client = TestClient(app)

def test_export_pack_endpoint(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        agent = AgentProfile(
            agency_name="Premier Properties",
            primary_color="#112233",
            secondary_color="#445566",
            staff_name="Jane Doe",
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

        res = client.get(f"/api/jobs/{job.id}/export")
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"
        assert len(res.content) > 100
    finally:
        app.dependency_overrides.clear()
