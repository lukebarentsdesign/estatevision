"""Tests for the Remotion assembly step's real CLI invocation.

Covers a real bug found while researching Remotion's CLI: the previous
`render_via_remotion_cli` omitted the required entry-point argument and used
a space-separated `--props <path>` instead of `--props=<path>` (documented as
unreliable on Windows shells) -- a call that would have failed against a real
Remotion install despite passing every prior test, since those tests never
exercised the `LOCAL_TOOLS_AVAILABLE=1` code path's exact command shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.pipeline.steps.assembly import RemotionRenderError, render_via_remotion_cli


@pytest.fixture(autouse=True)
def _tools_available(monkeypatch):
    monkeypatch.setenv("LOCAL_TOOLS_AVAILABLE", "1")


def _fake_run_factory(side_effect):
    def fake_run(cmd, cwd=None, capture_output=True, text=True):
        returncode = side_effect(cmd) or 0
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="mock failure" if returncode else "")

    return fake_run


def test_remotion_render_includes_entry_point_and_equals_props_flag(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def side_effect(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(subprocess, "run", _fake_run_factory(side_effect))

    props_path = tmp_path / "props.json"
    props_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "out.mp4"

    render_via_remotion_cli("Master16x9", props_path, output_path)

    cmd = captured["cmd"]
    assert cmd[:3] == ["npx", "remotion", "render"]
    assert "src/index.ts" in cmd  # entry point -- previously missing entirely
    assert "Master16x9" in cmd
    assert str(output_path) in cmd
    props_flags = [a for a in cmd if a.startswith("--props=")]
    assert len(props_flags) == 1, "props must use --props=<path>, not a separate '--props' + path pair"
    assert props_flags[0] == f"--props={props_path}"


def test_remotion_render_raises_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(lambda cmd: 1))

    props_path = tmp_path / "props.json"
    props_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RemotionRenderError):
        render_via_remotion_cli("Master16x9", props_path, tmp_path / "out.mp4")


def test_remotion_project_scaffold_exists() -> None:
    """Sanity check the real Remotion project files exist and aren't empty
    stubs -- catches an accidentally-deleted or half-written scaffold even
    though the subprocess call itself is mocked above."""
    remotion_dir = Path(__file__).resolve().parents[1] / "remotion"

    package_json = remotion_dir / "package.json"
    assert package_json.exists()
    assert "@remotion/cli" in package_json.read_text(encoding="utf-8")

    entry = remotion_dir / "src" / "index.ts"
    assert entry.exists()
    assert "registerRoot" in entry.read_text(encoding="utf-8")

    root = remotion_dir / "src" / "Root.tsx"
    assert root.exists()
    root_source = root.read_text(encoding="utf-8")
    assert 'id="Master16x9"' in root_source
    assert 'id="Reel9x16"' in root_source
    assert "calculateMetadata" in root_source

    composition = remotion_dir / "src" / "PropertyVideo.tsx"
    assert composition.exists()
    composition_source = composition.read_text(encoding="utf-8")
    assert "disclosure_badge" in composition_source  # §1.4 badge must be rendered
