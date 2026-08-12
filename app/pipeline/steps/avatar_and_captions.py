"""`plus`-level steps (spec §4): HeyGen avatar intro and WhisperX captions.

Two independent, non-critical steps. Avatar rendering only applies to
`use_avatar` jobs; captions run for every `plus`/`cinematic`/`custom` job with
narration audio. Neither step touches `price_guide`.
"""

from __future__ import annotations

from pathlib import Path

from ...clients.dispatch import get_active_avatar_client
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
        opening_line = self._resolve_opening_line(ctx)
        if not opening_line:
            return StepResult(StepStatus.SKIPPED, message="no avatar opening line available")

        # `job_snapshot["heygen_avatar_id"]` is populated only after
        # `services.consent.require_avatar` passed -- see
        # `app.pipeline.registry.build_job_snapshot`.
        avatar_id = job["agent"].get("heygen_avatar_id")
        if not avatar_id:
            raise RuntimeError(
                "job_snapshot has no verified heygen_avatar_id for an avatar job; "
                "this should have been caught before the pipeline started."
            )

        client = get_active_avatar_client()
        clip_path = client.generate_avatar_clip(
            avatar_id=avatar_id,
            script_text=opening_line,
            output_path=ctx.work_dir / "avatar" / "intro.mp4",
        )
        return StepResult(StepStatus.DONE, {"avatar_clip_path": str(clip_path)})

    @staticmethod
    def _resolve_opening_line(ctx: JobContext) -> str | None:
        """The intro line's text, from either the legacy `scripts` dict
        (`avatar_opening` key) or -- for jobs with ScriptSegment rows -- the
        segment flagged `is_intro`, looked up directly from the DB since
        ScriptAndVoiceStep's segmented branch doesn't populate `scripts`."""
        intro_via_avatar = ctx.artifacts.get("script_and_voice", {}).get("intro_via_avatar")
        if intro_via_avatar:
            from sqlmodel import Session

            from ...db import engine
            from ...services.script_segments import list_segments

            with Session(engine) as session:
                segments = list_segments(session, ctx.job_id)
            intro = next((s for s in segments if s.is_intro), None)
            return intro.text if intro else None

        scripts = ctx.artifact("script_and_voice", "scripts")
        if scripts is None:
            return None
        return scripts.get("avatar_opening")


class CaptionTimingStep(PipelineStep):
    """Word-level timestamps driving kinetic captions and cut timing (§4 `plus`)."""

    name = "caption_timing"
    levels = _PLUS_AND_ABOVE
    requires = ("script_and_voice",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        # ScriptAndVoiceStep's segmented branch (see script_and_voice.py)
        # produces `segment_audio_paths`, not `audio_paths` -- per-segment
        # kinetic captions aren't wired up yet (Segment.captions is always
        # empty in RemotionAssemblyStep's segmented path today), so this
        # step has nothing to do for a segmented job. Skip cleanly rather
        # than crash on `None.get(...)`, matching how AvatarIntroStep
        # handles the same legacy-vs-segmented artifact-shape split.
        if ctx.artifacts.get("script_and_voice", {}).get("segment_audio_paths") is not None:
            return StepResult(StepStatus.SKIPPED, message="per-segment captions not yet implemented")

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
