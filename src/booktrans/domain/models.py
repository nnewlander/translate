from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    code: str
    severity: Literal["warning", "blocker"]
    message: str
    part: str | None = None
    detail: str | None = None


class SourceSnapshot(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    mtime_utc: datetime | None = None
    source_mutated: bool = False


class PackageStats(BaseModel):
    zip_entries: int = 0
    ooxml_parts: int = 0
    relationship_parts: int = 0
    relationships: int = 0
    xml_parts: int = 0
    part_names: list[str] = Field(default_factory=list)


class ContentStats(BaseModel):
    body_paragraphs: int = 0
    headings: int = 0
    tables: int = 0
    table_cells: int = 0
    images: int = 0
    hyperlinks: int = 0


class StoryStats(BaseModel):
    parts: int = 0
    paragraphs: int = 0
    tables: int = 0


class OtherStories(BaseModel):
    headers: StoryStats = Field(default_factory=StoryStats)
    footers: StoryStats = Field(default_factory=StoryStats)
    footnotes: StoryStats = Field(default_factory=StoryStats)
    endnotes: StoryStats = Field(default_factory=StoryStats)
    comments: StoryStats = Field(default_factory=StoryStats)


class SpecialConstructStats(BaseModel):
    simple_fields: int = 0
    field_characters: int = 0
    field_instruction_texts: int = 0
    bookmarks: int = 0
    tracked_insertions: int = 0
    tracked_deletions: int = 0
    content_controls: int = 0
    text_boxes: int = 0
    vml_text_boxes: int = 0
    equations: int = 0
    smartart_parts: int = 0
    ole_objects: int = 0
    embedded_objects: int = 0
    drawings: int = 0
    anchored_shapes: int = 0
    alternate_content: int = 0
    alt_chunks: int = 0
    subdocuments: int = 0
    custom_xml_ranges: int = 0
    detection_confidence: dict[str, str] = Field(default_factory=dict)


class InspectionReport(BaseModel):
    run_id: str
    inspected_at_utc: datetime
    source: SourceSnapshot
    package: PackageStats
    content: ContentStats
    other_stories: OtherStories
    special_constructs: SpecialConstructStats
    warnings: list[Diagnostic] = Field(default_factory=list)
    blockers: list[Diagnostic] = Field(default_factory=list)
