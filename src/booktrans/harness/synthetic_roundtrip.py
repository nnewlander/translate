"""M4 synthetic non-identity candidate flow, intentionally not a Translator."""

from dataclasses import dataclass
from pathlib import Path

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ir import BlockIR, CoverageLedger, DocumentIR
from booktrans.domain.models import Diagnostic, SourceSnapshot
from booktrans.domain.roundtrip import PatchReport
from booktrans.domain.synthetic import (
    CapabilityReport,
    CandidateQAResult,
    SyntheticCandidate,
    SyntheticRoundTripReport,
)
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.package_preservation import verify_package_preservation
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.capability import NonIdentityCapabilityAudit
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.patcher import PatchOperation, apply_patch_operations


@dataclass(frozen=True)
class SyntheticRoundTripResult:
    source_document: DocumentIR
    source_coverage: CoverageLedger
    candidate_document: DocumentIR
    candidate_coverage: CoverageLedger
    capability: CapabilityReport
    candidates: list[SyntheticCandidate]
    qa_results: list[CandidateQAResult]
    patch_report: PatchReport
    validation_report: SyntheticRoundTripReport
    candidate_path: Path


class SyntheticRoundTrip:
    """Generate deterministic non-identity text only for audited simple blocks."""

    def run(self, source_path: Path, candidate_path: Path) -> SyntheticRoundTripResult:
        before = fingerprint(source_path)
        source = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
        extractor = DocumentExtractor()
        source_document, source_uncertainty, _ = extractor.extract(source_path, source)
        source_coverage = build_coverage_ledger(source_document, source_uncertainty, source_integrity_pass=True)
        capability = NonIdentityCapabilityAudit().audit(source_path, source_document)
        source_blocks = {block.unit_uid: block for block in source_document.blocks}
        known_ids = set(source_blocks)
        candidates = [
            SyntheticCandidate(
                unit_uid=record.unit_uid,
                display_id=record.display_id,
                source_text=source_blocks[record.unit_uid].source_plain_text,
                target_text=_synthetic_target(source_blocks[record.unit_uid].source_plain_text),
            )
            for record in capability.records if record.status == "SAFE_SIMPLE"
        ]
        qa = DeterministicCandidateQA()
        qa_results = [qa.validate(candidate, known_unit_ids=known_ids) for candidate in candidates]
        passed_ids = {result.unit_uid for result in qa_results if result.passed}
        for candidate in candidates:
            if candidate.unit_uid not in passed_ids:
                candidate.status = "rejected"
        operations, planned_spans = _projection_operations(candidates, source_blocks, passed_ids)
        patch_report = apply_patch_operations(
            source_path, candidate_path, operations,
            planned_blocks=len(passed_ids), planned_spans=planned_spans,
        )
        after = fingerprint(source_path)
        assert_unchanged(before, after, source_path)
        preservation = verify_package_preservation(
            source_path, candidate_path, patch_report.touched_ooxml_parts, require_touched_xml_equivalence=False,
        )
        candidate_fingerprint = fingerprint(candidate_path)
        candidate_snapshot = SourceSnapshot(
            path=str(candidate_path), sha256=candidate_fingerprint.sha256,
            size_bytes=candidate_fingerprint.size_bytes, mtime_utc=candidate_fingerprint.mtime_utc,
        )
        candidate_document, candidate_uncertainty, _ = extractor.extract(candidate_path, candidate_snapshot)
        candidate_coverage = build_coverage_ledger(candidate_document, candidate_uncertainty, source_integrity_pass=True)
        expected = {candidate.unit_uid: candidate.target_text for candidate in candidates if candidate.unit_uid in passed_ids}
        mismatches = _expected_block_mismatches(source_document, candidate_document, expected)
        blockers = list(patch_report.resolution_failures)
        for result in qa_results:
            blockers.extend(result.diagnostics)
        if not preservation.passed:
            blockers.append(Diagnostic(code="PACKAGE_PRESERVATION_FAILED", severity="blocker", message="Synthetic candidate changed protected package payload.", part=None))
        if not source_coverage.coverage_complete:
            blockers.append(Diagnostic(code="SOURCE_COVERAGE_INCOMPLETE", severity="blocker", message="M4 cannot claim PASS when source coverage is incomplete."))
        if not candidate_coverage.coverage_complete:
            blockers.append(Diagnostic(code="CANDIDATE_COVERAGE_INCOMPLETE", severity="blocker", message="Candidate coverage is incomplete after synthetic write."))
        if mismatches:
            blockers.append(Diagnostic(code="SYNTHETIC_REEXTRACTION_MISMATCH", severity="blocker", message="Candidate text does not match approved synthetic targets or unchanged complex blocks.", detail=f"mismatches={len(mismatches)}"))
        report = SyntheticRoundTripReport(
            source_sha256=before.sha256,
            candidate_sha256=candidate_fingerprint.sha256,
            source_integrity_pass=True,
            source_coverage_complete=source_coverage.coverage_complete,
            candidate_coverage_complete=candidate_coverage.coverage_complete,
            expected_block_mismatch_count=len(mismatches),
            expected_block_mismatches=mismatches,
            preservation=preservation,
            blockers=blockers,
        )
        return SyntheticRoundTripResult(
            source_document=source_document, source_coverage=source_coverage,
            candidate_document=candidate_document, candidate_coverage=candidate_coverage,
            capability=capability, candidates=candidates, qa_results=qa_results,
            patch_report=patch_report, validation_report=report, candidate_path=candidate_path,
        )


def _synthetic_target(source_text: str) -> str:
    """Predictable Unicode-containing non-translation used solely for M4 writes."""
    return f"【测试译文】{source_text}"


def _projection_operations(
    candidates: list[SyntheticCandidate],
    blocks: dict[str, BlockIR],
    passed_ids: set[str],
) -> tuple[list[PatchOperation], int]:
    operations: list[PatchOperation] = []
    planned_spans = 0
    for candidate in candidates:
        if candidate.unit_uid not in passed_ids:
            continue
        block = blocks[candidate.unit_uid]
        spans = [span for span in block.spans if span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE]
        # Capability audit permits exactly one non-empty text span.  Its first
        # node receives target; any merged source nodes become empty but remain
        # structurally intact.
        span = spans[0]
        planned_spans += 1
        operations.append(PatchOperation(span.locator, block.unit_uid, span.span_uid, candidate.target_text))
        operations.extend(PatchOperation(locator, block.unit_uid, span.span_uid, "") for locator in span.source_run_locators)
    return operations, planned_spans


def _expected_block_mismatches(
    source: DocumentIR,
    candidate: DocumentIR,
    targets: dict[str, str],
) -> list[Diagnostic]:
    source_by_key = {_key(block): block for block in source.blocks}
    candidate_by_key = {_key(block): block for block in candidate.blocks}
    mismatches: list[Diagnostic] = []
    for key in sorted(source_by_key.keys() | candidate_by_key.keys()):
        source_block = source_by_key.get(key)
        candidate_block = candidate_by_key.get(key)
        if source_block is None or candidate_block is None:
            mismatches.append(Diagnostic(code="BLOCK_LOCATOR_SET_CHANGED", severity="blocker", message="Block locator is missing from one synthetic round-trip side.", part=key[1], detail=str(key)))
            continue
        expected = targets.get(source_block.unit_uid, source_block.source_plain_text)
        if candidate_block.source_plain_text != expected:
            mismatches.append(Diagnostic(
                code="EXPECTED_BLOCK_TEXT_MISMATCH", severity="blocker",
                message="Re-extracted block text differs from the approved synthetic target or required unchanged source text.",
                part=key[1], detail=f"locator={key[2]}; expected={expected[:100]!r}; actual={candidate_block.source_plain_text[:100]!r}",
            ))
    return mismatches


def _key(block: BlockIR) -> tuple[str, str, str, str]:
    return block.story, block.part_name, block.locator.xml_path, block.block_type
