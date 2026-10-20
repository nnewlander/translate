"""Serializable M2 Document IR.  No OOXML implementation objects belong here."""

from datetime import datetime

from pydantic import BaseModel, Field

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.models import Diagnostic, SourceSnapshot


class SourceLocator(BaseModel):
    part_name: str
    story: str
    xml_path: str
    container_path: str | None = None
    paragraph_ordinal: int | None = None


class SpanIR(BaseModel):
    """One visible text span or a field boundary needed for M2 accounting."""

    span_uid: str
    kind: str  # "text" or "field_boundary"; deliberately not an Atom taxonomy.
    locator: SourceLocator
    text: str = ""
    classification: CoverageClassification | None = None
    # Extra source nodes only. ``locator`` is always the first source node.
    source_run_locators: list[SourceLocator] = Field(default_factory=list)
    direct_format_fingerprint: str | None = None
    container_context: dict[str, str] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class BlockIR(BaseModel):
    unit_uid: str
    display_id: str
    story: str
    part_name: str
    block_type: str
    content_role: str
    locator: SourceLocator
    source_plain_text: str
    source_hash: str
    structure_fingerprint: str
    spans: list[SpanIR] = Field(default_factory=list)
    translation_scope: CoverageClassification | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class DocumentStoryIR(BaseModel):
    story: str
    part_name: str
    blocks: list[BlockIR] = Field(default_factory=list)


class DocumentIR(BaseModel):
    schema_version: int = 1
    document_sha256: str
    source: SourceSnapshot
    extracted_at_utc: datetime
    stories: list[DocumentStoryIR] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @property
    def blocks(self) -> list[BlockIR]:
        return [block for story in self.stories for block in story.blocks]


class CoverageGroup(BaseModel):
    name: str
    visible_text_nodes: int = 0
    visible_blocks: int = 0
    visible_characters: int = 0
    translatable_characters: int = 0
    translatable_blocks: int = 0
    protected_characters: int = 0
    protected_blocks: int = 0
    excluded_characters: int = 0
    excluded_blocks: int = 0
    unclassified_visible_text: int = 0
    unclassified_blocks: int = 0
    unknown_potential_visible_content: int = 0
    unknown_potential_characters: int = 0
    units: int = 0


class CoverageEntry(BaseModel):
    classification: CoverageClassification
    locator: SourceLocator
    code: str
    message: str
    text_snippet: str = ""
    character_count: int = 0


class CoverageLedger(BaseModel):
    document_sha256: str
    source_integrity_pass: bool
    groups: list[CoverageGroup] = Field(default_factory=list)
    entries: list[CoverageEntry] = Field(default_factory=list)
    warnings: list[Diagnostic] = Field(default_factory=list)
    blockers: list[Diagnostic] = Field(default_factory=list)
    coverage_complete: bool
    visible_text_nodes: int = 0
    visible_blocks: int = 0
    visible_characters: int = 0
    translatable_characters: int = 0
    translatable_blocks: int = 0
    protected_characters: int = 0
    protected_blocks: int = 0
    excluded_characters: int = 0
    excluded_blocks: int = 0
    unclassified_visible_text: int = 0
    unclassified_blocks: int = 0
    unknown_potential_visible_content: int = 0
    unknown_potential_characters: int = 0
