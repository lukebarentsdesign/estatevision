"""Audio & script pass (spec §4 `standard`, step 3).

Builds every prompt through `services.script_prompt.build_prompt` -- the only
permitted way to construct a script prompt (§1.1, §1.2) -- then sends the
resulting narration to an LLM client and voices it with ElevenLabs. Never
constructs a prompt inline; never passes `job.price_guide` to anything in this
module.
"""

from __future__ import annotations

from typing import Protocol

from ...clients.credential_lookup import resolve_field
from ...clients.elevenlabs import get_elevenlabs_client
from ...models import FeatureLevel
from ...services.compliance import assert_price_free
from ...services.script_prompt import ScriptJobContext, ScriptVariant, build_prompt
from ..contract import JobContext, PipelineStep, StepResult, StepStatus


class ScriptLLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class StubScriptLLMClient:
    """Deterministic stand-in: returns the numbered source sentences, joined.

    This trivially satisfies §1.1 grounding (it uses only source sentences) and
    is good enough to exercise the pipeline without an LLM key.
    """

    def complete(self, prompt: str) -> str:
        marker = "SOURCE SENTENCES (your only permitted material):\n"
        _, _, tail = prompt.partition(marker)
        lines = [line.split(". ", 1)[-1] for line in tail.strip().splitlines() if line.strip()]
        return " ".join(lines)


def get_script_llm_client() -> ScriptLLMClient:
    if resolve_field("openai", "api_key"):
        raise NotImplementedError(
            "Real LLM client not implemented yet -- wire this up against the "
            "OpenAI (or compatible) API. A key is already configured via the "
            "admin panel or environment; only the request/response handling "
            "remains."
        )
    return StubScriptLLMClient()


class ScriptAndVoiceStep(PipelineStep):
    name = "script_and_voice"
    levels = frozenset(FeatureLevel)
    requires = ("ingest_brochure",)

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        agent = job["agent"]
        sentences = ctx.artifact("ingest_brochure", "sentences")

        script_ctx = ScriptJobContext.from_fields(
            address=job["address"],
            postcode=job["postcode"],
            garden_orientation=job.get("garden_orientation"),
            agency_name=agent["agency_name"],
            staff_name=agent.get("staff_name"),
            brochure_sentences=sentences,
        )

        llm = get_script_llm_client()

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
            client = get_elevenlabs_client()
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
