"""Motion pass (spec §4 `standard`, step 2).

Hybrid, standing rule: the hero/front-exterior shot goes to Gemini Omni for a
subtle forward-facing zoom/pan; every other shot goes to DepthFlow for depth
parallax. Runs after `restoration_pass` so it animates the cleaned-up photo.
"""

from __future__ import annotations

from pathlib import Path

from ...clients.gemini_omni import get_gemini_omni_client
from ...clients.local_tools import depthflow_parallax
from ...models import FeatureLevel
from ..contract import JobContext, PipelineStep, StepResult, StepStatus


class MotionPassStep(PipelineStep):
    name = "motion_pass"
    levels = frozenset(FeatureLevel)
    requires = ("restoration_pass",)

    def run(self, ctx: JobContext) -> StepResult:
        photos = ctx.job_snapshot["photos"]
        processed_paths = ctx.artifact("restoration_pass", "processed_paths")
        out_dir = ctx.work_dir / "motion"
        gemini = get_gemini_omni_client()

        clip_paths: dict[str, str] = {}
        for photo in photos:
            photo_id = str(photo["id"])
            source = Path(processed_paths[photo_id])
            output_path = out_dir / f"{photo_id}_clip.mp4"

            if photo.get("is_hero"):
                clip = gemini.animate_hero_shot(
                    image_path=source, motion_style="subtle_zoom_in", output_path=output_path
                )
            else:
                clip = depthflow_parallax(source, output_path)

            clip_paths[photo_id] = str(clip)

        return StepResult(StepStatus.DONE, {"clip_paths": clip_paths})
