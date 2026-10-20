"""Serializable M3 reports.  They intentionally contain no OOXML objects."""

from pydantic import BaseModel, Field

from booktrans.domain.models import Diagnostic


class PatchReport(BaseModel):
    planned_blocks: int = 0
    planned_spans: int = 0
    planned_text_nodes: int = 0
    locators_resolved: int = 0
    writes_attempted: int = 0
    writes_verified: int = 0
    mirrored_alternate_nodes: int = 0
    resolution_failures: list[Diagnostic] = Field(default_factory=list)
    skipped_protected: int = 0
    skipped_excluded: int = 0
    touched_ooxml_parts: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class PreservationReport(BaseModel):
    candidate_valid: bool = False
    untouched_entries_checked: int = 0
    unchanged_payload_mismatches: list[Diagnostic] = Field(default_factory=list)
    media_entries_checked: int = 0
    media_hash_mismatches: list[Diagnostic] = Field(default_factory=list)
    relationship_entries_checked: int = 0
    relationship_payload_mismatches: list[Diagnostic] = Field(default_factory=list)
    touched_xml_structure_mismatches: list[Diagnostic] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.candidate_valid and not (
            self.unchanged_payload_mismatches
            or self.media_hash_mismatches
            or self.relationship_payload_mismatches
            or self.touched_xml_structure_mismatches
        )


class RoundTripReport(BaseModel):
    source_sha256: str
    candidate_sha256: str
    source_integrity_pass: bool
    source_coverage_complete: bool
    candidate_coverage_complete: bool
    logical_mismatch_count: int = 0
    logical_mismatches: list[Diagnostic] = Field(default_factory=list)
    preservation: PreservationReport
    warnings: list[Diagnostic] = Field(default_factory=list)
    blockers: list[Diagnostic] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.source_integrity_pass
            and self.source_coverage_complete
            and self.candidate_coverage_complete
            and self.logical_mismatch_count == 0
            and self.preservation.passed
            and not self.blockers
        )
