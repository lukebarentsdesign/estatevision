# Multi-vendor integration selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin register more than one vendor per production category (avatar, TTS, hero-shot animation) and pick which one is active from the admin panel, with pipeline steps automatically using whichever vendor is active.

**Architecture:** Add a `category_key` field to `IntegrationDefinition` so multiple vendor definitions can share a category. Add a new `ActiveVendorChoice` table + `services/active_vendor.py` to store/read which vendor slug is active per category, defaulting to the first-registered vendor when unset. Add `clients/dispatch.py` with one `get_active_<category>_client()` function per category that pipeline steps call instead of naming a vendor directly. Extend the admin API and `admin_integrations.html` to show a radio selector per category and let the admin switch it. Add a small static preset list for the OpenAI base_url field.

**Tech Stack:** FastAPI, SQLModel/SQLite, vanilla JS admin panel (existing stack — no new dependencies).

---

## Spec coverage checklist (for self-review, not part of the plan body)

- §1 category grouping → Task 1
- §2 active vendor storage & lookup → Task 2
- §3 client dispatch → Task 4 (avatar), Task 5 (tts), Task 6 (hero-shot)
- §4 admin UI + endpoints → Task 7 (API), Task 8 (frontend)
- §5 backward compatibility → covered by Task 2's fallback behavior + Task 9 regression check
- §6 script-generation presets → Task 10

---

### Task 1: Add `category_key` to `IntegrationDefinition` and tag existing integrations

**Files:**
- Modify: `app/services/integration_registry.py`
- Test: `tests/test_integration_registry.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_integration_registry.py
from __future__ import annotations

from app.services.integration_registry import list_integrations


def test_every_integration_has_a_category_key() -> None:
    for definition in list_integrations():
        assert definition.category_key, f"{definition.slug} has no category_key"


def test_avatar_and_tts_and_hero_shot_categories_have_expected_members() -> None:
    by_category: dict[str, list[str]] = {}
    for d in list_integrations():
        by_category.setdefault(d.category_key, []).append(d.slug)

    assert by_category["avatar"] == ["heygen"]
    assert by_category["tts"] == ["elevenlabs"]
    assert by_category["hero_shot_animation"] == ["gemini_omni"]
    assert by_category["script_generation"] == ["openai"]
    assert by_category["aerial_flyover"] == ["google_3d_tiles"]
    assert by_category["schools_data"] == ["schools_api"]
    assert by_category["broadband_data"] == ["ofcom_broadband"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_registry.py -v`
Expected: FAIL with `AttributeError: 'IntegrationDefinition' object has no attribute 'category_key'` (or similar — the field doesn't exist yet).

- [ ] **Step 3: Add `category_key` field and tag every entry**

In `app/services/integration_registry.py`, add the field to the dataclass:

```python
@dataclass(frozen=True)
class IntegrationDefinition:
    slug: str                    # storage key, e.g. "heygen"
    name: str                    # display name, e.g. "HeyGen"
    category: str                # groups the admin UI list (display label)
    category_key: str            # stable machine id; multiple vendors can share one
    description: str
    fields: tuple[CredentialField, ...]
    docs_url: str = ""
    test_mode: ConnectionTestMode = ConnectionTestMode.FORMAT_ONLY
    used_by: tuple[str, ...] = field(default_factory=tuple)  # spec sections / pipeline steps
```

Then add `category_key=` to each of the 7 existing `IntegrationDefinition(...)` entries in the same file:

- `heygen` → `category_key="avatar"`
- `elevenlabs` → `category_key="tts"`
- `gemini_omni` → `category_key="hero_shot_animation"`
- `openai` → `category_key="script_generation"`
- `google_3d_tiles` → `category_key="aerial_flyover"`
- `schools_api` → `category_key="schools_data"`
- `ofcom_broadband` → `category_key="broadband_data"`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_registry.py -v`
Expected: PASS

- [ ] **Step 5: Run full existing test suite to check nothing else broke**

Run: `pytest tests/ -v`
Expected: All existing tests still PASS (dataclass gained a required field, but every call site is a literal in the same file, all updated in Step 3).

- [ ] **Step 6: Commit**

```bash
git add app/services/integration_registry.py tests/test_integration_registry.py
git commit -m "feat: add category_key to IntegrationDefinition"
```

---

### Task 2: `ActiveVendorChoice` model + `services/active_vendor.py`

**Files:**
- Modify: `app/models.py`
- Create: `app/services/active_vendor.py`
- Test: `tests/test_active_vendor.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_active_vendor.py
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.active_vendor import get_active_vendor, set_active_vendor


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_active_vendor_defaults_to_first_registered(session) -> None:
    # "avatar" category currently has only "heygen" registered.
    assert get_active_vendor(session, "avatar") == "heygen"


def test_set_then_get_active_vendor_round_trips(session) -> None:
    set_active_vendor(session, "avatar", "heygen")
    assert get_active_vendor(session, "avatar") == "heygen"


def test_set_active_vendor_rejects_slug_from_wrong_category(session) -> None:
    with pytest.raises(ValueError):
        set_active_vendor(session, "avatar", "elevenlabs")  # elevenlabs is "tts", not "avatar"


def test_set_active_vendor_rejects_unknown_slug(session) -> None:
    with pytest.raises(ValueError):
        set_active_vendor(session, "avatar", "not_a_real_vendor")


def test_get_active_vendor_rejects_unknown_category(session) -> None:
    with pytest.raises(ValueError):
        get_active_vendor(session, "not_a_real_category")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_active_vendor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.active_vendor'`

- [ ] **Step 3: Add the `ActiveVendorChoice` model**

In `app/models.py`, add after `IntegrationCredential`:

```python
class ActiveVendorChoice(SQLModel, table=True):
    """Which registered vendor is active for a given category_key.

    Absence of a row means "use the first-registered vendor in that
    category" -- see `services.active_vendor.get_active_vendor`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    category_key: str = Field(index=True, unique=True)
    vendor_slug: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Write `app/services/active_vendor.py`**

```python
"""Which vendor is active per integration category (avatar, tts, etc.).

Categories with only one registered vendor need no explicit choice stored --
`get_active_vendor` falls back to the first `IntegrationDefinition` with that
`category_key`, so existing single-vendor categories work unchanged.
"""

from __future__ import annotations

from sqlmodel import Session, select

from ..models import ActiveVendorChoice
from .integration_registry import list_integrations


def _vendors_in_category(category_key: str) -> list[str]:
    slugs = [d.slug for d in list_integrations() if d.category_key == category_key]
    if not slugs:
        raise ValueError(f"Unknown category_key {category_key!r}")
    return slugs


def get_active_vendor(session: Session, category_key: str) -> str:
    """Returns the slug of the active vendor for a category.

    Falls back to the first-registered IntegrationDefinition with that
    category_key if no explicit choice has been stored.
    """
    vendors = _vendors_in_category(category_key)

    stmt = select(ActiveVendorChoice).where(ActiveVendorChoice.category_key == category_key)
    row = session.exec(stmt).first()
    if row is not None and row.vendor_slug in vendors:
        return row.vendor_slug
    return vendors[0]


def set_active_vendor(session: Session, category_key: str, vendor_slug: str) -> None:
    """Stores the active vendor choice. Validates that `vendor_slug` is a
    registered integration belonging to `category_key`."""
    vendors = _vendors_in_category(category_key)
    if vendor_slug not in vendors:
        raise ValueError(
            f"{vendor_slug!r} is not a registered vendor in category {category_key!r} "
            f"(known vendors: {vendors})"
        )

    stmt = select(ActiveVendorChoice).where(ActiveVendorChoice.category_key == category_key)
    row = session.exec(stmt).first()
    if row is None:
        row = ActiveVendorChoice(category_key=category_key, vendor_slug=vendor_slug)
    else:
        row.vendor_slug = vendor_slug
    session.add(row)
    session.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_active_vendor.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/services/active_vendor.py tests/test_active_vendor.py
git commit -m "feat: add ActiveVendorChoice model and active_vendor service"
```

---

### Task 3: `clients/dispatch.py` scaffold + avatar dispatch

**Files:**
- Create: `app/clients/dispatch.py`
- Test: `tests/test_client_dispatch.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_dispatch.py
from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.clients.dispatch import get_active_avatar_client
from app.clients.heygen import StubHeyGenClient


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_active_avatar_client_returns_heygen_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)
    client = get_active_avatar_client(session=session)
    assert isinstance(client, StubHeyGenClient)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.clients.dispatch'`

- [ ] **Step 3: Write `app/clients/dispatch.py`**

```python
"""Category -> active-vendor client dispatch.

Pipeline steps call these instead of naming a vendor's client factory
directly (e.g. `get_heygen_client()`), so switching the active vendor in the
admin panel (`services.active_vendor`) takes effect without editing pipeline
code. Adding a new vendor to a category means writing a client module that
implements the same Protocol as the existing vendor(s) in that category,
registering it in `integration_registry.py` with a matching `category_key`,
and adding one branch below.
"""

from __future__ import annotations

from sqlmodel import Session

from ..services.active_vendor import get_active_vendor
from .heygen import HeyGenClient, get_heygen_client


def _resolve_session(session: Session | None) -> Session:
    if session is not None:
        return session
    from ..db import engine

    return Session(engine)


def get_active_avatar_client(*, session: Session | None = None) -> HeyGenClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "avatar")
        if vendor == "heygen":
            return get_heygen_client(session=s)
        raise ValueError(f"No client dispatch registered for avatar vendor {vendor!r}")
    finally:
        if owned:
            s.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_client_dispatch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/clients/dispatch.py tests/test_client_dispatch.py
git commit -m "feat: add avatar client dispatch by active vendor"
```

---

### Task 4: Wire `avatar_and_captions.py` to dispatch, add tts and hero-shot dispatch

**Files:**
- Modify: `app/clients/dispatch.py`
- Modify: `app/pipeline/steps/avatar_and_captions.py:12,49`
- Modify: `app/pipeline/steps/motion.py:12,27`
- Modify: `app/pipeline/steps/script_and_voice.py:15,105`
- Test: `tests/test_client_dispatch.py`

- [ ] **Step 1: Write the failing tests for tts and hero-shot dispatch**

Append to `tests/test_client_dispatch.py`:

```python
from app.clients.dispatch import get_active_hero_shot_client, get_active_tts_client
from app.clients.elevenlabs import StubElevenLabsClient
from app.clients.gemini_omni import StubGeminiOmniClient


def test_get_active_tts_client_returns_elevenlabs_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = get_active_tts_client(session=session)
    assert isinstance(client, StubElevenLabsClient)


def test_get_active_hero_shot_client_returns_gemini_by_default(session, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = get_active_hero_shot_client(session=session)
    assert isinstance(client, StubGeminiOmniClient)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client_dispatch.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_active_hero_shot_client'`

- [ ] **Step 3: Add the two new dispatch functions**

Append to `app/clients/dispatch.py`:

```python
from .elevenlabs import ElevenLabsClient, get_elevenlabs_client
from .gemini_omni import GeminiOmniClient, get_gemini_omni_client


def get_active_tts_client(*, session: Session | None = None) -> ElevenLabsClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "tts")
        if vendor == "elevenlabs":
            return get_elevenlabs_client(session=s)
        raise ValueError(f"No client dispatch registered for tts vendor {vendor!r}")
    finally:
        if owned:
            s.close()


def get_active_hero_shot_client(*, session: Session | None = None) -> GeminiOmniClient:
    owned = session is None
    s = _resolve_session(session)
    try:
        vendor = get_active_vendor(s, "hero_shot_animation")
        if vendor == "gemini_omni":
            return get_gemini_omni_client(session=s)
        raise ValueError(f"No client dispatch registered for hero_shot_animation vendor {vendor!r}")
    finally:
        if owned:
            s.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_client_dispatch.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update the three pipeline steps to use dispatch**

In `app/pipeline/steps/avatar_and_captions.py`, change line 12:

```python
from ...clients.dispatch import get_active_avatar_client
```

and line 49 (inside `AvatarIntroStep.run`):

```python
        client = get_active_avatar_client()
```

In `app/pipeline/steps/motion.py`, change line 12:

```python
from ...clients.dispatch import get_active_hero_shot_client
```

and line 27:

```python
        gemini = get_active_hero_shot_client()
```

In `app/pipeline/steps/script_and_voice.py`, change line 15:

```python
from ...clients.dispatch import get_active_tts_client
```

and line 105 (inside `ScriptAndVoiceStep.run`):

```python
            client = get_active_tts_client()
```

- [ ] **Step 6: Run the full pipeline test suite to confirm no regressions**

Run: `pytest tests/test_pipeline_end_to_end.py tests/test_assembly.py -v`
Expected: All PASS — behavior is unchanged because dispatch falls back to the same single vendor each category had before.

- [ ] **Step 7: Commit**

```bash
git add app/clients/dispatch.py app/pipeline/steps/avatar_and_captions.py app/pipeline/steps/motion.py app/pipeline/steps/script_and_voice.py tests/test_client_dispatch.py
git commit -m "feat: route avatar/tts/hero-shot pipeline steps through active-vendor dispatch"
```

---

### Task 5: Serialize `category_key` and `is_active` in the integrations API

**Files:**
- Modify: `app/main.py:159-195`
- Test: `tests/test_integration_settings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration_settings.py`:

```python
def test_list_integrations_includes_category_key_and_is_active(api_client) -> None:
    resp = api_client.get("/api/integrations")
    data = resp.json()
    heygen = next(item for item in data if item["slug"] == "heygen")
    assert heygen["category_key"] == "avatar"
    assert heygen["is_active"] is True  # only vendor in "avatar" -> active by default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_settings.py::test_list_integrations_includes_category_key_and_is_active -v`
Expected: FAIL with `KeyError: 'category_key'`

- [ ] **Step 3: Update `_serialize_status` in `app/main.py`**

Change the function at `app/main.py:159-185` to accept the session (needed to resolve active vendor) and add the two new fields:

```python
def _serialize_status(status, session: Session) -> dict:
    d = status.definition
    from .services.active_vendor import get_active_vendor

    return {
        "slug": d.slug,
        "name": d.name,
        "category": d.category,
        "category_key": d.category_key,
        "description": d.description,
        "docs_url": d.docs_url,
        "test_mode": d.test_mode.value,
        "used_by": list(d.used_by),
        "is_fully_configured": status.is_fully_configured,
        "is_active": get_active_vendor(session, d.category_key) == d.slug,
        "fields": [
            {
                "key": fs.field.key,
                "label": fs.field.label,
                "kind": fs.field.kind.value,
                "required": fs.field.required,
                "placeholder": fs.field.placeholder,
                "help_text": fs.field.help_text,
                "is_secret": fs.field.is_secret,
                "is_set": fs.is_set,
                "masked_value": fs.masked_value,
                "source": fs.source,
            }
            for fs in status.fields
        ],
    }
```

Update the three call sites in the same file to pass `session`:

`list_integration_statuses` (around line 189):
```python
@app.get("/api/integrations")
def list_integration_statuses(session: Session = Depends(get_session)) -> list[dict]:
    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]
```

`get_integration_status` (around line 199):
```python
@app.get("/api/integrations/{slug}")
def get_integration_status(slug: str, session: Session = Depends(get_session)) -> dict:
    settings = IntegrationSettings(session)
    try:
        return _serialize_status(settings.status_for(slug), session)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
```

`set_integration_field` (around line 208-219):
```python
@app.put("/api/integrations/{slug}/fields/{field_key}")
def set_integration_field(
    slug: str, field_key: str, body: SetFieldRequest, session: Session = Depends(get_session)
) -> dict:
    settings = IntegrationSettings(session)
    try:
        settings.set_field(slug, field_key, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _serialize_status(settings.status_for(slug), session)
```

`clear_integration_field` (around line 222-227):
```python
@app.delete("/api/integrations/{slug}/fields/{field_key}")
def clear_integration_field(
    slug: str, field_key: str, session: Session = Depends(get_session)
) -> dict:
    settings = IntegrationSettings(session)
    settings.clear_field(slug, field_key)
    return _serialize_status(settings.status_for(slug), session)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_settings.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_integration_settings.py
git commit -m "feat: include category_key and is_active in integrations API"
```

---

### Task 6: `PUT /api/categories/{category_key}/active-vendor` endpoint

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_integration_settings.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration_settings.py`:

```python
def test_set_active_vendor_endpoint_updates_is_active(api_client) -> None:
    resp = api_client.put("/api/categories/avatar/active-vendor", json={"slug": "heygen"})
    assert resp.status_code == 200
    data = resp.json()
    heygen = next(item for item in data if item["slug"] == "heygen")
    assert heygen["is_active"] is True


def test_set_active_vendor_endpoint_rejects_wrong_category(api_client) -> None:
    resp = api_client.put("/api/categories/avatar/active-vendor", json={"slug": "elevenlabs"})
    assert resp.status_code == 400


def test_set_active_vendor_endpoint_rejects_unknown_category(api_client) -> None:
    resp = api_client.put("/api/categories/not_a_real_category/active-vendor", json={"slug": "heygen"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_settings.py::test_set_active_vendor_endpoint_updates_is_active -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Add the endpoint**

In `app/main.py`, add near the other integration endpoints (after `clear_integration_field`):

```python
class SetActiveVendorRequest(BaseModel):
    slug: str


@app.put("/api/categories/{category_key}/active-vendor")
def set_category_active_vendor(
    category_key: str, body: SetActiveVendorRequest, session: Session = Depends(get_session)
) -> list[dict]:
    from .services.active_vendor import set_active_vendor

    try:
        set_active_vendor(session, category_key, body.slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_settings.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_integration_settings.py
git commit -m "feat: add PUT /api/categories/{category_key}/active-vendor endpoint"
```

---

### Task 7: Admin panel — radio selector per category

**Files:**
- Modify: `app/static/admin_integrations.html`

- [ ] **Step 1: Add CSS for the active-vendor radio control**

In the `<style>` block, after the existing `.category-heading` rules (around line 40), add:

```css
  .vendor-active-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 18px;
    font-size: 0.82rem;
    color: var(--text-dim);
  }
  .vendor-active-row input[type="radio"] { accent-color: var(--accent); }
  .vendor-active-row label { cursor: pointer; }
  .vendor-active-row .active-badge {
    font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--ok); border: 1px solid rgba(62, 207, 142, 0.35);
    border-radius: 4px; padding: 1px 6px; margin-left: 4px;
  }
```

- [ ] **Step 2: Change `render()` to group by `category_key` and show a radio row when a category has more than one vendor**

Replace the `render()` function (current lines 178-209) with:

```javascript
function render() {
  const app = document.getElementById("app");
  const categories = [...new Set(state.map(i => i.category))];

  const configuredCount = state.filter(i => i.is_active && i.is_fully_configured).length;
  const activeCount = state.filter(i => i.is_active).length;
  document.getElementById("summary").innerHTML =
    `<span><b>${configuredCount}</b> / ${activeCount} active vendors fully configured</span>`;

  app.innerHTML = categories.map(cat => {
    const itemsInCat = state.filter(i => i.category === cat);
    const categoryKeys = [...new Set(itemsInCat.map(i => i.category_key))];
    return categoryKeys.map(catKey => {
      const vendors = itemsInCat.filter(i => i.category_key === catKey);
      const heading = vendors.length > 1 ? `${escapeHtml(cat)} — choose active vendor` : escapeHtml(cat);
      const radioRow = vendors.length > 1 ? `
        <div class="vendor-active-row">
          ${vendors.map(v => `
            <label>
              <input type="radio" name="active-${catKey}" value="${v.slug}" ${v.is_active ? "checked" : ""}>
              ${escapeHtml(v.name)}
            </label>
          `).join("")}
        </div>` : "";
      return `
        <div class="category-heading">${heading}</div>
        ${radioRow}
        ${vendors.map(renderCard).join("")}
      `;
    }).join("");
  }).join("");

  state.forEach(item => {
    const cardEl = document.getElementById(`card-${item.slug}`);
    cardEl.querySelector(".card-head").addEventListener("click", () => {
      cardEl.classList.toggle("open");
    });
    item.fields.forEach(f => {
      const saveBtn = document.getElementById(`save-${item.slug}-${f.key}`);
      const clearBtn = document.getElementById(`clear-${item.slug}-${f.key}`);
      const input = document.getElementById(`input-${item.slug}-${f.key}`);
      if (saveBtn) saveBtn.addEventListener("click", () => saveField(item.slug, f.key, input));
      if (clearBtn) clearBtn.addEventListener("click", () => clearField(item.slug, f.key));
      if (input) input.addEventListener("keydown", e => {
        if (e.key === "Enter") saveField(item.slug, f.key, input);
      });
    });
    const testBtn = document.getElementById(`test-${item.slug}`);
    if (testBtn) testBtn.addEventListener("click", () => testConn(item.slug));
  });

  document.querySelectorAll(".vendor-active-row input[type=radio]").forEach(radio => {
    radio.addEventListener("change", e => {
      if (e.target.checked) setActiveVendor(e.target.name.replace("active-", ""), e.target.value);
    });
  });
}
```

- [ ] **Step 3: Add an "Active" badge to `renderCard` and gate save/test on being the active vendor's card is unnecessary — cards for inactive vendors stay fully usable so credentials can be pre-configured**

Update `renderCard` (current lines 211-257): add the active badge next to the title. Change the `<h2>` line:

```javascript
        <h2>${escapeHtml(item.name)}${item.is_active ? '<span class="active-badge">Active</span>' : ''}</h2>
```

(No other changes to `renderCard` — inactive vendors keep full save/clear/test functionality per the spec's "backup vendor" allowance.)

- [ ] **Step 4: Add `setActiveVendor()` function**

Add near `saveField`/`clearField` (after `clearField`, current line ~284):

```javascript
async function setActiveVendor(categoryKey, slug) {
  const res = await fetch(`/api/categories/${categoryKey}/active-vendor`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    showToast(`Error: ${err.detail || res.statusText}`);
    await loadAll();
    return;
  }
  showToast(`${slug} is now active.`);
  await loadAll();
}
```

- [ ] **Step 5: Manual verification**

Run: `python -m uvicorn app.main:app --port 8813` (from the project root), then open `http://127.0.0.1:8813/admin/integrations` in a browser.
Expected: page loads with no console errors; single-vendor categories (Script Generation, Video & Image Generation → Google 3D Tiles, Location Data) show no radio row; "Avatar & Voice" and other categories render normally (still single-vendor until Task 8/9 registers a second one, so no radio row is expected yet either — this step just confirms nothing broke).

Stop the server afterward:
Run: `pkill -f "uvicorn app.main"` (or Ctrl+C in the terminal running it)

- [ ] **Step 6: Commit**

```bash
git add app/static/admin_integrations.html
git commit -m "feat: admin panel shows active-vendor radio selector per category"
```

---

### Task 8: End-to-end test — register a second avatar vendor and switch to it

This task proves the whole mechanism works together: a second vendor sharing `category_key="avatar"`, switchable via the API, and picked up by dispatch — without touching any pipeline step code again. It intentionally does NOT add a real second HTTP client; it stubs one, matching how a future real vendor addition would look far enough to prove the wiring.

**Files:**
- Test: `tests/test_multi_vendor_switch.py` (new file)

- [ ] **Step 1: Write the test**

```python
# tests/test_multi_vendor_switch.py
"""Proves the multi-vendor mechanism end-to-end: register a second vendor in
an existing category, switch to it via the service layer, and confirm
dispatch picks it up -- without any pipeline step code change.

This uses a monkeypatched registry entry rather than a real second HTTP
client module, since adding a real competing vendor is a separate, larger
task left for whenever an actual competitor is chosen (see the design spec's
"adding a new vendor" non-goal).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services import integration_registry
from app.services.integration_registry import (
    CredentialField,
    FieldKind,
    IntegrationDefinition,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def second_avatar_vendor(monkeypatch):
    """Registers a fake second vendor in the 'avatar' category for this test only."""
    fake = IntegrationDefinition(
        slug="fake_avatar_vendor",
        name="Fake Avatar Vendor",
        category="Avatar & Voice",
        category_key="avatar",
        description="Test-only second avatar vendor.",
        fields=(CredentialField("api_key", "API Key", FieldKind.API_KEY),),
    )
    new_list = integration_registry.INTEGRATIONS + (fake,)
    monkeypatch.setattr(integration_registry, "INTEGRATIONS", new_list)
    monkeypatch.setattr(integration_registry, "_BY_SLUG", {i.slug: i for i in new_list})
    return fake


def test_switching_active_vendor_changes_get_active_vendor_result(session, second_avatar_vendor) -> None:
    from app.services.active_vendor import get_active_vendor, set_active_vendor

    assert get_active_vendor(session, "avatar") == "heygen"  # first-registered default

    set_active_vendor(session, "avatar", "fake_avatar_vendor")
    assert get_active_vendor(session, "avatar") == "fake_avatar_vendor"

    set_active_vendor(session, "avatar", "heygen")
    assert get_active_vendor(session, "avatar") == "heygen"


def test_dispatch_raises_clearly_for_a_vendor_with_no_client_branch(session, second_avatar_vendor) -> None:
    from app.clients.dispatch import get_active_avatar_client
    from app.services.active_vendor import set_active_vendor

    set_active_vendor(session, "avatar", "fake_avatar_vendor")
    with pytest.raises(ValueError, match="fake_avatar_vendor"):
        get_active_avatar_client(session=session)
```

- [ ] **Step 2: Run test to verify it fails first, confirming the assertions are meaningful**

Run: `pytest tests/test_multi_vendor_switch.py -v`
Expected: PASS immediately — this task doesn't add new production code, it only proves Tasks 1-4's mechanism composes correctly. If any test fails here, it's a real bug in Tasks 1-4 to go fix (not a "write minimal implementation" step).

- [ ] **Step 3: Commit**

```bash
git add tests/test_multi_vendor_switch.py
git commit -m "test: prove active-vendor switching works end-to-end with a second registered vendor"
```

---

### Task 9: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS, including every pre-existing test file (`test_assembly.py`, `test_compliance.py`, `test_export_pack.py`, `test_integration_settings.py`, `test_local_tools.py`, `test_pipeline_contract.py`, `test_pipeline_end_to_end.py`, `test_real_clients.py`) plus the five new files from Tasks 1-8.

- [ ] **Step 2: If anything fails, fix forward**

Do not skip or comment out a failing test. If a pre-existing test breaks, it means a Task above missed a call site — find it (likely another place `IntegrationDefinition(...)` or `_serialize_status(...)` is constructed or called) and fix it, then re-run Step 1.

---

### Task 10: Script-generation base_url presets

**Files:**
- Modify: `app/services/integration_registry.py`
- Modify: `app/static/admin_integrations.html`
- Test: `tests/test_integration_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration_registry.py`:

```python
def test_openai_compatible_presets_exist() -> None:
    from app.services.integration_registry import OPENAI_COMPATIBLE_PRESETS

    names = {p["name"] for p in OPENAI_COMPATIBLE_PRESETS}
    assert "Groq" in names
    assert "OpenRouter" in names
    for preset in OPENAI_COMPATIBLE_PRESETS:
        assert preset["base_url"].startswith("https://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_registry.py::test_openai_compatible_presets_exist -v`
Expected: FAIL with `ImportError: cannot import name 'OPENAI_COMPATIBLE_PRESETS'`

- [ ] **Step 3: Add the preset list**

In `app/services/integration_registry.py`, add near the bottom of the file (after `INTEGRATIONS` and before `_BY_SLUG`):

```python
# Known OpenAI-compatible chat-completions endpoints, offered as quick-fill
# presets on the "openai" integration's base_url field in the admin panel.
# The admin still supplies their own API key -- this only saves typing the
# base URL. Sourced from providers with a documented OpenAI-compatible
# `/chat/completions` endpoint as of 2026-08.
OPENAI_COMPATIBLE_PRESETS: tuple[dict[str, str], ...] = (
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    {"name": "Groq", "base_url": "https://api.groq.com/openai/v1"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    {"name": "Together AI", "base_url": "https://api.together.xyz/v1"},
    {"name": "Cerebras", "base_url": "https://api.cerebras.ai/v1"},
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Expose presets via the API**

In `app/main.py`, add near the other integration endpoints:

```python
@app.get("/api/integrations/openai/base-url-presets")
def openai_base_url_presets() -> list[dict]:
    from .services.integration_registry import OPENAI_COMPATIBLE_PRESETS

    return list(OPENAI_COMPATIBLE_PRESETS)
```

Add a test to `tests/test_integration_settings.py`:

```python
def test_openai_base_url_presets_endpoint(api_client) -> None:
    resp = api_client.get("/api/integrations/openai/base-url-presets")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "Groq" in names
```

Run: `pytest tests/test_integration_settings.py::test_openai_base_url_presets_endpoint -v`
Expected: PASS

- [ ] **Step 6: Add the preset dropdown to the admin panel**

In `app/static/admin_integrations.html`, modify `renderCard` (the `field-input-row` block, current lines 219-229) to add a preset `<select>` only for the `openai` integration's `base_url` field. Change the field row template:

```javascript
  const fieldsHtml = item.fields.map(f => {
    const presetSelect = (item.slug === "openai" && f.key === "base_url")
      ? `<select id="preset-${item.slug}-${f.key}" style="margin-right:8px;"><option value="">Preset…</option></select>`
      : "";
    return `
    <div class="field-row">
      <div class="field-label">
        <label for="input-${item.slug}-${f.key}">${escapeHtml(f.label)}${f.required ? '<span class="req">*</span>' : ''}</label>
        <span class="field-source">${f.is_set ? (f.source === 'environment' ? 'from .env' : 'saved') : 'not set'}</span>
      </div>
      <div class="field-input-row">
        ${presetSelect}
        <input
          type="${f.is_secret ? 'password' : 'text'}"
          id="input-${item.slug}-${f.key}"
          placeholder="${f.is_set ? escapeHtml(f.masked_value || '') : escapeHtml(f.placeholder || '')}"
          autocomplete="off"
          spellcheck="false"
        >
        <button id="save-${item.slug}-${f.key}">Save</button>
        ${f.is_set && f.source === 'database' ? `<button id="clear-${item.slug}-${f.key}" class="danger-ghost">Clear</button>` : ''}
      </div>
      ${f.help_text ? `<div class="field-help">${escapeHtml(f.help_text)}</div>` : ''}
    </div>
  `;
  }).join("");
```

Then in the event-wiring section of `render()` (where `saveBtn`/`clearBtn`/`input` listeners are attached), add preset-population and change-handling:

```javascript
    item.fields.forEach(f => {
      const saveBtn = document.getElementById(`save-${item.slug}-${f.key}`);
      const clearBtn = document.getElementById(`clear-${item.slug}-${f.key}`);
      const input = document.getElementById(`input-${item.slug}-${f.key}`);
      if (saveBtn) saveBtn.addEventListener("click", () => saveField(item.slug, f.key, input));
      if (clearBtn) clearBtn.addEventListener("click", () => clearField(item.slug, f.key));
      if (input) input.addEventListener("keydown", e => {
        if (e.key === "Enter") saveField(item.slug, f.key, input);
      });
      const presetSelect = document.getElementById(`preset-${item.slug}-${f.key}`);
      if (presetSelect) {
        loadOpenAiPresets(presetSelect, input);
      }
    });
```

Add the loader function near `loadAll()`:

```javascript
let openAiPresetsCache = null;
async function loadOpenAiPresets(selectEl, inputEl) {
  if (!openAiPresetsCache) {
    const res = await fetch("/api/integrations/openai/base-url-presets");
    openAiPresetsCache = await res.json();
  }
  selectEl.innerHTML = `<option value="">Preset…</option>` +
    openAiPresetsCache.map(p => `<option value="${escapeHtml(p.base_url)}">${escapeHtml(p.name)}</option>`).join("");
  selectEl.addEventListener("change", () => {
    if (selectEl.value) inputEl.value = selectEl.value;
  });
}
```

- [ ] **Step 7: Manual verification**

Run: `python -m uvicorn app.main:app --port 8813`, open `http://127.0.0.1:8813/admin/integrations`, expand the "OpenAI (or compatible)" card.
Expected: a "Preset…" dropdown appears to the left of the Base URL input; selecting "Groq" fills the input with `https://api.groq.com/openai/v1`.

Stop the server: `pkill -f "uvicorn app.main"`

- [ ] **Step 8: Commit**

```bash
git add app/services/integration_registry.py app/main.py app/static/admin_integrations.html tests/test_integration_registry.py tests/test_integration_settings.py
git commit -m "feat: add OpenAI-compatible base_url presets to admin panel"
```

---

## Post-plan note

No git repository exists yet at the project root (`e:\Antigravity not on onedrive\Estate agent marketing idea`). Every `git commit` step in this plan will fail until `git init` is run there. Run `git init` and an initial commit of the pre-existing files before starting Task 1, or ask the user whether they'd like that done first.
