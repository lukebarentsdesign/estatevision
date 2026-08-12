"""Turn an LLM's segmented-walkthrough JSON response into `ScriptSegment`
rows, and shared helpers for estimating spoken duration.

Every sentence -- LLM-generated or agent-authored -- passes through
`assert_price_free` before being persisted (spec: sentence-photo linking
design, 2026-08-12, §6).
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from ..models import ScriptSegment
from .compliance import assert_price_free

_WORDS_PER_SECOND = 2.5  # ~150 words/minute average spoken narration rate


def estimate_total_duration_sec(texts: list[str]) -> float:
    """Rough words-per-second estimate, used for the arrange screen's
    advisory 2-minute indicator. Not used to block anything (soft warning
    only, per the design doc)."""
    total_words = sum(len(t.split()) for t in texts)
    return total_words / _WORDS_PER_SECOND


def create_segments_from_llm_json(
    session: Session, *, job_id: int, llm_json_text: str
) -> list[ScriptSegment]:
    """Parse the LLM's JSON array response and persist one ScriptSegment per
    item, in order. Raises `ValueError` on malformed JSON/shape, or
    `ComplianceError` if any item's text mentions price.
    """
    try:
        items = json.loads(llm_json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc

    if not isinstance(items, list) or not items:
        raise ValueError(f"Expected a non-empty JSON array, got: {llm_json_text!r}")

    segments: list[ScriptSegment] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "text" not in item:
            raise ValueError(f"Segment {i} missing 'text' field: {item!r}")

        text = str(item["text"]).strip()
        assert_price_free(text, context=f"generated segment {i}")

        segment = ScriptSegment(
            job_id=job_id,
            order_index=i,
            text=text,
            is_intro=bool(item.get("is_intro", i == 0)),
        )
        session.add(segment)
        segments.append(segment)

    session.commit()
    for s in segments:
        session.refresh(s)
    return segments


def list_segments(session: Session, job_id: int) -> list[ScriptSegment]:
    return session.exec(
        select(ScriptSegment).where(ScriptSegment.job_id == job_id).order_by(ScriptSegment.order_index)
    ).all()
