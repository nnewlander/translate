"""Serializable M4.6 evidence report; deliberately not a format/write model."""

from pydantic import BaseModel, Field

from booktrans.domain.ir import SourceLocator


class PropertySample(BaseModel):
    display_id: str
    part_name: str
    locator: SourceLocator
    source_text: str
    cooccurring_differences: list[str] = Field(default_factory=list)


class DirectPropertyEvidence(BaseModel):
    property_name: str
    evidence_category: str
    block_count: int
    cooccurring_difference_counts: dict[str, int] = Field(default_factory=dict)
    samples: list[PropertySample] = Field(default_factory=list)


class FragmentDecisionSample(BaseModel):
    display_id: str
    story: str
    part_name: str
    locator: SourceLocator
    source_text: str
    previous_text: str | None = None
    next_text: str | None = None
    structural_evidence: list[str] = Field(default_factory=list)
    classification_reason: str


class FragmentBucket(BaseModel):
    count: int = 0
    percentage: float = 0.0
    samples: list[FragmentDecisionSample] = Field(default_factory=list)


class FormatFragmentDecisionAuditReport(BaseModel):
    source_sha256: str
    source_integrity_pass: bool
    coverage_complete: bool
    other_direct_format_blocks: int = 0
    direct_property_evidence: list[DirectPropertyEvidence] = Field(default_factory=list)
    logical_region_distribution: dict[str, int] = Field(default_factory=dict)
    simple_inline_emphasis_blocks: int = 0
    hyperlink_blocks: int = 0
    protected_content_blocks: int = 0
    multi_complex_factor_blocks: int = 0
    fragment_candidates_total: int = 0
    likely_true_prose_fragment: FragmentBucket
    likely_legitimate_standalone: FragmentBucket
    uncertain: FragmentBucket
    true_fragment_root_causes: dict[str, int] = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
