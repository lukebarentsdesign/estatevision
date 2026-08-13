# Staff Members (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `StaffMember` model (up to 5 per agency, each with their own HeyGen avatar ID, ElevenLabs voice ID, and voice consent), plus admin-only CRUD for managing staff and agency branding colors — restoring editing capability lost when the insecure `/api/agents` endpoint was removed, without touching the video pipeline.

**Architecture:** A new `StaffMember` table (foreign key to `AgentProfile`), a re-targeted copy of `services/consent.py`'s functions operating on `StaffMember` instead of `AgentProfile`, new admin-gated routes under `/api/admin/agencies/{id}/staff`, an extension to the existing `PATCH /api/admin/agencies/{id}` for branding colors, and a UI extension to the existing `app/static/admin_agencies.html` page. The pipeline (`pipeline/registry.py`, `pipeline/steps/*`, `services/consent.py`'s existing `AgentProfile`-facing functions) is untouched — this phase is purely additive.

**Tech Stack:** FastAPI, SQLModel/SQLite, `pytest` + `fastapi.testclient.TestClient` (all existing, matching the agency/admin auth plan's conventions).

---

## File Structure

- `app/models.py` — modify: add `StaffMember` table
- `app/services/consent.py` — modify: add `StaffMember`-facing equivalents of `set_elevenlabs_voice`/`require_voice_for_narration`/`require_avatar`, alongside (not replacing) the existing `AgentProfile`-facing ones
- `app/main.py` — modify: extend `UpdateAgencyRequest` with branding colors; add staff CRUD routes
- `app/static/admin_agencies.html` — modify: add branding-color fields to agency editing, add a staff roster section
- `tests/test_staff_members.py` — create: model + consent-function tests
- `tests/test_admin_staff_routes.py` — create: API route tests

---

### Task 1: `StaffMember` model

**Files:**
- Modify: `app/models.py` (add after `AdminAccount`, end of file)
- Test: `tests/test_staff_members.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_staff_members.py -v`
Expected: FAIL with `ImportError: cannot import name 'StaffMember'`

- [ ] **Step 3: Add the model**

Append to `app/models.py`, after the `AdminAccount` class at the end of the file:

```python


class StaffMember(SQLModel, table=True):
    """One agency staff member's presenter identity: their own HeyGen avatar
    and ElevenLabs voice, reused across every property job they present
    (spec: staff members phase 1 design, 2026-08-13).

    Consent is tracked per staff member, not per agency, since it concerns a
    specific person's cloned voice -- see `services.consent`'s StaffMember-
    facing functions, which are the only sanctioned way to set
    `elevenlabs_voice_id`.

    Capped at 5 rows per `agent_id`, enforced by the API layer (not a DB
    constraint -- SQLite has no portable per-group row-count check).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agentprofile.id")

    staff_name: str
    heygen_avatar_id: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    voice_consent_confirmed: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_staff_members.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_staff_members.py
git commit -m "feat: add StaffMember model"
```

---

### Task 2: `StaffMember`-facing consent functions

**Files:**
- Modify: `app/services/consent.py`
- Test: `tests/test_staff_members.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_staff_members.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_staff_members.py -v`
Expected: FAIL with `ImportError: cannot import name 'require_avatar_for_staff'`

- [ ] **Step 3: Add the StaffMember-facing functions**

Append to `app/services/consent.py`:

```python


def set_elevenlabs_voice_for_staff(
    staff: "StaffMember",
    voice_id: str | None,
    *,
    consent_confirmed: bool,
) -> "StaffMember":
    """Attach an ElevenLabs voice ID to a staff member, refusing without
    confirmed consent. Same rule as `set_elevenlabs_voice`, re-targeted at a
    `StaffMember` instead of an `AgentProfile` -- consent concerns a specific
    person's voice, so it is tracked per staff member (spec: staff members
    phase 1 design, 2026-08-13).
    """
    if voice_id is None:
        staff.elevenlabs_voice_id = None
        staff.voice_consent_confirmed = False
        return staff

    if not consent_confirmed:
        raise ConsentError(
            "Cannot store an ElevenLabs voice ID without confirmed consent. "
            "Spec §1.3 requires explicit, specific consent for reusable AI voice "
            "cloning from the person being cloned."
        )

    staff.elevenlabs_voice_id = voice_id
    staff.voice_consent_confirmed = True
    return staff


def require_voice_for_staff(staff: "StaffMember") -> str:
    """Return the voice ID to narrate with for this staff member, or refuse."""
    if not staff.elevenlabs_voice_id or not staff.voice_consent_confirmed:
        raise ConsentError(
            f"Staff member {staff.id} has no consented ElevenLabs voice. "
            "Record consent (§1.3) before running a voice-only job."
        )
    return staff.elevenlabs_voice_id


def require_avatar_for_staff(staff: "StaffMember") -> str:
    """Return the HeyGen avatar ID for this staff member, or refuse."""
    if not staff.heygen_avatar_id:
        raise ConsentError(
            f"Staff member {staff.id} has no verified HeyGen avatar. Avatar jobs "
            "require a clone created through HeyGen's identity-verification "
            "workflow (§1.3)."
        )
    return staff.heygen_avatar_id
```

Add `StaffMember` to the existing `TYPE_CHECKING`-style import at the top of `app/services/consent.py` — change:

```python
from ..models import AgentProfile
```

to:

```python
from ..models import AgentProfile, StaffMember
```

(This makes the `"StaffMember"` string annotations above resolvable as real type hints too — remove the quotes around `StaffMember` in the three new function signatures once the import is real, i.e. `staff: StaffMember` not `staff: "StaffMember"`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_staff_members.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full suite to confirm no regressions to the existing `AgentProfile`-facing consent functions**

Run: `pytest -q`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/consent.py tests/test_staff_members.py
git commit -m "feat: add StaffMember-facing consent functions"
```

---

### Task 3: Staff CRUD API routes

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_admin_staff_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_admin_staff_routes.py
from __future__ import annotations


def _create_agency(admin_client) -> int:
    resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Thornes", "email": "thornes@agency.example", "password": "pw"},
    )
    return resp.json()["id"]


def test_admin_can_create_staff_member(admin_client):
    agency_id = _create_agency(admin_client)
    resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff",
        json={"staff_name": "Jane Doe"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["staff_name"] == "Jane Doe"
    assert body["agent_id"] == agency_id
    assert body["heygen_avatar_id"] is None
    assert body["voice_consent_confirmed"] is False


def test_admin_can_set_voice_with_consent(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}/staff/{staff_id}",
        json={"elevenlabs_voice_id": "voice-123", "voice_consent_confirmed": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["elevenlabs_voice_id"] == "voice-123"
    assert body["voice_consent_confirmed"] is True


def test_admin_cannot_set_voice_without_consent(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}/staff/{staff_id}",
        json={"elevenlabs_voice_id": "voice-123", "voice_consent_confirmed": False},
    )
    assert resp.status_code == 400


def test_admin_can_list_staff(admin_client):
    agency_id = _create_agency(admin_client)
    admin_client.post(f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"})
    admin_client.post(f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "John Smith"})

    resp = admin_client.get(f"/api/admin/agencies/{agency_id}/staff")
    assert resp.status_code == 200
    names = [s["staff_name"] for s in resp.json()]
    assert names == ["Jane Doe", "John Smith"]


def test_admin_can_delete_staff(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.delete(f"/api/admin/agencies/{agency_id}/staff/{staff_id}")
    assert resp.status_code == 200

    list_resp = admin_client.get(f"/api/admin/agencies/{agency_id}/staff")
    assert list_resp.json() == []


def test_admin_cannot_exceed_five_staff(admin_client):
    agency_id = _create_agency(admin_client)
    for i in range(5):
        resp = admin_client.post(
            f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": f"Staff {i}"}
        )
        assert resp.status_code == 201

    resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Staff 6"}
    )
    assert resp.status_code == 400


def test_non_admin_cannot_manage_staff(agency_client):
    resp = agency_client.get("/api/admin/agencies/1/staff")
    assert resp.status_code == 401


def test_admin_can_update_agency_branding_colors(admin_client):
    agency_id = _create_agency(admin_client)
    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}",
        json={"primary_color": "#ff0000", "secondary_color": "#00ff00"},
    )
    assert resp.status_code == 200
    assert resp.json()["primary_color"] == "#ff0000"
    assert resp.json()["secondary_color"] == "#00ff00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_staff_routes.py -v`
Expected: FAIL with 404s (routes don't exist) and a `KeyError`/`AssertionError` for `primary_color` (not yet returned by `_serialize_agency`)

- [ ] **Step 3: Extend `UpdateAgencyRequest` and `_serialize_agency` for branding colors**

In `app/main.py`, replace the `UpdateAgencyRequest` class (currently lines 112-114):

```python
class UpdateAgencyRequest(BaseModel):
    is_active: Optional[bool] = None
    new_password: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
```

Replace `_serialize_agency` (currently lines 117-124) to include the color fields:

```python
def _serialize_agency(agent: AgentProfile) -> dict:
    return {
        "id": agent.id,
        "agency_name": agent.agency_name,
        "email": agent.email,
        "is_active": agent.is_active,
        "primary_color": agent.primary_color,
        "secondary_color": agent.secondary_color,
    }
```

Replace `update_admin_agency` (currently lines 160-179) to apply the new fields:

```python
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
    if body.primary_color is not None:
        agent.primary_color = body.primary_color
    if body.secondary_color is not None:
        agent.secondary_color = body.secondary_color

    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _serialize_agency(agent)
```

- [ ] **Step 4: Add the staff CRUD routes**

Add to `app/main.py`, immediately after `update_admin_agency` (after the code from Step 3):

```python


class CreateStaffRequest(BaseModel):
    staff_name: str


class UpdateStaffRequest(BaseModel):
    staff_name: Optional[str] = None
    heygen_avatar_id: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    voice_consent_confirmed: Optional[bool] = None


_MAX_STAFF_PER_AGENCY = 5


def _serialize_staff(staff: StaffMember) -> dict:
    return {
        "id": staff.id,
        "agent_id": staff.agent_id,
        "staff_name": staff.staff_name,
        "heygen_avatar_id": staff.heygen_avatar_id,
        "elevenlabs_voice_id": staff.elevenlabs_voice_id,
        "voice_consent_confirmed": staff.voice_consent_confirmed,
    }


@app.get("/api/admin/agencies/{agency_id}/staff")
def list_agency_staff(
    agency_id: int,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict]:
    agent = session.get(AgentProfile, agency_id)
    if agent is None:
        raise HTTPException(404, "agency not found")
    staff = session.exec(
        select(StaffMember).where(StaffMember.agent_id == agency_id).order_by(StaffMember.id)
    ).all()
    return [_serialize_staff(s) for s in staff]


@app.post("/api/admin/agencies/{agency_id}/staff", status_code=201)
def create_agency_staff(
    agency_id: int,
    body: CreateStaffRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    agent = session.get(AgentProfile, agency_id)
    if agent is None:
        raise HTTPException(404, "agency not found")

    existing_count = len(
        session.exec(select(StaffMember).where(StaffMember.agent_id == agency_id)).all()
    )
    if existing_count >= _MAX_STAFF_PER_AGENCY:
        raise HTTPException(
            400, f"agency already has the maximum of {_MAX_STAFF_PER_AGENCY} staff members"
        )

    staff = StaffMember(agent_id=agency_id, staff_name=body.staff_name)
    session.add(staff)
    session.commit()
    session.refresh(staff)
    return _serialize_staff(staff)


@app.patch("/api/admin/agencies/{agency_id}/staff/{staff_id}")
def update_agency_staff(
    agency_id: int,
    staff_id: int,
    body: UpdateStaffRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    staff = session.get(StaffMember, staff_id)
    if staff is None or staff.agent_id != agency_id:
        raise HTTPException(404, "staff member not found")

    if body.staff_name is not None:
        staff.staff_name = body.staff_name
    if body.heygen_avatar_id is not None:
        staff.heygen_avatar_id = body.heygen_avatar_id

    if body.elevenlabs_voice_id is not None:
        try:
            set_elevenlabs_voice_for_staff(
                staff, body.elevenlabs_voice_id, consent_confirmed=bool(body.voice_consent_confirmed)
            )
        except ConsentError as exc:
            raise HTTPException(400, str(exc)) from exc

    session.add(staff)
    session.commit()
    session.refresh(staff)
    return _serialize_staff(staff)


@app.delete("/api/admin/agencies/{agency_id}/staff/{staff_id}")
def delete_agency_staff(
    agency_id: int,
    staff_id: int,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    staff = session.get(StaffMember, staff_id)
    if staff is None or staff.agent_id != agency_id:
        raise HTTPException(404, "staff member not found")
    session.delete(staff)
    session.commit()
    return {"deleted": True}
```

Add the required imports. In `app/main.py`, add `StaffMember` to the existing model import line (find the line starting `from .models import AdminAccount, AgentProfile, ...` and add `StaffMember` to it, keeping it alphabetically consistent with the existing style):

```python
from .models import AdminAccount, AgentProfile, JobStatus, Photo, PropertyJob, ScriptSegment, StaffMember
```

Add the consent imports near the top of `app/main.py`, alongside other `from .services.*` imports:

```python
from .services.consent import ConsentError, set_elevenlabs_voice_for_staff
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_admin_staff_routes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_admin_staff_routes.py
git commit -m "feat: add admin staff CRUD routes and agency branding-color editing"
```

---

### Task 4: Admin UI — staff roster and branding colors

**Files:**
- Modify: `app/static/admin_agencies.html`

- [ ] **Step 1: Add branding-color fields and a staff section to each agency card**

Replace the entire `loadAgencies()` function and the script block's structure in `app/static/admin_agencies.html` to render an expandable detail section per agency. Replace the existing `<script>` block's `loadAgencies` function (currently lines 40-61) with:

```javascript
    let expandedAgencyId = null;

    async function loadAgencies() {
      const resp = await fetch('/api/admin/agencies');
      const agencies = await resp.json();
      const list = document.getElementById('agency-list');
      list.innerHTML = (await Promise.all(agencies.map(renderAgencyCard))).join('');
    }

    async function renderAgencyCard(a) {
      const isExpanded = expandedAgencyId === a.id;
      const staffHtml = isExpanded ? await renderStaffSection(a.id) : '';
      return `
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
          <div class="flex items-center justify-between">
            <div>
              <div class="font-medium">${escapeHtml(a.agency_name)}</div>
              <div class="text-sm text-slate-400">${escapeHtml(a.email)}</div>
            </div>
            <div class="flex gap-2 items-center">
              <span class="text-xs ${a.is_active ? 'text-emerald-400' : 'text-red-400'}">${a.is_active ? 'active' : 'inactive'}</span>
              <button onclick="toggleActive(${a.id}, ${!a.is_active})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
                ${a.is_active ? 'Deactivate' : 'Reactivate'}
              </button>
              <button onclick="resetPassword(${a.id})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
                Reset password
              </button>
              <button onclick="toggleExpanded(${a.id})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
                ${isExpanded ? 'Hide details' : 'Manage'}
              </button>
            </div>
          </div>
          ${isExpanded ? `
            <div class="border-t border-slate-800 pt-3 space-y-4">
              <div class="flex gap-3 items-end">
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Primary color</label>
                  <input type="color" id="color-primary-${a.id}" value="${escapeHtml(a.primary_color)}" class="rounded-lg bg-slate-950 border border-slate-800 h-9 w-14" />
                </div>
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Secondary color</label>
                  <input type="color" id="color-secondary-${a.id}" value="${escapeHtml(a.secondary_color)}" class="rounded-lg bg-slate-950 border border-slate-800 h-9 w-14" />
                </div>
                <button onclick="saveColors(${a.id})" class="text-xs rounded-lg bg-indigo-600 hover:bg-indigo-500 transition-colors duration-150 px-3 py-2 font-medium">
                  Save colors
                </button>
              </div>
              ${staffHtml}
            </div>
          ` : ''}
        </div>
      `;
    }

    async function renderStaffSection(agencyId) {
      const resp = await fetch(`/api/admin/agencies/${agencyId}/staff`);
      const staff = await resp.json();
      const rows = staff.map(s => `
        <div class="bg-slate-950 border border-slate-800 rounded-lg p-3 flex flex-wrap gap-2 items-end">
          <div>
            <label class="block text-xs text-slate-400 mb-1">Name</label>
            <input value="${escapeHtml(s.staff_name)}" id="staff-name-${s.id}" class="rounded-lg bg-slate-900 border border-slate-700 px-2 py-1 text-sm w-32" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">HeyGen avatar ID</label>
            <input value="${escapeHtml(s.heygen_avatar_id || '')}" id="staff-avatar-${s.id}" class="rounded-lg bg-slate-900 border border-slate-700 px-2 py-1 text-sm w-40" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">ElevenLabs voice ID</label>
            <input value="${escapeHtml(s.elevenlabs_voice_id || '')}" id="staff-voice-${s.id}" class="rounded-lg bg-slate-900 border border-slate-700 px-2 py-1 text-sm w-40" />
          </div>
          <label class="flex items-center gap-1 text-xs text-slate-400">
            <input type="checkbox" id="staff-consent-${s.id}" ${s.voice_consent_confirmed ? 'checked' : ''} />
            Voice consent confirmed
          </label>
          <button onclick="saveStaff(${agencyId}, ${s.id})" class="text-xs rounded-lg border border-slate-700 px-2 py-1 hover:bg-slate-800 transition-colors duration-150">
            Save
          </button>
          <button onclick="deleteStaff(${agencyId}, ${s.id})" class="text-xs rounded-lg border border-red-900 text-red-400 px-2 py-1 hover:bg-red-950 transition-colors duration-150">
            Remove
          </button>
        </div>
      `).join('');
      const addDisabled = staff.length >= 5;
      return `
        <div class="space-y-2">
          <div class="text-xs text-slate-400 uppercase tracking-wide">Staff (${staff.length}/5)</div>
          ${rows}
          <button onclick="addStaff(${agencyId})" ${addDisabled ? 'disabled' : ''} class="text-xs rounded-lg border border-slate-700 px-2 py-1 ${addDisabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-slate-800'} transition-colors duration-150">
            + Add staff member
          </button>
        </div>
      `;
    }

    function toggleExpanded(id) {
      expandedAgencyId = expandedAgencyId === id ? null : id;
      loadAgencies();
    }

    async function saveColors(id) {
      const primary_color = document.getElementById(`color-primary-${id}`).value;
      const secondary_color = document.getElementById(`color-secondary-${id}`).value;
      await fetch(`/api/admin/agencies/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ primary_color, secondary_color }),
      });
      loadAgencies();
    }

    async function addStaff(agencyId) {
      const staff_name = prompt('New staff member name:');
      if (!staff_name) return;
      const resp = await fetch(`/api/admin/agencies/${agencyId}/staff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ staff_name }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        alert(err.detail || 'Could not add staff member.');
        return;
      }
      loadAgencies();
    }

    async function saveStaff(agencyId, staffId) {
      const staff_name = document.getElementById(`staff-name-${staffId}`).value;
      const heygen_avatar_id = document.getElementById(`staff-avatar-${staffId}`).value || null;
      const elevenlabs_voice_id = document.getElementById(`staff-voice-${staffId}`).value || null;
      const voice_consent_confirmed = document.getElementById(`staff-consent-${staffId}`).checked;
      const resp = await fetch(`/api/admin/agencies/${agencyId}/staff/${staffId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ staff_name, heygen_avatar_id, elevenlabs_voice_id, voice_consent_confirmed }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        alert(err.detail || 'Could not save staff member.');
        return;
      }
      loadAgencies();
    }

    async function deleteStaff(agencyId, staffId) {
      await fetch(`/api/admin/agencies/${agencyId}/staff/${staffId}`, { method: 'DELETE' });
      loadAgencies();
    }
```

Note: `renderAgencyCard` is `async` and `loadAgencies` now `await`s each card via `Promise.all` because `renderStaffSection` needs its own `fetch` call for the expanded agency. This keeps the page's existing "fetch-on-demand, re-render whole list" pattern rather than introducing client-side state management, consistent with the rest of this codebase's minimal-JS style established in `index.html`.

- [ ] **Step 2: Manual smoke check**

Launch the app (`uvicorn app.main:app --reload` from a shell in the project root, using a scratch/throwaway DB via `PROPERTY_STUDIO_DB` env var so this doesn't touch any real data), log in as an admin (create one first via a Python shell if needed, following the same pattern used in the agency/admin auth plan's Task 6 manual check), open `/admin/agencies`, create a test agency, click "Manage", set colors and save, add a staff member, set their HeyGen avatar ID and ElevenLabs voice ID with consent checked, save, reload the page and confirm the values persisted, remove the staff member, confirm the list updates. Stop the server after.

- [ ] **Step 3: Run the full suite one more time**

Run: `pytest -q`
Expected: All tests PASS (this task only touches HTML/JS, so this just confirms nothing else broke)

- [ ] **Step 4: Commit**

```bash
git add app/static/admin_agencies.html
git commit -m "feat: add staff roster and branding-color editing to admin agencies UI"
```
