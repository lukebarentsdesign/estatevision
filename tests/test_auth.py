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


from app.services.auth import (
    hash_password,
    verify_password,
    encode_session_cookie,
    decode_session_cookie,
)


def test_hash_password_and_verify_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_session_cookie_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None

    token = encode_session_cookie(account_type="agency", account_id=42)
    payload = decode_session_cookie(token)

    assert payload == {"account_type": "agency", "account_id": 42}


def test_decode_session_cookie_rejects_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None

    assert decode_session_cookie("not-a-real-token") is None


def test_decode_session_cookie_rejects_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None

    token = encode_session_cookie(account_type="admin", account_id=1)
    payload = decode_session_cookie(token, max_age_seconds=-1)

    assert payload is None
