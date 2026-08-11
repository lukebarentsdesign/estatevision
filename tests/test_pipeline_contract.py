"""Tests for the pipeline orchestration contract (§9 Phase A item 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import FeatureLevel, JobStatus
from app.pipeline.contract import (
    JobContext,
    PipelineRunner,
    PipelineStep,
    StepResult,
    StepStatus,
    assert_transition,
)


class RecordingStep(PipelineStep):
    def __init__(self, name, requires=(), levels=None, critical=True, fail=False):
        self.name = name
        self.requires = requires
        self.levels = levels or frozenset(FeatureLevel)
        self.critical = critical
        self.fail = fail
        self.calls = 0

    def run(self, ctx: JobContext) -> StepResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        return StepResult(StepStatus.DONE, {"out": f"{self.name}.txt"})


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id=1,
        work_dir=tmp_path,
        feature_level=FeatureLevel.CINEMATIC,
        use_avatar=False,
    )


def test_steps_run_in_dependency_order(ctx: JobContext) -> None:
    order: list[str] = []

    class Tracked(RecordingStep):
        def run(self, c):
            order.append(self.name)
            return super().run(c)

    runner = PipelineRunner(
        [Tracked("assemble", requires=("motion",)), Tracked("motion", requires=("restore",)), Tracked("restore")]
    )
    runner.run(ctx)
    assert order == ["restore", "motion", "assemble"]


def test_circular_dependency_is_rejected() -> None:
    with pytest.raises(ValueError, match="Circular"):
        PipelineRunner([RecordingStep("a", requires=("b",)), RecordingStep("b", requires=("a",))])


def test_completed_steps_are_not_rerun_on_resume(ctx: JobContext) -> None:
    step = RecordingStep("restore")
    PipelineRunner([step]).run(ctx)
    assert step.calls == 1

    # Fresh context over the same work dir: state reloads from disk.
    resumed = JobContext(1, ctx.work_dir, FeatureLevel.CINEMATIC, use_avatar=False)
    PipelineRunner([step]).run(resumed, resume=True)
    assert step.calls == 1, "resume must not rerun a completed step"


def test_steps_outside_feature_level_are_skipped(tmp_path: Path) -> None:
    ctx = JobContext(1, tmp_path, FeatureLevel.STANDARD, use_avatar=False)
    cinematic_only = RecordingStep("lut", levels=frozenset({FeatureLevel.CINEMATIC}))
    results = PipelineRunner([cinematic_only]).run(ctx)
    assert results["lut"].status is StepStatus.SKIPPED
    assert cinematic_only.calls == 0


def test_critical_failure_aborts_run(ctx: JobContext) -> None:
    failing = RecordingStep("restore", fail=True)
    later = RecordingStep("motion", requires=("restore",))
    with pytest.raises(RuntimeError, match="exploded"):
        PipelineRunner([failing, later]).run(ctx)
    assert later.calls == 0


def test_noncritical_failure_continues(ctx: JobContext) -> None:
    optional = RecordingStep("music", critical=False, fail=True)
    later = RecordingStep("assemble")
    results = PipelineRunner([optional, later]).run(ctx)
    assert results["music"].status is StepStatus.FAILED
    assert later.calls == 1


def test_failure_is_persisted_for_diagnosis(ctx: JobContext) -> None:
    PipelineRunner([RecordingStep("music", critical=False, fail=True)]).run(ctx)
    reloaded = JobContext(1, ctx.work_dir, FeatureLevel.CINEMATIC, use_avatar=False)
    reloaded.load()
    assert reloaded.artifacts["music"]["_status"] == StepStatus.FAILED.value
    assert "exploded" in reloaded.artifacts["music"]["error"]


@pytest.mark.parametrize(
    "current,target",
    [
        (JobStatus.INGESTION, JobStatus.PROCESSING),
        (JobStatus.PROCESSING, JobStatus.REVIEW),
        (JobStatus.REVIEW, JobStatus.COMPLETED),
        (JobStatus.COMPLETED, JobStatus.REVIEW),
    ],
)
def test_legal_status_transitions(current, target) -> None:
    assert_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (JobStatus.INGESTION, JobStatus.COMPLETED),
        (JobStatus.INGESTION, JobStatus.REVIEW),
        (JobStatus.COMPLETED, JobStatus.INGESTION),
    ],
)
def test_illegal_status_transitions(current, target) -> None:
    with pytest.raises(ValueError, match="Illegal"):
        assert_transition(current, target)
