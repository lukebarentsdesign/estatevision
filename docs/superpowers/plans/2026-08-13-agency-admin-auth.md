# Agency & Admin Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add agency and admin login accounts, cookie-based sessions, and agency-scoped access control so each estate agency can only see and modify its own jobs/photos/segments, while a single admin account controls the integrations panel and agency management.

**Architecture:** `AgentProfile` (existing tenant table) gains `email`/`password_hash`/`is_active` columns and becomes the agency login identity. A new `AdminAccount` table holds the platform owner's login. Sessions are a signed, `HttpOnly` cookie (via `itsdangerous`) carrying `{account_type, account_id}` — no server-side session table. Two FastAPI dependencies, `require_agency` and `require_admin`, gate routes and inject the authenticated row; every job/photo/segment route is updated to scope its query to the injected agency's `id` instead of trusting the client-supplied job ID alone.

**Tech Stack:** FastAPI dependency injection, `passlib[bcrypt]` for password hashing, `itsdangerous` for signed cookies, SQLModel/SQLite (existing), `pytest` + `fastapi.testclient.TestClient` (existing).

---

## File Structure

- `app/models.py` — modify: add `email`/`password_hash`/`is_active` to `AgentProfile`; add new `AdminAccount` table
- `app/services/auth.py` — create: password hashing helpers, session cookie encode/decode, `require_agency`/`require_admin` FastAPI dependencies
- `app/main.py` — modify: add `/login`, `/logout` routes; add `Depends(require_agency)`/`Depends(require_admin)` to existing job/photo/segment/admin routes; add agency-management routes under `/admin/agencies`
- `app/static/login.html` — create: login form page
- `app/static/admin_agencies.html` — create: admin agency-management page
- `tests/test_auth.py` — create: unit tests for hashing, cookie encode/decode, dependency behavior
- `tests/test_auth_scoping.py` — create: integration tests proving cross-agency access is blocked
- `tests/conftest.py` — modify: extend `api_client` fixture to support creating/logging-in agencies and admin for other tests

---

### Task 1: Add auth columns to `AgentProfile` and create `AdminAccount`

**Files:**
- Modify: `app/models.py:30-51`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ImportError: cannot import name 'AdminAccount'` (and/or `TypeError` for unexpected `email` kwarg on `AgentProfile`)

- [ ] **Step 3: Add the columns and new table**

In `app/models.py`, modify the `AgentProfile` class (replace lines 30-51):

```python
class AgentProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agency_name: str

    # Login identity (spec: agency/admin auth design, 2026-08-13). One shared
    # login per agency -- not per staff member.
    email: str = Field(default="", index=True, unique=True)
    password_hash: str = Field(default="")
    is_active: bool = True

    primary_color: str = "#111827"
    secondary_color: str = "#6b7280"
    logo_path: Optional[str] = None
    staff_name: Optional[str] = None
    staff_headshot_path: Optional[str] = None

    # Avatar pipeline. HeyGen runs its own identity verification, so the
    # presence of an ID is itself the consent record (§1.3).
    heygen_avatar_id: Optional[str] = None

    # Voice-only pipeline. ElevenLabs consent is separate and NOT covered by
    # HeyGen verification, so this ID is gated on `voice_consent_confirmed`
    # by `services.consent` -- never set it directly.
    elevenlabs_voice_id: Optional[str] = None
    voice_consent_confirmed: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    jobs: List["PropertyJob"] = Relationship(back_populates="agent")
```

Then add a new class after `ActiveVendorChoice` at the end of the file (after line 161):

```python


class AdminAccount(SQLModel, table=True):
    """The platform owner's login (spec: agency/admin auth design,
    2026-08-13). Modeled as a table rather than an env-var credential so it
    can be changed without a redeploy; a single row is expected in practice.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_auth.py
git commit -m "feat: add agency login columns and AdminAccount table"
```

---

### Task 2: Password hashing and session cookie helpers

**Files:**
- Create: `app/services/auth.py`
- Modify: `requirements.txt`
- Test: `tests/test_auth.py` (append)

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
passlib[bcrypt]>=1.7
itsdangerous>=2.2
```

Run: `pip install passlib[bcrypt] itsdangerous`

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_auth.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.auth'`

- [ ] **Step 4: Implement `app/services/auth.py`**

```python
"""Agency/admin login: password hashing and signed session cookies
(spec: agency/admin auth design, 2026-08-13).

Sessions are a signed, HttpOnly cookie -- no server-side session table.
The signing key is a locally held file, generated on first use, mirroring
`services.secrets_store`'s key-file pattern so the two follow the same
operational model (lose the file, every session/credential needs
re-issuing).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, TypedDict

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt

SESSION_COOKIE_NAME = "ps_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days, sliding on each request

_DEFAULT_KEY_PATH = Path(os.environ.get("PROPERTY_STUDIO_SESSION_KEY_FILE", "session.key"))

_session_serializer: Optional[URLSafeTimedSerializer] = None


class SessionPayload(TypedDict):
    account_type: Literal["agency", "admin"]
    account_id: int


def hash_password(plaintext: str) -> str:
    return bcrypt.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.verify(plaintext, password_hash)
    except ValueError:
        return False


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes()

    import secrets

    key = secrets.token_bytes(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    return key


def _get_serializer() -> URLSafeTimedSerializer:
    global _session_serializer
    if _session_serializer is None:
        key_path = Path(os.environ.get("PROPERTY_STUDIO_SESSION_KEY_FILE", str(_DEFAULT_KEY_PATH)))
        key = _load_or_create_key(key_path)
        _session_serializer = URLSafeTimedSerializer(key.hex())
    return _session_serializer


def encode_session_cookie(*, account_type: Literal["agency", "admin"], account_id: int) -> str:
    serializer = _get_serializer()
    return serializer.dumps({"account_type": account_type, "account_id": account_id})


def decode_session_cookie(
    token: str, max_age_seconds: int = SESSION_MAX_AGE_SECONDS
) -> Optional[SessionPayload]:
    serializer = _get_serializer()
    try:
        data = serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "account_type" not in data or "account_id" not in data:
        return None
    return {"account_type": data["account_type"], "account_id": data["account_id"]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add app/services/auth.py requirements.txt tests/test_auth.py
git commit -m "feat: add password hashing and signed session cookie helpers"
```

---

### Task 3: `require_agency`/`require_admin` FastAPI dependencies

**Files:**
- Modify: `app/services/auth.py`
- Test: `tests/test_auth.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models import AdminAccount, AgentProfile
from app.services.auth import require_admin, require_agency


def _fake_request(cookie_value: str | None) -> Request:
    headers = []
    if cookie_value is not None:
        headers.append((b"cookie", f"ps_session={cookie_value}".encode()))
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def test_require_agency_rejects_missing_cookie(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None

    with pytest.raises(HTTPException) as exc_info:
        require_agency(_fake_request(None), session=db_session)
    assert exc_info.value.status_code == 401


def test_require_agency_accepts_valid_cookie(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None
    from app.services.auth import encode_session_cookie, hash_password

    agent = AgentProfile(agency_name="Thornes", email="a@b.com", password_hash=hash_password("x"))
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    token = encode_session_cookie(account_type="agency", account_id=agent.id)
    result = require_agency(_fake_request(token), session=db_session)
    assert result.id == agent.id


def test_require_agency_rejects_admin_cookie(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None
    from app.services.auth import encode_session_cookie

    token = encode_session_cookie(account_type="admin", account_id=1)
    with pytest.raises(HTTPException) as exc_info:
        require_agency(_fake_request(token), session=db_session)
    assert exc_info.value.status_code == 401


def test_require_agency_rejects_inactive_agency(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None
    from app.services.auth import encode_session_cookie, hash_password

    agent = AgentProfile(
        agency_name="Thornes", email="a@b.com", password_hash=hash_password("x"), is_active=False
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    token = encode_session_cookie(account_type="agency", account_id=agent.id)
    with pytest.raises(HTTPException) as exc_info:
        require_agency(_fake_request(token), session=db_session)
    assert exc_info.value.status_code == 401


def test_require_admin_accepts_valid_cookie(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None
    from app.services.auth import encode_session_cookie, hash_password

    admin = AdminAccount(email="luke@example.com", password_hash=hash_password("x"))
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)

    token = encode_session_cookie(account_type="admin", account_id=admin.id)
    result = require_admin(_fake_request(token), session=db_session)
    assert result.id == admin.id


def test_require_admin_rejects_agency_cookie(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_SESSION_KEY_FILE", str(tmp_path / "session.key"))
    import app.services.auth as auth_mod
    auth_mod._session_serializer = None
    from app.services.auth import encode_session_cookie

    token = encode_session_cookie(account_type="agency", account_id=1)
    with pytest.raises(HTTPException) as exc_info:
        require_admin(_fake_request(token), session=db_session)
    assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ImportError: cannot import name 'require_agency'`

- [ ] **Step 3: Implement the dependencies**

Append to `app/services/auth.py`:

```python


def _get_session_payload(request) -> Optional[SessionPayload]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        return None
    return decode_session_cookie(token)


def require_agency(request, session=None):
    """FastAPI dependency: returns the logged-in `AgentProfile` or raises 401.

    Imports are deferred to avoid a module-level circular import between
    this module and `app.models`/`app.db` (both of which are imported by
    `app.main`, which will import this module).
    """
    from fastapi import Depends, HTTPException

    from ..db import get_session
    from ..models import AgentProfile

    if session is None:
        raise RuntimeError("require_agency must be called with an explicit session in tests")

    payload = _get_session_payload(request)
    if payload is None or payload["account_type"] != "agency":
        raise HTTPException(401, "not logged in")

    agent = session.get(AgentProfile, payload["account_id"])
    if agent is None or not agent.is_active:
        raise HTTPException(401, "not logged in")
    return agent


def require_admin(request, session=None):
    """FastAPI dependency: returns the logged-in `AdminAccount` or raises 401."""
    from fastapi import HTTPException

    from ..models import AdminAccount

    if session is None:
        raise RuntimeError("require_admin must be called with an explicit session in tests")

    payload = _get_session_payload(request)
    if payload is None or payload["account_type"] != "admin":
        raise HTTPException(401, "not logged in")

    admin = session.get(AdminAccount, payload["account_id"])
    if admin is None:
        raise HTTPException(401, "not logged in")
    return admin
```

Note: the `session=None` + `RuntimeError` pattern above is a placeholder shape only for the unit tests calling these functions directly with `session=db_session`. Task 4 wires these as real FastAPI dependencies with `Depends(get_session)` as the default, which is what routes will actually use — replace the signatures in Step 3 of Task 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/auth.py tests/test_auth.py
git commit -m "feat: add require_agency/require_admin auth dependencies"
```

---

### Task 4: Wire dependencies with `Depends(get_session)` default and add login/logout routes

**Files:**
- Modify: `app/services/auth.py`
- Modify: `app/main.py`
- Modify: `app/static/index.html` (add nothing yet — routes only in this task)
- Create: `app/static/login.html`
- Test: `tests/conftest.py`
- Test: `tests/test_auth_scoping.py`

- [ ] **Step 1: Finalize the dependency signatures for FastAPI use**

In `app/services/auth.py`, replace the `require_agency`/`require_admin` functions written in Task 3 with versions that default their `session` parameter to `Depends(get_session)`, so routes can use them directly as `Depends(require_agency)` without passing a session manually:

```python
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from ..db import get_session


def require_agency(request: Request, session: Session = Depends(get_session)):
    """FastAPI dependency: returns the logged-in `AgentProfile` or raises 401."""
    from ..models import AgentProfile

    payload = _get_session_payload(request)
    if payload is None or payload["account_type"] != "agency":
        raise HTTPException(401, "not logged in")

    agent = session.get(AgentProfile, payload["account_id"])
    if agent is None or not agent.is_active:
        raise HTTPException(401, "not logged in")
    return agent


def require_admin(request: Request, session: Session = Depends(get_session)):
    """FastAPI dependency: returns the logged-in `AdminAccount` or raises 401."""
    from ..models import AdminAccount

    payload = _get_session_payload(request)
    if payload is None or payload["account_type"] != "admin":
        raise HTTPException(401, "not logged in")

    admin = session.get(AdminAccount, payload["account_id"])
    if admin is None:
        raise HTTPException(401, "not logged in")
    return admin
```

Move the `from fastapi import Depends, HTTPException, Request` and `from sqlmodel import Session` and `from ..db import get_session` imports to the top of the file (alongside the existing `itsdangerous`/`passlib` imports), removing the deferred per-function imports for those three from Task 3's version. Keep the deferred imports for `AgentProfile`/`AdminAccount` inside each function to avoid a circular import (`app.models` does not import `app.services.auth`, so this direction is safe to hoist too — hoist them to the top of the file as well).

The tests from Task 3 (`test_require_agency_rejects_missing_cookie` etc.) called these functions with `session=db_session` as a keyword argument, which remains valid — a default value doesn't break explicit keyword passing.

- [ ] **Step 2: Run existing auth tests to confirm no regression**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (13 tests, same as Task 3)

- [ ] **Step 3: Add login/logout routes to `app/main.py`**

Add near the top of `app/main.py`, after the existing imports (after line 30):

```python
from .services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    encode_session_cookie,
    hash_password,
    require_admin,
    require_agency,
    verify_password,
)
```

Add after the `admin_integrations_page` route (after line 51):

```python
@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "login.html")


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/login")
def login(body: LoginRequest, response: Response, session: Session = Depends(get_session)) -> dict:
    """Tries agency lookup first, then admin -- a single login form serves
    both account types (spec: agency/admin auth design, 2026-08-13)."""
    agent = session.exec(select(AgentProfile).where(AgentProfile.email == body.email)).first()
    if agent is not None and agent.is_active and verify_password(body.password, agent.password_hash):
        token = encode_session_cookie(account_type="agency", account_id=agent.id)
        response.set_cookie(
            SESSION_COOKIE_NAME, token, httponly=True, max_age=SESSION_MAX_AGE_SECONDS, samesite="lax"
        )
        return {"account_type": "agency", "redirect": "/"}

    from .models import AdminAccount

    admin = session.exec(select(AdminAccount).where(AdminAccount.email == body.email)).first()
    if admin is not None and verify_password(body.password, admin.password_hash):
        token = encode_session_cookie(account_type="admin", account_id=admin.id)
        response.set_cookie(
            SESSION_COOKIE_NAME, token, httponly=True, max_age=SESSION_MAX_AGE_SECONDS, samesite="lax"
        )
        return {"account_type": "admin", "redirect": "/admin/agencies"}

    raise HTTPException(401, "incorrect email or password")


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"logged_out": True}
```

`AdminAccount` needs to be importable in `main.py` — add it to the existing model import line (line 22):

```python
from .models import AdminAccount, AgentProfile, JobStatus, Photo, PropertyJob, ScriptSegment
```

- [ ] **Step 4: Create the login page**

```html
<!-- app/static/login.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Sign in — Property Content Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center">
  <form id="login-form" class="bg-slate-900 border border-slate-800 rounded-2xl p-8 w-full max-w-sm space-y-4">
    <h1 class="text-xl font-semibold">Sign in</h1>
    <div id="login-error" class="text-red-400 text-sm hidden"></div>
    <div>
      <label class="block text-sm text-slate-400 mb-1" for="email">Email</label>
      <input id="email" name="email" type="email" required
        class="w-full rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-sm" />
    </div>
    <div>
      <label class="block text-sm text-slate-400 mb-1" for="password">Password</label>
      <input id="password" name="password" type="password" required
        class="w-full rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-sm" />
    </div>
    <button type="submit"
      class="w-full rounded-lg bg-indigo-600 hover:bg-indigo-500 transition-colors duration-150 px-3 py-2 text-sm font-medium">
      Sign in
    </button>
  </form>
  <script>
    document.getElementById('login-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const errorBox = document.getElementById('login-error');
      errorBox.classList.add('hidden');
      const email = document.getElementById('email').value;
      const password = document.getElementById('password').value;
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!resp.ok) {
        errorBox.textContent = 'Incorrect email or password.';
        errorBox.classList.remove('hidden');
        return;
      }
      const data = await resp.json();
      window.location.href = data.redirect;
    });
  </script>
</body>
</html>
```

- [ ] **Step 5: Write the failing scoping tests**

```python
# tests/test_auth_scoping.py
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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_auth_scoping.py -v`
Expected: FAIL — routes not yet protected (`/api/jobs` returns 200 with no login, `test_admin_route_rejects_agency_session` fails since `/api/integrations` isn't gated yet, `test_agency_sees_only_own_jobs_in_list` fails since listing isn't scoped)

- [ ] **Step 7: Commit the currently-failing test file as a checkpoint**

This step intentionally commits red tests, since Task 5 is the scoping work that makes them pass — matches the plan's task boundaries (auth plumbing here, route scoping next).

```bash
git add app/main.py app/static/login.html app/services/auth.py tests/test_auth_scoping.py
git commit -m "feat: add login/logout routes and login page (routes not yet scoped)"
```

---

### Task 5: Scope job/photo/segment routes to the logged-in agency; gate admin routes

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Gate and scope the job/photo/segment routes**

In `app/main.py`, replace each of the following routes (exact line numbers refer to the pre-Task-4 file; use the function names to locate them after Task 4's edits, since line numbers will have shifted):

Replace `list_jobs` (originally lines 67-69):

```python
@app.get("/api/jobs")
def list_jobs(
    agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> list[PropertyJob]:
    return session.exec(select(PropertyJob).where(PropertyJob.agent_id == agency.id)).all()
```

Replace `get_job` (originally lines 72-77):

```python
@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: int, agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> PropertyJob:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    return job
```

Replace `create_job` (originally lines 80-85):

```python
@app.post("/api/jobs", status_code=201)
def create_job(
    job: PropertyJob,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> PropertyJob:
    job.agent_id = agency.id
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
```

Replace `upload_brochure` (originally lines 113-131) — add the dependency and ownership check:

```python
@app.post("/api/jobs/{job_id}/brochure")
async def upload_brochure(
    job_id: int,
    file: UploadFile = File(...),
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "brochure must be a PDF file")

    contents = await _read_capped(file, context="brochure")

    dest = _upload_dir(job_id) / "brochure.pdf"
    dest.write_bytes(contents)

    job.pdf_brochure_path = str(dest)
    session.add(job)
    session.commit()
    return {"pdf_brochure_path": job.pdf_brochure_path}
```

Replace `upload_photos` (originally lines 134-173) — add the dependency and ownership check (body unchanged otherwise):

```python
@app.post("/api/jobs/{job_id}/photos", status_code=201)
async def upload_photos(
    job_id: int,
    files: list[UploadFile] = File(...),
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> list[Photo]:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    for upload in files:
        if not (upload.content_type or "").startswith("image/"):
            raise HTTPException(
                400, f"{upload.filename or 'upload'} is not an image (content_type={upload.content_type!r})"
            )

    existing_count = len(
        session.exec(select(Photo).where(Photo.job_id == job_id)).all()
    )

    dest_dir = _upload_dir(job_id) / "photos"
    dest_dir.mkdir(parents=True, exist_ok=True)

    created: list[Photo] = []
    for i, upload in enumerate(files):
        contents = await _read_capped(upload, context=upload.filename or "photo")
        suffix = Path(upload.filename or "photo.jpg").suffix or ".jpg"
        dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(contents)

        photo = Photo(
            job_id=job_id,
            source_path=str(dest),
            order_index=existing_count + i,
        )
        session.add(photo)
        created.append(photo)

    session.commit()
    for photo in created:
        session.refresh(photo)
    return created
```

Replace `list_job_photos` (originally lines 176-183):

```python
@app.get("/api/jobs/{job_id}/photos")
def list_job_photos(
    job_id: int, agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> list[Photo]:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    return session.exec(
        select(Photo).where(Photo.job_id == job_id).order_by(Photo.order_index)
    ).all()
```

Replace `list_job_segments` (originally lines 210-215):

```python
@app.get("/api/jobs/{job_id}/segments")
def list_job_segments(
    job_id: int, agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> list[dict]:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    return [_serialize_segment(s) for s in list_segments(session, job_id)]
```

Replace `create_job_segment` (originally lines 218-240):

```python
@app.post("/api/jobs/{job_id}/segments", status_code=201)
def create_job_segment(
    job_id: int,
    body: CreateSegmentRequest,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    try:
        assert_price_free(body.text, context="agent-authored segment")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    order_index = body.order_index
    if order_index is None:
        existing = list_segments(session, job_id)
        order_index = (max((s.order_index for s in existing), default=-1)) + 1

    segment = ScriptSegment(job_id=job_id, order_index=order_index, text=body.text, is_intro=False)
    session.add(segment)
    session.commit()
    session.refresh(segment)
    return _serialize_segment(segment)
```

Replace `update_job_segment` (originally lines 243-268) — ownership is checked via the segment's parent job:

```python
@app.put("/api/segments/{segment_id}")
def update_job_segment(
    segment_id: int,
    body: UpdateSegmentRequest,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    segment = session.get(ScriptSegment, segment_id)
    if segment is None:
        raise HTTPException(404, "segment not found")
    job = session.get(PropertyJob, segment.job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "segment not found")

    if body.text is not None:
        try:
            assert_price_free(body.text, context="edited segment")
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        segment.text = body.text
    if body.photo_id is not None:
        photo = session.get(Photo, body.photo_id)
        if photo is None or photo.job_id != segment.job_id:
            raise HTTPException(400, f"photo {body.photo_id} does not belong to this job")
        segment.photo_id = body.photo_id
    if body.order_index is not None:
        segment.order_index = body.order_index

    session.add(segment)
    session.commit()
    session.refresh(segment)
    return _serialize_segment(segment)
```

Replace `delete_job_segment` (originally lines 271-278):

```python
@app.delete("/api/segments/{segment_id}")
def delete_job_segment(
    segment_id: int,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    segment = session.get(ScriptSegment, segment_id)
    if segment is None:
        raise HTTPException(404, "segment not found")
    job = session.get(PropertyJob, segment.job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "segment not found")
    session.delete(segment)
    session.commit()
    return {"deleted": True}
```

Replace `refresh_location_data` (originally lines 281-299):

```python
@app.post("/api/jobs/{job_id}/location")
def refresh_location_data(
    job_id: int,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    """Populate `job.location_data_json` from the §5 aggregator."""
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    data = uk_location.build_location_data(
        latitude=job.latitude,
        longitude=job.longitude,
        postcode=job.postcode,
        garden_orientation=job.garden_orientation,
    )
    job.location_data_json = data
    session.add(job)
    session.commit()
    return data
```

Replace `update_job` (originally lines 306-331):

```python
@app.patch("/api/jobs/{job_id}")
def update_job(
    job_id: int,
    body: UpdateJobRequest,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> PropertyJob:
    """Pre-run settings updates only. Once a job has moved past INGESTION,
    its already-rendered (or in-progress) artifacts no longer reflect a
    changed use_avatar value -- toggling it later would desync the DB from
    what was actually produced, with nothing to catch it. Mirrors the
    JobStatus-guard convention run_pipeline already uses for the same
    reason (assert_transition)."""
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    if body.use_avatar is not None and job.status != JobStatus.INGESTION:
        raise HTTPException(
            409, f"cannot change use_avatar after the job has left {JobStatus.INGESTION.value} status"
        )

    if body.use_avatar is not None:
        job.use_avatar = body.use_avatar

    session.add(job)
    session.commit()
    session.refresh(job)
    return job
```

Replace `run_pipeline` (originally lines 334-408) — add dependency and ownership check at the top, body otherwise unchanged:

```python
@app.post("/api/jobs/{job_id}/run")
def run_pipeline(
    job_id: int,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    """Run every applicable pipeline step for this job's feature level."""
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    segments = list_segments(session, job_id)
    if segments:
        unassigned = [
            s.id for s in segments
            if s.photo_id is None and not (s.is_intro and job.use_avatar)
        ]
        if unassigned:
            raise HTTPException(
                422,
                f"segment(s) {unassigned} have no photo assigned; assign a photo to "
                "every segment before running the pipeline",
            )

    assert_transition(job.status, JobStatus.PROCESSING)
    job.status = JobStatus.PROCESSING
    session.add(job)
    session.commit()

    try:
        snapshot = build_job_snapshot(session, job)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    work_dir = Path("work") / f"job_{job.id}"
    ctx = JobContext(
        job_id=job.id,
        work_dir=work_dir,
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )

    runner = build_runner()
    try:
        results = runner.run(ctx)
    except Exception as exc:
        raise HTTPException(500, f"pipeline failed: {exc}") from exc

    job.status = JobStatus.REVIEW
    scripts = ctx.artifact("script_and_voice", "scripts")
    if scripts is not None:
        job.script_json = scripts
    else:
        segments = list_segments(session, job.id)
        if segments:
            job.script_json = {
                "walkthrough_script": " ".join(s.text for s in segments),
                "segments": [{"id": s.id, "text": s.text, "is_intro": s.is_intro} for s in segments],
            }
    session.add(job)
    session.commit()

    return {
        "job_id": job.id,
        "status": job.status.value,
        "steps": {name: r.status.value for name, r in results.items()},
    }
```

Replace `update_job_script` (originally lines 544-561):

```python
@app.put("/api/jobs/{job_id}/script")
def update_job_script(
    job_id: int,
    body: UpdateScriptRequest,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> dict:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    current_script = job.script_json or {}
    if body.walkthrough_script is not None:
        current_script["walkthrough_script"] = body.walkthrough_script
    if body.social_shorts is not None:
        current_script["social_shorts"] = body.social_shorts

    job.script_json = current_script
    session.add(job)
    session.commit()
    return job.script_json
```

Replace `download_export_pack` (originally lines 564-596):

```python
@app.get("/api/jobs/{job_id}/export")
def download_export_pack(
    job_id: int,
    agency: AgentProfile = Depends(require_agency),
    session: Session = Depends(get_session),
) -> Response:
    from .services.export_pack import build_export_zip

    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")

    zip_bytes = build_export_zip(
        job_id=job.id,
        address=job.address,
        postcode=job.postcode,
        price_guide=job.price_guide,
        garden_orientation=job.garden_orientation,
        agency_name=agency.agency_name,
        primary_color=agency.primary_color,
        secondary_color=agency.secondary_color,
        logo_url=agency.logo_path or "",
        staff_name=agency.staff_name or "Property Agent",
        staff_headshot=agency.staff_headshot_path or "",
        script_json=job.script_json,
        location_data=job.location_data_json,
        work_dir=Path("work") / f"job_{job.id}",
    )

    filename = f"property_pack_job_{job.id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

Note: `download_export_pack` previously looked up `agent = session.get(AgentProfile, job.agent_id)` with `None` fallbacks throughout, since a job's agent was optional. Now that every job is created with `agent_id = agency.id` (Task 5, `create_job`) and route access itself requires a matching agency, the injected `agency` is guaranteed to be the job's real agent — the `if agent else` fallback branches are no longer reachable and are removed.

- [ ] **Step 2: Gate the admin routes**

Add `agency`/`admin` dependency to every route in the "Admin panel" section (originally lines 411-534: `list_integration_statuses`, `get_integration_status`, `set_integration_field`, `clear_integration_field`, `set_category_active_vendor`, `openai_base_url_presets`, `test_integration_connection`) and to `admin_integrations_page`. Example for `list_integration_statuses`:

```python
@app.get("/api/integrations")
def list_integration_statuses(
    admin: AdminAccount = Depends(require_admin), session: Session = Depends(get_session)
) -> list[dict]:
    """Every known system (§7 reference table) with its configuration status.

    Never returns raw secret values -- only masked previews (see `secrets_store.mask`).
    """
    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]
```

Apply the same `admin: AdminAccount = Depends(require_admin)` parameter (unused in the body, present purely to gate the route) to each of the other six admin routes listed above, and to `admin_integrations_page`:

```python
@app.get("/admin/integrations")
def admin_integrations_page(admin: AdminAccount = Depends(require_admin)) -> FileResponse:
    return FileResponse(_STATIC_DIR / "admin_integrations.html")
```

`list_agents`/`create_agent` (originally lines 54-64) become superseded by the new admin agency-management routes in Task 6 — leave them as-is for now (they are not part of this task's scope; Task 6 replaces their purpose).

- [ ] **Step 3: Run the scoping tests to verify they pass**

Run: `pytest tests/test_auth_scoping.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: Multiple pre-existing tests will now fail because they call job/photo/segment/admin routes without a session cookie. This is addressed in Step 5.

- [ ] **Step 5: Update `tests/conftest.py` to provide an authenticated client**

Existing tests across the suite (`test_uploads.py`, `test_job_update.py`, `test_export_pack.py`, `test_integration_settings.py`, etc.) call routes that are now gated. Add an autouse-friendly authenticated fixture without breaking the existing `api_client` fixture's signature, so each pre-existing test file can opt in with a one-line change:

Append to `tests/conftest.py`:

```python


@pytest.fixture
def agency_client(api_client):
    """`api_client` with a logged-in agency session already attached to its
    cookie jar. Existing tests that create/read/update jobs need this now
    that those routes require an authenticated agency (spec: agency/admin
    auth design, 2026-08-13)."""
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AgentProfile
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        agent = AgentProfile(agency_name="Test Agency", email="test@agency.example", password_hash=hash_password("test-password"))
        session.add(agent)
        session.commit()

    resp = api_client.post("/api/login", json={"email": "test@agency.example", "password": "test-password"})
    assert resp.status_code == 200
    return api_client


@pytest.fixture
def admin_client(api_client):
    """`api_client` with a logged-in admin session already attached."""
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AdminAccount
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        session.add(AdminAccount(email="admin@platform.example", password_hash=hash_password("admin-password")))
        session.commit()

    resp = api_client.post("/api/login", json={"email": "admin@platform.example", "password": "admin-password"})
    assert resp.status_code == 200
    return api_client
```

- [ ] **Step 6: Update pre-existing test files to use the authenticated fixtures**

For each of the following test files, replace every use of the `api_client` fixture parameter with `agency_client` (job/photo/segment/export-pack tests) or `admin_client` (integration-settings/active-vendor/multi-vendor tests), and replace corresponding usages of `api_client` inside the test bodies with the new parameter name:

- `tests/test_uploads.py` → `agency_client`
- `tests/test_job_update.py` → `agency_client`
- `tests/test_export_pack.py` → `agency_client`
- `tests/test_script_segments.py` (if it uses `api_client` for HTTP calls; check first — it may only use `db_session`) → `agency_client` if HTTP-based
- `tests/test_integration_settings.py` → `admin_client`
- `tests/test_active_vendor.py` → `admin_client` if it uses `api_client` for HTTP calls
- `tests/test_multi_vendor_switch.py` → `admin_client`
- `tests/test_integration_registry.py` → `admin_client` if it uses `api_client` for HTTP calls

For each file, run `pytest tests/<file>.py -v` after the rename to confirm it passes before moving to the next file. Files that only use `db_session` or don't hit HTTP routes at all (verify by checking their imports/fixture usage) need no change.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add app/main.py tests/
git commit -m "feat: scope job/photo/segment routes to logged-in agency; gate admin routes"
```

---

### Task 6: Admin agency-management screen

**Files:**
- Modify: `app/main.py`
- Create: `app/static/admin_agencies.html`
- Test: `tests/test_admin_agencies.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_admin_agencies.py
from __future__ import annotations


def test_admin_can_create_agency(admin_client):
    resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "New Agency", "email": "new@agency.example", "password": "starter-pw"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["agency_name"] == "New Agency"
    assert body["email"] == "new@agency.example"
    assert "password" not in body
    assert "password_hash" not in body


def test_created_agency_can_log_in(admin_client, api_client):
    admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "New Agency", "email": "new@agency.example", "password": "starter-pw"},
    )
    resp = api_client.post("/api/login", json={"email": "new@agency.example", "password": "starter-pw"})
    assert resp.status_code == 200


def test_admin_can_list_agencies(admin_client):
    admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "pw"},
    )
    resp = admin_client.get("/api/admin/agencies")
    assert resp.status_code == 200
    names = [a["agency_name"] for a in resp.json()]
    assert "Agency One" in names


def test_admin_can_deactivate_agency(admin_client, api_client):
    create_resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "pw"},
    )
    agency_id = create_resp.json()["id"]

    deactivate_resp = admin_client.patch(f"/api/admin/agencies/{agency_id}", json={"is_active": False})
    assert deactivate_resp.status_code == 200

    login_resp = api_client.post("/api/login", json={"email": "one@agency.example", "password": "pw"})
    assert login_resp.status_code == 401


def test_admin_can_reset_agency_password(admin_client, api_client):
    create_resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "old-pw"},
    )
    agency_id = create_resp.json()["id"]

    reset_resp = admin_client.patch(f"/api/admin/agencies/{agency_id}", json={"new_password": "new-pw"})
    assert reset_resp.status_code == 200

    old_login = api_client.post("/api/login", json={"email": "one@agency.example", "password": "old-pw"})
    assert old_login.status_code == 401

    new_login = api_client.post("/api/login", json={"email": "one@agency.example", "password": "new-pw"})
    assert new_login.status_code == 200


def test_non_admin_cannot_manage_agencies(agency_client):
    resp = agency_client.get("/api/admin/agencies")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_agencies.py -v`
Expected: FAIL with 404 (routes don't exist yet)

- [ ] **Step 3: Implement the admin agency-management routes**

Add to `app/main.py`, after the `logout` route added in Task 4:

```python
class CreateAgencyRequest(BaseModel):
    agency_name: str
    email: str
    password: str


class UpdateAgencyRequest(BaseModel):
    is_active: Optional[bool] = None
    new_password: Optional[str] = None


def _serialize_agency(agent: AgentProfile) -> dict:
    return {
        "id": agent.id,
        "agency_name": agent.agency_name,
        "email": agent.email,
        "is_active": agent.is_active,
    }


@app.get("/admin/agencies")
def admin_agencies_page(admin: AdminAccount = Depends(require_admin)) -> FileResponse:
    return FileResponse(_STATIC_DIR / "admin_agencies.html")


@app.get("/api/admin/agencies")
def list_admin_agencies(
    admin: AdminAccount = Depends(require_admin), session: Session = Depends(get_session)
) -> list[dict]:
    agencies = session.exec(select(AgentProfile)).all()
    return [_serialize_agency(a) for a in agencies]


@app.post("/api/admin/agencies", status_code=201)
def create_admin_agency(
    body: CreateAgencyRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    existing = session.exec(select(AgentProfile).where(AgentProfile.email == body.email)).first()
    if existing is not None:
        raise HTTPException(400, f"an agency with email {body.email!r} already exists")

    agent = AgentProfile(
        agency_name=body.agency_name,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _serialize_agency(agent)


@app.patch("/api/admin/agencies/{agency_id}")
def update_admin_agency(
    agency_id: int,
    body: UpdateAgencyRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    agent = session.get(AgentProfile, agency_id)
    if agent is None:
        raise HTTPException(404, "agency not found")

    if body.is_active is not None:
        agent.is_active = body.is_active
    if body.new_password is not None:
        agent.password_hash = hash_password(body.new_password)

    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _serialize_agency(agent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_agencies.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Create the admin agencies page**

```html
<!-- app/static/admin_agencies.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Agencies — Admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-8">
  <div class="max-w-3xl mx-auto space-y-6">
    <h1 class="text-2xl font-semibold">Agencies</h1>

    <form id="create-form" class="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-wrap gap-3 items-end">
      <div>
        <label class="block text-xs text-slate-400 mb-1">Agency name</label>
        <input id="new-name" required class="rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-xs text-slate-400 mb-1">Email</label>
        <input id="new-email" type="email" required class="rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-sm" />
      </div>
      <div>
        <label class="block text-xs text-slate-400 mb-1">Initial password</label>
        <input id="new-password" type="text" required class="rounded-lg bg-slate-950 border border-slate-800 px-3 py-2 text-sm" />
      </div>
      <button type="submit" class="rounded-lg bg-indigo-600 hover:bg-indigo-500 transition-colors duration-150 px-4 py-2 text-sm font-medium">
        Create agency
      </button>
    </form>

    <div id="agency-list" class="space-y-2"></div>
  </div>

  <script>
    async function loadAgencies() {
      const resp = await fetch('/api/admin/agencies');
      const agencies = await resp.json();
      const list = document.getElementById('agency-list');
      list.innerHTML = agencies.map(a => `
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
          <div>
            <div class="font-medium">${a.agency_name}</div>
            <div class="text-sm text-slate-400">${a.email}</div>
          </div>
          <div class="flex gap-2 items-center">
            <span class="text-xs ${a.is_active ? 'text-emerald-400' : 'text-red-400'}">${a.is_active ? 'active' : 'inactive'}</span>
            <button onclick="toggleActive(${a.id}, ${!a.is_active})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
              ${a.is_active ? 'Deactivate' : 'Reactivate'}
            </button>
            <button onclick="resetPassword(${a.id})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
              Reset password
            </button>
          </div>
        </div>
      `).join('');
    }

    async function toggleActive(id, newState) {
      await fetch(`/api/admin/agencies/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: newState }),
      });
      loadAgencies();
    }

    async function resetPassword(id) {
      const newPassword = prompt('New password for this agency:');
      if (!newPassword) return;
      await fetch(`/api/admin/agencies/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: newPassword }),
      });
      alert('Password updated. Share it with the agency directly.');
    }

    document.getElementById('create-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const agency_name = document.getElementById('new-name').value;
      const email = document.getElementById('new-email').value;
      const password = document.getElementById('new-password').value;
      const resp = await fetch('/api/admin/agencies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agency_name, email, password }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        alert(err.detail || 'Could not create agency.');
        return;
      }
      document.getElementById('create-form').reset();
      loadAgencies();
    });

    loadAgencies();
  </script>
</body>
</html>
```

- [ ] **Step 6: Manual smoke check**

Run the app locally (`uvicorn app.main:app --reload`), create an admin account directly via a Python shell (`from app.services.auth import hash_password; from app.models import AdminAccount; from sqlmodel import Session; from app.db import engine; s = Session(engine); s.add(AdminAccount(email="you@example.com", password_hash=hash_password("your-password"))); s.commit()`), log in at `/login`, confirm redirect to `/admin/agencies`, create a test agency, confirm it can log in at `/login` and lands on `/` with only its own (empty) job list visible.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/static/admin_agencies.html tests/test_admin_agencies.py
git commit -m "feat: add admin agency-management screen and API"
```

---

### Task 7: Redirect unauthenticated dashboard access to `/login`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_auth_scoping.py` (append)

The dashboard page itself (`GET /`) currently serves `index.html` unconditionally — an unauthenticated visitor sees the shell UI, which then fails its API calls with 401s rather than being redirected to log in. This task closes that gap with a clean redirect.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth_scoping.py`:

```python
def test_dashboard_redirects_to_login_when_not_authenticated(api_client):
    resp = api_client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


def test_dashboard_serves_normally_when_authenticated(api_client):
    import app.db as db_mod
    from sqlmodel import Session

    from app.models import AgentProfile
    from app.services.auth import hash_password

    with Session(db_mod.engine) as session:
        session.add(AgentProfile(agency_name="Thornes", email="a@thornes.org.uk", password_hash=hash_password("pw")))
        session.commit()

    api_client.post("/api/login", json={"email": "a@thornes.org.uk", "password": "pw"})
    resp = api_client.get("/", follow_redirects=False)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_scoping.py -v -k dashboard`
Expected: FAIL — `/` currently always returns 200

- [ ] **Step 3: Implement the redirect**

Replace the `dashboard_page` route (originally lines 44-46) in `app/main.py`:

```python
@app.get("/")
def dashboard_page(request: Request):
    from fastapi.responses import RedirectResponse

    from .services.auth import SESSION_COOKIE_NAME, decode_session_cookie

    token = request.cookies.get(SESSION_COOKIE_NAME)
    payload = decode_session_cookie(token) if token else None
    if payload is None or payload["account_type"] != "agency":
        return RedirectResponse("/login", status_code=307)
    return FileResponse(_STATIC_DIR / "index.html")
```

Add `Request` to the existing `fastapi` import line at the top of `app/main.py`:

```python
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_scoping.py -v -k dashboard`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_auth_scoping.py
git commit -m "feat: redirect unauthenticated dashboard access to /login"
```

---

## Post-plan manual verification

After all tasks complete, verify live (not just via pytest) using two browser sessions or a private/incognito window:

1. Visit `/` while logged out → redirected to `/login`.
2. Log in as an admin → land on `/admin/agencies`, create two agencies.
3. Log in as Agency A → create a job → confirm it appears in the list.
4. Log out, log in as Agency B → confirm Agency A's job is NOT visible in the list, and visiting its direct URL (`/api/jobs/{id}` in a new tab or via devtools) returns 404.
5. Visit `/admin/integrations` while logged in as Agency A → confirm 401/redirect, not the panel.
