# Property Content Studio — Build Spec (Final, for Claude Code / Antigravity)

You are an expert full-stack engineer and Python media automation specialist. Build a desktop-oriented local web application called **"Property Content Studio"**.

This internal dashboard is a production desk and lightweight CRM designed for a sole operator to ingest estate agency property assets (photos + PDF brochure) and programmatically output high-end, multi-format video and digital marketing packages.

The software is a **production tool only** — it has no concept of price, packages, or what the operator charges clients. It exposes feature toggles; how those get bundled and priced to agents happens entirely outside this system.

---

### 1. MANDATORY COMPLIANCE & ACCURACY CONSTRAINTS

Strictly enforce these across all AI generation, script writing, and image rendering modules:

1. **Misdescription Prevention (Strict Property Wording)**
   - The LLM prompt for script generation MUST be strictly constrained to re-sequencing, condensing, or formatting sentences that ALREADY EXIST in the source PDF brochure text.
   - NEVER invent new claims, unverified room features, or descriptive copy not present verbatim in the source PDF.

2. **Price Exclusion from Media**
   - `PropertyJob.price_guide` is strictly for internal CRM tracking and the property microsite landing page.
   - The LLM script generator MUST explicitly exclude price/price guide from all spoken voiceover scripts, avatar dialogue, and video caption/lower-third overlays.

3. **Avatar & Voice Consent Safeguards**
   - Do NOT use open-source face-swapping / LivePortrait-style engines without an equivalent verification control — none currently qualifies, so they are excluded from this build.
   - **Avatar pipeline: HeyGen only.** All avatar videos must use a verified, consented agent clone created through HeyGen's own identity-verification workflow. Store the verified `heygen_avatar_id` on `AgentProfile`.
   - **Voice-only pipeline (no avatar): ElevenLabs.** ElevenLabs has its own separate voice-cloning consent process (not covered by HeyGen's verification) — the UI must surface a clear reminder/checkbox at voice-clone creation time that explicit, specific consent for reusable AI cloning has been obtained from the person being cloned, before `elevenlabs_voice_id` can be saved against an `AgentProfile`.

4. **AI-Enhanced Image Disclosure**
   - If Sky Replacement / Dusk Conversion (ControlNet pass) is used on an exterior photo, the system MUST automatically render a mandatory overlay badge reading **"AI-Enhanced Visualization / Sky Replacement"** in the corner of the output image/video frame. Not optional, not togglable off.

---

### 2. TECH STACK & SYSTEM ARCHITECTURE

- **Backend:** Python FastAPI with Celery / Redis (or Python `BackgroundTasks`) for async local CLI/ML job execution.
- **Database:** SQLite via SQLModel / SQLAlchemy.
- **Video Assembly Engine:** [Remotion](https://github.com/remotion-dev/remotion) — React-based programmatic video framework. Maintain a `/remotion` subfolder of pre-built compositions.
- **Frontend:** React + Tailwind CSS (shadcn/ui where applicable), dark, high-density production-studio UI.
- **System dependency:** local FFmpeg binary for multiplexing, filtering, LUT colour grading.

---

### 3. LIGHTWEIGHT CRM & DATA MODELS

**AgentProfile**
- `id`: int (PK)
- `agency_name`: str
- `primary_color`, `secondary_color`: str (hex)
- `logo_path`: str
- `staff_name`: str
- `staff_headshot_path`: str
- `heygen_avatar_id`: str — verified HeyGen clone ID (avatar pipeline)
- `elevenlabs_voice_id`: str — verified ElevenLabs clone ID (voice-only pipeline)
- `voice_consent_confirmed`: bool — set true only after the UI consent checkbox (see §1.3) is ticked
- `created_at`: datetime

**PropertyJob**
- `id`: int (PK)
- `agent_id`: int (FK → AgentProfile.id)
- `address`, `postcode`: str
- `price_guide`: str — **CRM & microsite only; never passed into any script-generation or voice/avatar prompt**
- `garden_orientation`: str (e.g. "South-West")
- `latitude`, `longitude`: float (optional)
- `feature_level`: str — `"standard" | "plus" | "cinematic" | "custom"` (no price or tier-name baked in; purely a toggle set, see §4)
- `use_avatar`: bool — if false, pipeline uses ElevenLabs voice-only narration; if true, uses HeyGen avatar intro/outro
- `status`: str — `"ingestion" | "processing" | "review" | "completed"`
- `pdf_brochure_path`, `raw_photos_dir`: str
- `script_json`, `location_data_json`: JSON
- `created_at`: datetime

---

### 4. PRODUCTION PIPELINE (FEATURE-LEVEL TOGGLES, NOT PRICE TIERS)

The operator decides bundling/pricing outside this system. The pipeline just implements these as composable feature levels:

#### `standard`
1. **Restoration pass:**
   - [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) — JPEG artifact removal + 4K upscale
   - [Zero-DCE](https://github.com/Li-Chongyi/Zero-DCE) — adaptive shadow lift
   - OpenCV Gray-World auto white-balance
2. **Motion pass (hybrid, standing rule):**
   - Hero/front-exterior shot → **Gemini Omni API**, subtle zoom/pan only, forward-facing, no-reveal prompting (large or reversing moves cause hallucinated furniture — do not allow those prompt patterns)
   - Interior room shots → [DepthFlow](https://github.com/BrokenSource/DepthFlow) CLI (Depth Anything V2-based depth parallax)
3. **Audio & script pass:**
   - Parse brochure via `pdfplumber`
   - LLM script generation under the §1.1/§1.2 constraints → 1× walkthrough script (60s) + 2× social shorts (15–30s)
   - Voiceover via **ElevenLabs** (voice-only jobs) using `agent.elevenlabs_voice_id`
4. **Remotion assembly:** 16:9 master + 1× 9:16 reel, standard template, agency logo lower-third.

#### `plus` (everything in `standard`, plus:)
- **HeyGen avatar** intro/outro using `agent.heygen_avatar_id`, for the opening line only (per the two-ID architecture — avatar renders a few seconds, the rest of the narration stays as plain voice audio)
- [WhisperX](https://github.com/m-bain/whisperX) — word-level timestamps on the voiceover, driving kinetic captions and cut timing
- 3× 9:16 reels instead of 1

#### `cinematic` (everything in `plus`, plus:)
- Optional Sky/Dusk Conversion (ControlNet) — **mandatory disclosure badge per §1.4**
- Architectural 3D LUT (.cube) + subtle 35mm film grain via FFmpeg
- [Demucs](https://github.com/facebookresearch/demucs) — background music stem separation, auto-duck to −14dB under voiceover
- Google Photorealistic 3D Tiles API — 10s orbiting aerial flyover
- Standalone property microsite (see §6)

#### `custom`
- Manual override: per-photo motion vector assignment, custom lower-third typography, direct export hook into CapCut Pro for hands-on finishing.

---

### 5. UK NEIGHBOURHOOD DATA AGGREGATOR (`services/uk_location.py`)

1. **DfE / Ofsted Schools API** (`get-information-schools.service.gov.uk`) — 3 nearest "Outstanding"/"Good" schools, distance, phase, grade
2. **OpenStreetMap Overpass API** (`overpass-api.de`) — `(around:1000, lat, lng)` query for cafes, stations, parks, supermarkets; walking/driving distances
3. **Ofcom Broadband Checker API** (`checker.ofcom.org.uk`) — max download speed, FTTP/Ultrafast + 5G coverage
4. **SunCalc** (`suncalc` Python lib) — solar position from `garden_orientation` → daylight statement

Store output in `job.location_data_json`.

---

### 6. UI & WORKFLOW MODULES

1. **CRM & Jobs Dashboard (`/`)** — active agencies, job pipeline (`Ingestion → Processing → Review → Exported`)
2. **Agent Manager (`/agents`)** — CRUD: logo, headshot, brand colours, HeyGen Avatar ID, ElevenLabs Voice ID + consent checkbox (§1.3)
3. **Job Workspace (`/jobs/:id`)**
   - Left: ingestion form (PDF, photos, `feature_level` selector, `use_avatar` toggle, postcode, garden orientation)
   - Middle: script/audio editor (enforces §1.1/§1.2 constraints visibly), photo/motion gallery (Gemini Omni vs DepthFlow per shot), location insights preview
   - Right: render queue + live Remotion preview
4. **One-Click Pack Export (`/jobs/:id/export`)** — microsite builder (Tailwind static `index.html`, embeds video/gallery/contact widget/map/price/location insights), 7-day social calendar PDF, ZIP of master video + reels + microsite + calendar

---

### 7. GITHUB REPOS / EXTERNAL SERVICES REFERENCE

| Purpose | Repo / Service |
|---|---|
| Image restoration/upscale | github.com/xinntao/Real-ESRGAN |
| Low-light shadow lift | github.com/Li-Chongyi/Zero-DCE |
| Depth-based parallax motion | github.com/BrokenSource/DepthFlow |
| Hero-shot AI video generation | Gemini Omni API (Google) |
| Word-level audio timestamps | github.com/m-bain/whisperX |
| Music stem separation/ducking | github.com/facebookresearch/demucs |
| Avatar generation (verified) | HeyGen API — api.heygen.com |
| Voice cloning (voice-only jobs) | ElevenLabs API |
| Programmatic video assembly | github.com/remotion-dev/remotion |
| Aerial flyover | Google Photorealistic 3D Tiles API |
| Schools data | get-information-schools.service.gov.uk |
| Local amenities | overpass-api.de (OpenStreetMap Overpass) |
| Broadband/mobile coverage | checker.ofcom.org.uk |
| Solar/garden orientation | PyPI: `suncalc` |
| PDF parsing | PyPI: `pdfplumber` |

---

### 8. SETUP & ENVIRONMENT

`README.md` must document:
- Env vars: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `HEYGEN_API_KEY`, `GOOGLE_MAPS_3D_TILES_KEY`, `GEMINI_API_KEY`
- Local CLI dependency install commands: DepthFlow, WhisperX, Real-ESRGAN, Remotion, FFmpeg
- DB init + local run: `uvicorn main:app --reload` + `npm run dev`

---

### 9. BUILD SEQUENCING (MODEL ALLOCATION)

Two phases. Phase A must be complete and reviewed before Phase B begins.

#### Phase A — architecture (get exactly right first time; use Opus)

These decisions are load-bearing: every later step depends on them, and a subtle error here is invisible in review of the later work.

1. **Data models** (§3) — SQLModel definitions, relationships, migrations.
2. **§1 compliance enforcement layer** — the critical piece. Not documentation, not prompt wording: *structural* enforcement.
   - A single choke point, `services/script_prompt.py`, is the ONLY place a script-generation prompt may be assembled. Pipeline steps never build prompts inline.
   - It accepts an explicit **whitelist** of job fields. `price_guide` is not on the whitelist and is never passed in — the builder receives a narrowed DTO (`ScriptJobContext`) that physically has no price attribute, so leaking it is a type error rather than a review oversight.
   - Source-text grounding (§1.1): the builder takes brochure sentences as a discrete list and emits a prompt constrained to re-sequence/condense them.
   - Tests that MUST exist and MUST fail loudly:
     - a job with a distinctive `price_guide` sentinel renders a prompt containing no occurrence of it, across every prompt variant (walkthrough, shorts, avatar line, caption/lower-third);
     - `ScriptJobContext` cannot be constructed from a `PropertyJob` without dropping `price_guide`;
     - any generated caption/overlay text passes a price-pattern regex check (`£`, "guide price", "OIRO", "offers over") before render.
   - The §1.4 AI-enhanced badge is applied by the render layer, not by the step that requests sky replacement — a photo flagged `sky_replaced` gets the badge composited unconditionally at assembly time, so no future step can skip it.
   - `voice_consent_confirmed` gates `elevenlabs_voice_id` at the model/service layer, not just the form.
3. **Pipeline orchestration contract** — the step interface (input/output artifact types, idempotency, resumability, failure semantics), the job state machine (§3 `status`), and how `feature_level` composes steps. Get this right and each step becomes a self-contained fill-in.
4. **Remotion composition interfaces** — the prop schema passed from Python to Remotion (timeline, captions, branding, badge flags). This is the boundary between the two languages and is expensive to change later.

#### Phase B — routine implementation (use Sonnet)

Well-scoped work against the Phase A contracts:

- Individual pipeline steps (Real-ESRGAN, Zero-DCE, white-balance, DepthFlow, Gemini Omni, WhisperX, Demucs, LUT/grain FFmpeg pass)
- External API clients (HeyGen, ElevenLabs, Gemini, 3D Tiles)
- UK location aggregator (§5) — self-contained, no compliance surface
- React UI components and pages (§6)
- Microsite builder, social calendar PDF, ZIP export (§6.4)
- README and setup docs (§8)

**Rule of thumb:** if the work touches how a prompt is built, what reaches a rendered frame, or how steps are sequenced, it belongs in Phase A. If it's "call this tool and return a file," it's Phase B.

---
