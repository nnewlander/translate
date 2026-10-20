"""Serializable M4.5 audit output; no lxml objects or write instructions."""

from pydantic import BaseModel, Field

from booktrans.domain.ir import SourceLocator


class FragmentSample(BaseModel):
    display_id: str
    story: str
    part_name: str
    locator: SourceLocator
    source_text: str
    previous_text: str | None = None
    next_text: str | None = None
    reason: str


class TranslationUnitAudit(BaseModel):
    total_translatable_blocks: int = 0
    character_length_distribution: dict[str, int] = Field(default_factory=dict)
    word_count_distribution: dict[str, int] = Field(default_factory=dict)
    blocks_leq_one_word: int = 0
    blocks_leq_three_words: int = 0
    punctuation_only_blocks: int = 0
    number_or_page_like_blocks: int = 0
    short_heading_or_title_blocks: int = 0
    suspicious_fragment_candidates: int = 0
    suspicious_fragment_samples: list[FragmentSample] = Field(default_factory=list)


class FormatUnitAuditReport(BaseModel):
    source_sha256: str
    source_integrity_pass: bool
    coverage_complete: bool
    total_translatable_blocks: int = 0
    raw_safe_simple_blocks: int = 0
    raw_multi_format_context_blocks: int = 0
    normalization_collapsible_blocks: int = 0
    true_multi_format_blocks: int = 0
    normalized_safe_blocks: int = 0
    normalized_safe_percentage: float = 0.0
    collapsible_categories: dict[str, int] = Field(default_factory=dict)
    true_format_difference_categories: dict[str, int] = Field(default_factory=dict)
    remaining_complex_reasons: dict[str, int] = Field(default_factory=dict)
    format_samples: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    unit_audit: TranslationUnitAudit
