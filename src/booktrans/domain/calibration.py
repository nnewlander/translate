"""Small, file-driven M5.0 real-translation calibration contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktrans.domain.translation_view import StructuredTargetSegment


class CalibrationSourceSegment(BaseModel):
    """Sanitized source segment: no locator, OOXML or physical formatting."""

    kind: Literal["plain", "anchored", "protected"]
    text: str
    anchor_ref: str | None = None
    island_ref: str


class CalibrationAnchor(BaseModel):
    anchor_id: str
    semantics: list[str] = Field(default_factory=list)
    protected: bool = False
    protected_text: str | None = None
    container: str | None = None


class VerifiedFootnoteSourceSegment(BaseModel):
    """One exact source segment participating in a verified marker boundary."""

    anchor_ref: str | None = None
    text: str


class VerifiedFootnoteNumericBinding(BaseModel):
    """Closed-world evidence that a flattened marker contains a footnote."""

    unit_uid: str
    exact_source_marker_token: str
    footnote_number: str
    source_segment_binding: list[VerifiedFootnoteSourceSegment] = Field(min_length=1)
    pre_footnote_numeric_tokens: list[str] = Field(default_factory=list)


class ExplicitNumericRepresentationEquivalence(BaseModel):
    """One reviewed, exact numeric wording equivalence for deterministic QA."""

    unit_uid: str
    exact_source_span: str
    # A reviewed equivalence may bind an English number phrase (for example
    # ``two billion``) whose literal source span has no digit token.  The
    # exact span and semantic-value equality remain mandatory; an empty list
    # therefore does not turn this into a heuristic numeric parser.
    source_numeric_tokens: list[str] = Field(default_factory=list)
    approved_target_representation: str
    reviewed_source_semantic_numeric_value: str
    reviewed_target_semantic_numeric_value: str
    semantic_unit: str | None = None
    # Optional structured qualifiers make a Chinese integer representation
    # auditable without turning D051 into an inference mechanism.  Historical
    # D051 evidence intentionally remains valid without them.
    source_comparison_operator: str | None = None
    target_comparison_operator: str | None = None
    source_counter_context: str | None = None
    target_counter_context: str | None = None
    # Opt-in D051 form: one fully-spelled English cardinal rendered as one
    # ordinary target numeric literal.  Legacy D051 records remain unchanged.
    equivalence_kind: Literal["ENGLISH_CARDINAL_TO_LITERAL"] | None = None


class CalibrationRequest(BaseModel):
    """One translator-facing, selected BlockIR contract record."""

    schema_version: Literal["m5.0"] = "m5.0"
    source_sha256: str
    unit_uid: str
    display_id: str
    story: str
    content_role: str
    source_text: str
    source_segments: list[CalibrationSourceSegment]
    available_anchors: list[CalibrationAnchor] = Field(default_factory=list)
    continuation_hint: bool = False
    previous_text: str | None = None
    next_text: str | None = None
    selection_categories: list[str] = Field(default_factory=list)
    # This is deliberately evidence, not an invitation for the normalizer to
    # infer footnotes from arbitrary prose numbers.  An exporter may populate
    # it only when the source location has been established as a footnote-like
    # reference by its own structural evidence.
    footnote_reference_numbers: list[str] = Field(default_factory=list)
    # D050: this remains empty unless a separate, exact boundary record has
    # been proven. It is evidence for deterministic QA, never a detector.
    verified_footnote_numeric_bindings: list[VerifiedFootnoteNumericBinding] = Field(default_factory=list)
    # D051: an exact, reviewed equivalence may suppress only the literal
    # source tokens inside its named span. It is never inferred from wording.
    explicit_numeric_representation_equivalences: list[ExplicitNumericRepresentationEquivalence] = Field(default_factory=list)


class TranslationResult(BaseModel):
    """Strict file-import response. Target segments reuse the M4.7 model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["m5.0"] = "m5.0"
    unit_uid: str
    target_segments: list[StructuredTargetSegment]
    translator_notes: str | None = None


class CalibrationResponseStatus(BaseModel):
    """Auditable outcome for one imported response; notes are not QA failures."""

    unit_uid: str
    display_id: str | None = None
    status: Literal["PASS", "FAIL", "REVIEW_REQUIRED"]
    diagnostics: list[str] = Field(default_factory=list)
    translator_notes: str | None = None
    source_text: str | None = None


class VisualReviewEntry(BaseModel):
    unit_uid: str
    display_id: str
    content_role: str
    story: str
    selection_categories: list[str] = Field(default_factory=list)
    nearby_drawing_or_textbox: bool = False
    structural_island: bool = False
    continuation_hint: bool = False
    approximate_page: int | None = None
    page_mapping_available: bool = False
    review_reason: str
