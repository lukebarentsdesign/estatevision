"""Brochure ingestion step (spec §4 `standard`, audio & script pass, part 1).

Parses the source PDF into discrete sentences and immediately strips any that
mention price (§1.2), before that text goes anywhere near a script prompt.
Later steps read `ctx.artifacts["ingest_brochure"]["sentences"]`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...models import FeatureLevel
from ...services.script_prompt import sanitize_source_sentences
from ..contract import JobContext, PipelineStep, StepResult, StepStatus

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def extract_sentences(pdf_path: Path) -> list[str]:
    """Pull brochure body text out as a flat list of sentences."""
    import pdfplumber

    raw_lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            raw_lines.append(text)

    full_text = " ".join(raw_lines).replace("\n", " ")
    full_text = re.sub(r"\s+", " ", full_text).strip()
    if not full_text:
        return []

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(full_text) if s.strip()]
    return sentences


class IngestBrochureStep(PipelineStep):
    name = "ingest_brochure"
    levels = frozenset(FeatureLevel)
    requires = ()

    def run(self, ctx: JobContext) -> StepResult:
        pdf_path = Path(ctx.job_snapshot["pdf_brochure_path"])
        raw_sentences = extract_sentences(pdf_path)
        clean_sentences = sanitize_source_sentences(raw_sentences)

        dropped = len(raw_sentences) - len(clean_sentences)
        return StepResult(
            StepStatus.DONE,
            {
                "sentences": clean_sentences,
                "raw_sentence_count": len(raw_sentences),
                "price_sentences_dropped": dropped,
            },
        )
