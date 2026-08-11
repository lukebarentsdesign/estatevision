"""Restoration pass (spec §4 `standard`, step 1).

Real-ESRGAN upscale -> Zero-DCE shadow lift -> Gray-World white balance, applied
to every photo in order. Runs at every feature level since it's the baseline
quality pass everything else builds on.
"""

from __future__ import annotations

from pathlib import Path

from ...clients.local_tools import (
    gray_world_white_balance,
    real_esrgan_upscale,
    zero_dce_shadow_lift,
)
from ...models import FeatureLevel
from ..contract import JobContext, PipelineStep, StepResult, StepStatus


class RestorationPassStep(PipelineStep):
    name = "restoration_pass"
    levels = frozenset(FeatureLevel)
    requires = ()

    def run(self, ctx: JobContext) -> StepResult:
        photos = ctx.job_snapshot["photos"]
        out_dir = ctx.work_dir / "restored"
        processed: dict[str, str] = {}

        for photo in photos:
            photo_id = str(photo["id"])
            source = Path(photo["source_path"])

            upscaled = real_esrgan_upscale(source, out_dir / f"{photo_id}_upscaled.png")
            lifted = zero_dce_shadow_lift(upscaled, out_dir / f"{photo_id}_lifted.png")
            balanced = gray_world_white_balance(lifted, out_dir / f"{photo_id}_final.png")

            processed[photo_id] = str(balanced)

        return StepResult(StepStatus.DONE, {"processed_paths": processed})
