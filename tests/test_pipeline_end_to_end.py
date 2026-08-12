"""End-to-end pipeline tests, fully stubbed (spec §9 Phase B glue).

Runs a real job through the real orchestration and step code, using stub
clients only (no network, no local ML tools, no API keys). Confirms:

* a `standard`-level voice-only job completes start to finish;
* `cinematic` jobs get every extra artifact including the microsite;
* the consent gate actually blocks an unconsented job before any step runs;
* price never appears in any generated script even via the real pipeline path
  (not just the unit-level compliance tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.models import AgentProfile, FeatureLevel, JobStatus, Photo, PropertyJob
from app.pipeline.contract import JobContext, StepStatus
from app.pipeline.registry import build_job_snapshot, build_runner
from app.services import consent
from app.services.compliance import find_price_mentions

PRICE_SENTINEL = "£875,000"


def _make_job(
    session: Session,
    *,
    brochure_pdf: Path,
    photo: Path,
    feature_level: FeatureLevel,
    use_avatar: bool = False,
) -> tuple[PropertyJob, AgentProfile]:
    agent = AgentProfile(agency_name="Thornes", staff_name="Luke")
    if use_avatar:
        agent.heygen_avatar_id = "avatar_verified_123"
    else:
        consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    session.add(agent)
    session.commit()
    session.refresh(agent)

    job = PropertyJob(
        agent_id=agent.id,
        address="32 Tregonwell Road",
        postcode="BH2 5NT",
        price_guide=PRICE_SENTINEL,
        garden_orientation="South-West",
        latitude=50.7192,
        longitude=-1.8808,
        feature_level=feature_level,
        use_avatar=use_avatar,
        pdf_brochure_path=str(brochure_pdf),
        status=JobStatus.INGESTION,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    session.add(Photo(job_id=job.id, source_path=str(photo), is_hero=True, order_index=0))
    session.add(Photo(job_id=job.id, source_path=str(photo), is_hero=False, order_index=1))
    session.commit()
    session.refresh(job)
    return job, agent


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    """Belt and braces: fail loudly if a test accidentally hits the network.

    Also redirects the credential-lookup fallback path (used when a pipeline
    step's client factory is called with no session, e.g. `get_heygen_client()`
    inside `avatar_and_captions.py`) away from the real project DB and secret
    key file -- otherwise these tests silently create `property_studio.db` /
    `secret.key` in the repo root.
    """

    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a stubbed pipeline test")

    monkeypatch.delenv("LOCAL_TOOLS_AVAILABLE", raising=False)
    monkeypatch.delenv("HEYGEN_API_KEY", raising=False)

    from sqlmodel import SQLModel, create_engine

    import app.db as db_mod
    import app.services.secrets_store as secrets_mod

    isolated_engine = create_engine(
        f"sqlite:///{tmp_path / 'isolated.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(isolated_engine)
    monkeypatch.setattr(db_mod, "engine", isolated_engine)
    monkeypatch.setattr(secrets_mod, "_default_store", None)
    monkeypatch.setattr(secrets_mod, "DEFAULT_KEY_PATH", tmp_path / "isolated_secret.key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_3D_TILES_KEY", raising=False)


def test_standard_job_runs_to_completion(db_session, brochure_pdf, sample_photo, tmp_path):
    job, agent = _make_job(
        db_session, brochure_pdf=brochure_pdf, photo=sample_photo, feature_level=FeatureLevel.STANDARD
    )
    snapshot = build_job_snapshot(db_session, job)

    ctx = JobContext(
        job_id=job.id,
        work_dir=tmp_path / "work",
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )
    results = build_runner().run(ctx)

    assert results["ingest_brochure"].status is StepStatus.DONE
    assert results["restoration_pass"].status is StepStatus.DONE
    assert results["motion_pass"].status is StepStatus.DONE
    assert results["script_and_voice"].status is StepStatus.DONE
    assert results["remotion_assembly"].status is StepStatus.DONE

    # cinematic-only steps must not have run for a standard job
    assert results["sky_replacement"].status is StepStatus.SKIPPED
    assert results["microsite_builder"].status is StepStatus.SKIPPED

    render_outputs = ctx.artifact("remotion_assembly", "render_outputs")
    assert Path(render_outputs["master_16x9"]).exists()


def test_price_never_reaches_any_generated_script(db_session, brochure_pdf, sample_photo, tmp_path):
    job, agent = _make_job(
        db_session, brochure_pdf=brochure_pdf, photo=sample_photo, feature_level=FeatureLevel.STANDARD
    )
    snapshot = build_job_snapshot(db_session, job)
    assert snapshot["price_guide"] == PRICE_SENTINEL  # present in snapshot...

    ctx = JobContext(
        job_id=job.id,
        work_dir=tmp_path / "work",
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )
    build_runner().run(ctx)

    scripts = ctx.artifact("script_and_voice", "scripts")
    assert scripts, "expected at least one generated script"
    for variant, text in scripts.items():
        assert not find_price_mentions(text), f"{variant} leaked price: {text!r}"


def test_cinematic_job_produces_microsite_and_extra_reels(
    db_session, brochure_pdf, sample_photo, tmp_path
):
    job, agent = _make_job(
        db_session, brochure_pdf=brochure_pdf, photo=sample_photo, feature_level=FeatureLevel.CINEMATIC
    )
    snapshot = build_job_snapshot(db_session, job)

    ctx = JobContext(
        job_id=job.id,
        work_dir=tmp_path / "work",
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )
    results = build_runner().run(ctx)

    assert results["microsite_builder"].status is StepStatus.DONE
    micro_path = Path(ctx.artifact("microsite_builder", "microsite_path"))
    assert micro_path.exists()
    # The microsite is the one place price legitimately appears (§1.2 carve-out).
    assert PRICE_SENTINEL in micro_path.read_text(encoding="utf-8")

    render_outputs = ctx.artifact("remotion_assembly", "render_outputs")
    reel_keys = [k for k in render_outputs if k.startswith("reel_")]
    assert len(reel_keys) == 3, "cinematic jobs should render 3 reels per §4 plus"


def test_avatar_job_runs_avatar_intro_step(db_session, brochure_pdf, sample_photo, tmp_path):
    job, agent = _make_job(
        db_session,
        brochure_pdf=brochure_pdf,
        photo=sample_photo,
        feature_level=FeatureLevel.PLUS,
        use_avatar=True,
    )
    snapshot = build_job_snapshot(db_session, job)
    assert "elevenlabs_voice_id" not in snapshot["agent"]
    assert snapshot["agent"]["heygen_avatar_id"] == "avatar_verified_123"

    ctx = JobContext(
        job_id=job.id,
        work_dir=tmp_path / "work",
        feature_level=job.feature_level,
        use_avatar=job.use_avatar,
        job_snapshot=snapshot,
    )
    results = build_runner().run(ctx)

    assert results["avatar_intro"].status is StepStatus.DONE
    avatar_clip = Path(ctx.artifact("avatar_intro", "avatar_clip_path"))
    assert avatar_clip.exists()


def test_snapshot_construction_refuses_unconsented_voice_job(db_session, brochure_pdf, sample_photo):
    agent = AgentProfile(agency_name="Thornes")  # no consent, no voice, no avatar
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(
        agent_id=agent.id,
        address="32 Tregonwell Road",
        postcode="BH2 5NT",
        pdf_brochure_path=str(brochure_pdf),
        use_avatar=False,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with pytest.raises(consent.ConsentError):
        build_job_snapshot(db_session, job)


def test_snapshot_construction_refuses_unverified_avatar_job(db_session, brochure_pdf, sample_photo):
    agent = AgentProfile(agency_name="Thornes")  # no heygen_avatar_id
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(
        agent_id=agent.id,
        address="32 Tregonwell Road",
        postcode="BH2 5NT",
        pdf_brochure_path=str(brochure_pdf),
        use_avatar=True,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with pytest.raises(consent.ConsentError):
        build_job_snapshot(db_session, job)


def test_script_and_voice_uses_segments_when_present(db_session, monkeypatch, tmp_path) -> None:
    """When a job has ScriptSegment rows, ScriptAndVoiceStep should synthesize
    them as one continuous take (excluding an avatar-on intro) rather than
    using the legacy walkthrough/avatar_opening script keys."""
    import wave

    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext
    from app.pipeline.steps import script_and_voice as script_and_voice_mod
    from app.pipeline.steps.script_and_voice import ScriptAndVoiceStep

    def _write_silent_wav(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        return path

    class _FakeTtsClient:
        """Writes a real (silent) WAV instead of StubElevenLabsClient's text
        placeholder, since this path feeds pydub for slicing -- same pattern
        as tests/test_script_audio.py's _FakeElevenLabsClient."""

        def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
            return _write_silent_wav(output_path)

    monkeypatch.setattr(script_and_voice_mod, "get_active_tts_client", lambda **kw: _FakeTtsClient())

    # `_no_network` (autouse) points app.db.engine at its own isolated engine,
    # separate from db_session's in-memory engine. ScriptAndVoiceStep's
    # segmented path opens its own `Session(engine)` via `from ...db import
    # engine`, so it must see the SAME database db_session writes to here --
    # otherwise it would find zero ScriptSegment rows and silently fall
    # through to the legacy path. Point app.db.engine at db_session's engine.
    import app.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db_session.get_bind())

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


def _wire_segmented_test_doubles(monkeypatch, db_session) -> None:
    """Shared setup for segmented-path tests: fake TTS client that writes
    real (silent) WAV audio instead of StubElevenLabsClient's text
    placeholder (pydub can't decode that), and points app.db.engine at
    db_session's engine so ScriptAndVoiceStep's own `Session(engine)` sees
    the ScriptSegment rows this test creates -- see the comment in
    test_script_and_voice_uses_segments_when_present for why this is needed."""
    import wave

    from app.pipeline.steps import script_and_voice as script_and_voice_mod

    def _write_silent_wav(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        return path

    class _FakeTtsClient:
        def synthesize(self, *, voice_id: str, text: str, output_path: Path) -> Path:
            return _write_silent_wav(output_path)

    monkeypatch.setattr(script_and_voice_mod, "get_active_tts_client", lambda **kw: _FakeTtsClient())

    import app.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db_session.get_bind())


def test_script_and_voice_avatar_on_excludes_intro_from_voicing(db_session, monkeypatch, tmp_path) -> None:
    """With avatar on, the intro segment's audio comes from HeyGen elsewhere
    (AvatarIntroStep), so ScriptAndVoiceStep must exclude it from the
    continuous ElevenLabs take -- only the non-intro segment gets voiced."""
    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext
    from app.pipeline.steps.script_and_voice import ScriptAndVoiceStep

    _wire_segmented_test_doubles(monkeypatch, db_session)

    # Avatar-on jobs still need a consented ElevenLabs voice for any
    # non-intro segments -- only the intro's audio comes from HeyGen.
    agent = AgentProfile(agency_name="Thornes", staff_name="Luke", heygen_avatar_id="avatar_verified_123")
    consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST", use_avatar=True)
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
    assert result.artifacts["intro_via_avatar"] is True
    # Only the non-intro segment was voiced -- exactly 1 entry, not 2.
    assert len(result.artifacts["segment_audio_paths"]) == 1


def test_script_and_voice_avatar_on_with_only_intro_segment_voices_nothing(
    db_session, monkeypatch, tmp_path
) -> None:
    """With avatar on and the intro as the ONLY segment, segments_to_voice is
    empty -- ScriptAndVoiceStep must not call synthesize_and_slice_segments
    with an empty list (which raises ValueError), and should return an empty
    segment_audio_paths rather than erroring."""
    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext
    from app.pipeline.steps.script_and_voice import ScriptAndVoiceStep

    _wire_segmented_test_doubles(monkeypatch, db_session)

    agent = AgentProfile(agency_name="Thornes", staff_name="Luke", heygen_avatar_id="avatar_verified_123")
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST", use_avatar=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.add(ScriptSegment(job_id=job.id, order_index=0, text="Hi there.", is_intro=True))
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
    assert result.artifacts["intro_via_avatar"] is True
    assert result.artifacts["segment_audio_paths"] == {}


def test_avatar_intro_step_resolves_segmented_intro_text(db_session, monkeypatch, tmp_path) -> None:
    """AvatarIntroStep must resolve the intro line's text from the
    ScriptSegment flagged is_intro when ScriptAndVoiceStep took the
    segmented path (which populates intro_via_avatar, not a `scripts` dict
    with an `avatar_opening` key) -- previously this crashed with
    AttributeError on `None.get(...)` for segmented avatar jobs."""
    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext, StepStatus
    from app.pipeline.steps.avatar_and_captions import AvatarIntroStep
    from app.pipeline.steps.script_and_voice import ScriptAndVoiceStep

    _wire_segmented_test_doubles(monkeypatch, db_session)

    agent = AgentProfile(agency_name="Thornes", staff_name="Luke", heygen_avatar_id="avatar_verified_123")
    consent.set_elevenlabs_voice(agent, "voice_abc", consent_confirmed=True)
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST", use_avatar=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    db_session.add(ScriptSegment(job_id=job.id, order_index=0, text="Hi, I'm Luke.", is_intro=True))
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

    script_result = ScriptAndVoiceStep().run(ctx)
    ctx.artifacts["script_and_voice"] = script_result.artifacts

    calls: list[str] = []

    class _RecordingAvatarClient:
        def generate_avatar_clip(self, *, avatar_id: str, script_text: str, output_path: Path) -> Path:
            calls.append(script_text)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake avatar clip")
            return output_path

    import app.pipeline.steps.avatar_and_captions as avatar_mod

    monkeypatch.setattr(avatar_mod, "get_active_avatar_client", lambda **kw: _RecordingAvatarClient())

    result = AvatarIntroStep().run(ctx)

    assert result.status is StepStatus.DONE
    assert calls == ["Hi, I'm Luke."]


def test_avatar_intro_runs_before_remotion_assembly_in_real_pipeline_order() -> None:
    """Regression test for a real ordering bug: RemotionAssemblyStep's
    segmented path reads ctx.artifacts["avatar_intro"]["avatar_clip_path"],
    but ALL_STEPS previously declared RemotionAssemblyStep before
    AvatarIntroStep, so _toposort (which breaks ties on declaration order)
    ran remotion_assembly first -- avatar_clip_path was always missing,
    silently producing an empty-visual avatar segment in every rendered
    video for segmented, avatar-on jobs. Confirms the real production
    step order (from build_runner(), not a hand-picked subset) has
    avatar_intro before remotion_assembly."""
    from app.pipeline.registry import build_runner

    runner = build_runner()
    step_names = [s.name for s in runner.steps]

    assert step_names.index("avatar_intro") < step_names.index("remotion_assembly"), (
        "avatar_intro must run before remotion_assembly so avatar_clip_path "
        "is available when the segmented render path builds the intro segment"
    )


def test_resume_does_not_rerun_completed_steps(db_session, brochure_pdf, sample_photo, tmp_path):
    job, agent = _make_job(
        db_session, brochure_pdf=brochure_pdf, photo=sample_photo, feature_level=FeatureLevel.STANDARD
    )
    snapshot = build_job_snapshot(db_session, job)
    work_dir = tmp_path / "work"

    ctx1 = JobContext(job.id, work_dir, job.feature_level, job.use_avatar, snapshot)
    build_runner().run(ctx1)

    # Simulate a restart: fresh context, same work dir, state reloaded from disk.
    ctx2 = JobContext(job.id, work_dir, job.feature_level, job.use_avatar, snapshot)
    results2 = build_runner().run(ctx2, resume=True)

    for name, result in results2.items():
        if result.status is StepStatus.DONE:
            assert ctx2.artifacts[name]["_status"] == StepStatus.DONE.value
