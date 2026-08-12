# Sentence-by-sentence photo linking + script editing workflow

Status: approved design, not yet implemented.

## Problem

Voiceover and visuals aren't guaranteed to match today. Script generation
produces one continuous ~150-word walkthrough narration (a single LLM call,
`ScriptVariant.WALKTHROUGH`); photos are an independently-ordered sequence
(`Photo.order_index`) with fixed 4-second clips. The two streams only meet at
final assembly, by *position in a list*, never by any explicit pairing. A
sentence describing the kitchen can end up playing over a photo of the
garden, with no structural guarantee otherwise.

An audit of the existing codebase (2026-08-12) confirmed:
- Sentence-level text extraction already exists at ingestion, but is only
  raw source material — the final script is continuous prose, not discrete
  sentence elements.
- No cap of any kind exists on script length (only an unenforced ~60s prompt
  hint).
- The avatar on/off toggle (`PropertyJob.use_avatar`) currently changes
  *which script content gets generated* (an `AVATAR_OPENING` line only
  exists when avatar is on) rather than just which renderer is used for a
  fixed intro sentence.
- No photo-upload UI or endpoint exists at all.
- No photo↔sentence linkage exists anywhere in the data model.
- Remotion assembly (`render_contract.py`, `PropertyVideo.tsx`) plays one
  continuous voiceover track underneath a sequence of fixed-duration photo
  clips, with captions timed against that one track — structurally the
  opposite of "each sentence's audio synced to its own photo."
- WhisperX runs once against the single continuous walkthrough track; there
  is no per-segment invocation or stitching logic.
- Compliance checks (`assert_price_free`) are enforced at the level of
  arbitrary text strings already, so they extend cleanly to per-sentence
  editable text — except the existing `PUT /api/jobs/{job_id}/script`
  endpoint does not currently re-run compliance checks on edited text, a
  pre-existing gap this work also closes for the new segment model.

## Goal

Let the agent see the full narration and full photo set before committing to
anything, and have them do the sentence-to-photo pairing themselves — making
mismatch structurally impossible rather than something to catch after
rendering. All creative decisions happen at upload/arrange time, not as a
post-hoc review of a finished video.

## Non-goals

- No automatic/AI-based matching of photos to sentences. Rejected outright —
  this is the entire point of the feature.
- No separate avatar setup/creation flow. The avatar toggle is a simple
  per-property on/off switch; agent/HeyGen avatar IDs are assumed to already
  exist from onboarding (unchanged from today).
- No changes to compliance rules themselves (no price, no invented copy,
  mandatory AI-disclosure badges) — this workflow sits on top of those
  unchanged rules, and closes one enforcement gap (edited-script text wasn't
  re-checked) as a natural side effect, not as new scope.
- No change to `social_shorts` generation or the existing multi-shorts
  feature-level behavior — this spec covers the primary walkthrough
  narration only.
- No change to feature-level gating of avatar rendering. The toggle remains
  available only at `plus`/`cinematic`/`custom` levels, exactly as today;
  at `standard` level it does not show (or is locked off).

## Design

### 1. Data model

New `ScriptSegment` table:

| field | type | notes |
|---|---|---|
| `id` | int, PK | |
| `job_id` | FK → `PropertyJob` | |
| `order_index` | int | agent-editable via reorder |
| `text` | str | agent-editable; always compliance-checked on save |
| `is_intro` | bool | true only for the first segment |
| `photo_id` | FK → `Photo`, nullable | null until assigned; a `Photo` may be referenced by more than one `ScriptSegment` (reuse allowed) |
| `audio_path` | str, nullable | set once the segment's sliced audio clip exists (§4) |
| `duration_sec` | float, nullable | derived from the segment's actual audio clip length, replacing today's hardcoded 4.0s per photo |

This replaces the `walkthrough_script` string inside `script_json` for jobs
using this workflow. `social_shorts` is untouched and continues to live in
`script_json` as today.

`Photo` gains no new required field. `PropertyJob.use_avatar` remains the
existing per-job/per-property toggle — the audit confirmed this was already
correctly scoped (not per-agent), so no model change needed there.

### 2. Script generation

Replace the single "write ~150 words of continuous narration" LLM prompt
(for jobs using this workflow) with a new prompt that returns a **structured
JSON list** of 5–10 short sentences, one per room/feature, each still
strictly re-sequenced/adapted from the brochure's own extracted sentences
only (same source material, same grounding constraint as today — no new
compliance surface).

- The first item in the returned list is always flagged as the intro line,
  generated identically regardless of the avatar toggle's state (closing the
  gap where today's `AVATAR_OPENING` variant only exists when avatar is on).
  Style/length matches today's existing avatar-opening brief (≤25 words,
  e.g. "Hi, I'm James, I'd love to show you around 5 Wardington Crescent").
- Every returned sentence — and every sentence the agent subsequently adds
  or edits — passes through `assert_price_free` before being persisted,
  same enforcement point used elsewhere in the pipeline today.
- No hard word/duration cap is enforced at generation time. See §3 for how
  the 2-minute target is surfaced during editing instead.

### 3. Upload + arrange screen

Workflow shape (confirmed via mockup): **batch upload, then one arrange
screen** — not a one-sentence-at-a-time wizard.

1. Agent uploads the brochure PDF and a batch of property photos together.
   This requires new upload endpoints and UI — neither exists today (the
   current "Create Job" modal has no file fields at all).
2. System runs ingestion + the new structured script-generation prompt (§2),
   producing the initial `ScriptSegment` rows (photo_id null on all of
   them).
3. Agent lands on the arrange screen: an editable, reorderable sentence list
   on one side, and the uploaded-photo pool on the other. Dragging a photo
   onto a sentence sets that segment's `photo_id`; the same photo can be
   dragged onto more than one sentence (reuse allowed, per explicit
   decision — reused photos are not treated differently in the pipeline,
   e.g. no distinct motion treatment is required per reuse).
   - Agent can edit any sentence's text inline.
   - Agent can delete a generated sentence.
   - Agent can add a new blank sentence and write its own text — subject to
     the same `assert_price_free` check as generated text.
   - Agent can drag to reorder sentences (updates `order_index`).
4. A running estimated-duration total is shown (e.g. "1:42 / 2:00 cap"),
   turning into a warning color past 2:00. This is **advisory only** — it
   does not block proceeding. (Estimated via a words-per-second heuristic
   until real synthesized-audio lengths exist post-generation.)
5. Below the arrange area: the avatar toggle ("Include video avatar for the
   intro?" — shown only at `plus`/`cinematic`/`custom` feature levels,
   matching today's existing gate) and a "Generate Video" button. The button
   is disabled only when any segment lacks an assigned photo — never
   disabled purely for exceeding the 2-minute target.

### 4. Audio + captions

To avoid narration sounding like a list of disconnected sentences rather
than one continuous tour (an intonation/prosody concern, not a sync
concern):

1. On Generate, all segment text **except** the intro-when-avatar-is-on case
   is concatenated in order and sent to ElevenLabs as **one continuous
   synthesis call** — this preserves natural sentence-to-sentence
   intonation across the full passage, since the voice model never sees the
   text as isolated fragments.
2. WhisperX then runs **once** on that single continuous audio file to get
   word-level timestamps. Because the exact segment texts and their order
   are already known going in (not inferred from free-form prose), this is
   a forced-alignment problem — aligning known text to audio — not a
   boundary-guessing problem. This is a materially different (and reliable)
   use of WhisperX than "infer where sentences start" would have been.
3. The single audio file is sliced at the aligned per-segment boundaries
   into individual clips. Each `ScriptSegment.audio_path` and
   `duration_sec` are set from its slice. Slicing needs one new capability
   in `clients/elevenlabs.py`/`local_tools.py` (e.g. via `pydub` or ffmpeg
   at known timestamps) — no new external service dependency.
4. **Avatar intro exception**: when avatar is on, the intro segment is
   excluded from the continuous-take concatenation. HeyGen synthesizes that
   line's speech itself as part of `generate_avatar_clip` (unchanged
   existing behavior) — the intro segment's "audio" is really the avatar
   clip's own audio track, referenced via `avatar_clip_path` in assembly
   (§5), not a sliced ElevenLabs clip.
5. When avatar is off, the intro segment **is** included in the continuous
   take like every other segment, per the earlier decision that the intro
   sentence and its voicing are identical either way — only the *rendering*
   (video vs. voice-only) differs.
6. Voice consent: since the intro's audio comes from ElevenLabs whenever
   avatar is off, `consent.require_voice_for_narration`'s existing gate
   applies in that case exactly as it already does for every other segment
   — no separate consent carve-out for the intro line anymore.

### 5. Assembly (Remotion)

This is the largest structural change identified by the audit.

`render_contract.py`'s `RenderProps` moves from:
- one shared `voiceover_path` + one `captions` tuple + a `clips` sequence of
  fixed-4-second photo clips (today),

to:
- a `segments: tuple[Segment, ...]`, each carrying its own
  `photo_clip_path` (or `avatar_clip_path` for an on-avatar intro),
  `audio_path`, `captions` (word cues local to that segment), and
  `duration_sec` derived from the segment's actual audio length.

`PropertyVideo.tsx` changes from "photo sequence + one independent global
audio track" to "sequence of self-contained (visual, audio, captions)
segments," each rendered for exactly its own audio's duration — this is the
change that structurally guarantees photo-audio sync, rather than relying on
fixed durations that happened to roughly line up.

The intro segment, when avatar is on, renders using the existing
`avatar_clip_path` prop in place of a photo+audio pair for that one segment.
This prop already exists in `props.ts` but is currently unreferenced dead
code in `PropertyVideo.tsx` — this work wires it in for the first time.

### 6. Compliance

No rule changes. `assert_price_free` runs:
- on every LLM-generated segment at generation time (as today, extended to
  the new per-segment shape instead of one walkthrough string),
- on every agent edit or agent-authored addition, at save time,
- closing the existing gap where `PUT /api/jobs/{job_id}/script` does not
  currently re-check edited `walkthrough_script` text — the new
  segment-save endpoint(s) apply the same check every existing pipeline
  boundary already uses.

## Testing

- Unit tests for the new structured script-generation prompt: returns 5-10
  items, first item flagged `is_intro`, every item passes
  `assert_price_free` grounding checks against fixture brochure sentences.
- Unit tests for segment CRUD (add/edit/delete/reorder), including that
  `assert_price_free` rejects a price-bearing agent-authored addition.
- Unit tests for photo-reuse: the same `Photo.id` assigned to two
  `ScriptSegment` rows is valid and both segments resolve it correctly.
- Unit tests for the continuous-synthesis-then-slice audio path: given a
  known concatenated script and mocked WhisperX word timings, confirms the
  resulting per-segment audio files have the expected boundaries/durations
  (using short local test fixtures, not real ElevenLabs calls, matching the
  existing `StubElevenLabsClient`/`StubHeyGenClient` pattern used
  elsewhere in the test suite).
- Unit tests confirming the avatar-on intro segment is excluded from the
  concatenated take, and the avatar-off intro segment is included.
- API tests for the new upload endpoints (brochure + photo batch).
- API test confirming `Generate Video` is blocked while any segment lacks a
  photo, and confirming it is *not* blocked by exceeding the 2-minute
  target.
- `render_contract.py` test confirming `RenderProps.segments` shape and
  per-segment `duration_sec` derivation from actual (stubbed) audio length,
  replacing the old fixed-4.0s-per-photo test.
- Existing pipeline/compliance/export-pack tests must continue passing
  unchanged for any code paths not touched by this feature (e.g. social
  shorts generation, feature-level gating logic itself).
