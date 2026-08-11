"""Shared client conventions.

Every external service client exposes a Protocol and a stub implementation.
Real implementations (calling actual HTTP APIs) are swapped in via
`app/clients/__init__.py` factory functions once API keys are configured;
until then, stubs let the full pipeline run and be tested with zero network
access and zero keys.
"""

from __future__ import annotations

from pathlib import Path


def write_placeholder_file(path: Path, contents: str) -> Path:
    """Write a small marker file standing in for a real media artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path
