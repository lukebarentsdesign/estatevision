"""FastAPI application entry point (spec §2, §6).

Routes are thin: they load ORM rows, delegate to services/pipeline modules, and
serialize results. No business logic -- and critically, no prompt assembly or
badge logic -- lives in this file. See §9 for why that split matters.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .db import get_session, init_db
from .models import AgentProfile, JobStatus, PropertyJob
from .pipeline.contract import JobContext, assert_transition
from .pipeline.registry import build_job_snapshot, build_runner
from .services import uk_location
from .services.integration_registry import list_integrations
from .services.integration_settings import IntegrationSettings
from .services.integration_test_connection import test_connection


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
def admin_integrations_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "admin_integrations.html")


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
def list_jobs(session: Session = Depends(get_session)) -> list[PropertyJob]:
    return session.exec(select(PropertyJob)).all()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> PropertyJob:
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.post("/api/jobs", status_code=201)
def create_job(job: PropertyJob, session: Session = Depends(get_session)) -> PropertyJob:
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@app.post("/api/jobs/{job_id}/location")
def refresh_location_data(
    job_id: int, session: Session = Depends(get_session)
) -> dict:
    """Populate `job.location_data_json` from the §5 aggregator."""
    job = session.get(PropertyJob, job_id)
    if job is None:
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


@app.post("/api/jobs/{job_id}/run")
def run_pipeline(job_id: int, session: Session = Depends(get_session)) -> dict:
    """Run every applicable pipeline step for this job's feature level."""
    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

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
    job.script_json = ctx.artifact("script_and_voice", "scripts")
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
def list_integration_statuses(session: Session = Depends(get_session)) -> list[dict]:
    """Every known system (§7 reference table) with its configuration status.

    Never returns raw secret values -- only masked previews (see `secrets_store.mask`).
    """
    settings = IntegrationSettings(session)
    return [_serialize_status(s, session) for s in settings.all_statuses()]


@app.get("/api/integrations/{slug}")
def get_integration_status(slug: str, session: Session = Depends(get_session)) -> dict:
    settings = IntegrationSettings(session)
    try:
        return _serialize_status(settings.status_for(slug), session)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/integrations/{slug}/fields/{field_key}")
def set_integration_field(
    slug: str, field_key: str, body: SetFieldRequest, session: Session = Depends(get_session)
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
    slug: str, field_key: str, session: Session = Depends(get_session)
) -> dict:
    settings = IntegrationSettings(session)
    settings.clear_field(slug, field_key)
    return _serialize_status(settings.status_for(slug), session)


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


@app.get("/api/integrations/openai/base-url-presets")
def openai_base_url_presets() -> list[dict]:
    from .services.integration_registry import OPENAI_COMPATIBLE_PRESETS

    return list(OPENAI_COMPATIBLE_PRESETS)


@app.post("/api/integrations/{slug}/test")
def test_integration_connection(slug: str, session: Session = Depends(get_session)) -> dict:
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
    job_id: int, body: UpdateScriptRequest, session: Session = Depends(get_session)
) -> dict:
    job = session.get(PropertyJob, job_id)
    if job is None:
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
def download_export_pack(job_id: int, session: Session = Depends(get_session)) -> Response:
    from .services.export_pack import build_export_zip

    job = session.get(PropertyJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    agent = session.get(AgentProfile, job.agent_id) if job.agent_id else None

    zip_bytes = build_export_zip(
        job_id=job.id,
        address=job.address,
        postcode=job.postcode,
        price_guide=job.price_guide,
        garden_orientation=job.garden_orientation,
        agency_name=agent.agency_name if agent else "Property Studio Agency",
        primary_color=agent.primary_color if agent else "#1E293B",
        secondary_color=agent.secondary_color if agent else "#0F172A",
        logo_url=agent.logo_path if agent else "",
        staff_name=agent.staff_name if agent else "Property Agent",
        staff_headshot=agent.staff_headshot_path if agent else "",
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

