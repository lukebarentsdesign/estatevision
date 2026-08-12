"""Tests for the Remotion assembly step's real CLI invocation.

Covers a real bug found while researching Remotion's CLI: the previous
`render_via_remotion_cli` omitted the required entry-point argument and used
a space-separated `--props <path>` instead of `--props=<path>` (documented as
unreliable on Windows shells) -- a call that would have failed against a real
Remotion install despite passing every prior test, since those tests never
exercised the `LOCAL_TOOLS_AVAILABLE=1` code path's exact command shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline.steps.assembly import RemotionRenderError, render_via_remotion_cli


@pytest.fixture(autouse=True)
def _tools_available(monkeypatch):
    monkeypatch.setenv("LOCAL_TOOLS_AVAILABLE", "1")


def _fake_run_factory(side_effect):
    def fake_run(cmd, cwd=None, capture_output=True, text=True):
        returncode = side_effect(cmd) or 0
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="mock failure" if returncode else "")

    return fake_run


def test_remotion_render_includes_entry_point_and_equals_props_flag(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    props_path = tmp_path / "props.json"
    props_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "out.mp4"

    render_via_remotion_cli("Master16x9", props_path, output_path)

    cmd = captured["cmd"]
    assert cmd[:3] == ["npx", "remotion", "render"]
    assert "src/index.ts" in cmd  # entry point -- previously missing entirely
    assert "Master16x9" in cmd
    assert str(output_path) in cmd
    props_flags = [a for a in cmd if a.startswith("--props=")]
    assert len(props_flags) == 1, "props must use --props=<path>, not a separate '--props' + path pair"
    assert props_flags[0] == f"--props={props_path}"


def test_remotion_render_raises_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 1))

    props_path = tmp_path / "props.json"
    props_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RemotionRenderError):
        render_via_remotion_cli("Master16x9", props_path, tmp_path / "out.mp4")


def test_remotion_project_scaffold_exists() -> None:
    """Sanity check the real Remotion project files exist and aren't empty
    stubs -- catches an accidentally-deleted or half-written scaffold even
    though the subprocess call itself is mocked above."""
    remotion_dir = Path(__file__).resolve().parents[1] / "remotion"

    package_json = remotion_dir / "package.json"
    assert package_json.exists()
    assert "@remotion/cli" in package_json.read_text(encoding="utf-8")

    entry = remotion_dir / "src" / "index.ts"
    assert entry.exists()
    assert "registerRoot" in entry.read_text(encoding="utf-8")

    root = remotion_dir / "src" / "Root.tsx"
    assert root.exists()
    root_source = root.read_text(encoding="utf-8")
    assert 'id="Master16x9"' in root_source
    assert 'id="Reel9x16"' in root_source
    assert "calculateMetadata" in root_source

    composition = remotion_dir / "src" / "PropertyVideo.tsx"
    assert composition.exists()
    composition_source = composition.read_text(encoding="utf-8")
    assert "disclosure_badge" in composition_source  # §1.4 badge must be rendered


def _write_silent_wav(path: Path, duration_ms: int = 500) -> None:
    """Writes a trivial, real, silent mono WAV file so pydub's
    `AudioSegment.from_file` (used by `_audio_duration_sec`) has a real file
    to open, matching the technique used by fake TTS clients elsewhere in
    this test suite."""
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    framerate = 8000
    n_frames = int(framerate * duration_ms / 1000)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * n_frames)


def test_remotion_assembly_uses_segmented_path_when_segments_exist(tmp_path, monkeypatch) -> None:
    """When motion_pass/script_and_voice artifacts include segment audio
    (from ScriptAndVoiceStep's segmented branch), RemotionAssemblyStep
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

        job_id, photo_id, segment_id, agent_id = job.id, photo.id, segment.id, agent.id

    # Named .wav (not .mp3): pydub's AudioSegment.from_file auto-detects
    # format from the extension by default, and a real WAV file named
    # ".mp3" would make it try to decode via ffmpeg, which isn't installed
    # in this environment (see app/clients/audio_slicing.py's own reasoning
    # for defaulting to WAV over MP3 for the same underlying reason).
    segment_audio_path = tmp_path / "segment_0.wav"
    _write_silent_wav(segment_audio_path)

    snapshot = {
        "id": job_id,
        "address": "1 Test St",
        "postcode": "TE1 1ST",
        "feature_level": "standard",
        "agent": {"id": agent_id, "agency_name": "Test Agency", "primary_color": "#111827", "secondary_color": "#6b7280"},
        "photos": [
            {"id": photo_id, "job_id": job_id, "source_path": "/p/kitchen.jpg", "processed_path": "/p/kitchen_p.jpg", "order_index": 0},
        ],
    }
    ctx = JobContext(job_id=job_id, work_dir=tmp_path, feature_level="standard", use_avatar=False, job_snapshot=snapshot)
    ctx.artifacts["motion_pass"] = {"clip_paths": {str(photo_id): "/p/kitchen_clip.mp4"}}
    ctx.artifacts["script_and_voice"] = {
        "segment_audio_paths": {segment_id: str(segment_audio_path)},
        "intro_segment_id": None,
        "intro_via_avatar": False,
    }

    step = RemotionAssemblyStep()
    result = step.run(ctx)

    assert result.status is StepStatus.DONE
    assert "render_outputs" in result.artifacts


def test_remotion_assembly_uses_real_avatar_clip_path_for_intro_when_avatar_on(tmp_path, monkeypatch) -> None:
    """Regression test: RemotionAssemblyStep's segmented path reads
    ctx.artifacts["avatar_intro"]["avatar_clip_path"] to build the intro
    segment's visual. This artifact is only present if AvatarIntroStep has
    already run by the time this step runs -- this test sets it directly to
    prove the READING side works correctly (that RemotionAssemblyStep
    actually threads the real path through rather than silently defaulting
    to an empty string). Real pipeline step ORDER is covered separately by
    test_avatar_intro_runs_before_remotion_assembly_in_real_pipeline_order
    in tests/test_pipeline_end_to_end.py."""
    from sqlmodel import Session, SQLModel, create_engine

    import app.db as db_mod
    from app.models import AgentProfile, PropertyJob, ScriptSegment
    from app.pipeline.contract import JobContext, StepStatus
    from app.pipeline.steps.assembly import RemotionAssemblyStep

    monkeypatch.delenv("LOCAL_TOOLS_AVAILABLE", raising=False)

    isolated_engine = create_engine(
        f"sqlite:///{tmp_path / 'isolated2.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(isolated_engine)
    monkeypatch.setattr(db_mod, "engine", isolated_engine)

    with Session(isolated_engine) as session:
        agent = AgentProfile(agency_name="Test Agency")
        session.add(agent)
        session.commit()
        session.refresh(agent)

        job = PropertyJob(agent_id=agent.id, address="1 Test St", postcode="TE1 1ST", use_avatar=True)
        session.add(job)
        session.commit()
        session.refresh(job)

        segment = ScriptSegment(job_id=job.id, order_index=0, text="Hi there.", is_intro=True)
        session.add(segment)
        session.commit()
        session.refresh(segment)

        job_id, agent_id, segment_id = job.id, agent.id, segment.id

    # .wav extension (not .mp4): pydub's AudioSegment.from_file auto-detects
    # format from the extension, and ffmpeg/ffprobe aren't installed in this
    # environment -- see the WAV-vs-MP3 comment earlier in this file for the
    # same underlying reason. A real video file would carry its own audio
    # track pydub could still measure; this WAV file is a stand-in with the
    # same property (a real, pydub-readable duration) for test purposes.
    avatar_clip_path = tmp_path / "avatar" / "intro.wav"
    _write_silent_wav(avatar_clip_path)

    snapshot = {
        "id": job_id,
        "address": "1 Test St",
        "postcode": "TE1 1ST",
        "feature_level": "standard",
        "agent": {"id": agent_id, "agency_name": "Test Agency", "primary_color": "#111827", "secondary_color": "#6b7280"},
        "photos": [],
    }
    ctx = JobContext(job_id=job_id, work_dir=tmp_path, feature_level="standard", use_avatar=True, job_snapshot=snapshot)
    ctx.artifacts["motion_pass"] = {"clip_paths": {}}
    ctx.artifacts["script_and_voice"] = {
        "segment_audio_paths": {},
        "intro_segment_id": segment_id,
        "intro_via_avatar": True,
    }
    ctx.artifacts["avatar_intro"] = {"avatar_clip_path": str(avatar_clip_path)}

    captured_segments = {}
    import app.pipeline.steps.assembly as assembly_mod

    original_build = assembly_mod.build_segmented_render_props

    def _capturing_build(*, segments, **kwargs):
        captured_segments["segments"] = segments
        return original_build(segments=segments, **kwargs)

    monkeypatch.setattr(assembly_mod, "build_segmented_render_props", _capturing_build)

    result = RemotionAssemblyStep().run(ctx)

    assert result.status is StepStatus.DONE
    intro_segment = captured_segments["segments"][0]
    assert intro_segment.is_avatar is True
    assert intro_segment.visual_path == str(avatar_clip_path)
    assert intro_segment.visual_path != ""
    assert intro_segment.duration_sec > 0
