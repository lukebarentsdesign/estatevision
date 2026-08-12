# Agent-facing Arrange Screen (Frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `app/static/index.html`'s job workspace with brochure/photo upload and a sentence-arrange UI (drag-and-drop photo assignment, add/edit/delete/reorder sentences, avatar toggle, runtime indicator), making the already-shipped sentence-photo-linking backend actually usable by an agent.

**Architecture:** One small new backend endpoint (`PATCH /api/jobs/{id}` for `use_avatar`); the rest is a pure frontend layer — vanilla JS + the existing Tailwind CDN include — replacing the workspace's single script textarea with an upload-or-arrange view driven entirely by the already-shipped `/api/jobs/{id}/brochure`, `/api/jobs/{id}/photos`, `/api/jobs/{id}/segments`, `/api/segments/{id}` endpoints.

**Tech Stack:** FastAPI (one endpoint), vanilla JS/HTML5 Drag and Drop API, Tailwind (CDN, already included), Playwright for manual/browser verification (no JS test runner in this repo).

---

## Spec coverage checklist (for self-review, not part of the plan body)

- §1 workspace layout (upload state, arrange state) → Task 2, Task 3
- §2 data flow (fetch segments/photos, CRUD calls) → Task 2, Task 3
- §3 avatar toggle + new PATCH endpoint → Task 1, Task 4
- §4 run guard → already shipped in backend; Task 5 confirms no frontend change needed, verifies error surfacing
- §5 compliance → already shipped in backend; Task 3 confirms error surfacing works
- Testing → Task 6 (Playwright manual verification)

---

### Task 1: `PATCH /api/jobs/{id}` endpoint for `use_avatar`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_job_update.py` (new file)

No endpoint currently allows updating `PropertyJob.use_avatar` after
creation — the "New Job" modal sets it once at creation
(`POST /api/jobs`) and nothing since. This task adds a minimal PATCH
endpoint following the same load-mutate-commit-return pattern already used
by `PUT /api/jobs/{job_id}/script` (`app/main.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_job_update.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job(api_client) -> int:
    resp = api_client.post(
        "/api/jobs",
        json={"address": "1 Test St", "postcode": "TE1 1ST", "feature_level": "plus"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_patch_job_updates_use_avatar(api_client) -> None:
    job_id = _create_job(api_client)

    resp = api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})
    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is True

    get_resp = api_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["use_avatar"] is True


def test_patch_job_can_toggle_back_to_false(api_client) -> None:
    job_id = _create_job(api_client)

    api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})
    resp = api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": False})

    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is False


def test_patch_job_unknown_job_returns_404(api_client) -> None:
    resp = api_client.patch("/api/jobs/999999", json={"use_avatar": True})
    assert resp.status_code == 404


def test_patch_job_ignores_absent_fields(api_client) -> None:
    """A PATCH body with use_avatar omitted (None) must not overwrite the
    existing value -- this is a partial update, not a replace."""
    job_id = _create_job(api_client)
    api_client.patch(f"/api/jobs/{job_id}", json={"use_avatar": True})

    resp = api_client.patch(f"/api/jobs/{job_id}", json={})
    assert resp.status_code == 200
    assert resp.json()["use_avatar"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_job_update.py -v`
Expected: FAIL with 404/405 (route doesn't exist yet — FastAPI returns 405
Method Not Allowed for an unregistered PATCH on a path that has other
methods, or 404 if the whole path is unknown; either is an acceptable "not
implemented yet" signal here).

- [ ] **Step 3: Add the endpoint**

Read the full current `app/main.py` first (it has grown across the earlier
backend plan's 9 tasks — confirm exact current line numbers and imports
before editing, don't assume stale line numbers from memory). Add near the
other job-scoped endpoints (after `create_job`, before the upload
endpoints, following the file's existing "job CRUD, then uploads, then
segments" grouping):

```python
class UpdateJobRequest(BaseModel):
    use_avatar: Optional[bool] = None


@app.patch("/api/jobs/{job_id}")
def update_job(
    job_id: int, body: UpdateJobRequest, session: Session = Depends(get_session)
) -> PropertyJob:
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    if body.use_avatar is not None:
        job.use_avatar = body.use_avatar

    session.add(job)
    session.commit()
    session.refresh(job)
    return job
```

`BaseModel` and `Optional` are already imported at the top of `app/main.py`
(confirmed in the existing file) — no new imports needed for this step.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_job_update.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass — this is a new, isolated endpoint; nothing else in the
file changes.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_job_update.py
git commit -m "feat: add PATCH /api/jobs/{id} endpoint for updating use_avatar"
```

---

### Task 2: Upload state — brochure + photo upload UI in the workspace

**Files:**
- Modify: `app/static/index.html`

This task replaces the existing "📜 Voiceover Script" card
(`ws-script-text` textarea and its "Save Edits" button) with a new
container that shows one of two states. This task builds the **upload
state** only; Task 3 builds the arrange state. Both states live in the same
container, toggled by `renderScriptCard()` (new function) based on whether
the job has any photos.

- [ ] **Step 1: Read the current file in full**

Read `app/static/index.html` completely before editing — it's ~565 lines
and this task touches both the HTML structure (middle workspace column) and
the `<script>` block (`openWorkspace`, plus new functions). Confirm exact
current line numbers before editing; do not assume stale line numbers.

- [ ] **Step 2: Replace the script-editor card's HTML with a container div**

Find the existing card:

```html
            <!-- Script Editor -->
            <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5">
              <div class="flex items-center justify-between mb-4">
                <h3 class="text-base font-bold text-white flex items-center gap-2">
                  <span>📜 Voiceover Script (§1.1 Grounded)</span>
                </h3>
                <button onclick="saveScript()" class="bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white px-3 py-1.5 rounded-lg">Save Edits</button>
              </div>
              <textarea id="ws-script-text" rows="6" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 leading-relaxed"></textarea>
            </div>
```

Replace it with an empty container the JS will populate:

```html
            <!-- Script/Arrange Card (populated by renderScriptCard()) -->
            <div id="ws-script-card" class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5"></div>
```

Do not remove `saveScript()` from the `<script>` block yet — Task 3's arrange
state does not call it, but removing it now is out of scope for this task
(it becomes genuinely dead code once Task 3 lands; leaving an unused
function for one task's duration is fine and avoids partial-file churn).

- [ ] **Step 3: Add upload-state rendering**

Add a new function near `openWorkspace` in the `<script>` block:

```javascript
    async function renderScriptCard() {
      const card = document.getElementById('ws-script-card');
      const photosRes = await fetch(`/api/jobs/${currentJobId}/photos`);
      const photos = await photosRes.json();

      if (photos.length === 0) {
        renderUploadState(card);
      } else {
        renderArrangeState(card, photos);
      }
    }

    function renderUploadState(card) {
      card.innerHTML = `
        <h3 class="text-base font-bold text-white flex items-center gap-2 mb-4">
          <span>📤 Upload Brochure &amp; Photos</span>
        </h3>
        <div class="space-y-4">
          <div>
            <label class="text-xs font-semibold text-slate-400 uppercase">Particulars Brochure (PDF)</label>
            <input id="ws-upload-brochure" type="file" accept="application/pdf"
              class="w-full bg-slate-950 border border-slate-800 text-xs text-slate-300 rounded-xl p-2.5 mt-1 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-indigo-600 file:text-white file:text-xs file:font-semibold" />
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-400 uppercase">Property Photos (select multiple)</label>
            <input id="ws-upload-photos" type="file" accept="image/*" multiple
              class="w-full bg-slate-950 border border-slate-800 text-xs text-slate-300 rounded-xl p-2.5 mt-1 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-indigo-600 file:text-white file:text-xs file:font-semibold" />
          </div>
          <button onclick="submitUploads()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm py-2.5 rounded-xl transition">
            Upload &amp; Continue
          </button>
          <div id="ws-upload-status" class="text-xs text-slate-400"></div>
        </div>
      `;
    }

    async function submitUploads() {
      const brochureInput = document.getElementById('ws-upload-brochure');
      const photosInput = document.getElementById('ws-upload-photos');
      const status = document.getElementById('ws-upload-status');

      if (photosInput.files.length === 0) {
        status.textContent = 'Select at least one photo before continuing.';
        status.className = 'text-xs text-amber-400';
        return;
      }

      status.textContent = 'Uploading…';
      status.className = 'text-xs text-slate-400';

      try {
        if (brochureInput.files.length > 0) {
          const brochureForm = new FormData();
          brochureForm.append('file', brochureInput.files[0]);
          const brochureRes = await fetch(`/api/jobs/${currentJobId}/brochure`, {
            method: 'POST', body: brochureForm
          });
          if (!brochureRes.ok) {
            const err = await brochureRes.json().catch(() => ({}));
            throw new Error(err.detail || 'Brochure upload failed');
          }
        }

        const photosForm = new FormData();
        for (const file of photosInput.files) {
          photosForm.append('files', file);
        }
        const photosRes = await fetch(`/api/jobs/${currentJobId}/photos`, {
          method: 'POST', body: photosForm
        });
        if (!photosRes.ok) {
          const err = await photosRes.json().catch(() => ({}));
          throw new Error(err.detail || 'Photo upload failed');
        }

        await renderScriptCard();
      } catch (e) {
        status.textContent = 'Error: ' + e.message;
        status.className = 'text-xs text-rose-400';
      }
    }
```

- [ ] **Step 4: Wire `renderScriptCard()` into `openWorkspace`**

Find the existing `openWorkspace` function's body (it currently sets
`ws-script-text.value` and renders `ws-social-shorts` directly). Remove the
two lines that reference `ws-script-text` (that element no longer exists
after Step 2) and add a call to the new function:

```javascript
      // (remove these two lines from the existing function body:)
      // const scriptJson = job.script_json || {};
      // document.getElementById('ws-script-text').value = scriptJson.walkthrough_script || '';

      // (add this call, keeping the rest of the function -- social shorts
      // rendering, location data, switchTab -- unchanged:)
      await renderScriptCard();
```

Keep the `scriptJson`/social-shorts block as-is if it reads `job.script_json`
for social shorts rendering — only remove the two `ws-script-text` lines
specifically. Read the actual current function body before editing to
confirm exact placement.

- [ ] **Step 5: Manual verification**

Run: `python -m uvicorn app.main:app --port 8818` (from the project root).
Open `http://127.0.0.1:8818/` in a browser, create a job, open its
workspace. Expected: the middle column shows the new upload UI instead of
the old textarea. Selecting a PDF + photos and clicking "Upload & Continue"
should succeed (check Network tab / no console errors) — the arrange state
doesn't exist yet (Task 3), so after upload the card will show nothing
useful yet (`renderArrangeState` doesn't exist until Task 3) — this is
expected and will be fixed by Task 3; do not treat this as a Task 2 failure
as long as the upload calls themselves succeed (200/201 responses).

Stop the server afterward (Ctrl+C, or on Windows:
`Get-Process python | Where-Object { $_.CommandLine -like '*uvicorn*' } | Stop-Process`).

- [ ] **Step 6: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass — this task only touches `app/static/index.html`, which
has no direct test coverage (per the file's own established pattern — see
`tests/test_integration_settings.py::test_admin_page_is_served`-style smoke
tests for the *other* static file, but `index.html` currently has none). No
Python tests should be affected.

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add brochure/photo upload UI to job workspace"
```

---

### Task 3: Arrange state — sentence list, photo pool, drag-and-drop

**Files:**
- Modify: `app/static/index.html`

This is the core UI task: the sentence-list + photo-pool arrange view,
matching the approved mockup from the design session (sentence rows with
editable text and a photo drop-zone; a photo pool of draggable thumbnails;
add/delete sentence; drag-to-reorder; a runtime estimate).

- [ ] **Step 1: Read the current file state after Task 2**

Read `app/static/index.html` in full again — Task 2 changed it, and this
task adds significantly to the same `<script>` block. Confirm exact current
state before editing.

- [ ] **Step 2: Add `renderArrangeState`**

Add this function alongside `renderUploadState` (both are called from
`renderScriptCard`, added in Task 2):

```javascript
    let arrangeState = { segments: [], photos: [] };

    function renderArrangeState(card, photos) {
      arrangeState.photos = photos;
      card.innerHTML = `
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-base font-bold text-white flex items-center gap-2">
            <span>🎬 Arrange Script &amp; Photos</span>
          </h3>
          <span id="ws-runtime-estimate" class="text-xs text-slate-400"></span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div id="ws-segment-list" class="space-y-2"></div>
            <button onclick="addSegment()" class="w-full mt-2 border border-dashed border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 text-xs font-medium py-2 rounded-xl transition">
              + Add sentence
            </button>
          </div>
          <div>
            <div class="text-xs font-semibold text-slate-400 uppercase mb-2">Photos (drag onto a sentence)</div>
            <div id="ws-photo-pool" class="grid grid-cols-3 gap-2"></div>
            <button onclick="reopenUpload()" class="w-full mt-2 border border-dashed border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 text-xs font-medium py-2 rounded-xl transition">
              + Add more photos
            </button>
          </div>
        </div>
      `;
      loadSegments();
    }

    function reopenUpload() {
      renderUploadState(document.getElementById('ws-script-card'));
    }

    async function loadSegments() {
      const res = await fetch(`/api/jobs/${currentJobId}/segments`);
      arrangeState.segments = await res.json();
      renderSegmentList();
      renderPhotoPool();
      renderRuntimeEstimate();
    }

    function renderPhotoPool() {
      const pool = document.getElementById('ws-photo-pool');
      if (!pool) return;
      pool.innerHTML = arrangeState.photos.map(p => `
        <div class="aspect-square rounded-lg overflow-hidden border border-slate-800 cursor-grab bg-slate-800 flex items-center justify-center text-[9px] text-slate-500 text-center p-1"
          draggable="true" ondragstart="onPhotoDragStart(event, ${p.id})" title="${p.source_path}">
          Photo #${p.id}
        </div>
      `).join('');
    }

    function renderSegmentList() {
      const list = document.getElementById('ws-segment-list');
      if (!list) return;
      list.innerHTML = arrangeState.segments.map((s, i) => `
        <div class="bg-slate-950 border ${s.is_intro ? 'border-indigo-600' : 'border-slate-800'} rounded-xl p-3 flex gap-3 items-start"
          draggable="true" ondragstart="onSegmentDragStart(event, ${s.id})"
          ondragover="event.preventDefault()" ondrop="onSegmentDrop(event, ${s.id})">
          <div class="text-[10px] text-slate-500 pt-2 w-4">${i + 1}</div>
          <div class="flex-1">
            ${s.is_intro ? '<div class="text-[9px] uppercase tracking-wide text-indigo-400 font-bold mb-1">Intro</div>' : ''}
            <textarea rows="2" class="w-full bg-slate-900 border border-slate-800 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              onblur="saveSegmentText(${s.id}, this.value)">${escapeHtml(s.text)}</textarea>
          </div>
          <div class="w-14 h-14 rounded-lg border-2 ${s.photo_id ? 'border-solid border-slate-700' : 'border-dashed border-slate-600'} flex items-center justify-center text-[8px] text-slate-500 flex-shrink-0 text-center"
            ondragover="event.preventDefault()" ondrop="onPhotoDropOnSegment(event, ${s.id})">
            ${s.photo_id ? 'Photo #' + s.photo_id : 'drop photo'}
          </div>
          <button onclick="deleteSegment(${s.id})" class="text-slate-600 hover:text-rose-400 text-xs pt-2">✕</button>
        </div>
      `).join('');
    }

    function escapeHtml(s) {
      const div = document.createElement('div');
      div.textContent = s || '';
      return div.innerHTML;
    }

    function renderRuntimeEstimate() {
      const el = document.getElementById('ws-runtime-estimate');
      if (!el) return;
      const totalWords = arrangeState.segments.reduce((sum, s) => sum + (s.text.split(/\s+/).filter(Boolean).length), 0);
      const seconds = totalWords / 2.5; // matches backend's words-per-second heuristic
      const mm = Math.floor(seconds / 60);
      const ss = Math.round(seconds % 60).toString().padStart(2, '0');
      el.textContent = `${mm}:${ss} / 2:00 cap`;
      el.className = seconds > 120 ? 'text-xs text-amber-400 font-semibold' : 'text-xs text-slate-400';
    }

    async function addSegment() {
      await fetch(`/api/jobs/${currentJobId}/segments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: '' })
      });
      loadSegments();
    }

    async function deleteSegment(segmentId) {
      await fetch(`/api/segments/${segmentId}`, { method: 'DELETE' });
      loadSegments();
    }

    async function saveSegmentText(segmentId, text) {
      const res = await fetch(`/api/segments/${segmentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert('Could not save sentence: ' + (err.detail || 'unknown error'));
        loadSegments();
        return;
      }
      loadSegments();
    }

    let draggedPhotoId = null;
    let draggedSegmentId = null;

    function onPhotoDragStart(event, photoId) {
      draggedPhotoId = photoId;
    }

    async function onPhotoDropOnSegment(event, segmentId) {
      event.preventDefault();
      if (draggedPhotoId == null) return;
      await fetch(`/api/segments/${segmentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ photo_id: draggedPhotoId })
      });
      draggedPhotoId = null;
      loadSegments();
    }

    function onSegmentDragStart(event, segmentId) {
      draggedSegmentId = segmentId;
    }

    async function onSegmentDrop(event, targetSegmentId) {
      event.preventDefault();
      if (draggedSegmentId == null || draggedSegmentId === targetSegmentId) return;

      const targetIndex = arrangeState.segments.findIndex(s => s.id === targetSegmentId);
      if (targetIndex === -1) return;

      await fetch(`/api/segments/${draggedSegmentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_index: targetIndex })
      });
      draggedSegmentId = null;
      loadSegments();
    }
```

**Note on reordering semantics:** setting `order_index` to the target row's
current index is a simple, "good enough" reorder that may produce
duplicate `order_index` values across segments in some drag sequences
(the backend does not enforce uniqueness on `order_index`). This matches
the plan's YAGNI framing — the backend's `list_segments` sorts by
`order_index` and ties resolve to whatever SQL's stable sort does, which is
acceptable for this first version. If this proves confusing in manual
testing (Step 4 below), the fix is to renumber all segments sequentially
after a reorder rather than just the one that moved — note this as a
possible follow-up in the task's self-review rather than pre-emptively
building it.

- [ ] **Step 3: Wire the avatar toggle (§3 of the spec)**

Find the existing read-only avatar display:

```html
            <div>
              <label class="text-xs font-semibold text-slate-400 uppercase">Narration Format</label>
              <div id="ws-avatar-toggle" class="text-sm text-indigo-400 font-medium mt-1"></div>
            </div>
```

Replace with a container the JS populates conditionally:

```html
            <div id="ws-avatar-toggle-container">
              <label class="text-xs font-semibold text-slate-400 uppercase">Narration Format</label>
              <div id="ws-avatar-toggle" class="text-sm text-indigo-400 font-medium mt-1"></div>
            </div>
```

(This is a minimal wrapper addition — the `ws-avatar-toggle` div itself is
kept, since the JS below replaces its `innerHTML` conditionally rather than
needing a new container id.)

In `openWorkspace`, find the existing line:

```javascript
      document.getElementById('ws-avatar-toggle').innerText = job.use_avatar ? 'HeyGen Avatar Intro Enabled' : 'ElevenLabs Voice-Only Narration';
```

Replace it with a call to a new function that renders either a read-only
label (at `standard` level) or an editable toggle (at `plus`/`cinematic`/`custom`):

```javascript
      renderAvatarToggle(job);
```

Add the new function:

```javascript
    function renderAvatarToggle(job) {
      const el = document.getElementById('ws-avatar-toggle');
      const editable = ['plus', 'cinematic', 'custom'].includes(job.feature_level);

      if (!editable) {
        el.innerHTML = job.use_avatar ? 'HeyGen Avatar Intro Enabled' : 'ElevenLabs Voice-Only Narration';
        return;
      }

      el.innerHTML = `
        <select id="ws-avatar-select" class="w-full bg-slate-950 border border-slate-800 text-sm text-slate-200 rounded-xl p-2 mt-1"
          onchange="updateAvatarToggle(this.value === 'true')">
          <option value="false" ${!job.use_avatar ? 'selected' : ''}>Voice-Only (ElevenLabs)</option>
          <option value="true" ${job.use_avatar ? 'selected' : ''}>Avatar Intro (HeyGen)</option>
        </select>
      `;
    }

    async function updateAvatarToggle(useAvatar) {
      await fetch(`/api/jobs/${currentJobId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_avatar: useAvatar })
      });
    }
```

- [ ] **Step 4: Manual verification with Playwright**

Use the `webapp-testing` skill's pattern (already used earlier in this
project). Start the server, then drive it:

1. Navigate to `/`, create a job at `feature_level: "plus"`.
2. Open its workspace. Confirm the upload UI appears (Task 2).
3. Upload a real small PDF and 2-3 real small images.
4. Confirm the card switches to the arrange UI: segment list (empty),
   photo pool (2-3 thumbnails).
5. Click "+ Add sentence" twice. Confirm 2 rows appear.
6. Type text into one row's textarea, click elsewhere (blur). Reload the
   page, reopen the workspace, confirm the text persisted.
7. Drag a photo thumbnail onto a sentence row's drop-zone. Confirm the
   drop-zone updates to show "Photo #N" instead of "drop photo".
8. Drag the SAME photo onto a second sentence row. Confirm both rows now
   show the same photo id (reuse allowed).
9. Confirm the runtime estimate text updates as text is typed/segments
   added.
10. Confirm the avatar toggle `<select>` appears (feature_level is "plus").
    Change it, reload, reopen workspace, confirm the selection persisted
    (via a `GET /api/jobs/{id}` check or by re-observing the select's
    value).
11. Click the delete (✕) button on a sentence. Confirm it disappears from
    the list.
12. Check the browser console throughout for JS errors — zero expected.

Report actual findings; if any step fails, fix the underlying code (not the
test) before proceeding, per this project's established verification
discipline.

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass — no Python code changed in this task.

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add sentence-arrange UI (drag-and-drop photo assignment, editable avatar toggle)"
```

---

### Task 4: Remove now-dead `saveScript()` and `ws-script-text` references

**Files:**
- Modify: `app/static/index.html`

Task 2 stopped rendering `ws-script-text`; `saveScript()` (bound to the old
"Save Edits" button, which no longer exists in the DOM after Task 2/3) is
now unreachable dead code. Clean it up now that Tasks 2-3 have proven the
replacement works end-to-end.

- [ ] **Step 1: Confirm `saveScript` is genuinely unreferenced**

Read the current file and confirm no remaining `onclick="saveScript()"` or
`getElementById('ws-script-text')` references exist anywhere (Task 2/3
should have removed all call sites already — this task only removes the
now-orphaned function definition itself).

- [ ] **Step 2: Remove the dead function**

Delete the `saveScript` function body from the `<script>` block.

- [ ] **Step 3: Manual smoke check**

Reload the workspace in a browser (server already running from Task 3's
verification, or restart it), open dev tools console, confirm no errors on
page load or when opening a job's workspace.

- [ ] **Step 4: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html
git commit -m "chore: remove dead saveScript function after arrange-screen migration"
```

---

## Post-plan notes

- **`social_shorts` rendering is untouched.** The existing
  `ws-social-shorts` block (reading `job.script_json.social_shorts`) is
  unrelated to this plan's scope and continues to render whatever
  `job.script_json` contains — including the backend's own fallback
  (added during the backend plan's final review) that populates
  `script_json.walkthrough_script`/`segments` from `ScriptSegment` rows
  for segmented jobs. No frontend change needed there.
- **No JS test infrastructure exists in this repo.** All verification in
  this plan is manual/Playwright-driven per Task 3 Step 4, consistent with
  how the Remotion `.tsx` files were verified in the backend plan (`tsc
  --noEmit` plus manual review, no automated JS test runner).
- **Drag-and-drop reorder semantics are intentionally minimal** (see Task
  3 Step 2's note) — a follow-up task to renumber all segments sequentially
  on reorder is a reasonable future improvement, not required for this
  plan's scope.
