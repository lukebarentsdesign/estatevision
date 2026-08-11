"""Compliance primitives shared by the prompt builder and the render layer (§1).

Two independent guards live here:

* `assert_price_free` -- a text-level check applied to anything that becomes
  spoken narration or on-screen text. This is a backstop, not the primary
  defence; the primary defence is that `ScriptJobContext` never carries a price
  at all.
* `AI_DISCLOSURE_BADGE_TEXT` -- the §1.4 badge copy. Fixed here so no caller
  can reword or omit it.
"""

from __future__ import annotations

import re

AI_DISCLOSURE_BADGE_TEXT = "AI-Enhanced Visualization / Sky Replacement"


class ComplianceError(Exception):
    """Raised when generated content violates a §1 constraint."""


# Patterns that indicate price has reached narration or on-screen text.
# Deliberately broad: a false positive costs one regeneration, a false negative
# is a regulatory breach.
_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[£$€]\s?[\d,]+"),
    re.compile(r"\b\d[\d,]{2,}\s?(?:k|pcm|pw)\b", re.I),
    re.compile(r"\bguide\s+price\b", re.I),
    re.compile(r"\basking\s+price\b", re.I),
    re.compile(r"\boffers?\s+(?:over|in\s+(?:the\s+)?(?:region|excess))\b", re.I),
    re.compile(r"\bOIRO\b|\bOIEO\b", re.I),
    re.compile(r"\bpriced?\s+at\b", re.I),
    re.compile(r"\bpound(?:s)?\s+sterling\b", re.I),
)


def find_price_mentions(text: str) -> list[str]:
    """Return every price-like fragment found in `text`."""
    hits: list[str] = []
    for pattern in _PRICE_PATTERNS:
        hits.extend(match.group(0) for match in pattern.finditer(text))
    return hits


def assert_price_free(text: str, *, context: str) -> None:
    """Raise `ComplianceError` if `text` contains price information (§1.2).

    Call this on every voiceover script, avatar line, caption, and lower-third
    before it is rendered or sent to a TTS/avatar API.
    """
    hits = find_price_mentions(text)
    if hits:
        raise ComplianceError(
            f"Price information reached {context}, which violates spec §1.2. "
            f"Offending fragments: {hits!r}"
        )


def assert_grounded(text: str, source_sentences: list[str], *, context: str) -> None:
    """Flag script content that is not traceable to the brochure (§1.1).

    This is a heuristic tripwire rather than a proof: it checks that the script
    does not introduce substantive content words absent from the source. The
    binding constraint is the prompt itself; this catches model drift.
    """
    source_vocab = set()
    for sentence in source_sentences:
        source_vocab.update(_content_words(sentence))

    invented = sorted(w for w in _content_words(text) if w not in source_vocab)
    if invented:
        raise ComplianceError(
            f"{context} contains wording with no basis in the source brochure, "
            f"which violates spec §1.1. Unsupported terms: {invented!r}"
        )


# Function words plus the small connective vocabulary a re-sequencing pass is
# allowed to introduce when stitching brochure sentences together.
_ALLOWED_CONNECTIVES = {
    "a", "an", "and", "as", "at", "be", "but", "by", "for", "from", "has",
    "have", "here", "in", "into", "is", "it", "its", "of", "on", "or", "s",
    "that", "the", "there", "this", "to", "welcome", "with", "you", "your",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in _ALLOWED_CONNECTIVES and len(w) > 2}
