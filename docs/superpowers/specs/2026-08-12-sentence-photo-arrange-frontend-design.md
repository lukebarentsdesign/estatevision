# Agent-facing arrange screen (frontend)

Status: approved design, not yet implemented.

## Problem

The sentence-photo linking backend (spec: `2026-08-12-sentence-photo-linking-design.md`,
fully implemented and merged to `master`) has no UI. `app/static/index.html`'s
job workspace has no brochure/photo upload controls at all, and its script
editor is a single legacy textarea bound to `walkthrough_script` — there is
no way for an agent to actually create `ScriptSegment` rows, assign photos to
them, or use any of the backend work already shipped.

## Goal

Extend the existing job workspace (`#sec-workspace` in `app/static/index.html`)
with: brochure + photo batch upload, and a sentence-arrange UI (editable
sentence list, drag-and-drop photo assignment from a reusable photo pool,
add/delete/reorder sentences, a soft 2-minute runtime indicator, and an
avatar toggle where applicable) — replacing the current single-textarea
script editor. This makes the already-built backend actually usable by an
agent for the first time.

## Non-goals

- No auto-generation of the initial sentence list from the brochure via LLM.
  The backend's real LLM client for this is not wired up yet (a pre-existing
  gap unrelated to this feature — `get_script_llm_client()` raises
  `NotImplementedError` once a key is configured, for every script variant).
  Agents start from an empty sentence list and add sentences manually via
  the existing `POST /api/jobs/{id}/segments` endpoint. Auto-generation is a
  fast-follow once that LLM wiring exists — the UI needs no changes to
  support it later, since "how a sentence's text got there" doesn't affect
  how it's displayed/edited.
- No live video preview. The existing static Remotion placeholder panel in
  the right-hand workspace column is untouched.
- No photo reordering within the photo pool itself — only sentence reordering
  and photo-to-sentence-slot assignment.
- No changes to the legacy (non-segmented) job code path anywhere in the
  backend. This is a purely additive frontend layer on top of already-shipped
  backend work.
- No build tooling / frontend framework introduced. Vanilla JS + the existing
  Tailwind CDN include, matching `index.html`'s current implementation
  exactly.

## Design

### 1. Workspace layout changes

`#sec-workspace`'s existing 3-column grid (left: job setup, middle: script
editor, right: Remotion preview) keeps its left and right columns unchanged.
The middle column's "📜 Voiceover Script" card (`index.html:159-167`,
`ws-script-text` textarea) is replaced with two states, switched based on
whether the job has photos uploaded yet:

**Upload state** (shown when `GET /api/jobs/{id}/photos` returns an empty
list): a brochure PDF file input (posting to the existing
`POST /api/jobs/{id}/brochure`) and a multi-file photo input (posting to
`POST /api/jobs/{id}/photos`), both already-shipped endpoints with existing
size caps and content-type validation. After a successful photo upload, the
UI switches to the arrange state.

**Arrange state** (shown once photos exist): the sentence-list + photo-pool
layout already validated via the earlier session's approved mockup:
- Left sub-column: an ordered list of sentence rows. Each row has an
  editable text field (debounced save on blur, not per-keystroke), a
  thumbnail/drop-zone showing its assigned photo (or an empty dashed-border
  drop target if unassigned), and a delete button. Below the list, an
  "+ Add sentence" row that creates a new blank segment via
  `POST /api/jobs/{id}/segments` and appends it. Below that, a runtime
  estimate ("1:42 / 2:00 cap", turning amber past the cap) computed
  client-side from word counts using the same words-per-second heuristic as
  the backend's `estimate_total_duration_sec` — advisory only, never blocks
  anything.
- Right sub-column: a grid of photo thumbnails from `GET /api/jobs/{id}/photos`,
  each draggable. Dragging a photo onto a sentence row's drop-zone calls
  `PUT /api/segments/{id}` with `photo_id`. The same photo can be dragged
  onto multiple sentence rows (reuse allowed, matching the backend's data
  model — no uniqueness constraint on `photo_id`). A small "+ Add more
  photos" tile at the end of the grid re-opens the photo upload flow for
  this job without leaving the arrange screen.

Sentence reordering: dragging a sentence row to a new position within the
list calls `PUT /api/segments/{id}` with the new `order_index`.

### 2. Data flow

On `openWorkspace(jobId)` (existing function, `index.html:388`), in addition
to the existing `GET /api/jobs/{id}` call, also fetch
`GET /api/jobs/{id}/segments` and `GET /api/jobs/{id}/photos` in parallel.
Render the arrange UI from these two lists — no client-side caching beyond
what's needed for the current render; every mutating interaction (add,
edit, delete, reorder, assign photo) calls its corresponding existing REST
endpoint directly and then re-fetches the affected list, rather than
maintaining optimistic local state. This matches the existing file's
pattern (e.g. `submitCreateJob`, `saveScript`) of "call API, then reload."

### 3. Avatar toggle

The existing job-setup column already shows a read-only "Narration Format"
field (`index.html:139-142`, `ws-avatar-toggle`) reflecting `job.use_avatar`,
set once at job creation via the existing "New Job" modal and never
editable afterward. This becomes an editable toggle in the workspace view,
shown only when `job.feature_level` is `plus`/`cinematic`/`custom` (mirroring
the backend's existing avatar-availability gate at those levels) — otherwise
it stays exactly as today (read-only, hidden/disabled at `standard` level).

**New backend endpoint required** (small, additive): `PATCH /api/jobs/{id}`
accepting `{"use_avatar": bool}`, since no endpoint currently allows
updating a job's `use_avatar` after creation. Implemented following the
existing `PUT /api/jobs/{id}/script`-style pattern (load job, apply field,
commit, return updated job) — no new service module needed, this is a
direct, minimal ORM field update in `app/main.py`.

### 4. "Generate Video" / run guard

No changes needed to the existing "⚡ Execute Pipeline Pass" button
(`triggerJobRun()`, `index.html:418-437`) or its backend endpoint. The
backend's existing `POST /api/jobs/{id}/run` guard (added during the
backend implementation's final review) already returns a 422 with a clear
message — "segment(s) [...] have no photo assigned" — when any non-avatar-intro
segment lacks a photo, and the existing frontend code already surfaces
`err.detail` via `alert()` on a non-OK response. This flow requires no new
frontend code.

### 5. Compliance

No new compliance surface. Every sentence add/edit already re-runs
`assert_price_free` server-side via the existing segment CRUD endpoints
(`POST`/`PUT /api/segments/...`); a rejected request returns 400 with a
detail message, surfaced via the same `alert()` pattern already used
elsewhere in this file for failed requests.

## Testing

Since this repo has no JS test infrastructure (a documented, pre-existing
gap noted throughout the backend implementation), this frontend work is
verified via:
- Manual browser testing using Playwright (per the `webapp-testing` skill
  already used earlier in this project) against a running local server:
  upload flow, sentence add/edit/delete/reorder, drag-and-drop photo
  assignment (including reusing one photo across two sentences), avatar
  toggle visibility at different feature levels, and the run-guard's error
  surfacing for an incomplete job.
- Backend test coverage for the one new endpoint (`PATCH /api/jobs/{id}`
  for `use_avatar`), following the existing `pytest`/`TestClient` pattern
  used throughout `tests/test_script_segments.py` and `tests/test_uploads.py`.
