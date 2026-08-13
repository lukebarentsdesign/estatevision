from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.models import AdminAccount, AgentProfile


def test_agent_profile_has_auth_columns():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        agent = AgentProfile(
            agency_name="Thornes",
            email="agent@thornes.org.uk",
            password_hash="not-a-real-hash",
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)

        assert agent.email == "agent@thornes.org.uk"
        assert agent.password_hash == "not-a-real-hash"
        assert agent.is_active is True


def test_admin_account_table_exists():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        admin = AdminAccount(email="luke@example.com", password_hash="not-a-real-hash")
        session.add(admin)
        session.commit()
        session.refresh(admin)

        assert admin.id is not None
        assert admin.email == "luke@example.com"
