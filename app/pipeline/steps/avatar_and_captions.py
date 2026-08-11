"""`plus`-level steps (spec §4): HeyGen avatar intro and WhisperX captions.

Two independent, non-critical steps. Avatar rendering only applies to
`use_avatar` jobs; captions run for every `plus`/`cinematic`/`custom` job with
narration audio. Neither step touches `price_guide`.
"""

from __future__ import annotations

from pathlib import Path

from ...clients.heygen import get_heygen_client
from ...clients.local_tools import whisperx_word_timestamps
from ...models import FeatureLevel
from ...services.compliance import assert_price_free
from ..contract import JobContext, PipelineStep, StepResult, StepStatus

_PLUS_AND_ABOVE = frozenset({FeatureLevel.PLUS, FeatureLevel.CINEMATIC, FeatureLevel.CUSTOM})


class AvatarIntroStep(PipelineStep):
    """HeyGen avatar clip for the opening line only (§4 `plus`, two-ID architecture)."""

    name = "avatar_intro"
    levels = _PLUS_AND_ABOVE
    requires = ("script_and_voice",)
    critical = False

    def applies_to(self, ctx: JobContext) -> bool:
        return super().applies_to(ctx) and ctx.use_avatar

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        scripts = ctx.artifact("script_and_voice", "scripts")
        opening_line = scripts.get("avatar_opening")
        if not opening_line:
            return StepResult(StepStatus.SKIPPED, message="no avatar_opening script produced")

        # `job_snapshot["heygen_avatar_id"]` is populated only after
        # `services.consent.require_avatar` passed -- see
        # `app.pipeline.registry.build_job_snapshot`.
        avatar_id = job["agent"].get("heygen_avatar_id")
        if not avatar_id:
            raise RuntimeError(
                "job_snapshot has no verified heygen_avatar_id for an avatar job; "
                "this should have been caught before the pipeline started."
            )

        client = get_heygen_client()
        clip_path = client.generate_avatar_clip(
            avatar_id=avatar_id,
            script_text=opening_line,
            output_path=ctx.work_dir / "avatar" / "intro.mp4",
        )
        return StepResult(StepStatus.DONE, {"avatar_clip_path": str(clip_path)})


class CaptionTimingStep(PipelineStep):
    """Word-level timestamps driving kinetic captions and cut timing (§4 `plus`)."""

    name = "caption_timing"
    levels = _PLUS_AND_ABOVE
    requires = ("script_and_voice",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        audio_paths = ctx.artifact("script_and_voice", "audio_paths")
        walkthrough_audio = audio_paths.get("walkthrough")
        if not walkthrough_audio:
            return StepResult(StepStatus.SKIPPED, message="no walkthrough audio to caption")

        words = whisperx_word_timestamps(Path(walkthrough_audio))
        cues = []
        for w in words:
            assert_price_free(w.word, context="a caption word")
            cues.append({"text": w.word, "start_sec": w.start_sec, "end_sec": w.end_sec})

        return StepResult(StepStatus.DONE, {"caption_cues": cues})
