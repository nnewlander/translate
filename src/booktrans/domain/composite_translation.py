"""Narrow M5.3 contracts for source-proven composite prose.

Composite grouping changes semantic source ownership, never the closed-world
validation standard. This module holds serializable contract records only; the
ordinary deterministic harness remains the validator of their text.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.domain.translation_view import StructuredTargetSegment


MOVABLE_ANCHOR_KINDS = frozenset({
    "bold", "italic", "underline", "color", "highlight", "hyperlink", "character_style",
})


class CompositeValidationEnvelope(BaseModel):
    """Aggregated, pre-existing validation evidence for one logical work order."""

    model_config = ConfigDict(extra="forbid")

    source_sha256: str
    group_id: str
    member_unit_uids: list[str]
    member_display_ids: list[str]
    source_text: str
    # Logical destination count, not physical member paragraph count. A flat
    # projection writes its designated primary island and may clear only
    # approved continuation payloads in secondary members.
    logical_editable_island_count: int
    semantic_anchor_kinds: list[str] = Field(default_factory=list)
    protected_literals: list[str] = Field(default_factory=list)
    footnote_reference_numbers: list[str] = Field(default_factory=list)
    numeric_source_tokens: list[str] = Field(default_factory=list)
    url_source_tokens: list[str] = Field(default_factory=list)
    email_source_tokens: list[str] = Field(default_factory=list)
    doi_source_tokens: list[str] = Field(default_factory=list)
    isbn_source_tokens: list[str] = Field(default_factory=list)
    flat_target_eligible: bool
    required_response_contract: Literal["FLAT_TEXT", "STRUCTURED_SEGMENTS"]

    def as_ordinary_request(self) -> CalibrationRequest:
        """Adapt flat composite evidence to the existing normalizer/QA shape.

        No anchors or footnote evidence are invented. The ordinary harness
        derives numeric/URL/etc. checks from the same combined source text.
        """
        if not self.flat_target_eligible:
            raise ValueError("COMPOSITE_STRUCTURED_RESPONSE_REQUIRED")
        return CalibrationRequest(
            source_sha256=self.source_sha256,
            unit_uid=self.group_id,
            display_id=f"composite.{self.group_id}",
            story="composite",
            content_role="proven_composite_prose",
            source_text=self.source_text,
            source_segments=[CalibrationSourceSegment(kind="plain", text=self.source_text, island_ref="island.001")],
            footnote_reference_numbers=self.footnote_reference_numbers,
        )


class CompositeTranslationRequest(BaseModel):
    """Translator-facing request with its deterministic validation envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["m5.3-composite-request"] = "m5.3-composite-request"
    envelope: CompositeValidationEnvelope
    previous_text: str | None = None
    next_text: str | None = None


class CompositeTranslationResult(BaseModel):
    """Flat response permitted only when the envelope explicitly permits it."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["m5.3-composite"] = "m5.3-composite"
    group_id: str
    target_text: str
    translator_notes: str | None = None


class CompositeStructuredTranslationResult(BaseModel):
    """Normal structured segments for non-flat composites; not a new system."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["m5.3-composite-structured"] = "m5.3-composite-structured"
    group_id: str
    target_segments: list[StructuredTargetSegment]
    translator_notes: str | None = None


def flat_target_eligibility(
    *,
    logical_editable_island_count: int,
    semantic_anchor_kinds: set[str],
    protected_literals: list[str],
    structural_segmentation_required: bool,
) -> tuple[bool, list[str]]:
    """Return a conservative flat-target decision and auditable reasons."""
    reasons: list[str] = []
    if logical_editable_island_count != 1:
        reasons.append("COMPOSITE_FLAT_TARGET_MULTI_ISLAND")
    if semantic_anchor_kinds & MOVABLE_ANCHOR_KINDS:
        reasons.append("COMPOSITE_FLAT_TARGET_MOVABLE_ANCHOR")
    if protected_literals:
        reasons.append("COMPOSITE_FLAT_TARGET_PROTECTED_LITERAL")
    if structural_segmentation_required:
        reasons.append("COMPOSITE_FLAT_TARGET_STRUCTURAL_SEGMENTATION")
    return not reasons, reasons


def validate_composite_response(
    response: CompositeTranslationResult,
    envelope: CompositeValidationEnvelope,
    approved_group_ids: set[str],
) -> list[str]:
    """Validate shape/eligibility only; shared QA validates target literals."""
    errors: list[str] = []
    if response.group_id not in approved_group_ids:
        errors.append("UNKNOWN_COMPOSITE_GROUP")
    if response.group_id != envelope.group_id:
        errors.append("COMPOSITE_ENVELOPE_MISMATCH")
    if not response.target_text.strip():
        errors.append("EMPTY_TARGET")
    if not envelope.flat_target_eligible:
        errors.append("COMPOSITE_STRUCTURED_RESPONSE_REQUIRED")
    return errors
