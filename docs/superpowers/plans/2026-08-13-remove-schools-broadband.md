# Remove Schools/Broadband Location Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove nearby-schools and broadband-coverage lookups from the location-data feature, leaving amenities and daylight untouched, and remove their admin-panel integration entries.

**Architecture:** Four files change: `app/services/uk_location.py` (remove the two fetch functions/dataclasses), `app/services/integration_registry.py` (remove the two integration entries), `app/static/index.html` (drop the two lines from the workspace info box), `app/pipeline/steps/cinematic.py` (drop schools/broadband from the exported microsite HTML). One existing test asserting on the two removed integration slugs is updated.

**Tech Stack:** Python/FastAPI (existing), vanilla JS (existing), `pytest` (existing).

---

## File Structure

- `app/services/uk_location.py` — modify: remove `get_nearby_schools`, `get_broadband_info`, `School`, `BroadbandInfo`, `SCHOOLS_API_BASE`, `OFCOM_API_BASE`; update `build_location_data`
- `app/services/integration_registry.py` — modify: remove `schools_api` and `ofcom_broadband` entries
- `app/static/index.html` — modify: `renderLocationData` drops schools/broadband lines
- `app/pipeline/steps/cinematic.py` — modify: `MicrositeBuilderStep._render_html` drops schools/broadband from generated HTML
- `tests/test_integration_registry.py` — modify: remove assertions on the two removed slugs
- `tests/test_uk_location.py` — create (if not already present) or modify: cover the new two-key shape of `build_location_data`

---

### Task 1: Remove schools/broadband from `uk_location.py`

**Files:**
- Modify: `app/services/uk_location.py`
- Test: `tests/test_uk_location.py`

- [ ] **Step 1: Check whether `tests/test_uk_location.py` already exists**

Run: `ls tests/test_uk_location.py` (or check via your file tools). If it exists, read it fully before proceeding — you'll need to remove any tests calling `get_nearby_schools`/`get_broadband_info` and update any test asserting on `build_location_data`'s return shape. If it doesn't exist, you'll create it fresh in Step 2.

- [ ] **Step 2: Write/update the test proving the new two-key shape**

If `tests/test_uk_location.py` exists and has a test for `build_location_data`, update it to match this. Otherwise create the file with this content (adjust imports if a fixture pattern already exists in the file):

```python
# tests/test_uk_location.py
from __future__ import annotations

from app.services import uk_location


def test_build_location_data_returns_only_amenities_and_daylight():
    result = uk_location.build_location_data(
        latitude=None,
        longitude=None,
        postcode="TE1 1ST",
        garden_orientation="south",
    )
    assert set(result.keys()) == {"amenities", "daylight"}
    assert result["daylight"]["orientation"] == "south"


def test_build_location_data_has_no_schools_or_broadband_functions():
    assert not hasattr(uk_location, "get_nearby_schools")
    assert not hasattr(uk_location, "get_broadband_info")
    assert not hasattr(uk_location, "School")
    assert not hasattr(uk_location, "BroadbandInfo")
```

If the file already existed with OTHER tests unrelated to schools/broadband (e.g. testing `get_nearby_amenities` or `get_daylight_info` directly), leave those tests as-is and only remove/update the ones touching schools/broadband/the old 4-key shape.

- [ ] **Step 3: Run the new/updated test to verify it fails**

Run: `pytest tests/test_uk_location.py -v`
Expected: FAIL — `build_location_data` currently returns 4 keys (`schools`, `amenities`, `broadband`, `daylight`), and `get_nearby_schools`/`get_broadband_info`/`School`/`BroadbandInfo` still exist

- [ ] **Step 4: Remove schools/broadband from `app/services/uk_location.py`**

Remove the `SCHOOLS_API_BASE` and `OFCOM_API_BASE` constants (currently lines 20 and 22):

```python
SCHOOLS_API_BASE = "https://get-information-schools.service.gov.uk/api"
```
```python
OFCOM_API_BASE = "https://api.checker.ofcom.org.uk"
```

Remove the `School` dataclass (currently lines 27-32):

```python
@dataclass(frozen=True)
class School:
    name: str
    phase: str
    ofsted_rating: str
    distance_km: float
```

Remove the `BroadbandInfo` dataclass (currently lines 42-47):

```python
@dataclass(frozen=True)
class BroadbandInfo:
    max_download_mbps: float | None
    fttp_available: bool
    ultrafast_available: bool
    five_g_available: bool
```

Remove the entire `get_nearby_schools` function (currently lines 65-95):

```python
def get_nearby_schools(
    latitude: float, longitude: float, *, limit: int = 3, client: httpx.Client | None = None
) -> list[School]:
    """3 nearest Outstanding/Good schools (§5.1). Empty list on failure."""
    owns_client = client is None
    client = client or httpx.Client(timeout=_REQUEST_TIMEOUT)
    try:
        resp = client.get(
            f"{SCHOOLS_API_BASE}/schools",
            params={"lat": latitude, "lon": longitude, "radius_km": 5},
        )
        resp.raise_for_status()
        raw = resp.json().get("schools", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("schools lookup failed: %s", exc)
        return []
    finally:
        if owns_client:
            client.close()

    schools = [
        School(
            name=s["name"],
            phase=s.get("phase", "unknown"),
            ofsted_rating=s.get("ofsted_rating", "unknown"),
            distance_km=_haversine_km(latitude, longitude, s["lat"], s["lon"]),
        )
        for s in raw
        if s.get("ofsted_rating") in {"Outstanding", "Good"}
    ]
    return sorted(schools, key=lambda s: s.distance_km)[:limit]
```

Remove the entire `get_broadband_info` function (currently lines 155-177):

```python
def get_broadband_info(
    postcode: str, *, client: httpx.Client | None = None
) -> BroadbandInfo:
    """Max download speed and coverage flags (§5.3). Defaults on failure."""
    owns_client = client is None
    client = client or httpx.Client(timeout=_REQUEST_TIMEOUT)
    try:
        resp = client.get(f"{OFCOM_API_BASE}/coverage", params={"postcode": postcode})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("broadband lookup failed: %s", exc)
        return BroadbandInfo(None, False, False, False)
    finally:
        if owns_client:
            client.close()

    return BroadbandInfo(
        max_download_mbps=data.get("max_download_mbps"),
        fttp_available=bool(data.get("fttp_available")),
        ultrafast_available=bool(data.get("ultrafast_available")),
        five_g_available=bool(data.get("five_g_available")),
    )
```

Replace `build_location_data` (currently lines 201-225) with:

```python
def build_location_data(
    *,
    latitude: float | None,
    longitude: float | None,
    postcode: str,
    garden_orientation: str | None,
) -> dict[str, Any]:
    """Aggregate amenities and daylight into the dict stored on `job.location_data_json`.

    Schools and broadband were removed from this aggregation (spec: remove
    schools/broadband design, 2026-08-13) -- they added marketing colour the
    user judged not worth the API surface and screen time. `postcode` is kept
    as a parameter for signature stability even though it's now unused here,
    since callers already pass it and daylight/amenities may grow a postcode-
    based source later.
    """
    schools_unused = postcode  # noqa: intentionally kept for signature stability; see docstring
    del schools_unused

    amenities: list[Amenity] = []

    if latitude is not None and longitude is not None:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            amenities = get_nearby_amenities(latitude, longitude, client=client)

    daylight = get_daylight_info(garden_orientation)

    return {
        "amenities": [asdict(a) for a in amenities],
        "daylight": asdict(daylight) if daylight else None,
    }
```

Note: the `schools_unused`/`del` lines above are a deliberately explicit way to keep `postcode` in the signature (callers in `main.py` already pass it positionally/by keyword) without an unused-parameter warning looking like an oversight. If your editor/linter doesn't flag unused parameters, you may omit those two lines and just leave `postcode` unused in the body — either is fine, but do not remove `postcode` from the signature, since `app/main.py`'s `refresh_location_data` route calls this function with `postcode=job.postcode` and removing the parameter would break that call site (out of scope for this plan to touch `main.py`).

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_uk_location.py -v`
Expected: PASS (2 tests, or more if the file had pre-existing tests you kept)

- [ ] **Step 6: Commit**

```bash
git add app/services/uk_location.py tests/test_uk_location.py
git commit -m "refactor: remove schools/broadband lookups from uk_location"
```

---

### Task 2: Remove schools/broadband integration entries

**Files:**
- Modify: `app/services/integration_registry.py`
- Test: `tests/test_integration_registry.py`

- [ ] **Step 1: Update the failing test**

In `tests/test_integration_registry.py`, replace `test_avatar_and_tts_and_hero_shot_categories_have_expected_members` (currently lines 11-22):

```python
def test_avatar_and_tts_and_hero_shot_categories_have_expected_members() -> None:
    by_category: dict[str, list[str]] = {}
    for d in list_integrations():
        by_category.setdefault(d.category_key, []).append(d.slug)

    assert by_category["avatar"] == ["heygen"]
    assert by_category["tts"] == ["elevenlabs"]
    assert by_category["hero_shot_animation"] == ["gemini_omni"]
    assert by_category["script_generation"] == ["openai"]
    assert by_category["aerial_flyover"] == ["google_3d_tiles"]
    assert "schools_data" not in by_category
    assert "broadband_data" not in by_category
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_integration_registry.py -v -k categories_have_expected_members`
Expected: FAIL — `schools_data`/`broadband_data` are still present in `by_category`

- [ ] **Step 3: Remove the two integration entries**

In `app/services/integration_registry.py`, remove the `schools_api` entry (currently lines 131-145):

```python
    IntegrationDefinition(
        slug="schools_api",
        name="DfE / Ofsted Schools API",
        category="Location Data",
        category_key="schools_data",
        description="Nearest Outstanding/Good schools for the location insights panel (§5.1).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, required=False,
                             help_text="Leave blank if using the public endpoint without a key."),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://get-information-schools.service.gov.uk/api"),
        ),
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("services.uk_location.get_nearby_schools",),
    ),
```

Remove the `ofcom_broadband` entry (currently lines 146-159):

```python
    IntegrationDefinition(
        slug="ofcom_broadband",
        name="Ofcom Broadband Checker",
        category="Location Data",
        category_key="broadband_data",
        description="Broadband/mobile coverage for the location insights panel (§5.3).",
        fields=(
            CredentialField("api_key", "API Key", FieldKind.API_KEY, required=False),
            CredentialField("base_url", "API Base URL", FieldKind.BASE_URL, required=False,
                             placeholder="https://api.checker.ofcom.org.uk"),
        ),
        test_mode=ConnectionTestMode.FORMAT_ONLY,
        used_by=("services.uk_location.get_broadband_info",),
    ),
```

Ensure the `INTEGRATIONS` tuple's closing paren remains valid Python after removal (the entry before these two, `google_3d_tiles`, should now be immediately followed by the tuple's closing `)`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_integration_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite to catch any other test referencing these slugs**

Run: `pytest -q`
Expected: All PASS. If any other test fails referencing `schools_api`/`ofcom_broadband`/`schools_data`/`broadband_data`, update it the same way as Step 1 (remove the assertion, don't weaken unrelated assertions in the same test).

- [ ] **Step 6: Commit**

```bash
git add app/services/integration_registry.py tests/test_integration_registry.py
git commit -m "refactor: remove schools/broadband integration entries"
```

---

### Task 3: Remove schools/broadband from the workspace UI and exported microsite

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/pipeline/steps/cinematic.py`

- [ ] **Step 1: Update `renderLocationData` in `app/static/index.html`**

Find and replace the `renderLocationData` function (currently around lines 763-775):

```javascript
    function renderLocationData(data) {
      const container = document.getElementById('ws-location-data');
      if (!data || Object.keys(data).length === 0) {
        container.innerHTML = '<p class="text-slate-500 italic">No location data fetched yet.</p>';
        return;
      }
      const schools = (data.schools || []).slice(0, 2).map(s => `<li>• ${s.name} (${s.rating})</li>`).join('');
      container.innerHTML = `
        <div><strong>Daylight:</strong> ${data.daylight_statement || 'N/A'}</div>
        <div><strong>Broadband:</strong> ${data.broadband?.max_download_speed || 'Ultrafast'}</div>
        <div><strong>Schools:</strong><ul class="pl-2 mt-1 space-y-0.5">${schools || 'None'}</ul></div>
      `;
    }
```

Replace with:

```javascript
    function renderLocationData(data) {
      const container = document.getElementById('ws-location-data');
      if (!data || Object.keys(data).length === 0) {
        container.innerHTML = '<p class="text-slate-500 italic">No location data fetched yet.</p>';
        return;
      }
      container.innerHTML = `
        <div><strong>Daylight:</strong> ${data.daylight?.statement || 'N/A'}</div>
      `;
    }
```

Note: this also fixes a latent bug in the original code — it read `data.daylight_statement` (flat key) but `build_location_data` actually nests it under `data.daylight.statement` (see `uk_location.py`'s return shape: `{"daylight": {"orientation": ..., "statement": ...}}`). The corrected access path (`data.daylight?.statement`) is used here since fixing the display is directly relevant to this task (the line is being touched anyway to remove schools/broadband) and leaving a known-broken read in newly-touched code would be worse than the file's current state.

- [ ] **Step 2: Update `MicrositeBuilderStep._render_html` in `app/pipeline/steps/cinematic.py`**

Read the current `_render_html` method (lines 160-169) — it does NOT currently reference `location_data`'s schools/broadband keys in its returned HTML string (only `job['address']`, `job['postcode']`, `price`, and `render_outputs`). The `location_data` parameter is accepted but unused in the HTML body itself.

Confirm this by re-reading the method body. If it's still exactly as shown below, no code change is needed in this method — the `location_data` parameter and the `location_data = job.get("location_data") or {}` line in `run()` (line 151) can stay as-is, since `location_data` will now just contain `{"amenities": [...], "daylight": {...}}` instead of the old 4-key shape, and nothing in `_render_html` reads the removed keys:

```python
    def _render_html(self, job: dict, render_outputs: dict, location_data: dict) -> str:
        price = job.get("price_guide") or "Price on application"
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{job['address']}</title></head><body>"
            f"<h1>{job['address']}</h1><p>{job['postcode']}</p>"
            f"<p class='price'>{price}</p>"
            f"<video src='{render_outputs.get('master_16x9', '')}' controls></video>"
            "</body></html>"
        )
    ```

If your read of the current file shows this method already matches the above (no schools/broadband HTML), skip straight to Step 3 — there is nothing to change in `cinematic.py`. If it differs from what's shown here (e.g. a newer version of the file already added schools/broadband rendering that this plan's author didn't see), read the actual current content and remove any schools/broadband-referencing lines from the returned HTML string, keeping everything else.

- [ ] **Step 3: Search for any other schools/broadband references across the app**

Run a search for remaining references to confirm nothing was missed:

Run: `grep -ril "schools\|broadband\|ofcom\|ofsted" app/ --include="*.py" --include="*.html"` (or use your search tool equivalent)

Expected: no matches in `app/services/uk_location.py`, `app/services/integration_registry.py`, `app/static/index.html`, or `app/pipeline/steps/cinematic.py`. If any other file references these terms (e.g. `app/services/export_pack.py` if it independently builds HTML separate from `cinematic.py`'s microsite builder), read that file and remove the schools/broadband-referencing lines the same way as Step 1, following that file's existing style.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: All tests PASS

- [ ] **Step 5: Manual check**

Launch the app (`uvicorn app.main:app --reload` with a scratch DB), create a job with a postcode and coordinates, call `POST /api/jobs/{id}/location` (or trigger it via the UI if there's a button for it), open the job's workspace page, confirm the location-data info box shows only "Daylight" (or "No location data fetched yet" before fetching) with no Schools/Broadband lines and no JS console errors.

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html app/pipeline/steps/cinematic.py
git commit -m "refactor: remove schools/broadband from workspace UI and microsite export"
```
