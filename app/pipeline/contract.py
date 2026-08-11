"""Pipeline step interface and job state machine (§9 Phase A item 3).

Each production step is a self-contained unit with declared inputs and outputs.
The runner handles ordering, idempotency, resumability, and failure semantics so
individual steps (Phase B work) never have to.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from ..models import FeatureLevel, JobStatus

log = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    status: StepStatus
    artifacts: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


@dataclass
class JobContext:
    """Mutable working state threaded through a pipeline run.

    `artifacts` accumulates each step's output, keyed by step name, and is what
    makes resumption possible: a completed step's artifacts are reloaded rather
    than recomputed.
    """

    job_id: int
    work_dir: Path
    feature_level: FeatureLevel
    use_avatar: bool
    #: Read-only snapshot of job/agent/photo fields steps need as input.
    #: Distinct from `artifacts`, which holds step *output* and is persisted
    #: across resumes -- `job_snapshot` is rebuilt fresh from the DB each run.
    job_snapshot: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def artifact(self, step_name: str, key: str, default: Any = None) -> Any:
        return self.artifacts.get(step_name, {}).get(key, default)

    def state_file(self) -> Path:
        return self.work_dir / "pipeline_state.json"

    def save(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_file().write_text(
            json.dumps(self.artifacts, indent=2, default=str), encoding="utf-8"
        )

    def load(self) -> None:
        path = self.state_file()
        if path.exists():
            self.artifacts = json.loads(path.read_text(encoding="utf-8"))


class PipelineStep(ABC):
    """Base class for every production step.

    Contract for implementers (Phase B):

    * `run` must be idempotent -- calling it twice with the same context must
      not corrupt state or duplicate output.
    * `run` must write its outputs under `ctx.work_dir` and return their paths
      in `StepResult.artifacts`. Do not mutate `ctx.artifacts` directly.
    * Raise on genuine failure. Do not return a partial success.
    * Never build an LLM prompt inline -- use `services.script_prompt` (§1).
    """

    name: str
    #: Feature levels this step participates in.
    levels: frozenset[FeatureLevel] = frozenset(FeatureLevel)
    #: Step names that must complete before this one.
    requires: tuple[str, ...] = ()
    #: If True, a failure fails the whole job. If False, the job continues.
    critical: bool = True

    @abstractmethod
    def run(self, ctx: JobContext) -> StepResult:
        ...

    def applies_to(self, ctx: JobContext) -> bool:
        """Override for conditions beyond feature level (e.g. `use_avatar`)."""
        return ctx.feature_level in self.levels

    def is_satisfied(self, ctx: JobContext) -> bool:
        """True if this step's work already exists (drives resumability)."""
        return ctx.artifacts.get(self.name, {}).get("_status") == StepStatus.DONE.value


class PipelineRunner:
    """Executes steps in dependency order with resume-on-restart semantics."""

    def __init__(self, steps: Iterable[PipelineStep]) -> None:
        self.steps = _toposort(list(steps))

    def run(self, ctx: JobContext, *, resume: bool = True) -> dict[str, StepResult]:
        if resume:
            ctx.load()

        results: dict[str, StepResult] = {}
        for step in self.steps:
            if not step.applies_to(ctx):
                results[step.name] = StepResult(StepStatus.SKIPPED, message="not in feature level")
                continue

            if resume and step.is_satisfied(ctx):
                log.info("step %s already complete, skipping", step.name)
                results[step.name] = StepResult(StepStatus.DONE, ctx.artifacts[step.name])
                continue

            missing = [r for r in step.requires if r not in ctx.artifacts]
            if missing:
                msg = f"unmet dependencies: {missing}"
                results[step.name] = StepResult(StepStatus.SKIPPED, message=msg)
                if step.critical:
                    raise RuntimeError(f"Step {step.name} cannot run -- {msg}")
                continue

            log.info("running step %s", step.name)
            try:
                result = step.run(ctx)
            except Exception as exc:  # noqa: BLE001 - recorded and re-raised below
                log.exception("step %s failed", step.name)
                ctx.artifacts[step.name] = {"_status": StepStatus.FAILED.value, "error": str(exc)}
                ctx.save()
                results[step.name] = StepResult(StepStatus.FAILED, message=str(exc))
                if step.critical:
                    raise
                continue

            ctx.artifacts[step.name] = {**result.artifacts, "_status": result.status.value}
            ctx.save()
            results[step.name] = result

        return results


def _toposort(steps: list[PipelineStep]) -> list[PipelineStep]:
    """Order steps so dependencies precede dependents."""
    by_name = {s.name: s for s in steps}
    ordered: list[PipelineStep] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(step: PipelineStep) -> None:
        if step.name in seen:
            return
        if step.name in visiting:
            raise ValueError(f"Circular dependency involving step {step.name!r}")
        visiting.add(step.name)
        for dep in step.requires:
            if dep in by_name:
                visit(by_name[dep])
        visiting.discard(step.name)
        seen.add(step.name)
        ordered.append(step)

    for step in steps:
        visit(step)
    return ordered


#: Legal job status transitions (§3 `status`).
STATUS_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.INGESTION: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.REVIEW, JobStatus.INGESTION}),
    JobStatus.REVIEW: frozenset({JobStatus.COMPLETED, JobStatus.PROCESSING}),
    JobStatus.COMPLETED: frozenset({JobStatus.REVIEW}),
}


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in STATUS_TRANSITIONS[current]:
        raise ValueError(f"Illegal job status transition {current.value} -> {target.value}")
