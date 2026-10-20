"""M3 orchestration: identity writes plus package and logical round-trip checks."""

from dataclasses import dataclass
from pathlib import Path

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ir import CoverageLedger, DocumentIR
from booktrans.domain.models import Diagnostic, SourceSnapshot
from booktrans.domain.roundtrip import PatchReport, RoundTripReport
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.package_preservation import verify_package_preservation
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.patcher import PatchOperation, apply_patch_operations


@dataclass(frozen=True)
class IdentityRoundTripResult:
    source_document: DocumentIR
    source_coverage: CoverageLedger
    candidate_document: DocumentIR
    candidate_coverage: CoverageLedger
    patch_report: PatchReport
    roundtrip_report: RoundTripReport
    candidate_path: Path


class IdentityRoundTrip:
    """The bounded M3 workflow; it never writes the input package."""

    def run(self, source_path: Path, candidate_path: Path) -> IdentityRoundTripResult:
        before = fingerprint(source_path)
        source = SourceSnapshot(
            path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc,
        )
        extractor = DocumentExtractor()
        source_document, source_uncertainty, _ = extractor.extract(source_path, source)
        source_coverage = build_coverage_ledger(source_document, source_uncertainty, source_integrity_pass=True)
        operations, planned_blocks, planned_spans, skipped_protected, skipped_excluded = _identity_plan(source_document)
        patch_report = apply_patch_operations(
            source_path,
            candidate_path,
            operations,
            planned_blocks=planned_blocks,
            planned_spans=planned_spans,
            skipped_protected=skipped_protected,
            skipped_excluded=skipped_excluded,
        )
        after = fingerprint(source_path)
        assert_unchanged(before, after, source_path)

        preservation = verify_package_preservation(
            source_path, candidate_path, patch_report.touched_ooxml_parts, require_touched_xml_equivalence=True,
        )
        candidate_fingerprint = fingerprint(candidate_path)
        candidate = SourceSnapshot(
            path=str(candidate_path), sha256=candidate_fingerprint.sha256,
            size_bytes=candidate_fingerprint.size_bytes, mtime_utc=candidate_fingerprint.mtime_utc,
        )
        candidate_document, candidate_uncertainty, _ = extractor.extract(candidate_path, candidate)
        candidate_coverage = build_coverage_ledger(candidate_document, candidate_uncertainty, source_integrity_pass=True)
        mismatches = _logical_mismatches(source_document, source_coverage, candidate_document, candidate_coverage)
        blockers = list(patch_report.resolution_failures)
        if not preservation.passed:
            blockers.append(Diagnostic(
                code="PACKAGE_PRESERVATION_FAILED", severity="blocker",
                message="Candidate package did not preserve an M3 package invariant.", part=None,
            ))
        if not source_coverage.coverage_complete:
            blockers.append(Diagnostic(
                code="SOURCE_COVERAGE_INCOMPLETE", severity="blocker",
                message="Identity round-trip cannot claim M3 PASS when source coverage is incomplete.", part=None,
            ))
        if not candidate_coverage.coverage_complete:
            blockers.append(Diagnostic(
                code="CANDIDATE_COVERAGE_INCOMPLETE", severity="blocker",
                message="Candidate coverage is incomplete after the identity write.", part=None,
            ))
        if mismatches:
            blockers.append(Diagnostic(
                code="ROUNDTRIP_LOGICAL_MISMATCH", severity="blocker",
                message="Source and candidate M2 logical content differ after identity patching.", part=None,
                detail=f"mismatches={len(mismatches)}",
            ))
        report = RoundTripReport(
            source_sha256=before.sha256,
            candidate_sha256=candidate_fingerprint.sha256,
            source_integrity_pass=True,
            source_coverage_complete=source_coverage.coverage_complete,
            candidate_coverage_complete=candidate_coverage.coverage_complete,
            logical_mismatch_count=len(mismatches),
            logical_mismatches=mismatches,
            preservation=preservation,
            blockers=blockers,
        )
        return IdentityRoundTripResult(
            source_document=source_document,
            source_coverage=source_coverage,
            candidate_document=candidate_document,
            candidate_coverage=candidate_coverage,
            patch_report=patch_report,
            roundtrip_report=report,
            candidate_path=candidate_path,
        )


def _identity_plan(document: DocumentIR) -> tuple[list[PatchOperation], int, int, int, int]:
    operations: list[PatchOperation] = []
    seen: set[tuple[str, str]] = set()
    planned_blocks: set[str] = set()
    planned_spans = skipped_protected = skipped_excluded = 0
    for block in document.blocks:
        for span in block.spans:
            if span.kind != "text" or span.classification is None:
                continue
            if span.classification == CoverageClassification.TRANSLATABLE:
                planned_blocks.add(block.unit_uid)
                planned_spans += 1
                for locator in [span.locator, *span.source_run_locators]:
                    key = (locator.part_name, locator.xml_path)
                    if key not in seen:
                        seen.add(key)
                        # target_text=None deliberately means: assign this copied
                        # source node's existing text through the writer path.
                        operations.append(PatchOperation(locator, block.unit_uid, span.span_uid))
            elif span.classification == CoverageClassification.PROTECTED:
                skipped_protected += 1
            elif span.classification == CoverageClassification.EXCLUDED_BY_POLICY:
                skipped_excluded += 1
    return operations, len(planned_blocks), planned_spans, skipped_protected, skipped_excluded


def _logical_mismatches(
    source: DocumentIR,
    source_ledger: CoverageLedger,
    candidate: DocumentIR,
    candidate_ledger: CoverageLedger,
) -> list[Diagnostic]:
    mismatches: list[Diagnostic] = []
    source_blocks = {_block_key(block): block for block in source.blocks}
    candidate_blocks = {_block_key(block): block for block in candidate.blocks}
    for key in sorted(source_blocks.keys() | candidate_blocks.keys()):
        left, right = source_blocks.get(key), candidate_blocks.get(key)
        if left is None or right is None:
            mismatches.append(Diagnostic(code="BLOCK_LOCATOR_SET_CHANGED", severity="blocker", message="Block locator is missing from one round-trip side.", part=key[1], detail=str(key)))
        elif left.source_plain_text != right.source_plain_text or _span_signature(left) != _span_signature(right):
            mismatches.append(Diagnostic(code="BLOCK_LOGICAL_CONTENT_CHANGED", severity="blocker", message="Block text or span content changed after identity patching.", part=key[1], detail=key[2]))
    fields = (
        "coverage_complete", "visible_characters", "translatable_characters", "protected_characters", "excluded_characters",
        "unclassified_visible_text", "unknown_potential_visible_content", "visible_blocks",
    )
    for field in fields:
        if getattr(source_ledger, field) != getattr(candidate_ledger, field):
            mismatches.append(Diagnostic(code="COVERAGE_ACCOUNTING_CHANGED", severity="blocker", message="Coverage accounting differs after identity patching.", detail=f"{field}: {getattr(source_ledger, field)} != {getattr(candidate_ledger, field)}"))
    source_groups = {group.name: group.model_dump() for group in source_ledger.groups}
    candidate_groups = {group.name: group.model_dump() for group in candidate_ledger.groups}
    if source_groups != candidate_groups:
        mismatches.append(Diagnostic(code="COVERAGE_STORY_BREAKDOWN_CHANGED", severity="blocker", message="Coverage story/part breakdown differs after identity patching."))
    return mismatches


def _block_key(block) -> tuple[str, str, str, str]:
    return block.story, block.part_name, block.locator.xml_path, block.block_type


def _span_signature(block) -> tuple[tuple[str, str, str | None, str], ...]:
    return tuple(
        (span.kind, span.locator.xml_path, span.classification.value if span.classification else None, span.text)
        for span in block.spans
    )
