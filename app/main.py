"""FastAPI application entry point (spec §2, §6).

Routes are thin: they load ORM rows, delegate to services/pipeline modules, and
serialize results. No business logic -- and critically, no prompt assembly or
badge logic -- lives in this file. See §9 for why that split matters.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import get_session, init_db
from .models import AdminAccount, AgentProfile, JobStatus, Photo, PropertyJob, ScriptSegment
from .pipeline.contract import JobContext, assert_transition
from .pipeline.registry import build_job_snapshot, build_runner
from .services import uk_location
from .services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    encode_session_cookie,
    hash_password,
    require_admin,
    require_agency,
    verify_password,
)
from .services.compliance import assert_price_free
from .services.integration_registry import list_integrations
from .services.integration_settings import IntegrationSettings
from .services.integration_test_connection import test_connection
from .services.script_segments import list_segments


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Property Content Studio", lifespan=lifespan)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
def dashboard_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/admin/integrations")
def admin_integrations_page(admin: AdminAccount = Depends(require_admin)) -> FileResponse:
    return FileResponse(_STATIC_DIR / "admin_integrations.html")


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


@app.get("/api/agents")
def list_agents(session: Session = Depends(get_session)) -> list[AgentProfile]:
    return session.exec(select(AgentProfile)).all()


@app.post("/api/agents", status_code=201)
def create_agent(agent: AgentProfile, session: Session = Depends(get_session)) -> AgentProfile:
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


@app.get("/api/jobs")
def list_jobs(
    agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> list[PropertyJob]:
    return session.exec(select(PropertyJob).where(PropertyJob.agent_id == agency.id)).all()


@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: int, agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> PropertyJob:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    return job


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


_MAX_UPLOAD_MB = int(os.environ.get("PROPERTY_STUDIO_MAX_UPLOAD_MB", "25"))
_MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024


def _upload_dir(job_id: int) -> Path:
    base = Path(os.environ.get("PROPERTY_STUDIO_UPLOAD_DIR", "uploads"))
    job_dir = base / f"job_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def _read_capped(upload: UploadFile, *, context: str) -> bytes:
    """Read an upload's contents, rejecting anything over `_MAX_UPLOAD_BYTES`.

    `UploadFile.read()` buffers the whole body into memory with no size limit
    of its own, which makes an uncapped read a memory-exhaustion vector on a
    public upload endpoint. Reading one extra byte past the cap is enough to
    detect an oversized upload without buffering the entire file first.
    """
    contents = await upload.read(_MAX_UPLOAD_BYTES + 1)
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{context} exceeds the {_MAX_UPLOAD_MB}MB upload limit")
    return contents


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


class CreateSegmentRequest(BaseModel):
    text: str
    order_index: Optional[int] = None


class UpdateSegmentRequest(BaseModel):
    text: Optional[str] = None
    photo_id: Optional[int] = None
    order_index: Optional[int] = None


def _serialize_segment(segment: ScriptSegment) -> dict:
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
def list_job_segments(
    job_id: int, agency: AgentProfile = Depends(require_agency), session: Session = Depends(get_session)
) -> list[dict]:
    job = session.get(PropertyJob, job_id)
    if job is None or job.agent_id != agency.id:
        raise HTTPException(404, "job not found")
    return [_serialize_segment(s) for s in list_segments(session, job_id)]


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


class UpdateJobRequest(BaseModel):
    use_avatar: Optional[bool] = None


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
        # A segment with no assigned photo renders with an empty visual_path
        # (see RemotionAssemblyStep._run_segmented) -- refuse up front rather
        # than produce a malformed video. The avatar-intro segment is exempt:
        # its visual comes from the HeyGen clip, not a photo, when use_avatar
        # is set (checked here defensively even though avatar_intro's own
        # gating already covers the common case).
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
    except Exception as exc:  # consent errors, missing agent, etc.
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
        # Segmented jobs never populate the legacy "scripts" artifact (see
        # ScriptAndVoiceStep._run_segmented) -- fall back to the segments
        # themselves so job.script_json isn't silently left None for a
        # segmented job. Segment CRUD (app.services.script_segments) is the
        # source of truth for editing; this is a read-oriented summary.
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


# --- Admin panel: integrations & credentials --------------------------------
#
# Nothing here bypasses `IntegrationSettings`/`secrets_store` -- responses only
# ever include masked values, never raw secrets, so the admin UI cannot leak a
# stored key back out through this API.


class SetFieldRequest(BaseModel):
    value: str


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


@app.get("/api/integrations")
def list_integration_statuses(
    admin: AdminAccount = Depends(require_admin), session: Session = Depends(get_session)
) -> list[dict]:
    """Every known system (§7 reference table) with its configuration status.

    Never returns raw secret values -- only masked previews (see `secrets_store.mask`).
    """
    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]


@app.get("/api/integrations/{slug}")
def get_integration_status(
    slug: str, admin: AdminAccount = Depends(require_admin), session: Session = Depends(get_session)
) -> dict:
    settings = IntegrationSettings(session)
    try:
        return _serialize_status(settings.status_for(slug), session)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/integrations/{slug}/fields/{field_key}")
def set_integration_field(
    slug: str,
    field_key: str,
    body: SetFieldRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Store (encrypted) a credential field value. Takes effect immediately --
    no restart or .env edit needed, since client factories read live from here."""
    settings = IntegrationSettings(session)
    try:
        settings.set_field(slug, field_key, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _serialize_status(settings.status_for(slug), session)


@app.delete("/api/integrations/{slug}/fields/{field_key}")
def clear_integration_field(
    slug: str,
    field_key: str,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    settings = IntegrationSettings(session)
    settings.clear_field(slug, field_key)
    return _serialize_status(settings.status_for(slug), session)


class SetActiveVendorRequest(BaseModel):
    slug: str


@app.put("/api/categories/{category_key}/active-vendor")
def set_category_active_vendor(
    category_key: str,
    body: SetActiveVendorRequest,
    admin: AdminAccount = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict]:
    from .services.active_vendor import set_active_vendor

    try:
        set_active_vendor(session, category_key, body.slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]


@app.get("/api/integrations/openai/base-url-presets")
def openai_base_url_presets(admin: AdminAccount = Depends(require_admin)) -> list[dict]:
    from .services.integration_registry import OPENAI_COMPATIBLE_PRESETS

    return list(OPENAI_COMPATIBLE_PRESETS)


@app.post("/api/integrations/{slug}/test")
def test_integration_connection(
    slug: str, admin: AdminAccount = Depends(require_admin), session: Session = Depends(get_session)
) -> dict:
    try:
        result = test_connection(session, slug)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "slug": result.slug,
        "ok": result.ok,
        "mode": result.mode.value,
        "message": result.message,
    }


# --- Job Deliverables & Export Pack -----------------------------------------

class UpdateScriptRequest(BaseModel):
    walkthrough_script: Optional[str] = None
    social_shorts: Optional[list[str]] = None


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

