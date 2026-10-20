"""HUMAN_REQUIRED triage.  Human adjudication cannot mask a pipeline gap."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from booktrans.production_worker.reviewer_context import (
    DISPOSITION_PRESERVE_NUMERIC_LITERAL,
    LANE_FOOTNOTE,
    REVIEW_CONTEXT_VERSION,
)
from booktrans.production_worker.semantic_review_group import REVIEW_GROUP_CONTRACT_VERSION
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult


TRIAGE_SYSTEMIC_PIPELINE_GAP = "SYSTEMIC_PIPELINE_GAP"
TRIAGE_TRUE_SEMANTIC_HUMAN = "TRUE_SEMANTIC_HUMAN"
TRIAGE_UNKNOWN = "UNKNOWN"
TRIAGE_RESOLVED = "RESOLVED"

HUMAN_ADJUDICATION_ALLOWED = frozenset({TRIAGE_TRUE_SEMANTIC_HUMAN})
SEMANTIC_PASS_TERMINALS = frozenset({"MODEL_ACCEPTED", "HUMAN_APPROVED"})

_DELETION_CODES = frozenset({"UNTRANSLATED_TEXT", "ERR.NON_PROSE_FRAGMENT", "NON_PROSE_FRAGMENT"})
_FOOTNOTE_MARKER_CODES = frozenset({"E_OMISSION", "OMISSION", "UNTRANSLATED_TEXT"})


class HumanRequiredTriageError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def assert_human_adjudication_allowed(classification: str) -> None:
    if classification not in HUMAN_ADJUDICATION_ALLOWED:
        raise HumanRequiredTriageError("HUMAN_ADJUDICATION_NOT_ALLOWED_FOR_PIPELINE_GAP")


def _instruction_requests_deletion(text: str) -> bool:
    lowered = text.casefold()
    return any(token in lowered for token in ("remove", "delete", "stray", "artifact"))


def is_preserve_numeric_deletion(issue: ReviewIssue, identity: Mapping[str, Any]) -> bool:
    if identity.get("review_disposition") != DISPOSITION_PRESERVE_NUMERIC_LITERAL:
        return False
    if str(issue.code) in _DELETION_CODES:
        return True
    return _instruction_requests_deletion(str(issue.repair_instruction or "") + " " + str(issue.reason or ""))


def is_footnote_marker_false_positive(issue: ReviewIssue, *, current_lane: str | None) -> bool:
    if current_lane != LANE_FOOTNOTE:
        return False
    if str(issue.code) not in _FOOTNOTE_MARKER_CODES:
        return False
    blob = f"{issue.code} {issue.reason} {issue.repair_instruction}".casefold()
    return any(token in blob for token in ("footnote", "layout artifact", "unexplained number", "list index", "superscript"))


def retain_review_issues(review: ReviewResult, semantic_context: Mapping[str, Any] | None) -> ReviewResult:
    """Drop category-level false positives.  Never relaxes NUMBER/D050."""
    if review.verdict != "REPAIR" or not semantic_context:
        return review
    identity = semantic_context.get("source_structural_identity") if isinstance(semantic_context.get("source_structural_identity"), Mapping) else {}
    lane = semantic_context.get("current_lane")
    remaining = [
        issue
        for issue in review.issues
        if not is_preserve_numeric_deletion(issue, identity or {}) and not is_footnote_marker_false_positive(issue, current_lane=str(lane) if lane else None)
    ]
    if remaining:
        return review.model_copy(update={"issues": remaining})
    return review.model_copy(update={"issues": [], "verdict": "ACCEPT"})


def pipeline_gaps(*, required_context: Mapping[str, Any], persisted_payload: Mapping[str, Any] | None) -> list[str]:
    gaps: list[str] = []
    if persisted_payload is None:
        return ["REVIEWER_PAYLOAD_NOT_PERSISTED"]
    unit_context = persisted_payload.get("unit_context") if isinstance(persisted_payload.get("unit_context"), Mapping) else {}
    if unit_context.get("review_context_version") != REVIEW_CONTEXT_VERSION:
        gaps.append("MISSING_REVIEW_CONTEXT_V2")
    if required_context.get("continuation_relation") == "BODY_CONTINUATION" and not persisted_payload.get("semantic_review_group"):
        gaps.append("MISSING_SEMANTIC_REVIEW_GROUP")
    if required_context.get("review_contract_version") == REVIEW_GROUP_CONTRACT_VERSION and unit_context.get("review_contract_version") != REVIEW_GROUP_CONTRACT_VERSION:
        gaps.append("MISSING_REVIEW_GROUP_CONTRACT")
    if required_context.get("current_lane") == LANE_FOOTNOTE and unit_context.get("current_lane") != LANE_FOOTNOTE:
        gaps.append("MISSING_FOOTNOTE_LANE")
    required_identity = required_context.get("source_structural_identity") if isinstance(required_context.get("source_structural_identity"), Mapping) else {}
    got_identity = unit_context.get("source_structural_identity") if isinstance(unit_context.get("source_structural_identity"), Mapping) else persisted_payload.get("source_structural_identity")
    got_identity = got_identity if isinstance(got_identity, Mapping) else {}
    if required_identity.get("review_disposition") == DISPOSITION_PRESERVE_NUMERIC_LITERAL and got_identity.get("review_disposition") != DISPOSITION_PRESERVE_NUMERIC_LITERAL:
        gaps.append("MISSING_STRUCTURAL_IDENTITY")
    required_previous = required_context.get("previous_semantic_unit_uid")
    if required_previous and unit_context.get("previous_semantic_unit_uid") != required_previous:
        gaps.append("PHYSICAL_PREVIOUS_NOT_SEMANTIC")
    return gaps


def classify_human_required(
    *,
    required_context: Mapping[str, Any],
    persisted_payload: Mapping[str, Any] | None,
    reprocess_terminal: str | None = None,
    auto_repair_exhausted: bool = False,
    escalation_reason: str | None = None,
) -> dict[str, Any]:
    if reprocess_terminal in SEMANTIC_PASS_TERMINALS:
        return {
            "classification": TRIAGE_RESOLVED,
            "gaps": [],
            "human_adjudication_allowed": False,
            "semantic_terminal": reprocess_terminal,
        }
    gaps = pipeline_gaps(required_context=required_context, persisted_payload=persisted_payload)
    if gaps and reprocess_terminal is None:
        return {
            "classification": TRIAGE_SYSTEMIC_PIPELINE_GAP,
            "gaps": gaps,
            "human_adjudication_allowed": False,
        }
    # HUMAN_REQUIRED is an outcome emitted by several lower-level paths.  It
    # is not, by itself, proof that a human must decide semantics.  The sole
    # terminal human lane therefore requires an explicit evidence-backed
    # reason from the production disposition boundary.
    if reprocess_terminal == "HUMAN_REQUIRED" and not gaps and escalation_reason:
        return {
            "classification": TRIAGE_TRUE_SEMANTIC_HUMAN,
            "gaps": gaps,
            "human_adjudication_allowed": True,
            "auto_repair_exhausted": auto_repair_exhausted,
            "escalation_reason": escalation_reason,
        }
    if reprocess_terminal == "HUMAN_REQUIRED" and not gaps:
        return {
            "classification": TRIAGE_SYSTEMIC_PIPELINE_GAP,
            "gaps": ["HUMAN_ESCALATION_REASON_MISSING"],
            "human_adjudication_allowed": False,
        }
    if gaps:
        return {
            "classification": TRIAGE_SYSTEMIC_PIPELINE_GAP,
            "gaps": gaps,
            "human_adjudication_allowed": False,
        }
    return {
        "classification": TRIAGE_UNKNOWN,
        "gaps": gaps,
        "human_adjudication_allowed": False,
    }
