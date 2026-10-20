"""Small, fail-closed final-review issue severity policy."""

from __future__ import annotations

from typing import Iterable


BLOCKING_SEMANTIC = "BLOCKING_SEMANTIC"
AUTO_POLISH = "AUTO_POLISH"
COSMETIC_DEBT = "COSMETIC_DEBT"

_AUTO_POLISH_CODES = frozenset({
    "fluency", "naturalness", "spacing", "spacing_error", "missing_spacing", "join_error",
    "punctuation", "punctuation_error", "punctuation_mismatch", "punctuation_spacing",
    "punctuation_space", "continuation_punctuation_error", "segmentation_artifact", "register_and_style",
    "syntax_error", "syntax_unnatural", "word_order", "local_word_order",
    # Core Harness already verified the exact marker identity before a
    # reviewer can classify a remaining display-only marker separation.
    "footnote_placement",
})
_COSMETIC_CODES = frozenset({
    "cosmetic", "style_preference", "misalignment", "segment_misalignment",
    "fidelity_added_meaning", "semantic_fidelity_addition",
    # The frozen IR context proves the adjacent member; when the reviewer
    # explicitly says the current target correctly handles an incomplete
    # source fragment and requests no target change, this is diagnostic debt,
    # not a semantic defect in the candidate.
    "incomplete_source_span",
})
_BLOCKING_CODES = frozenset({
    "omission", "semantic_mismatch", "semantic_drift", "mistranslation",
    "segment_misalignment_duplication", "semantic_integrity_break",
    "unsupported_addition", "meaning_ambiguity", "terminology_canonical_name",
    "number", "numeric", "url", "doi", "protected", "integrity", "binding",
})


def review_issue_severity(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized in _AUTO_POLISH_CODES:
        return AUTO_POLISH
    if normalized in _COSMETIC_CODES:
        return COSMETIC_DEBT
    if normalized in _BLOCKING_CODES or not normalized:
        return BLOCKING_SEMANTIC
    # An unfamiliar issue code is not silently downgraded.
    return BLOCKING_SEMANTIC


def review_issue_severities(codes: Iterable[str]) -> set[str]:
    return {review_issue_severity(code) for code in codes}
