"""Thin M4 data records; these are not a future Translator framework."""

from typing import Literal

from pydantic import BaseModel, Field

from booktrans.domain.ir import SourceLocator
from booktrans.domain.models import Diagnostic
from booktrans.domain.roundtrip import PatchReport, PreservationReport


class CapabilityRecord(BaseModel):
    unit_uid: str
    display_id: str
    part_name: str
    locator: SourceLocator
    status: Literal["SAFE_SIMPLE", "COMPLEX_UNSUPPORTED"]
    reasons: list[str] = Field(default_factory=list)


class CapabilityReport(BaseModel):
    total_translatable_blocks: int = 0
    safe_simple_blocks: int = 0
    complex_unsupported_blocks: int = 0
    safe_simple_percentage: float = 0.0
    reason_counts: dict[str, int] = Field(default_factory=dict)
    reason_samples: dict[str, list[CapabilityRecord]] = Field(default_factory=dict)
    records: list[CapabilityRecord] = Field(default_factory=list)


class SyntheticCandidate(BaseModel):
    unit_uid: str
    display_id: str
    source_text: str
    target_text: str
    generation_kind: Literal["synthetic"] = "synthetic"
    status: Literal["approved", "rejected"] = "approved"


class CandidateQAResult(BaseModel):
    unit_uid: str
    passed: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class SyntheticRoundTripReport(BaseModel):
    source_sha256: str
    candidate_sha256: str
    source_integrity_pass: bool
    source_coverage_complete: bool
    candidate_coverage_complete: bool
    expected_block_mismatch_count: int = 0
    expected_block_mismatches: list[Diagnostic] = Field(default_factory=list)
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
            and self.preservation.passed
            and not self.blockers
        )


class SyntheticRunArtifacts(BaseModel):
    capability: CapabilityReport
    candidates: list[SyntheticCandidate] = Field(default_factory=list)
    qa_results: list[CandidateQAResult] = Field(default_factory=list)
    patch: PatchReport
    validation: SyntheticRoundTripReport
