# Sentence-Photo Linking — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend half of the sentence-by-sentence photo-linking workflow: a `ScriptSegment` data model, brochure+photo upload endpoints, structured (JSON-list) script generation, continuous-synthesis-then-slice audio production, and a per-segment Remotion assembly contract — all independently testable via API/unit tests with no UI required.

**Architecture:** Add `ScriptSegment` (SQLModel table) linking one sentence of narration to one `Photo` and one audio clip. Replace the single-blob walkthrough LLM prompt with a new prompt that returns a JSON list of segments. Synthesize all non-avatar-intro segment text as one continuous ElevenLabs call (for natural intonation), then use WhisperX forced-alignment against the already-known segment texts to slice that one audio file into per-segment clips. Rework `RenderProps`/`PropertyVideo.tsx` from "one photo sequence + one global voiceover track" to "an ordered list of self-contained (visual, audio, captions, duration) segments," each rendered for exactly its own audio's length.

**Tech Stack:** FastAPI, SQLModel/SQLite, existing `ElevenLabsClient`/`whisperx_word_timestamps` clients, `pydub` (new dependency, for audio slicing), Remotion/React/TypeScript for the render layer mirror.

---

## Spec coverage checklist (for self-review, not part of the plan body)

- §1 data model (`ScriptSegment`) → Task 1
- §2 script generation (structured JSON prompt, intro flag, price-check) → Task 3
- §3 upload endpoints (brochure + photo batch) → Task 2
- §3 segment CRUD (edit/add/delete/reorder), compliance re-check on save → Task 4
- §3 runtime estimate (soft warning, non-blocking) → Task 4 (estimate field), enforced client-side later in the frontend plan
- §4 continuous-synthesis-then-slice audio, avatar-intro exception, voice consent gate → Task 5, Task 6
- §5 Remotion assembly contract rework → Task 7, Task 8
- §6 compliance (price-check on every save path) → Task 4 (folded in directly, no separate task needed — see Task 4 Step 3)

---

### Task 1: `ScriptSegment` model

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_script_segment_model.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_script_segment_model.py
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentProfile, Photo, PropertyJob, ScriptSegment


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_job(session: Session) -> PropertyJob:
    agent = AgentProfile(agency_name="Test Agency")
    session.add(agent)
    session.commit()
    session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_script_segment_round_trips(session) -> None:
    job = _make_job(session)
    segment = ScriptSegment(job_id=job.id, order_index=0, text="Hi, I'm James.", is_intro=True)
    session.add(segment)
    session.commit()
    session.refresh(segment)

    assert segment.id is not None
    assert segment.photo_id is None
    assert segment.audio_path is None
    assert segment.duration_sec is None


def test_script_segment_can_reference_a_photo(session) -> None:
    job = _make_job(session)
    photo = Photo(job_id=job.id, source_path="/photos/kitchen.jpg", order_index=0)
    session.add(photo)
    session.commit()
    session.refresh(photo)

    segment = ScriptSegment(job_id=job.id, order_index=1, text="The kitchen has bi-fold doors.", photo_id=photo.id)
    session.add(segment)
    session.commit()
    session.refresh(segment)

    assert segment.photo_id == photo.id


def test_same_photo_can_back_two_segments(session) -> None:
    job = _make_job(session)
    photo = Photo(job_id=job.id, source_path="/photos/exterior.jpg", order_index=0)
    session.add(photo)
    session.commit()
    session.refresh(photo)

    seg_a = ScriptSegment(job_id=job.id, order_index=0, text="Welcome to the property.", photo_id=photo.id, is_intro=True)
    seg_b = ScriptSegment(job_id=job.id, order_index=4, text="And that's the view from the front.", photo_id=photo.id)
    session.add(seg_a)
    session.add(seg_b)
    session.commit()

    stmt_count = session.exec(
        __import__("sqlmodel").select(ScriptSegment).where(ScriptSegment.photo_id == photo.id)
    ).all()
    assert len(stmt_count) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_script_segment_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScriptSegment' from 'app.models'`

- [ ] **Step 3: Add the model**

In `app/models.py`, add after the `Photo` class:

```python
class ScriptSegment(SQLModel, table=True):
    """One sentence of narration, paired with the one photo that illustrates
    it and the one audio clip that voices it (spec: sentence-photo linking
    design, 2026-08-12).

    `photo_id` and `audio_path` start null and are filled in during the
    arrange step / generate step respectively. A `Photo` may be referenced by
    more than one `ScriptSegment` -- reuse across sentences is allowed by
    design, so there is no uniqueness constraint on `photo_id`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="propertyjob.id")

    order_index: int = 0
    text: str
    is_intro: bool = False

    photo_id: Optional[int] = Field(default=None, foreign_key="photo.id")
    audio_path: Optional[str] = None
    duration_sec: Optional[float] = None

    job: Optional[PropertyJob] = Relationship()
    photo: Optional[Photo] = Relationship()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_script_segment_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All existing tests still pass — this is a purely additive model change.

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_script_segment_model.py
git commit -m "feat: add ScriptSegment model for sentence-photo linking"
```

---

### Task 2: Brochure + photo batch upload endpoints

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_uploads.py` (new file)

There is currently no file-upload endpoint anywhere in the app (confirmed by
the design-doc audit). `PropertyJob.pdf_brochure_path` and
`PropertyJob.raw_photos_dir`/individual `Photo` rows are populated today by
pointing at a filesystem path out of band. This task adds the missing HTTP
upload surface, storing files under a per-job directory and creating `Photo`
rows for each uploaded image.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_uploads.py
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    monkeypatch.setenv("PROPERTY_STUDIO_UPLOAD_DIR", str(tmp_path / "uploads"))

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job(api_client) -> int:
    resp = api_client.post(
        "/api/jobs",
        json={"address": "1 Test St", "postcode": "TE1 1ST"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_upload_brochure_sets_pdf_path(api_client) -> None:
    job_id = _create_job(api_client)
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    resp = api_client.post(
        f"/api/jobs/{job_id}/brochure",
        files={"file": ("brochure.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_brochure_path"]

    job_resp = api_client.get(f"/api/jobs/{job_id}")
    assert job_resp.json()["pdf_brochure_path"] == data["pdf_brochure_path"]


def test_upload_brochure_rejects_non_pdf(api_client) -> None:
    job_id = _create_job(api_client)
    resp = api_client.post(
        f"/api/jobs/{job_id}/brochure",
        files={"file": ("brochure.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_photos_creates_photo_rows(api_client) -> None:
    job_id = _create_job(api_client)
    img_bytes = b"\xff\xd8\xff\xe0fake jpeg content"
    resp = api_client.post(
        f"/api/jobs/{job_id}/photos",
        files=[
            ("files", ("kitchen.jpg", io.BytesIO(img_bytes), "image/jpeg")),
            ("files", ("garden.jpg", io.BytesIO(img_bytes), "image/jpeg")),
        ],
    )
    assert resp.status_code == 201
    photos = resp.json()
    assert len(photos) == 2
    assert {p["source_path"] for p in photos} == {
        p["source_path"] for p in photos
    }  # sanity: both distinct rows returned

    list_resp = api_client.get(f"/api/jobs/{job_id}/photos")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 2


def test_upload_photos_unknown_job_returns_404(api_client) -> None:
    resp = api_client.post(
        "/api/jobs/999999/photos",
        files=[("files", ("x.jpg", io.BytesIO(b"x"), "image/jpeg"))],
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_uploads.py -v`
Expected: FAIL with 404s (routes don't exist yet).

- [ ] **Step 3: Add the upload endpoints**

In `app/main.py`, add near the other job endpoints (after `create_job`):

```python
import os
import uuid

from fastapi import File, UploadFile


def _upload_dir(job_id: int) -> Path:
    base = Path(os.environ.get("PROPERTY_STUDIO_UPLOAD_DIR", "uploads"))
    job_dir = base / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


@app.post("/api/jobs/{job_id}/brochure")
async def upload_brochure(
    job_id: int, file: UploadFile = File(...), session: Session = Depends(get_session)
) -> dict:
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "brochure must be a PDF file")

    dest = _upload_dir(job_id) / "brochure.pdf"
    contents = await file.read()
    dest.write_bytes(contents)

    job.pdf_brochure_path = str(dest)
    session.add(job)
    session.commit()
    return {"pdf_brochure_path": job.pdf_brochure_path}


@app.post("/api/jobs/{job_id}/photos", status_code=201)
async def upload_photos(
    job_id: int, files: list[UploadFile] = File(...), session: Session = Depends(get_session)
) -> list[Photo]:
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    existing_count = len(
        session.exec(select(Photo).where(Photo.job_id == job_id)).all()
    )

    dest_dir = _upload_dir(job_id) / "photos"
    dest_dir.mkdir(parents=True, exist_ok=True)

    created: list[Photo] = []
    for i, upload in enumerate(files):
        suffix = Path(upload.filename or "photo.jpg").suffix or ".jpg"
        dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"
        contents = await upload.read()
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


@app.get("/api/jobs/{job_id}/photos")
def list_job_photos(job_id: int, session: Session = Depends(get_session)) -> list[Photo]:
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return session.exec(
        select(Photo).where(Photo.job_id == job_id).order_by(Photo.order_index)
    ).all()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_uploads.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_uploads.py
git commit -m "feat: add brochure and photo batch upload endpoints"
```

---

### Task 3: Structured (JSON-list) script generation

**Files:**
- Modify: `app/services/script_prompt.py`
- Test: `tests/test_script_prompt.py`

Replace the single-blob `WALKTHROUGH` prompt with a new `SEGMENTED_WALKTHROUGH`
variant that asks the LLM to return a JSON list of 5-10 short sentences, the
first always flagged as intro. Keep `WALKTHROUGH`/`SHORT`/`AVATAR_OPENING`/
`CAPTION` untouched — `social_shorts` generation is explicitly out of scope
per the spec's non-goals, and existing tests must keep passing.

- [ ] **Step 1: Check existing script_prompt tests to avoid duplicate fixtures**

Run: `pytest tests/ -k script_prompt --collect-only -q`
Expected: lists any existing test file/functions for `script_prompt.py` (there may be none yet — the current suite covers this module only indirectly via `test_pipeline_end_to_end.py`). Proceed regardless; the new test file below is additive.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_script_prompt.py
from __future__ import annotations

import pytest

from app.services.script_prompt import ScriptJobContext, ScriptVariant, build_prompt


@pytest.fixture
def context() -> ScriptJobContext:
    return ScriptJobContext.from_fields(
        address="5 Wardington Crescent",
        postcode="SW1A 2AA",
        garden_orientation="South-West",
        agency_name="Test Agency",
        staff_name="James",
        brochure_sentences=[
            "A detached four bedroom family home set back from the road.",
            "The kitchen has been extended to the rear with bi-fold doors.",
            "The garden is mainly laid to lawn with a paved terrace.",
        ],
    )


def test_segmented_walkthrough_variant_exists() -> None:
    assert ScriptVariant.SEGMENTED_WALKTHROUGH == "segmented_walkthrough"


def test_segmented_walkthrough_prompt_asks_for_json_list(context) -> None:
    prompt = build_prompt(context, ScriptVariant.SEGMENTED_WALKTHROUGH)
    assert "JSON" in prompt
    assert "5" in prompt and "10" in prompt  # sentence count range mentioned
    assert "is_intro" in prompt
    assert "SOURCE SENTENCES" in prompt


def test_segmented_walkthrough_prompt_is_price_free(context) -> None:
    # build_prompt already runs assert_price_free internally; this just
    # confirms it doesn't raise for a clean context.
    prompt = build_prompt(context, ScriptVariant.SEGMENTED_WALKTHROUGH)
    assert "price" not in prompt.lower().split("absolute rules")[0]  # sanity smoke check


def test_existing_walkthrough_variant_unchanged(context) -> None:
    prompt = build_prompt(context, ScriptVariant.WALKTHROUGH)
    assert "roughly 60 seconds" in prompt
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_script_prompt.py -v`
Expected: FAIL with `AttributeError: SEGMENTED_WALKTHROUGH` (enum member doesn't exist yet).

- [ ] **Step 4: Add the new variant and brief**

In `app/services/script_prompt.py`, update the `ScriptVariant` enum:

```python
class ScriptVariant(str, Enum):
    WALKTHROUGH = "walkthrough"       # 60s master narration (legacy, unused by the segmented flow)
    SEGMENTED_WALKTHROUGH = "segmented_walkthrough"  # 5-10 discrete sentence-elements as JSON
    SHORT = "short"                   # 15-30s social cut
    AVATAR_OPENING = "avatar_opening" # HeyGen opening line only (legacy, unused by the segmented flow)
    CAPTION = "caption"               # kinetic captions / lower-thirds
```

Add the new brief to `_VARIANT_BRIEF`:

```python
_VARIANT_BRIEF: dict[ScriptVariant, str] = {
    ScriptVariant.WALKTHROUGH: (
        "Write a single continuous voiceover of roughly 60 seconds "
        "(about 150 words) that walks the viewer through the property."
    ),
    ScriptVariant.SEGMENTED_WALKTHROUGH: (
        "Split the property tour into 5 to 10 short sentence-elements, each "
        "covering exactly one room, feature, or aspect of the property "
        "(e.g. kitchen, living room, garden). The FIRST sentence must always "
        "be a short spoken introduction (max 25 words) welcoming the viewer "
        "and naming the property address, e.g. \"Hi, I'm James, I'd love to "
        "show you around 5 Wardington Crescent.\" Every sentence after the "
        "first should be one short, natural spoken line (roughly 10-25 words) "
        "about a single room or feature. Aim for a total spoken length of "
        "around two minutes across all sentences combined.\n\n"
        "Return ONLY a JSON array, no other text, in this exact shape:\n"
        '[{"text": "...", "is_intro": true}, {"text": "...", "is_intro": false}, ...]\n'
        "The first array item must have \"is_intro\": true; every other item "
        "must have \"is_intro\": false."
    ),
    ScriptVariant.SHORT: (
        "Write a punchy social voiceover of 15-30 seconds (about 45 words) "
        "covering only the two or three strongest points from the source."
    ),
    ScriptVariant.AVATAR_OPENING: (
        "Write ONE spoken opening sentence (max 25 words) introducing the "
        "property, to be delivered to camera by the named agent."
    ),
    ScriptVariant.CAPTION: (
        "Write 4-6 short on-screen caption phrases, one per line, max 6 words "
        "each. These are overlay text, not narration."
    ),
}
```

`build_prompt` needs no other changes — it already looks up
`_VARIANT_BRIEF[variant]` generically and runs `assert_price_free` on the
full payload regardless of variant, so the new variant inherits the same
grounding/compliance guarantees as every other variant with zero additional
code.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_script_prompt.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass — this only adds an enum member and a dict entry, no existing variant's brief text changed.

- [ ] **Step 7: Commit**

```bash
git add app/services/script_prompt.py tests/test_script_prompt.py
git commit -m "feat: add segmented_walkthrough script prompt variant"
```

---

### Task 4: Segment generation step + CRUD endpoints

**Files:**
- Create: `app/services/script_segments.py`
- Modify: `app/main.py`
- Test: `tests/test_script_segments.py` (new file)

This task adds the service that turns an LLM's JSON response into
`ScriptSegment` rows, and the API surface for the agent to edit/add/delete/
reorder/assign-photo on those rows, each write re-running `assert_price_free`
(closing the compliance gap the design doc identified in the existing
`PUT /api/jobs/{job_id}/script` endpoint).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_script_segments.py
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentProfile, Photo, PropertyJob, ScriptSegment
from app.services.compliance import ComplianceError
from app.services.script_segments import (
    create_segments_from_llm_json,
    estimate_total_duration_sec,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def job(session) -> PropertyJob:
    agent = AgentProfile(agency_name="Test Agency")
    session.add(agent)
    session.commit()
    session.refresh(agent)
    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_create_segments_from_llm_json_persists_ordered_rows(session, job) -> None:
    llm_output = (
        '[{"text": "Hi, I am James.", "is_intro": true}, '
        '{"text": "The kitchen is bright.", "is_intro": false}, '
        '{"text": "The garden faces south.", "is_intro": false}]'
    )
    segments = create_segments_from_llm_json(session, job_id=job.id, llm_json_text=llm_output)

    assert len(segments) == 3
    assert segments[0].is_intro is True
    assert segments[0].order_index == 0
    assert segments[1].order_index == 1
    assert segments[2].is_intro is False


def test_create_segments_rejects_price_bearing_text(session, job) -> None:
    llm_output = '[{"text": "Yours for £450,000.", "is_intro": true}]'
    with pytest.raises(ComplianceError):
        create_segments_from_llm_json(session, job_id=job.id, llm_json_text=llm_output)


def test_create_segments_rejects_malformed_json(session, job) -> None:
    with pytest.raises(ValueError):
        create_segments_from_llm_json(session, job_id=job.id, llm_json_text="not json")


def test_estimate_total_duration_sec_uses_words_per_second_heuristic() -> None:
    # ~150 words/min speech rate => 2.5 words/sec => 1 word ~= 0.4s
    texts = ["one two three four five"] * 4  # 20 words total
    seconds = estimate_total_duration_sec(texts)
    assert 6.0 < seconds < 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_script_segments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.script_segments'`

- [ ] **Step 3: Check `compliance.py` for the exact exception type**

Run this to confirm the exception class name before writing the service (avoids guessing):

```bash
python -c "from app.services.compliance import ComplianceError; print(ComplianceError)"
```

Expected: prints the class without error (`assert_price_free` at
`compliance.py:47-58` raises this per the earlier codebase reads). If this
import fails, open `app/services/compliance.py` and use whatever exception
`assert_price_free` actually raises in Step 4 below instead.

- [ ] **Step 4: Write `app/services/script_segments.py`**

```python
"""Turn an LLM's segmented-walkthrough JSON response into `ScriptSegment`
rows, and shared helpers for estimating spoken duration.

Every sentence -- LLM-generated or agent-authored -- passes through
`assert_price_free` before being persisted (spec: sentence-photo linking
design, 2026-08-12, §6).
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from ..models import ScriptSegment
from .compliance import assert_price_free

_WORDS_PER_SECOND = 2.5  # ~150 words/minute average spoken narration rate


def estimate_total_duration_sec(texts: list[str]) -> float:
    """Rough words-per-second estimate, used for the arrange screen's
    advisory 2-minute indicator. Not used to block anything (soft warning
    only, per the design doc)."""
    total_words = sum(len(t.split()) for t in texts)
    return total_words / _WORDS_PER_SECOND


def create_segments_from_llm_json(
    session: Session, *, job_id: int, llm_json_text: str
) -> list[ScriptSegment]:
    """Parse the LLM's JSON array response and persist one ScriptSegment per
    item, in order. Raises `ValueError` on malformed JSON/shape, or whatever
    `assert_price_free` raises if any item's text mentions price.
    """
    try:
        items = json.loads(llm_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc

    if not isinstance(items, list) or not items:
        raise ValueError(f"Expected a non-empty JSON array, got: {llm_json_text!r}")

    segments: list[ScriptSegment] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "text" not in item:
            raise ValueError(f"Segment {i} missing 'text' field: {item!r}")

        text = str(item["text"]).strip()
        assert_price_free(text, context=f"generated segment {i}")

        segment = ScriptSegment(
            job_id=job_id,
            order_index=i,
            text=text,
            is_intro=bool(item.get("is_intro", i == 0)),
        )
        session.add(segment)
        segments.append(segment)

    session.commit()
    for s in segments:
        session.refresh(s)
    return segments


def list_segments(session: Session, job_id: int) -> list[ScriptSegment]:
    return session.exec(
        select(ScriptSegment).where(ScriptSegment.job_id == job_id).order_by(ScriptSegment.order_index)
    ).all()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_script_segments.py -v`
Expected: PASS (4 tests). If Step 3's exception-name check showed a
different class than `ComplianceError`, update the test file's import and
`pytest.raises(...)` to match before re-running.

- [ ] **Step 6: Add CRUD API endpoints**

In `app/main.py`, add near the other job-scoped endpoints:

```python
class CreateSegmentRequest(BaseModel):
    text: str
    order_index: Optional[int] = None


class UpdateSegmentRequest(BaseModel):
    text: Optional[str] = None
    photo_id: Optional[int] = None
    order_index: Optional[int] = None


def _serialize_segment(segment) -> dict:
    return {
        "id": segment.id,
        "job_id": segment.job_id,
        "order_index": segment.order_index,
        "text": segment.text,
        "is_intro": segment.is_intro,
        "photo_id": segment.photo_id,
        "audio_path": segment.audio_path,
        "duration_sec": segment.duration_sec,
    }


@app.get("/api/jobs/{job_id}/segments")
def list_job_segments(job_id: int, session: Session = Depends(get_session)) -> list[dict]:
    from .services.script_segments import list_segments

    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return [_serialize_segment(s) for s in list_segments(session, job_id)]


@app.post("/api/jobs/{job_id}/segments", status_code=201)
def create_job_segment(
    job_id: int, body: CreateSegmentRequest, session: Session = Depends(get_session)
) -> dict:
    from .services.compliance import assert_price_free
    from .services.script_segments import list_segments

    job = session.get(PropertyJob, job_id)
    if job is None:
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


@app.put("/api/segments/{segment_id}")
def update_job_segment(
    segment_id: int, body: UpdateSegmentRequest, session: Session = Depends(get_session)
) -> dict:
    from .services.compliance import assert_price_free

    segment = session.get(ScriptSegment, segment_id)
    if segment is None:
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


@app.delete("/api/segments/{segment_id}")
def delete_job_segment(segment_id: int, session: Session = Depends(get_session)) -> dict:
    segment = session.get(ScriptSegment, segment_id)
    if segment is None:
        raise HTTPException(404, "segment not found")
    session.delete(segment)
    session.commit()
    return {"deleted": True}
```

Add the `ScriptSegment` import to the top of `app/main.py` alongside the
other model imports:

```python
from .models import AgentProfile, JobStatus, PropertyJob, ScriptSegment
```

- [ ] **Step 7: Write API tests for the CRUD endpoints**

```python
# append to tests/test_script_segments.py

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPERTY_STUDIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PROPERTY_STUDIO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod
    from sqlmodel import create_engine

    db_mod.engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    secrets_mod._default_store = None
    secrets_mod.DEFAULT_KEY_PATH = tmp_path / "secret.key"

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


def _create_job_via_api(api_client) -> int:
    resp = api_client.post("/api/jobs", json={"address": "1 Test St", "postcode": "TE1 1ST"})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_create_and_list_segments_via_api(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A lovely hallway."})
    assert resp.status_code == 201
    assert resp.json()["order_index"] == 0

    list_resp = api_client.get(f"/api/jobs/{job_id}/segments")
    assert len(list_resp.json()) == 1


def test_create_segment_rejects_price_text(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "Offers over £300,000."})
    assert resp.status_code == 400


def test_update_segment_text_rejects_price(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A nice garden."})
    segment_id = create_resp.json()["id"]

    resp = api_client.put(f"/api/segments/{segment_id}", json={"text": "Guide price £500,000."})
    assert resp.status_code == 400


def test_update_segment_photo_id_must_belong_to_same_job(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    other_job_id = _create_job_via_api(api_client)

    import io
    photo_resp = api_client.post(
        f"/api/jobs/{other_job_id}/photos",
        files=[("files", ("x.jpg", io.BytesIO(b"x"), "image/jpeg"))],
    )
    other_photo_id = photo_resp.json()[0]["id"]

    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "The kitchen."})
    segment_id = create_resp.json()["id"]

    resp = api_client.put(f"/api/segments/{segment_id}", json={"photo_id": other_photo_id})
    assert resp.status_code == 400


def test_delete_segment(api_client) -> None:
    job_id = _create_job_via_api(api_client)
    create_resp = api_client.post(f"/api/jobs/{job_id}/segments", json={"text": "A spare room."})
    segment_id = create_resp.json()["id"]

    resp = api_client.delete(f"/api/segments/{segment_id}")
    assert resp.status_code == 200

    list_resp = api_client.get(f"/api/jobs/{job_id}/segments")
    assert list_resp.json() == []
```

- [ ] **Step 8: Run all segment tests**

Run: `pytest tests/test_script_segments.py -v`
Expected: PASS (10 tests total: 4 service-layer + 6 API).

- [ ] **Step 9: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 10: Commit**

```bash
git add app/services/script_segments.py app/main.py tests/test_script_segments.py
git commit -m "feat: add script segment generation service and CRUD endpoints"
```

---

### Task 5: Audio slicing capability (`pydub`-based)

**Files:**
- Modify: `requirements.txt`
- Create: `app/clients/audio_slicing.py`
- Test: `tests/test_audio_slicing.py` (new file)

Adds the one new capability the design calls for: given one audio file and a
list of known (text, start_sec, end_sec) boundaries, slice it into separate
per-segment files. This task builds and tests slicing in isolation, against
a locally-generated WAV fixture — no ElevenLabs/WhisperX calls, no network.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, add under the "Media / ingestion" section:

```
pydub>=0.25
```

Run: `pip install pydub>=0.25`
Expected: installs successfully (pydub is pure-Python except for needing
`ffmpeg` on PATH for non-WAV formats at runtime — the test fixture below
uses WAV, which pydub can read via the stdlib `wave` module without ffmpeg).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_audio_slicing.py
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.clients.audio_slicing import AudioBoundary, slice_audio_file


@pytest.fixture
def silent_wav(tmp_path: Path) -> Path:
    """A 3-second silent mono WAV fixture -- no ffmpeg required to read it."""
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 3)  # 3 seconds of silence
    return path


def test_slice_audio_file_produces_one_file_per_boundary(silent_wav: Path, tmp_path: Path) -> None:
    boundaries = [
        AudioBoundary(start_sec=0.0, end_sec=1.0),
        AudioBoundary(start_sec=1.0, end_sec=2.5),
        AudioBoundary(start_sec=2.5, end_sec=3.0),
    ]
    out_dir = tmp_path / "slices"
    paths = slice_audio_file(silent_wav, boundaries, out_dir=out_dir, stem="segment")

    assert len(paths) == 3
    assert all(p.exists() for p in paths)
    assert paths[0].name == "segment_0.mp3" or paths[0].name == "segment_0.wav"


def test_slice_audio_file_slice_durations_match_boundaries(silent_wav: Path, tmp_path: Path) -> None:
    from pydub import AudioSegment

    boundaries = [AudioBoundary(start_sec=0.0, end_sec=1.5), AudioBoundary(start_sec=1.5, end_sec=3.0)]
    out_dir = tmp_path / "slices2"
    paths = slice_audio_file(silent_wav, boundaries, out_dir=out_dir, stem="seg")

    first = AudioSegment.from_file(paths[0])
    assert 1400 <= len(first) <= 1600  # ~1.5s in milliseconds, small tolerance


def test_slice_audio_file_rejects_empty_boundaries(silent_wav: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        slice_audio_file(silent_wav, [], out_dir=tmp_path / "slices3", stem="seg")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_audio_slicing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.clients.audio_slicing'`

- [ ] **Step 4: Write `app/clients/audio_slicing.py`**

```python
"""Slice one continuous audio file into per-segment clips at known
boundaries.

Used by the sentence-photo linking workflow: narration is synthesized as one
continuous ElevenLabs take (for natural sentence-to-sentence intonation),
then sliced here at boundaries found by forced-aligning the already-known
segment texts against that audio via WhisperX. This module only does the
slicing -- alignment lives in `services.script_audio` (Task 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment


@dataclass(frozen=True)
class AudioBoundary:
    start_sec: float
    end_sec: float


def slice_audio_file(
    source: Path, boundaries: list[AudioBoundary], *, out_dir: Path, stem: str
) -> list[Path]:
    """Slice `source` into one file per boundary, named `{stem}_{i}.mp3`.

    Boundaries are given in seconds and must be non-empty.
    """
    if not boundaries:
        raise ValueError("boundaries must be non-empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    audio = AudioSegment.from_file(source)

    paths: list[Path] = []
    for i, boundary in enumerate(boundaries):
        start_ms = int(boundary.start_sec * 1000)
        end_ms = int(boundary.end_sec * 1000)
        clip = audio[start_ms:end_ms]

        dest = out_dir / f"{stem}_{i}.mp3"
        clip.export(dest, format="mp3")
        paths.append(dest)

    return paths
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_audio_slicing.py -v`
Expected: PASS (3 tests). If `pydub`'s mp3 export fails locally because
`ffmpeg`/`libav` isn't on PATH, that is an environment gap for real usage,
not a code bug — note it and continue; Task 9's manual verification step
covers the real end-to-end path once `LOCAL_TOOLS_AVAILABLE=1` tooling is
actually installed. `wave`-only slicing (source read) does not require
ffmpeg; only `export(format="mp3")` does. If needed for CI/test purposes,
adjust `slice_audio_file`'s `format` argument to `"wav"` and adjust the test
fixture's expected extension accordingly, keeping mp3 as the real default in
production paths documented in Task 6.

- [ ] **Step 6: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/clients/audio_slicing.py tests/test_audio_slicing.py
git commit -m "feat: add audio slicing capability for per-segment clips"
```

---

### Task 6: Continuous-synthesis-then-slice segment audio service

**Files:**
- Create: `app/services/script_audio.py`
- Modify: `app/clients/local_tools.py` (no functional change — read only, confirming `WordTimestamp` shape used below)
- Test: `tests/test_script_audio.py` (new file)

This is the core of §4: synthesize all non-avatar-intro segment text as ONE
ElevenLabs call, then force-align that continuous audio against the known
segment texts using WhisperX word timestamps to find per-segment boundaries,
then slice.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_script_audio.py
from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.elevenlabs import StubElevenLabsClient
from app.clients.local_tools import WordTimestamp
from app.services.script_audio import synthesize_and_slice_segments


class _FakeElevenLabsClient(StubElevenLabsClient):
    """Records the exact text it was asked to synthesize, so tests can assert
    the segments were concatenated into ONE call rather than N calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        self.calls.append(text)
        return super().synthesize(voice_id=voice_id, text=text, output_path=output_path)


def _fake_whisperx(audio_path: Path, *, work_dir: Path | None = None) -> list[WordTimestamp]:
    """Deterministic word timings covering 'Hi there. The kitchen is bright.'
    at a fixed rate, standing in for a real forced-alignment call."""
    words = ["Hi", "there.", "The", "kitchen", "is", "bright."]
    timings = []
    t = 0.0
    for w in words:
        timings.append(WordTimestamp(w, t, t + 0.5))
        t += 0.5
    return timings


def test_synthesize_and_slice_makes_exactly_one_elevenlabs_call(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    segments_text = ["Hi there.", "The kitchen is bright."]

    result = synthesize_and_slice_segments(
        segments_text,
        voice_id="voice-123",
        elevenlabs_client=client,
        whisperx_fn=_fake_whisperx,
        out_dir=tmp_path / "audio",
        stem="walkthrough",
    )

    assert len(client.calls) == 1
    assert client.calls[0] == "Hi there. The kitchen is bright."


def test_synthesize_and_slice_returns_one_path_per_segment(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    segments_text = ["Hi there.", "The kitchen is bright."]

    result = synthesize_and_slice_segments(
        segments_text,
        voice_id="voice-123",
        elevenlabs_client=client,
        whisperx_fn=_fake_whisperx,
        out_dir=tmp_path / "audio2",
        stem="walkthrough",
    )

    assert len(result) == 2
    assert all(p.exists() for p in result)


def test_synthesize_and_slice_rejects_empty_segment_list(tmp_path: Path) -> None:
    client = _FakeElevenLabsClient()
    with pytest.raises(ValueError):
        synthesize_and_slice_segments(
            [],
            voice_id="voice-123",
            elevenlabs_client=client,
            whisperx_fn=_fake_whisperx,
            out_dir=tmp_path / "audio3",
            stem="walkthrough",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_script_audio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.script_audio'`

- [ ] **Step 3: Write `app/services/script_audio.py`**

```python
"""Continuous-synthesis-then-slice audio production for script segments.

Rationale (spec: sentence-photo linking design, 2026-08-12, §4): synthesizing
each segment as its own isolated ElevenLabs call makes the narration sound
like a list of disconnected sentences rather than one continuous tour, because
each call defaults to sentence-level intonation. Instead, ALL segment text is
sent to ElevenLabs as one concatenated take, preserving natural prosody
across the whole passage. WhisperX then force-aligns that single audio file's
words against the already-known segment texts (not free-form inference --
the exact texts and order are known going in) to find per-segment boundaries,
and the file is sliced at those boundaries into one clip per segment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from ..clients.audio_slicing import AudioBoundary, slice_audio_file
from ..clients.local_tools import WordTimestamp


class _SynthesizesSpeech(Protocol):
    def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
        ...


def _find_segment_boundaries(
    segments_text: list[str], words: list[WordTimestamp]
) -> list[AudioBoundary]:
    """Walk the word-timing stream in lockstep with each segment's own word
    count to find that segment's [start_sec, end_sec) window.

    This is forced alignment, not inference: the segment texts and their
    word counts are already known, so this only needs to consume that many
    words off the front of the timing stream for each segment in turn.
    """
    boundaries: list[AudioBoundary] = []
    cursor = 0
    for text in segments_text:
        word_count = len(text.split())
        segment_words = words[cursor : cursor + word_count]
        if not segment_words:
            # WhisperX produced fewer aligned words than expected (e.g. it
            # dropped a word it couldn't align) -- fall back to the last
            # known timestamp so slicing doesn't crash on a short transcript.
            start = boundaries[-1].end_sec if boundaries else 0.0
            end = start
        else:
            start = segment_words[0].start_sec
            end = segment_words[-1].end_sec
        boundaries.append(AudioBoundary(start_sec=start, end_sec=end))
        cursor += word_count
    return boundaries


def synthesize_and_slice_segments(
    segments_text: list[str],
    *,
    voice_id: str,
    elevenlabs_client: _SynthesizesSpeech,
    whisperx_fn: Callable[..., list[WordTimestamp]],
    out_dir: Path,
    stem: str,
) -> list[Path]:
    """Synthesize `segments_text` as one continuous take, then slice it into
    one audio file per segment, in order. Returns one path per input segment.
    """
    if not segments_text:
        raise ValueError("segments_text must be non-empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    continuous_text = " ".join(segments_text)

    continuous_audio_path = out_dir / f"{stem}_continuous.mp3"
    elevenlabs_client.synthesize(voice_id=voice_id, text=continuous_text, output_path=continuous_audio_path)

    words = whisperx_fn(continuous_audio_path, work_dir=out_dir)
    boundaries = _find_segment_boundaries(segments_text, words)

    return slice_audio_file(continuous_audio_path, boundaries, out_dir=out_dir, stem=stem)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_script_audio.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/script_audio.py tests/test_script_audio.py
git commit -m "feat: add continuous-synthesis-then-slice segment audio service"
```

---

### Task 7: Wire segment audio into the pipeline, avatar-intro exception, voice consent

**Files:**
- Modify: `app/pipeline/steps/script_and_voice.py`
- Test: `tests/test_pipeline_end_to_end.py` (existing file — read before editing)

This task changes `ScriptAndVoiceStep` to use `ScriptSegment` rows (when
present for a job) instead of the legacy `walkthrough`/`avatar_opening`
variant keys, applying the avatar-intro exception from §4: when
`use_avatar` is true, the intro segment is excluded from the continuous
take (HeyGen synthesizes it separately, unchanged existing behavior); when
`use_avatar` is false, the intro segment is included like every other
segment and voice consent is required exactly as it already is for
non-avatar jobs today.

- [ ] **Step 1: Write the failing test**

`JobContext` (confirmed in `app/pipeline/contract.py:38-73`) takes
`job_id, work_dir, feature_level, use_avatar, job_snapshot` positionally or
by keyword, and has an `artifacts: dict[str, Any]` field set directly (there
is no `set_artifact` method — only the read-side `ctx.artifact(step, key)`
helper exists). `StepResult` (same file, lines 31-35) has fields `status`
and `artifacts` (not `data`). Add to `tests/test_pipeline_end_to_end.py`,
reusing that file's existing `db_session`/`brochure_pdf`/`sample_photo`
fixtures (from `tests/conftest.py`, already confirmed in this session) and
its `consent.set_elevenlabs_voice` pattern from `_make_job`:

```python
def test_script_and_voice_uses_segments_when_present(db_session, tmp_path) -> None:
    """When a job has ScriptSegment rows, ScriptAndVoiceStep should synthesize
    them as one continuous take (excluding an avatar-on intro) rather than
    using the legacy walkthrough/avatar_opening script keys."""
    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext
    from app.pipeline.steps.script_and_voice import ScriptAndVoiceStep

    agent = AgentProfile(agency_name="Thornes", staff_name="Luke")
    consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST", use_avatar=False)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.add(ScriptSegment(job_id=job.id, order_index=0, text="Hi there.", is_intro=True))
    db_session.add(ScriptSegment(job_id=job.id, order_index=1, text="The kitchen is bright."))
    db_session.commit()

    snapshot = build_job_snapshot(db_session, job)
    ctx = JobContext(
        job_id=job.id,
        work_dir=tmp_path / "work",
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )

    step = ScriptAndVoiceStep()
    result = step.run(ctx)

    assert result.status is StepStatus.DONE
    assert "segment_audio_paths" in result.artifacts
    assert len(result.artifacts["segment_audio_paths"]) == 2
```

This reuses `consent`, `build_job_snapshot`, `StepStatus` — all already
imported at the top of `tests/test_pipeline_end_to_end.py` (confirmed lines
20-24 of that file), so no new imports are needed in the test file itself.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_end_to_end.py::test_script_and_voice_uses_segments_when_present -v`
Expected: FAIL — `ScriptAndVoiceStep.run` does not yet look for `ScriptSegment` rows or produce `segment_audio_paths`.

- [ ] **Step 4: Modify `ScriptAndVoiceStep.run`**

In `app/pipeline/steps/script_and_voice.py`, add the segment-aware branch.
Read the full current file (already read earlier in this session — lines
1-118) before editing; the change adds new logic without removing the
existing legacy-variant path, since older jobs without `ScriptSegment` rows
must keep working exactly as today:

```python
from ...clients.dispatch import get_active_tts_client
from ...models import FeatureLevel
from ...services.compliance import assert_price_free
from ...services.script_prompt import ScriptJobContext, ScriptVariant, build_prompt
from ...services.script_segments import list_segments
from ...services.script_audio import synthesize_and_slice_segments
from ..contract import JobContext, PipelineStep, StepResult, StepStatus
```

Add a new method to `ScriptAndVoiceStep`, and call it from `run` before the
legacy variant loop when segments exist for this job:

```python
    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        agent = job["agent"]

        segments = self._load_segments(ctx.job_id)
        if segments:
            return self._run_segmented(ctx, job, agent, segments)

        # --- legacy path (unchanged) ---
        sentences = ctx.artifact("ingest_brochure", "sentences")
        # ... existing body from here down, unchanged ...
```

Add the two new methods (segment loading needs a DB session; use the same
`credential_lookup`-style pattern already used elsewhere in `clients/` for
opening a short-lived session when the caller doesn't supply one):

```python
    def _load_segments(self, job_id: int) -> list:
        from sqlmodel import Session
        from ...db import engine

        with Session(engine) as session:
            return list_segments(session, job_id)

    def _run_segmented(self, ctx: JobContext, job: dict, agent: dict, segments: list) -> StepResult:
        use_avatar = ctx.use_avatar
        intro_segment = next((s for s in segments if s.is_intro), None)
        other_segments = [s for s in segments if not s.is_intro]

        # §4 avatar-intro exception: when avatar is on, the intro's audio
        # comes from HeyGen inside AvatarIntroStep, not from ElevenLabs here.
        # When avatar is off, the intro is voiced identically to every other
        # segment and included in the continuous take below.
        segments_to_voice = segments if not use_avatar else other_segments

        for s in segments_to_voice:
            assert_price_free(s.text, context=f"segment {s.id}")

        if use_avatar:
            voice_id = None
        else:
            from ...services.consent import require_voice_for_narration
            from types import SimpleNamespace

            voice_id = require_voice_for_narration(
                SimpleNamespace(
                    id=agent.get("id"),
                    elevenlabs_voice_id=agent.get("elevenlabs_voice_id"),
                    voice_consent_confirmed=agent.get("voice_consent_confirmed", False),
                )
            )

        segment_audio_paths: dict[int, str] = {}
        if segments_to_voice and voice_id:
            client = get_active_tts_client()
            out_dir = ctx.work_dir / "audio" / "segments"
            texts = [s.text for s in segments_to_voice]
            sliced_paths = synthesize_and_slice_segments(
                texts,
                voice_id=voice_id,
                elevenlabs_client=client,
                whisperx_fn=self._whisperx_fn(),
                out_dir=out_dir,
                stem="segment",
            )
            for s, path in zip(segments_to_voice, sliced_paths):
                segment_audio_paths[s.id] = str(path)

        return StepResult(
            StepStatus.DONE,
            {
                "segment_audio_paths": segment_audio_paths,
                "intro_segment_id": intro_segment.id if intro_segment else None,
                "intro_via_avatar": bool(use_avatar and intro_segment),
            },
        )

    @staticmethod
    def _whisperx_fn():
        from ...clients.local_tools import whisperx_word_timestamps

        return whisperx_word_timestamps
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_pipeline_end_to_end.py::test_script_and_voice_uses_segments_when_present -v`
Expected: PASS

- [ ] **Step 6: Run full regression, including existing legacy-path tests**

Run: `pytest tests/ -q`
Expected: All pass, including every pre-existing `test_pipeline_end_to_end.py` test that exercises the legacy (no-`ScriptSegment`) path — confirming the new branch is additive and doesn't change behavior for jobs without segments.

- [ ] **Step 7: Commit**

```bash
git add app/pipeline/steps/script_and_voice.py tests/test_pipeline_end_to_end.py
git commit -m "feat: wire segmented audio synthesis into ScriptAndVoiceStep"
```

---

### Task 8: Per-segment `RenderProps`/`PropertyVideo.tsx` contract

**Files:**
- Modify: `app/services/render_contract.py`
- Modify: `remotion/src/props.ts`
- Modify: `remotion/src/PropertyVideo.tsx`
- Test: `tests/test_render_contract.py` (new file, or extend existing if one is found)

This is the structural core of §5: `RenderProps` moves from one shared
`voiceover_path`/`captions`/fixed-duration `clips` to a `segments` list,
each self-contained. The existing `clips`/`voiceover_path`/`captions` fields
stay on `RenderProps` (renamed usage, not removed) so any code exercising
the legacy path (jobs without `ScriptSegment` rows) keeps working — this
task adds a new, parallel `build_segmented_render_props` function rather
than breaking `build_render_props`'s existing signature.

- [ ] **Step 1: Check for an existing render_contract test file**

Run: `pytest tests/ -k render_contract --collect-only -q`
Expected: lists any existing tests (there may be none dedicated to this
module yet, exercised only indirectly via `test_assembly.py` — read that
file if it exists before writing the new test file, to reuse fixture
patterns).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_render_contract.py
from __future__ import annotations

from app.models import AgentProfile, Photo, PropertyJob
from app.services.render_contract import Segment, build_segmented_render_props


def test_build_segmented_render_props_one_segment_per_input() -> None:
    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")
    photo = Photo(id=1, job_id=1, source_path="/photos/kitchen.jpg", processed_path="/photos/kitchen_p.jpg")

    segments_input = [
        Segment(
            text="Hi there.",
            visual_path="/avatar/intro.mp4",
            audio_path=None,  # avatar clip carries its own audio
            duration_sec=3.2,
            captions=(),
            is_avatar=True,
            disclosure_badge=None,
        ),
        Segment(
            text="The kitchen is bright.",
            visual_path=photo.clip_path or photo.processed_path or photo.source_path,
            audio_path="/audio/segment_1.mp3",
            duration_sec=2.8,
            captions=(),
            is_avatar=False,
            disclosure_badge=None,
        ),
    ]

    props = build_segmented_render_props(
        composition="Master16x9",
        job=job,
        agent=agent,
        segments=segments_input,
    )

    assert len(props.segments) == 2
    assert props.segments[0].is_avatar is True
    assert props.segments[1].duration_sec == 2.8
    assert props.composition == "Master16x9"


def test_build_segmented_render_props_rejects_price_in_captions() -> None:
    import pytest
    from app.models import AgentProfile, PropertyJob
    from app.services.render_contract import CaptionCue, Segment, build_segmented_render_props

    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    segments_input = [
        Segment(
            text="Yours for £400,000.",
            visual_path="/photos/x.jpg",
            audio_path="/audio/x.mp3",
            duration_sec=2.0,
            captions=(CaptionCue(text="£400,000", start_sec=0.0, end_sec=1.0),),
            is_avatar=False,
            disclosure_badge=None,
        )
    ]

    with pytest.raises(Exception):  # ComplianceError or whatever assert_price_free raises
        build_segmented_render_props(composition="Master16x9", job=job, agent=agent, segments=segments_input)


def test_build_segmented_render_props_unknown_composition_raises() -> None:
    import pytest
    from app.models import AgentProfile, PropertyJob
    from app.services.render_contract import build_segmented_render_props

    job = PropertyJob(id=1, address="1 Test St", postcode="TE1 1ST")
    agent = AgentProfile(id=1, agency_name="Test Agency")

    with pytest.raises(ValueError):
        build_segmented_render_props(composition="NotReal", job=job, agent=agent, segments=[])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_render_contract.py -v`
Expected: FAIL with `ImportError: cannot import name 'Segment' from 'app.services.render_contract'`

- [ ] **Step 4: Add `Segment` and `build_segmented_render_props` to `render_contract.py`**

Add to `app/services/render_contract.py`, after the existing `Clip`
dataclass (leave `Clip`, `RenderProps`, `build_render_props`, `ASPECTS`
completely unchanged for the legacy path):

```python
@dataclass(frozen=True)
class Segment:
    """One self-contained (visual, audio, captions, duration) unit in a
    segmented timeline. Unlike the legacy `Clip`, a `Segment` carries its own
    audio and duration rather than sharing one global voiceover track --
    this is what structurally guarantees a photo is on screen for exactly as
    long as its matching sentence is spoken (spec: sentence-photo linking
    design, 2026-08-12, §5).
    """

    text: str
    visual_path: str          # a photo's clip_path, or an avatar clip's path when is_avatar
    audio_path: str | None    # None only when is_avatar (avatar clip carries its own audio)
    duration_sec: float
    captions: tuple[CaptionCue, ...]
    is_avatar: bool
    disclosure_badge: str | None


@dataclass(frozen=True)
class SegmentedRenderProps:
    """The segmented-timeline counterpart to `RenderProps`, used for jobs
    with `ScriptSegment` rows. Structurally distinct from `RenderProps`
    (rather than an optional field bolted onto it) so a caller cannot
    accidentally mix a shared `voiceover_path` with per-segment audio."""

    composition: str
    width: int
    height: int
    fps: int
    branding: Branding
    segments: tuple[Segment, ...]
    music_path: str | None = None
    music_duck_db: float = -14.0
    lower_third: str | None = None

    def to_props(self) -> dict[str, Any]:
        return asdict(self)


def build_segmented_render_props(
    *,
    composition: str,
    job: PropertyJob,
    agent: AgentProfile,
    segments: list[Segment],
    music_path: str | None = None,
    lower_third: str | None = None,
    fps: int = 30,
) -> SegmentedRenderProps:
    """Assemble segmented render props. Mirrors `build_render_props`'s
    compliance checkpoint: every segment's text and every caption cue is
    re-checked for price here, the last point before pixels/audio are
    produced (§1.2)."""
    if composition not in ASPECTS:
        raise ValueError(f"Unknown composition {composition!r}; expected one of {sorted(ASPECTS)}")

    for segment in segments:
        assert_price_free(segment.text, context="a segment's narration text")
        for cue in segment.captions:
            assert_price_free(cue.text, context="a segment caption cue")
    if lower_third:
        assert_price_free(lower_third, context="the lower-third")

    width, height = ASPECTS[composition]
    return SegmentedRenderProps(
        composition=composition,
        width=width,
        height=height,
        fps=fps,
        branding=Branding(
            agency_name=agent.agency_name,
            primary_color=agent.primary_color,
            secondary_color=agent.secondary_color,
            logo_path=agent.logo_path,
            staff_name=agent.staff_name,
        ),
        segments=tuple(segments),
        music_path=music_path,
        lower_third=lower_third,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_render_contract.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Mirror the new shape in `remotion/src/props.ts`**

Add to `remotion/src/props.ts`, alongside the existing types (leave
`RenderProps`, `Clip`, `defaultRenderProps` unchanged):

```typescript
export type Segment = {
  text: string;
  visual_path: string;
  audio_path: string | null;
  duration_sec: number;
  captions: CaptionCue[];
  is_avatar: boolean;
  disclosure_badge: string | null;
};

export type SegmentedRenderProps = {
  composition: string;
  width: number;
  height: number;
  fps: number;
  branding: Branding;
  segments: Segment[];
  music_path: string | null;
  music_duck_db: number;
  lower_third: string | null;
};

export const defaultSegmentedRenderProps: SegmentedRenderProps = {
  composition: "Master16x9",
  width: 3840,
  height: 2160,
  fps: 30,
  branding: {
    agency_name: "",
    primary_color: "#111827",
    secondary_color: "#6b7280",
    logo_path: null,
    staff_name: null,
  },
  segments: [],
  music_path: null,
  music_duck_db: -14.0,
  lower_third: null,
};
```

- [ ] **Step 7: Add a `SegmentedPropertyVideo` component**

Create `remotion/src/SegmentedPropertyVideo.tsx` (new file — the existing
`PropertyVideo.tsx` stays untouched for the legacy `clips`/`voiceover_path`
path, avoiding any regression risk to jobs without segments):

```tsx
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  Video,
  useVideoConfig,
  interpolate,
  useCurrentFrame,
} from "remotion";
import type { SegmentedRenderProps, Segment } from "./props";

/**
 * Renders one self-contained segment: its own visual, its own audio, its own
 * captions, for exactly its own duration. Unlike the legacy ClipShot (which
 * relies on a duration handed down independently of any audio), a Segment's
 * duration always matches its own audio_path's actual length, produced
 * upstream by `services.script_audio.synthesize_and_slice_segments` --
 * this component has no way to desync visual and audio because they always
 * travel together as one unit.
 */
const SegmentShot: React.FC<{ segment: Segment; durationInFrames: number; fps: number }> = ({
  segment,
  durationInFrames,
  fps,
}) => {
  const frame = useCurrentFrame();
  const fadeFrames = Math.min(10, Math.floor(durationInFrames / 4));
  const opacity = interpolate(
    frame,
    [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const t = frame / fps;
  const activeCaption = segment.captions.find((c) => t >= c.start_sec && t < c.end_sec);

  return (
    <AbsoluteFill style={{ opacity }}>
      <Video src={segment.visual_path} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      {segment.disclosure_badge && (
        <div
          style={{
            position: "absolute",
            bottom: 24,
            right: 24,
            background: "rgba(0,0,0,0.72)",
            color: "#fff",
            padding: "8px 14px",
            borderRadius: 6,
            fontSize: 20,
            fontFamily: "sans-serif",
            fontWeight: 600,
          }}
        >
          {segment.disclosure_badge}
        </div>
      )}
      {activeCaption && (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 120,
            textAlign: "center",
            fontFamily: "sans-serif",
            fontSize: 40,
            fontWeight: 700,
            color: "#fff",
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }}
        >
          {activeCaption.text}
        </div>
      )}
      {/* is_avatar segments carry their own audio track inside visual_path's
          video file; only non-avatar segments need a separate <Audio> tag. */}
      {!segment.is_avatar && segment.audio_path && <Audio src={segment.audio_path} />}
    </AbsoluteFill>
  );
};

const LowerThird: React.FC<{ text: string; branding: SegmentedRenderProps["branding"] }> = ({
  text,
  branding,
}) => (
  <div
    style={{
      position: "absolute",
      left: 32,
      bottom: 32,
      padding: "10px 20px",
      background: branding.primary_color,
      color: "#fff",
      fontFamily: "sans-serif",
      fontSize: 28,
      fontWeight: 700,
      borderRadius: 4,
    }}
  >
    {text}
  </div>
);

export const SegmentedPropertyVideo: React.FC<SegmentedRenderProps> = (props) => {
  const { fps } = useVideoConfig();
  let cursorFrame = 0;

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {props.segments.map((segment, i) => {
        const durationInFrames = Math.round(segment.duration_sec * fps);
        const from = cursorFrame;
        cursorFrame += durationInFrames;
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <SegmentShot segment={segment} durationInFrames={durationInFrames} fps={fps} />
          </Sequence>
        );
      })}

      {props.lower_third && <LowerThird text={props.lower_third} branding={props.branding} />}

      {props.music_path && (
        <Audio src={props.music_path} volume={Math.pow(10, props.music_duck_db / 20)} />
      )}
    </AbsoluteFill>
  );
};
```

- [ ] **Step 8: Register the new composition in `remotion/src/Root.tsx`**

Read `remotion/src/Root.tsx` first to see how `PropertyVideo` is currently
registered as a composition, then add a parallel registration for
`SegmentedPropertyVideo` following the exact same pattern (same
`calculateMetadata`/`durationInFrames`/`fps` wiring, new composition id e.g.
`"SegmentedMaster16x9"`) — do not remove or modify the existing
`PropertyVideo` registration.

- [ ] **Step 9: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass. (The `.tsx`/`.ts` changes have no Python test coverage
in this plan — TypeScript compilation is checked in Task 9's manual
verification, since this plan doesn't add a JS test runner and none exists
in the repo today.)

- [ ] **Step 10: Commit**

```bash
git add app/services/render_contract.py remotion/src/props.ts remotion/src/SegmentedPropertyVideo.tsx remotion/src/Root.tsx tests/test_render_contract.py
git commit -m "feat: add segmented RenderProps contract and SegmentedPropertyVideo composition"
```

---

### Task 9: Wire `RemotionAssemblyStep` to the segmented path + manual verification

**Files:**
- Modify: `app/pipeline/steps/assembly.py`
- Test: `tests/test_assembly.py` (existing file — read before editing)

Final task: make `RemotionAssemblyStep` build `SegmentedRenderProps` (via
`build_segmented_render_props`) when a job has `ScriptSegment` rows, falling
back to the existing `build_render_props` legacy path otherwise — mirroring
the same branch-on-segment-presence pattern used in Task 7.

- [ ] **Step 1: Write the failing test**

`tests/test_assembly.py` (confirmed in full this session) tests
`render_via_remotion_cli` directly via `subprocess.run` monkeypatching — it
has no existing `JobContext`-driving test to extend, so this test builds its
own context from scratch, following the exact pattern
`tests/test_pipeline_end_to_end.py`'s `_no_network` fixture and `_make_job`
helper already establish (isolated in-memory engine, `JobContext.artifacts`
set directly since `JobContext` has no `set_artifact` method — confirmed in
`app/pipeline/contract.py:38-58`, only a read-side `ctx.artifact(step, key)`
helper exists). `StepResult`'s data field is `artifacts`, not `data`
(confirmed same file, lines 31-35). Add to `tests/test_assembly.py`:

```python
# add to tests/test_assembly.py

def test_remotion_assembly_uses_segmented_path_when_segments_exist(tmp_path, monkeypatch) -> None:
    """When motion_pass/script_and_voice artifacts include segment audio
    (from the new ScriptAndVoiceStep segmented branch), RemotionAssemblyStep
    should build SegmentedRenderProps instead of the legacy RenderProps."""
    from sqlmodel import Session, SQLModel, create_engine

    import app.db as db_mod
    from app.models import AgentProfile, Photo, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext, StepStatus
    from app.pipeline.steps.assembly import RemotionAssemblyStep

    monkeypatch.delenv("LOCAL_TOOLS_AVAILABLE", raising=False)

    isolated_engine = create_engine(
        f"sqlite:///{tmp_path / 'isolated.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(isolated_engine)
    monkeypatch.setattr(db_mod, "engine", isolated_engine)

    with Session(isolated_engine) as session:
        agent = AgentProfile(agency_name="Test Agency")
        session.add(agent)
        session.commit()
        session.refresh(agent)

        job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST")
        session.add(job)
        session.commit()
        session.refresh(job)

        photo = Photo(job_id=job.id, source_path="/p/kitchen.jpg", processed_path="/p/kitchen_p.jpg", order_index=0)
        session.add(photo)
        session.commit()
        session.refresh(photo)

        segment = ScriptSegment(job_id=job.id, order_index=0, text="The kitchen is bright.", photo_id=photo.id)
        session.add(segment)
        session.commit()
        session.refresh(segment)

        job_id, photo_id, segment_id = job.id, photo.id, segment.id

    snapshot = {
        "id": job_id,
        "address": "1 Test St",
        "postcode": "TE1 1ST",
        "feature_level": "standard",
        "agent": {"id": agent.id, "agency_name": "Test Agency", "primary_color": "#111827", "secondary_color": "#6b7280"},
        "photos": [
            {"id": photo_id, "job_id": job_id, "source_path": "/p/kitchen.jpg", "processed_path": "/p/kitchen_p.jpg", "order_index": 0},
        ],
    }
    ctx = JobContext(job_id=job_id, work_dir=tmp_path, feature_level="standard", use_avatar=False, job_snapshot=snapshot)
    ctx.artifacts["motion_pass"] = {"clip_paths": {str(photo_id): "/p/kitchen_clip.mp4"}}
    ctx.artifacts["script_and_voice"] = {
        "segment_audio_paths": {segment_id: str(tmp_path / "segment_0.mp3")},
        "intro_segment_id": None,
        "intro_via_avatar": False,
    }

    step = RemotionAssemblyStep()
    result = step.run(ctx)

    assert result.status is StepStatus.DONE
    assert "render_outputs" in result.artifacts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py::test_remotion_assembly_uses_segmented_path_when_segments_exist -v`
Expected: FAIL — `RemotionAssemblyStep.run` doesn't yet branch on segment presence.

- [ ] **Step 3: Modify `RemotionAssemblyStep.run`**

In `app/pipeline/steps/assembly.py`, add the segmented branch. Read the full
current file (already read earlier — lines 1-148) before editing:

```python
from ...services.render_contract import CaptionCue, Segment, build_segmented_render_props


class RemotionAssemblyStep(PipelineStep):
    name = "remotion_assembly"
    levels = frozenset(FeatureLevel)
    requires = ("motion_pass", "script_and_voice")

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        agent = _agent_from_snapshot(job["agent"])

        segment_audio_paths = ctx.artifacts.get("script_and_voice", {}).get("segment_audio_paths")
        if segment_audio_paths is not None:
            return self._run_segmented(ctx, job, agent)

        # --- legacy path (unchanged) ---
        clip_paths = ctx.artifact("motion_pass", "clip_paths")
        audio_paths = ctx.artifact("script_and_voice", "audio_paths")
        # ... existing body from here down, unchanged ...

    def _run_segmented(self, ctx: JobContext, job: dict, agent) -> StepResult:
        from sqlmodel import Session

        from ...db import engine
        from ...services.script_segments import list_segments

        clip_paths = ctx.artifact("motion_pass", "clip_paths")
        segment_audio_paths = ctx.artifact("script_and_voice", "segment_audio_paths")
        intro_via_avatar = ctx.artifact("script_and_voice", "intro_via_avatar")

        with Session(engine) as session:
            segments = list_segments(session, ctx.job_id)

        photos_by_id = {p["id"]: p for p in job["photos"]}

        render_segments: list[Segment] = []
        for seg in segments:
            if seg.is_intro and intro_via_avatar:
                avatar_clip_path = ctx.artifacts.get("avatar_intro", {}).get("avatar_clip_path")
                render_segments.append(
                    Segment(
                        text=seg.text,
                        visual_path=avatar_clip_path or "",
                        audio_path=None,
                        duration_sec=seg.duration_sec or 4.0,
                        captions=(),
                        is_avatar=True,
                        disclosure_badge=None,
                    )
                )
                continue

            photo = photos_by_id.get(seg.photo_id)
            visual_path = clip_paths.get(str(seg.photo_id), "") if photo else ""
            audio_path = segment_audio_paths.get(seg.id)
            render_segments.append(
                Segment(
                    text=seg.text,
                    visual_path=visual_path,
                    audio_path=audio_path,
                    duration_sec=seg.duration_sec or 4.0,
                    captions=(),
                    is_avatar=False,
                    disclosure_badge=(
                        __import__("app.services.compliance", fromlist=["AI_DISCLOSURE_BADGE_TEXT"]).AI_DISCLOSURE_BADGE_TEXT
                        if photo and photo.get("sky_replaced")
                        else None
                    ),
                )
            )

        out_dir = ctx.work_dir / "render"
        props = build_segmented_render_props(
            composition="Master16x9",
            job=_job_stub(job),
            agent=agent,
            segments=render_segments,
            lower_third=agent.agency_name,
        )
        output_path = self._render(props, out_dir / "master_16x9.mp4")

        return StepResult(StepStatus.DONE, {"render_outputs": {"master_16x9": output_path}})
```

(The `__import__(...)` for `AI_DISCLOSURE_BADGE_TEXT` avoids a circular
import at module load time since `render_contract.py` already imports from
`compliance.py`; a cleaner fix is a top-of-file `from ...services.compliance
import AI_DISCLOSURE_BADGE_TEXT` import instead — use that top-level import
form when implementing, the inline `__import__` above is only to keep this
plan step's diff self-contained without re-showing the full existing import
block.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py::test_remotion_assembly_uses_segmented_path_when_segments_exist -v`
Expected: PASS

- [ ] **Step 5: Run full regression**

Run: `pytest tests/ -q`
Expected: All pass, including every pre-existing `test_assembly.py` test
exercising the legacy (no-segments) path.

- [ ] **Step 6: Manual verification — brochure/photo upload + segment generation round trip**

Run: `python -m uvicorn app.main:app --port 8815` (from the project root), then:

```bash
curl -s -X POST http://127.0.0.1:8815/api/jobs -H "Content-Type: application/json" -d '{"address":"5 Wardington Crescent","postcode":"SW1A 2AA"}'
```

Note the returned `id`, then:

```bash
curl -s -X POST http://127.0.0.1:8815/api/jobs/<id>/photos -F "files=@some_photo.jpg"
curl -s -X POST http://127.0.0.1:8815/api/jobs/<id>/segments -H "Content-Type: application/json" -d '{"text":"A lovely bright kitchen."}'
curl -s http://127.0.0.1:8815/api/jobs/<id>/segments
```

Expected: the photo upload returns a `Photo` row with an `id`; the segment
create returns a segment with `order_index: 0`; the list call shows it
persisted. Stop the server afterward with Ctrl+C in the terminal running
`uvicorn`, or on Windows: `Get-Process python | Where-Object { $_.CommandLine -like '*uvicorn*' } | Stop-Process`.

- [ ] **Step 7: Commit**

```bash
git add app/pipeline/steps/assembly.py tests/test_assembly.py
git commit -m "feat: wire RemotionAssemblyStep to segmented render props path"
```

---

## Post-plan notes

- **Frontend plan is separate.** This plan produces a fully working,
  API-testable backend (upload, generate, edit, assemble) with no UI. The
  arrange screen (drag-and-drop, avatar toggle, generate button, runtime
  estimate display) is Task-planned separately once this backend plan is
  merged, per the earlier scope-split decision.
- **LLM call wiring not included.** Task 3 builds the new prompt variant;
  Task 4 builds the service that turns an LLM's JSON response into rows.
  Neither task wires an actual LLM HTTP call to produce that JSON — per the
  existing codebase's own `get_script_llm_client()` in
  `script_and_voice.py:41-49`, real LLM calling is explicitly marked as not
  yet implemented for ANY script variant in this codebase (raises
  `NotImplementedError` once a key is configured). This plan does not
  change that — it only ensures that once real LLM calling is wired up
  (a pre-existing, separate gap, not part of this feature), the
  `SEGMENTED_WALKTHROUGH` prompt and `create_segments_from_llm_json` are
  ready to consume its output. Until then, segment creation in practice
  happens via the CRUD endpoints (agent adding sentences manually) or a
  test/fixture path, not a live LLM call — this matches how the rest of the
  pipeline already handles the same pre-existing gap.
- **`ffmpeg`/`pydub` real-world dependency.** Task 5's slicing uses
  `pydub`, which needs `ffmpeg` on PATH for mp3 export at real runtime
  (already a stated project dependency pattern — `ffmpeg_mux` in
  `local_tools.py` has the same requirement, gated behind
  `LOCAL_TOOLS_AVAILABLE=1`). No new installation-story risk beyond what
  the project already carries for its other local tools.
