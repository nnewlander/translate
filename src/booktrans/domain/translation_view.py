"""Thin M4.7 translation-facing records, deliberately separate from source IR.

These models expose only semantic formatting/container anchors that a future
translator must preserve.  They never contain OOXML objects or full ``w:rPr``
payloads; source locators remain the patcher's authority for those details.
"""

from typing import Literal

from pydantic import BaseModel, Field

from booktrans.domain.ir import SourceLocator
from booktrans.domain.models import Diagnostic
from booktrans.domain.roundtrip import PatchReport, PreservationReport


class FormatAnchor(BaseModel):
    """A deterministic semantic/container template reference for one segment."""

    anchor_id: str
    kinds: list[str] = Field(default_factory=list)
    template_locator: SourceLocator
    container_context: dict[str, str] = Field(default_factory=dict)
    protected: bool = False


class TranslationSegment(BaseModel):
    """One ordered source segment in a paragraph-like translation view."""

    segment_id: str
    text: str
    kind: Literal["plain", "anchored", "protected"]
    anchor_ref: str | None = None
    island_ref: str = "island.001"
    template_locator: SourceLocator
    container_context: dict[str, str] = Field(default_factory=dict)


class EditableTextIsland(BaseModel):
    """A contiguous, independently replaceable text-bearing paragraph region."""

    island_id: str
    segment_ids: list[str] = Field(default_factory=list)


class StructuralAnchor(BaseModel):
    """A preserved paragraph-level or structural-run node between text islands."""

    anchor_id: str
    kind: str
    fingerprint: str
    relative_position: int


class TranslationView(BaseModel):
    """Translation-facing projection of one existing BlockIR, not a new unit."""

    unit_uid: str
    display_id: str
    source_text: str
    segments: list[TranslationSegment] = Field(default_factory=list)
    text_islands: list[EditableTextIsland] = Field(default_factory=list)
    structural_anchors: list[StructuralAnchor] = Field(default_factory=list)
    anchors: list[FormatAnchor] = Field(default_factory=list)
    base_template_locator: SourceLocator
    continuation_hint: bool = False
    previous_text: str | None = None
    next_text: str | None = None


class TaggedCapabilityRecord(BaseModel):
    unit_uid: str
    display_id: str
    part_name: str
    locator: SourceLocator
    status: Literal["TAGGED_SUPPORTED", "TAGGED_UNSUPPORTED"]
    reasons: list[str] = Field(default_factory=list)


class TaggedCapabilityReport(BaseModel):
    total_translatable_blocks: int = 0
    tagged_supported_blocks: int = 0
    tagged_unsupported_blocks: int = 0
    tagged_supported_percentage: float = 0.0
    reason_counts: dict[str, int] = Field(default_factory=dict)
    reason_samples: dict[str, list[TaggedCapabilityRecord]] = Field(default_factory=dict)
    records: list[TaggedCapabilityRecord] = Field(default_factory=list)


class StructuredTargetSegment(BaseModel):
    """One approved synthetic target segment; anchor refs are closed-world."""

    text: str
    kind: Literal["plain", "anchored", "protected"]
    anchor_ref: str | None = None
    island_ref: str = "island.001"


class TaggedSyntheticCandidate(BaseModel):
    unit_uid: str
    display_id: str
    source_text: str
    target_segments: list[StructuredTargetSegment] = Field(default_factory=list)
    target_text: str
    generation_kind: Literal["synthetic_tagged", "calibration"] = "synthetic_tagged"
    status: Literal["approved", "rejected"] = "approved"


class AnchorQAResult(BaseModel):
    unit_uid: str
    passed: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class TaggedRoundTripReport(BaseModel):
    source_sha256: str
    candidate_sha256: str
    source_integrity_pass: bool
    source_coverage_complete: bool
    candidate_coverage_complete: bool
    expected_block_mismatch_count: int = 0
    expected_block_mismatches: list[Diagnostic] = Field(default_factory=list)
    anchor_qa_failure_count: int = 0
    structural_anchor_count: int = 0
    structural_anchor_qa_failure_count: int = 0
    preservation: PreservationReport
    warnings: list[Diagnostic] = Field(default_factory=list)
    blockers: list[Diagnostic] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.source_integrity_pass
            and self.source_coverage_complete
            and self.candidate_coverage_complete
            and self.expected_block_mismatch_count == 0
            and self.anchor_qa_failure_count == 0
            and self.structural_anchor_qa_failure_count == 0
            and self.preservation.passed
            and not self.blockers
        )


class TaggedRunArtifacts(BaseModel):
    capability: TaggedCapabilityReport
    views: list[TranslationView] = Field(default_factory=list)
    candidates: list[TaggedSyntheticCandidate] = Field(default_factory=list)
    qa_results: list[AnchorQAResult] = Field(default_factory=list)
    patch: PatchReport
    validation: TaggedRoundTripReport


class StructuralSubtypeEvidence(BaseModel):
    subtype: str
    affected_blocks: int = 0
    safely_supported: bool = False
    reason: str
    samples: list[TaggedCapabilityRecord] = Field(default_factory=list)


class StructuralCapabilityReport(BaseModel):
    previous_tagged_supported_blocks: int
    previous_tagged_unsupported_blocks: int
    subtype_evidence: list[StructuralSubtypeEvidence] = Field(default_factory=list)
