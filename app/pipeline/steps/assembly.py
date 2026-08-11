"""Remotion assembly step (spec §4 `standard`, step 4).

Builds `RenderProps` via `services.render_contract.build_render_props` -- the
only supported way to derive the §1.4 disclosure badge and reach the
Python -> Remotion boundary -- then invokes the Remotion CLI to render.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ...models import AgentProfile, FeatureLevel, Photo
from ...services.render_contract import build_render_props
from ..contract import JobContext, PipelineStep, StepResult, StepStatus

REMOTION_DIR = Path(__file__).resolve().parents[3] / "remotion"


def _photo_from_snapshot(raw: dict, *, clip_path: str) -> Photo:
    return Photo(
        id=raw["id"],
        job_id=raw["job_id"],
        source_path=raw["source_path"],
        processed_path=raw.get("processed_path"),
        clip_path=clip_path,
        is_hero=raw.get("is_hero", False),
        sky_replaced=raw.get("sky_replaced", False),
        order_index=raw.get("order_index", 0),
    )


def _agent_from_snapshot(raw: dict) -> AgentProfile:
    return AgentProfile(
        id=raw.get("id"),
        agency_name=raw["agency_name"],
        primary_color=raw.get("primary_color", "#111827"),
        secondary_color=raw.get("secondary_color", "#6b7280"),
        logo_path=raw.get("logo_path"),
        staff_name=raw.get("staff_name"),
    )


class RemotionRenderError(RuntimeError):
    """Raised when the Remotion CLI exits non-zero."""


def render_via_remotion_cli(composition: str, props_path: Path, output_path: Path) -> Path:
    """Invoke `npx remotion render` with a serialized props file.

    CLI shape confirmed against remotion.dev/docs/cli/render:
    `npx remotion render <entry-point> <composition-id> <output> --props=<path>`.
    The entry point and `--props=` (equals sign, not a separate argument -- the
    space-separated form is documented as unreliable on Windows shells) are
    both required; the previous version of this call omitted the entry point
    entirely, which would have failed against a real Remotion install.
    """
    import os

    if os.environ.get("LOCAL_TOOLS_AVAILABLE") != "1":
        from ...clients.base import write_placeholder_file

        return write_placeholder_file(
            output_path, f"[stub Remotion render]\ncomposition={composition}\nprops={props_path}\n"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "npx", "remotion", "render",
            "src/index.ts", composition, str(output_path),
            f"--props={props_path}",
        ],
        cwd=REMOTION_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RemotionRenderError(f"Remotion render failed: {result.stderr[-2000:]}")
    return output_path


class RemotionAssemblyStep(PipelineStep):
    name = "remotion_assembly"
    levels = frozenset(FeatureLevel)
    requires = ("motion_pass", "script_and_voice")

    def run(self, ctx: JobContext) -> StepResult:
        job = ctx.job_snapshot
        agent = _agent_from_snapshot(job["agent"])
        clip_paths = ctx.artifact("motion_pass", "clip_paths")
        audio_paths = ctx.artifact("script_and_voice", "audio_paths")

        photos = [
            _photo_from_snapshot(p, clip_path=clip_paths[str(p["id"])])
            for p in job["photos"]
        ]
        clip_durations = {p.id: 4.0 for p in photos}

        out_dir = ctx.work_dir / "render"
        outputs: dict[str, str] = {}

        master_props = build_render_props(
            composition="Master16x9",
            job=_job_stub(job),
            agent=agent,
            photos=photos,
            clip_durations=clip_durations,
            voiceover_path=audio_paths.get("walkthrough"),
            lower_third=agent.agency_name,
        )
        outputs["master_16x9"] = self._render(master_props, out_dir / "master_16x9.mp4")

        reel_count = 3 if job["feature_level"] in ("plus", "cinematic", "custom") else 1
        short_keys = [k for k in audio_paths if k.startswith("short_")][:reel_count]
        for i, key in enumerate(short_keys or ["walkthrough"]):
            reel_props = build_render_props(
                composition="Reel9x16",
                job=_job_stub(job),
                agent=agent,
                photos=photos,
                clip_durations=clip_durations,
                voiceover_path=audio_paths.get(key),
                lower_third=agent.agency_name,
            )
            outputs[f"reel_{i}"] = self._render(reel_props, out_dir / f"reel_{i}.mp4")

        return StepResult(StepStatus.DONE, {"render_outputs": outputs})

    def _render(self, props, output_path: Path) -> str:
        props_path = output_path.with_suffix(".props.json")
        props_path.parent.mkdir(parents=True, exist_ok=True)
        props_path.write_text(json.dumps(props.to_props(), default=str), encoding="utf-8")
        return str(render_via_remotion_cli(props.composition, props_path, output_path))


def _job_stub(job: dict):
    """Minimal object satisfying `build_render_props`' `job` parameter.

    `build_render_props` only reads `job` for type consistency in this build --
    it never reads price or any other field from it directly, by design (§1.2).
    A namespace stand-in avoids constructing a full `PropertyJob` ORM row here.
    """
    from types import SimpleNamespace

    return SimpleNamespace(**job)
