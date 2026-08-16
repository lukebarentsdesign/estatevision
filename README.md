# Property Content Studio

Local production desk + lightweight CRM for turning estate agency property assets
(photos + PDF brochure) into multi-format video and digital marketing packages.

Production tool only — it has no concept of price, packages, or client charges.
See [property-content-studio-spec.md](property-content-studio-spec.md).

## Build status

**Phase A (architecture) — complete.** Data models, the §1 compliance enforcement
layer, the pipeline orchestration contract, and the Remotion prop schema are in
place.

**Phase B backend — complete, mostly stubbed.** Every pipeline step (§4 `standard`
through `custom`), the UK location aggregator (§5), a minimal FastAPI app
(§2, §6), an admin panel for API credentials (`/admin/integrations`), and the
pipeline registry/job-snapshot glue are built and wired end-to-end.

**The Remotion video assembly is now a real project, not an empty folder.**
`/remotion` previously had no `package.json`, no entry point, no
compositions -- `render_via_remotion_cli` was calling a project that didn't
exist. Built: `Master16x9`/`Reel9x16` compositions matching `RenderProps`
field-for-field (`remotion/src/props.ts`), a shared `PropertyVideo` component
that composites the §1.4 disclosure badge unconditionally (no prop exists to
suppress it, mirroring the Python-side guarantee), a lower-third, timed
captions, voiceover/music audio tracks, and `calculateMetadata` to derive
duration/dimensions from the actual per-job props rather than fixed values.
**Verified against the real Remotion CLI**, not just typechecked: `npx tsc
--noEmit` passes clean, and a real `npx remotion render` run got through
bundling, composition lookup, and into frame-by-frame rendering before
failing only on this sandbox's restricted outbound network access to the test
media file -- i.e. the scaffold, entry point, and composition registration are
confirmed working; a full render with real local media was not completed in
this session and should be the first thing verified on a real machine with
network access.

Fixed two real bugs surfaced while building this:
- `render_via_remotion_cli` was missing the required entry-point argument
  (`src/index.ts`) and used a space-separated `--props <path>` instead of the
  documented `--props=<path>` form -- both would have failed against a real
  Remotion CLI. Confirmed against remotion.dev/docs/cli/render.
- `ffmpeg_mux`'s `lut3d` filter would have broken on any Windows LUT path,
  since ffmpeg's filtergraph parser treats `:` as a key=value separator,
  colliding with a Windows drive letter (`C:\...`). Now escapes the colon and
  normalizes to forward slashes.

**ElevenLabs (voiceover), HeyGen (avatar), and Gemini Omni/Veo (hero-shot
motion) are real, working HTTP clients** — each confirmed against live API
docs where a stable reference existed, with any unverified detail flagged
directly in the source (see the docstring caveats in `heygen.py` and
`gemini_omni.py`). All three auto-activate the moment a key is entered in the
admin panel, no restart required.

**Real-ESRGAN, WhisperX, Demucs, and DepthFlow are real, working local CLI
invocations** behind the `LOCAL_TOOLS_AVAILABLE=1` flag — confirmed against
each project's own README/docs. Two real problems surfaced during this work,
both fixed:

- **Demucs** — the stub-era placeholder path assumed an output file
  (`no_vocals.wav`) that Demucs never actually writes; the real output is
  `{output_dir}/{model}/{track_stem}/other.wav`. Regression test in
  `tests/test_local_tools.py`.
- **DepthFlow** — its CLI has no built-in animation preset (the project's own
  docs call this "a future release"); a bare `depthflow` call renders a
  static, non-moving clip. Real parallax motion needs a custom `DepthScene`
  Python subclass driving `self.state.offset` off `self.cycle` — this is
  bundled at `app/depthflow_scenes/subtle_parallax.py` (pattern confirmed
  against DepthFlow's own `examples/presets.py` and matches a previously
  verified working test) and invoked in place of the bare command.

**Not implementable as scoped, both deferred and documented rather than
half-built:**

- **§4 `cinematic` aerial flyover.** Google Photorealistic 3D Tiles is not a
  video-generation API — it returns raw 3D mesh data for a client-side
  renderer (the standard pairing is Cesium.js) to draw. Producing an actual
  flyover clip needs a small render sub-pipeline (headless Cesium + scripted
  camera path + frame capture + encode), structurally closer to the Remotion
  CLI step than to an HTTP client. `AerialFlyoverStep` stays stubbed
  regardless of whether a key is configured, since a key alone doesn't unlock
  this — see the docstring in `app/pipeline/steps/cinematic.py`.
- **Zero-DCE shadow lift.** The reference implementation
  (`Li-Chongyi/Zero-DCE`) has no usable CLI — its inference script hardcodes
  `.cuda()` with no CPU fallback and hardcodes both the input directory and
  weights path, with no per-file input/output interface at all. Wiring this
  up means forking and rewriting their script into an actual CLI, not calling
  one. `zero_dce_shadow_lift` stays stubbed regardless of
  `LOCAL_TOOLS_AVAILABLE` — see the docstring in `app/clients/local_tools.py`.

Everything else (Google 3D Tiles proper) still runs behind stub clients that
produce placeholder artifacts — no API keys or local ML tools are required to
run a job start to finish. Real implementations slot in behind the same
interfaces (`app/clients/`) once available; see "Switching stubs for real
clients" below.

**Phase B frontend — complete.** Built a high-density, dark-themed production desk and CRM dashboard at `/` ([`app/static/index.html`](file:///e:/Antigravity%20not%20on%20onedrive/Estate%20agent%20marketing%20idea/app/static/index.html)), featuring:
- **CRM & Pipeline Kanban (`/`)**: Real-time tracking across `Ingestion` → `Processing` → `Review` → `Completed`.
- **Agent Manager (`/agents`)**: Agent profiles, branding colors, HeyGen Avatar ID, ElevenLabs Voice ID + mandatory explicit consent checkbox (§1.3).
- **Job Workspace (`/jobs/:id`)**: Script editor (strictly grounded per §1.1/§1.2), location insights preview, and Remotion rendering controls.
- **One-Click Export Pack (`/jobs/:id/export`)**: Service generating standalone HTML property microsite (`index.html`), 7-day social marketing plan (`social_calendar_7day.md`), metadata, and bundling output videos into a single downloadable ZIP file.

104 tests pass, covering the §1 compliance guarantees, export pack generation, pipeline orchestration
semantics, full end-to-end runs at `standard`, `plus`, and `cinematic` feature
levels, and real client/CLI request-response handling.

## Architecture you must not work around

Three rules are enforced structurally rather than by convention. If a change
requires breaking one of them, the change is wrong.

### 1. Prompts are built in exactly one place

[`app/services/script_prompt.py`](app/services/script_prompt.py) is the only
module permitted to assemble a script-generation prompt. Pipeline steps call
`build_prompt(context, variant)` and nothing else.

`build_prompt` accepts a `ScriptJobContext` — a frozen DTO narrowed from
`PropertyJob` by an explicit whitelist. It **has no price field at all**, so a
price leak is a construction error at the boundary rather than something a
reviewer has to spot in a template string.

Brochure text is a second leak path (PDFs carry a price line), so run
`sanitize_source_sentences()` before constructing the context. `build_prompt`
raises `ComplianceError` if you forget.

### 2. The AI disclosure badge is derived, never passed

[`app/services/render_contract.py`](app/services/render_contract.py) sets the
§1.4 badge itself from `Photo.sky_replaced`. There is deliberately **no
caller-supplied flag** to enable or suppress it — a test asserts the parameter
does not exist. The step that performs sky replacement sets the photo flag; the
render layer does the rest.

### 3. Voice cloning is gated at the service layer

[`app/services/consent.py`](app/services/consent.py) refuses to store an
`elevenlabs_voice_id` without `consent_confirmed=True`, so an API client or
import script cannot bypass the UI checkbox. Clearing a voice also clears
consent, meaning re-adding one requires fresh confirmation.

HeyGen avatars need no separate flag — HeyGen performs identity verification at
clone-creation time, so a stored ID is itself the consent record.

### Pipeline contract

[`app/pipeline/contract.py`](app/pipeline/contract.py) defines `PipelineStep`.
Implementers must ensure `run()` is idempotent, writes outputs under
`ctx.work_dir`, returns paths in `StepResult.artifacts`, and raises on genuine
failure rather than returning a partial success. The runner handles ordering,
resumption, and failure escalation — steps never deal with those.

## Setup

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

### Credentials

Preferred path: run the app and open **`/admin/integrations`**. Every known
system (HeyGen, ElevenLabs, Gemini Omni, OpenAI) is listed
with its required fields, a masked view of
what's already configured, and a "Test connection" button that makes a real
read-only API call where one safely exists. Keys are encrypted at rest and
take effect immediately — no restart, no `.env` edit.

Environment variables remain a fallback (checked only when nothing is stored
in the admin panel) so existing `.env`-based setups keep working:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Script generation LLM |
| `ELEVENLABS_API_KEY` | Voiceover (voice-only jobs) — **real client implemented** |
| `HEYGEN_API_KEY` | Avatar intro/outro — **real client implemented** |
| `GEMINI_API_KEY` | Hero-shot motion (Veo, via Gemini API) — **real client implemented** |
| `REPLICATE_API_TOKEN` | Hero-shot motion (Wan 2.2, via Replicate) — **real client implemented**; alternate vendor in the same `hero_shot_animation` category as Gemini Omni, runs on Replicate's hosted GPUs instead of a local one |
| `LOCAL_TOOLS_AVAILABLE=1` | Switch Real-ESRGAN/Zero-DCE/DepthFlow/WhisperX/Demucs/FFmpeg/Remotion from stubs to real subprocess calls |
| `PROPERTY_STUDIO_DB` | SQLite file path (default `property_studio.db`) |
| `PROPERTY_STUDIO_SECRET_KEY_FILE` | Admin-panel encryption key file path (default `secret.key`, gitignored) |

### Switching stubs for real clients

Every `app/clients/*.py` module exposes a `get_*_client(*, session=None)`
factory that returns a stub unless a key is configured (admin panel first,
env var fallback), at which point it returns an `Http*` class.

**ElevenLabs, HeyGen, OpenAI, Gemini Omni (Veo), and Replicate (Wan 2.2) are
done** — real HTTP calls, tested against a mocked transport in
`tests/test_real_clients.py`. Several details weren't independently confirmed
against live docs and are flagged in the source with a pointer to what to
check first if the real call fails:

- `app/clients/heygen.py` — the generation-request field names are the
  well-established v2 avatar schema, but HeyGen's docs site is JS-rendered
  and couldn't be fetched past the page shell. The polling endpoint and auth
  header were confirmed. Check
  `https://docs.heygen.com/reference/create-an-avatar-video-v2` first.
- `app/clients/gemini_omni.py` — the generate + poll flow is fully confirmed
  (`ai.google.dev/gemini-api/docs/veo`), but the download-authentication step
  for the finished video URI isn't documented; this sends the same
  `x-goog-api-key` header used everywhere else in the API, which is the
  family's standard pattern but wasn't verified for this specific step. The
  image field is `bytesBase64Encoded`/`mimeType`, not the `inlineData` shape
  shown in Google's own published example — confirmed by a live 400 against
  `veo-3.1-generate-preview` on 2026-08-15 (`inlineData` is rejected outright
  regardless of image validity).
- `app/clients/replicate_wan.py` — the prediction create/poll/download flow
  (`POST /v1/predictions`, `Bearer` auth) is confirmed against Replicate's
  general HTTP API reference, but the Wan 2.2 model's exact input field names
  (`image`, `prompt`) are Replicate's conventional names, not verified
  against this specific model's OpenAPI schema (which requires an
  authenticated call to inspect). Check
  `https://replicate.com/wan-video/wan-2.2-i2v-fast/api` first if the real
  call fails. Runs on Replicate's hosted GPUs rather than locally — this app
  doesn't assume the host machine has a GPU with enough VRAM for the
  upstream [Wan2GP](https://github.com/deepbeepmeep/Wan2GP) project (6GB+
  minimum) to run natively.

Everything else (Google 3D Tiles) still raises `NotImplementedError` with a
pointer to what needs wiring once available — the call sites in
`app/pipeline/steps/` do not need to change either way.

Local CLI tools (Real-ESRGAN, DepthFlow, WhisperX, Demucs, FFmpeg, Remotion)
are gated by a single `LOCAL_TOOLS_AVAILABLE=1` flag in
`app/clients/local_tools.py` and `app/pipeline/steps/assembly.py`, since they
all need to be installed together for a real render to work. Zero-DCE is
excluded — see "Not implementable as scoped" above:

```bash
# FFmpeg — multiplexing, LUT grading, film grain
winget install Gyan.FFmpeg          # Windows
brew install ffmpeg                  # macOS

pip install realesrgan               # Real-ESRGAN — restoration + 4K upscale
pip install depthflow attrs          # DepthFlow — depth parallax motion (attrs for the bundled scene script)
pip install whisperx                 # WhisperX — word-level caption timing
pip install demucs                   # Demucs — music stem separation

cd remotion && npm install           # Remotion — video assembly (Master16x9 + Reel9x16 compositions built)
```

### Running

```bash
uvicorn app.main:app --reload        # backend — creates property_studio.db on first run
cd remotion && npm run dev           # Remotion Studio — preview compositions interactively
```

Endpoints: `GET/POST /api/admin/agencies` (admin-auth), `GET/POST /api/jobs`,
`POST /api/jobs/{id}/location` (§5 aggregator), `POST /api/jobs/{id}/run`
(runs every applicable pipeline step for the job's feature level),
`GET /api/integrations`, `PUT/DELETE /api/integrations/{slug}/fields/{key}`,
`POST /api/integrations/{slug}/test` (admin panel backend). The panel itself
is at `GET /admin/integrations`.

`cd remotion && npx tsc --noEmit` typechecks clean. A real `npx remotion
render` was smoke-tested in this session and got through bundling and
composition lookup into frame rendering; a full render with real local media
has not yet been verified end-to-end and should be the first check on a
machine with normal network access and FFmpeg installed.

## Layout

```
app/
  main.py                     # FastAPI app + routes
  db.py                       # SQLite engine/session
  models.py                   # §3 data models
  services/
    compliance.py             # §1 primitives: price detection, badge text
    script_prompt.py          # §1.1/§1.2 prompt choke point
    consent.py                # §1.3 voice/avatar consent gate
    render_contract.py        # §1.4 badge + Python→Remotion prop schema
    uk_location.py            # §5 neighbourhood data aggregator
    secrets_store.py          # Fernet encryption for stored credentials
    integration_registry.py   # known systems + their required fields
    integration_settings.py   # DB-backed credential read/write (admin panel backend)
    integration_test_connection.py  # per-system connection test logic
  clients/                    # external API/CLI wrappers
    heygen.py, elevenlabs.py, gemini_omni.py  # real HTTP clients, admin-panel/env credentialed
    local_tools.py             # real CLI calls: Real-ESRGAN, DepthFlow, WhisperX, Demucs, FFmpeg (LOCAL_TOOLS_AVAILABLE=1)
    credential_lookup.py       # shared session-aware credential resolution helper
  static/
    admin_integrations.html    # admin panel UI (no build step)
  pipeline/
    contract.py               # step interface, runner, job state machine
    registry.py                # feature-level composition + job_snapshot construction (consent gate lives here)
    steps/
      ingestion.py             # PDF → sanitized brochure sentences
      restoration.py           # Real-ESRGAN → Zero-DCE → white balance
      motion.py                # Gemini Omni (hero) / DepthFlow (interiors)
      script_and_voice.py      # script_prompt.py → LLM → ElevenLabs
      assembly.py              # render_contract.py → real Remotion CLI (entry point, composition ID, --props=)
      avatar_and_captions.py   # `plus`: HeyGen intro, WhisperX captions
      cinematic.py             # `cinematic`: sky replace, LUT/grain, music duck, aerial, microsite
  depthflow_scenes/
    subtle_parallax.py          # bundled DepthScene subclass -- DepthFlow has no built-in preset
tests/
  test_compliance.py            # 27 tests — the §1 guarantees
  test_pipeline_contract.py     # 14 tests — ordering, resume, failure semantics
  test_pipeline_end_to_end.py   # 7 tests — full stubbed runs at each feature level
  test_integration_settings.py  # 23 tests — encryption, masking, admin endpoints
  test_real_clients.py          # 16 tests — ElevenLabs/HeyGen/Gemini Omni request+response handling, mocked HTTP
  test_local_tools.py           # 13 tests — Real-ESRGAN/WhisperX/Demucs/DepthFlow/FFmpeg CLI invocation, mocked subprocess
  test_assembly.py              # 3 tests — real Remotion CLI invocation shape + project scaffold sanity check
remotion/                       # real Remotion project: Master16x9 + Reel9x16 compositions
  package.json, remotion.config.ts, tsconfig.json
  src/
    index.ts                    # registerRoot entry point
    Root.tsx                    # Composition registration + calculateMetadata
    props.ts                    # RenderProps TS types, mirrors render_contract.py's to_props() shape
    PropertyVideo.tsx           # shared composition: clips, §1.4 badge, lower-third, captions, audio
```
