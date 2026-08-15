"""`cinematic`-level steps (spec §4).

Sky replacement, LUT + film grain, music ducking, aerial flyover, and the
microsite. The sky-replacement step is the only place `Photo.sky_replaced` is
set -- once set, the §1.4 badge is applied unconditionally downstream by
`services.render_contract.build_render_props`, with no override available here.
"""

from __future__ import annotations

from pathlib import Path

from ...clients.base import write_placeholder_file
from ...clients.local_tools import demucs_separate_music, ffmpeg_mux
from ...models import FeatureLevel
from ..contract import JobContext, PipelineStep, StepResult, StepStatus

_CINEMATIC_AND_ABOVE = frozenset({FeatureLevel.CINEMATIC, FeatureLevel.CUSTOM})

_DEFAULT_LUT_PATH = Path(__file__).resolve().parents[3] / "assets" / "luts" / "architectural.cube"


class SkyReplacementStep(PipelineStep):
    """Optional dusk/sky conversion on exterior photos (§4 `cinematic`).

    This step's only externally visible effect on compliance is flipping
    `sky_replaced` on the photos it touches; it does not (and must not) decide
    whether a badge is shown. That decision belongs solely to
    `render_contract.build_render_props`.
    """

    name = "sky_replacement"
    levels = _CINEMATIC_AND_ABOVE
    requires = ("restoration_pass",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        processed_paths = ctx.artifact("restoration_pass", "processed_paths")
        exteriors = [p for p in job["photos"] if p.get("is_hero") or p.get("is_exterior")]

        if not exteriors:
            return StepResult(StepStatus.SKIPPED, message="no exterior photos flagged")

        out_dir = ctx.work_dir / "sky_replaced"
        replaced_ids: list[int] = []
        for photo in exteriors:
            photo_id = str(photo["id"])
            source = Path(processed_paths[photo_id])
            write_placeholder_file(
                out_dir / f"{photo_id}_dusk.png", f"[stub sky replacement]\nsource={source}\n"
            )
            replaced_ids.append(photo["id"])

        return StepResult(StepStatus.DONE, {"sky_replaced_photo_ids": replaced_ids})


class MusicDuckingStep(PipelineStep):
    """Background music stem separation and auto-duck under voiceover (§4 `cinematic`)."""

    name = "music_ducking"
    levels = _CINEMATIC_AND_ABOVE
    requires = ("script_and_voice",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        music_path = job.get("music_bed_path")
        if not music_path:
            return StepResult(StepStatus.SKIPPED, message="no music bed configured")

        stem = demucs_separate_music(Path(music_path), ctx.work_dir / "music")
        return StepResult(StepStatus.DONE, {"ducked_music_path": str(stem), "duck_db": -14.0})


class LutAndGrainStep(PipelineStep):
    """Architectural 3D LUT + subtle 35mm film grain via FFmpeg (§4 `cinematic`)."""

    name = "lut_and_grain"
    levels = _CINEMATIC_AND_ABOVE
    requires = ("remotion_assembly",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        render_outputs = ctx.artifact("remotion_assembly", "render_outputs")
        out_dir = ctx.work_dir / "graded"
        graded: dict[str, str] = {}

        for key, path in render_outputs.items():
            output = ffmpeg_mux(
                video_path=Path(path),
                audio_path=None,
                output_path=out_dir / f"{key}_graded.mp4",
                lut_path=_DEFAULT_LUT_PATH,
            )
            graded[key] = str(output)

        return StepResult(StepStatus.DONE, {"graded_outputs": graded})


class MicrositeBuilderStep(PipelineStep):
    """Standalone property microsite (§4 `cinematic`, §6.4).

    This is the one place `price_guide` is legitimately used -- the microsite
    is explicitly carved out by §1.2 as CRM/landing-page display, not spoken
    narration or video overlay.
    """

    name = "microsite_builder"
    levels = _CINEMATIC_AND_ABOVE
    requires = ("remotion_assembly",)
    critical = False

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        render_outputs = ctx.artifact("remotion_assembly", "render_outputs")
        location_data = job.get("location_data") or {}

        html = self._render_html(job, render_outputs, location_data)
        out_path = ctx.work_dir / "microsite" / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")

        return StepResult(StepStatus.DONE, {"microsite_path": str(out_path)})

    def _render_html(self, job: dict, render_outputs: dict, location_data: dict) -> str:
        price = job.get("price_guide") or "Price on application"
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{job['address']}</title></head><body>"
            f"<h1>{job['address']}</h1><p>{job['postcode']}</p>"
            f"<p class='price'>{price}</p>"
            f"<video src='{render_outputs.get('master_16x9', '')}' controls></video>"
            "</body></html>"
        )
