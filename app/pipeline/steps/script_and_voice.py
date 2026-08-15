"""Audio & script pass (spec §4 `standard`, step 3).

Builds every prompt through `services.script_prompt.build_prompt` -- the only
permitted way to construct a script prompt (§1.1, §1.2) -- then sends the
resulting narration to an LLM client and voices it with ElevenLabs. Never
constructs a prompt inline; never passes `job.price_guide` to anything in this
module.
"""

from __future__ import annotations

from ...clients.dispatch import get_active_tts_client
from ...clients.openai_llm import get_openai_llm_client
from ...models import FeatureLevel
from ...services.compliance import assert_price_free
from ...services.script_audio import synthesize_and_slice_segments
from ...services.script_prompt import ScriptJobContext, ScriptVariant, build_prompt
from ...services.script_segments import list_segments
from ..contract import JobContext, PipelineStep, StepResult, StepStatus


class ScriptAndVoiceStep(PipelineStep):
    name = "script_and_voice"
    levels = frozenset(FeatureLevel)
    requires = ("ingest_brochure",)

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        agent = job["agent"]

        segments = self._load_segments(ctx.job_id)
        if segments:
            return self._run_segmented(ctx, job, agent, segments)

        # --- legacy path (unchanged) ---
        sentences = ctx.artifact("ingest_brochure", "sentences")

        script_ctx = ScriptJobContext.from_fields(
            address=job["address"],
            postcode=job["postcode"],
            garden_orientation=job.get("garden_orientation"),
            agency_name=agent["agency_name"],
            staff_name=agent.get("staff_name"),
            brochure_sentences=sentences,
        )

        llm = get_openai_llm_client()

        # §4: `standard` produces 1 short-form reel script's worth of shorts
        # covering a single social cut; `plus`/`cinematic`/`custom` need 3
        # reels, so 3 short scripts are generated to back them.
        short_count = 3 if ctx.feature_level != FeatureLevel.STANDARD else 1
        variants = (ScriptVariant.WALKTHROUGH,) + (ScriptVariant.SHORT,) * short_count
        if ctx.use_avatar:
            variants += (ScriptVariant.AVATAR_OPENING,)

        scripts: dict[str, str] = {}
        for i, variant in enumerate(variants):
            prompt = build_prompt(script_ctx, variant)
            text = llm.complete(prompt)
            # Last check before this text is spoken or stored (§1.2).
            assert_price_free(text, context=f"generated {variant.value} script")
            key = f"{variant.value}_{i}" if variant is ScriptVariant.SHORT else variant.value
            scripts[key] = text

        # `job_snapshot["agent"]["elevenlabs_voice_id"]` is populated only after
        # `services.consent.require_voice_for_narration` has passed -- see
        # `app.pipeline.registry.build_job_snapshot`. This step trusts that
        # gate rather than re-checking it, so it never needs the ORM.
        voice_id = None if ctx.use_avatar else agent.get("elevenlabs_voice_id")
        if not ctx.use_avatar and not voice_id:
            raise RuntimeError(
                "job_snapshot has no consented elevenlabs_voice_id for a "
                "voice-only job; this should have been caught before the "
                "pipeline started."
            )

        out_dir = ctx.work_dir / "audio"
        audio_paths: dict[str, str] = {}
        if voice_id:
            client = get_active_tts_client()
            for key, text in scripts.items():
                if key == ScriptVariant.AVATAR_OPENING.value:
                    continue
                path = client.synthesize(
                    voice_id=voice_id, text=text, output_path=out_dir / f"{key}.mp3"
                )
                audio_paths[key] = str(path)

        return StepResult(
            StepStatus.DONE,
            {"scripts": scripts, "audio_paths": audio_paths},
        )

    def _load_segments(self, job_id: int) -> list:
        from sqlmodel import Session

        from ...db import engine

        with Session(engine) as session:
            return list_segments(session, job_id)

    def _run_segmented(self, ctx: JobContext, job: dict, agent: dict, segments: list) -> StepResult:
        use_avatar = ctx.use_avatar
        intro_segment = next((s for s in segments if s.is_intro), None)
        other_segments = [s for s in segments if not s.is_intro]

        # Avatar-intro exception: when avatar is on, the intro's audio comes
        # from HeyGen inside AvatarIntroStep, not from ElevenLabs here. When
        # avatar is off, the intro is voiced identically to every other
        # segment and included in the continuous take below.
        segments_to_voice = segments if not use_avatar else other_segments

        for s in segments_to_voice:
            assert_price_free(s.text, context=f"segment {s.id}")

        # Unlike the legacy path, avatar-on jobs here still need an
        # ElevenLabs voice: only the intro segment's audio comes from
        # HeyGen -- every other segment (in `segments_to_voice`) is always
        # voiced by ElevenLabs, avatar or not. `job_snapshot["agent"]
        # ["elevenlabs_voice_id"]` is populated only after
        # `services.consent.require_voice_for_narration` has passed -- see
        # `app.pipeline.registry.build_job_snapshot`. This step trusts that
        # gate rather than re-checking it, so it never needs the ORM.
        voice_id = agent.get("elevenlabs_voice_id") if segments_to_voice else None
        if segments_to_voice and not voice_id:
            raise RuntimeError(
                "job_snapshot has no consented elevenlabs_voice_id for a "
                "job with segments to voice; this should have been caught "
                "before the pipeline started."
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
