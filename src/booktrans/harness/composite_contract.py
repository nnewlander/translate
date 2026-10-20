"""Shared deterministic-envelope construction for M5.3 composites.

This is deliberately a small adapter, not a second validator.  It aggregates
only evidence already present in the member TranslationViews/requests, then
hands flat target text to the ordinary deterministic QA implementation.
"""

from collections.abc import Mapping
import re

from booktrans.domain.composite_translation import (
    CompositeTranslationRequest,
    CompositeTranslationResult,
    CompositeValidationEnvelope,
    flat_target_eligibility,
    validate_composite_response,
)
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.deterministic_qa import DOI, EMAIL, ISBN, _numeric_tokens, _url_tokens, DeterministicCandidateQA


def build_composite_request(
    group: Mapping[str, object],
    views_by_display_id: Mapping[str, TranslationView],
    *,
    source_sha256: str,
    member_footnote_evidence: Mapping[str, list[str]] | None = None,
) -> tuple[CompositeTranslationRequest, list[str]]:
    """Aggregate approved member evidence into one auditable request.

    The final scope artifact is the authority for member order and combined
    semantic source.  No heuristic derives footnote evidence from typography.
    """
    group_id = str(group["group_id"])
    member_ids = [str(item) for item in group["members"]]
    views = [views_by_display_id[item] for item in member_ids]
    anchor_kinds = sorted({kind for view in views for anchor in view.anchors for kind in anchor.kinds})
    protected_literals = [
        segment.text for view in views for segment in view.segments if segment.kind == "protected" and segment.text
    ]
    footnote_map = member_footnote_evidence or {}
    footnote_refs = sorted({value for member in member_ids for value in footnote_map.get(member, [])})
    # Composite projection has one designated primary writable island.  A
    # member's internal multi-island shape would need structured placement;
    # paragraph-level drawing anchors alone are preserved by the existing
    # composite writeback contract and do not make a flat target unsafe.
    structural_segmentation_required = any(len(view.text_islands) != 1 for view in views)
    eligible, reasons = flat_target_eligibility(
        logical_editable_island_count=1,
        semantic_anchor_kinds=set(anchor_kinds),
        protected_literals=protected_literals,
        structural_segmentation_required=structural_segmentation_required,
    )
    source_text = str(group["source_combined_text"])
    envelope = CompositeValidationEnvelope(
        source_sha256=source_sha256,
        group_id=group_id,
        member_unit_uids=[view.unit_uid for view in views],
        member_display_ids=member_ids,
        source_text=source_text,
        logical_editable_island_count=1,
        semantic_anchor_kinds=anchor_kinds,
        protected_literals=protected_literals,
        footnote_reference_numbers=footnote_refs,
        numeric_source_tokens=_numeric_tokens(source_text),
        url_source_tokens=_url_tokens(source_text),
        email_source_tokens=EMAIL.findall(source_text),
        doi_source_tokens=DOI.findall(source_text),
        isbn_source_tokens=ISBN.findall(source_text),
        flat_target_eligible=eligible,
        required_response_contract="FLAT_TEXT" if eligible else "STRUCTURED_SEGMENTS",
    )
    return CompositeTranslationRequest(envelope=envelope), reasons


def validate_flat_composite_with_ordinary_qa(
    response: CompositeTranslationResult,
    request: CompositeTranslationRequest,
    *,
    approved_group_ids: set[str],
) -> list[str]:
    """Use the ordinary text validator after composite shape checks.

    This makes number/URL/email/DOI/ISBN/protected checks identical to an
    ordinary response.  Flat composites by construction have no movable
    anchors; non-flat groups are rejected here and must use structured output.
    """
    envelope = request.envelope
    errors = validate_composite_response(response, envelope, approved_group_ids)
    if errors:
        return errors
    ordinary_request = envelope.as_ordinary_request()
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=envelope.group_id,
            display_id=ordinary_request.display_id,
            source_text=ordinary_request.source_text,
            target_text=response.target_text,
        ),
        known_unit_ids={envelope.group_id},
        protected_text=envelope.protected_literals,
    )
    errors.extend(item.code for item in result.diagnostics)
    # Superscript numerals are not recognised by the ordinary NUMBER regex.
    # Keep the approved normalizer's closed-world rule explicit and fail before
    # any normalization when its prerequisite evidence is absent.
    for superscript in re.findall(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", response.target_text):
        value = superscript.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789"))
        if value in envelope.numeric_source_tokens and value not in envelope.footnote_reference_numbers:
            errors.append("UNEVIDENCED_FOOTNOTE_SUPERSCRIPT")
            break
    return sorted(set(errors))
