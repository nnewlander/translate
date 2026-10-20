"""M5.0 file-driven real translation calibration import, without model APIs."""

from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import ValidationError

from booktrans.domain.calibration import CalibrationRequest, CalibrationResponseStatus, TranslationResult
from booktrans.domain.models import Diagnostic, SourceSnapshot
from booktrans.domain.translation_view import AnchorQAResult, TaggedRoundTripReport, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.package_preservation import verify_package_preservation
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA, _expected_block_mismatches
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.tagged_projection import TaggedProjection, apply_tagged_projections
from booktrans.ooxml.translation_view import TaggedCapabilityAudit, TranslationViewBuilder


@dataclass(frozen=True)
class CalibrationImportResult:
    source_document: object
    candidate_document: object | None
    source_coverage: object
    candidate_coverage: object | None
    qa_results: list[AnchorQAResult]
    import_diagnostics: list[Diagnostic]
    patch_report: object | None
    validation_report: TaggedRoundTripReport | None
    response_statuses: list[CalibrationResponseStatus]
    review_required: list[CalibrationResponseStatus]
    response_count: int
    approved_count: int
    rejected_count: int


def load_requests(path: Path) -> list[CalibrationRequest]:
    return _load_jsonl(path, CalibrationRequest, "REQUEST_SCHEMA_INVALID")


def load_responses(path: Path) -> list[TranslationResult]:
    return _load_jsonl(path, TranslationResult, "RESPONSE_SCHEMA_INVALID")


def _load_jsonl(path: Path, model, code: str):
    records = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except (ValidationError, ValueError) as exc:
            errors.append(f"{code}: line={line_number}; detail={exc}")
    if errors:
        raise ValueError("\n".join(errors))
    return records


class CalibrationImporter:
    """Patch only response units that clear the existing closed-world QA."""

    def run(self, source_path: Path, request_path: Path, response_path: Path, candidate_path: Path) -> CalibrationImportResult:
        before = fingerprint(source_path)
        snapshot = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
        extractor = DocumentExtractor()
        source_document, uncertainty, _ = extractor.extract(source_path, snapshot)
        source_coverage = build_coverage_ledger(source_document, uncertainty, source_integrity_pass=True)
        requests = load_requests(request_path)
        responses = load_responses(response_path)
        diagnostics: list[Diagnostic] = []
        if any(request.source_sha256 != before.sha256 for request in requests):
            diagnostics.append(_failure("REQUEST_SOURCE_MISMATCH", "Calibration request source SHA-256 does not match input DOCX."))
        request_by_uid = {request.unit_uid: request for request in requests}
        if len(request_by_uid) != len(requests):
            diagnostics.append(_failure("DUPLICATE_REQUEST_UNIT", "Calibration request contains duplicate unit_uid records."))
        views = TranslationViewBuilder().build(source_path, source_document)
        views_by_uid = {view.unit_uid: view for view in views}
        capability = TaggedCapabilityAudit().audit(source_path, source_document, views)
        supported = {record.unit_uid for record in capability.records if record.status == "TAGGED_SUPPORTED"}
        known_ids = {block.unit_uid for block in source_document.blocks}
        qa = FormatAnchorQA()
        qa_results: list[AnchorQAResult] = []
        response_statuses: list[CalibrationResponseStatus] = []
        review_required: list[CalibrationResponseStatus] = []
        candidates: list[TaggedSyntheticCandidate] = []
        response_ids: set[str] = set()
        for response in responses:
            if response.unit_uid in response_ids:
                result = AnchorQAResult(unit_uid=response.unit_uid, passed=False, diagnostics=[_failure("DUPLICATE_RESPONSE_UNIT", "Response contains duplicate unit_uid.")])
                qa_results.append(result)
                response_statuses.append(_status(response.unit_uid, None, "FAIL", result.diagnostics))
                continue
            response_ids.add(response.unit_uid)
            if response.unit_uid not in request_by_uid:
                result = AnchorQAResult(unit_uid=response.unit_uid, passed=False, diagnostics=[_failure("UNKNOWN_UNIT_ID", "Response unit is not present in calibration request.")])
                qa_results.append(result)
                response_statuses.append(_status(response.unit_uid, None, "FAIL", result.diagnostics))
                continue
            request = request_by_uid[response.unit_uid]
            if response.translator_notes is not None:
                status = CalibrationResponseStatus(
                    unit_uid=response.unit_uid,
                    display_id=request.display_id,
                    status="REVIEW_REQUIRED",
                    translator_notes=response.translator_notes,
                    source_text=request.source_text,
                )
                response_statuses.append(status)
                review_required.append(status)
                continue
            if response.unit_uid not in supported or response.unit_uid not in views_by_uid:
                result = AnchorQAResult(unit_uid=response.unit_uid, passed=False, diagnostics=[_failure("UNIT_NOT_TAGGED_SUPPORTED", "Response unit is not supported by the M4.8 tagged projection contract.")])
                qa_results.append(result)
                response_statuses.append(_status(response.unit_uid, request, "FAIL", result.diagnostics))
                continue
            view = views_by_uid[response.unit_uid]
            candidate = TaggedSyntheticCandidate(
                unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
                target_segments=response.target_segments,
                target_text="".join(segment.text for segment in response.target_segments),
                generation_kind="calibration",
            )
            result = qa.validate(view, candidate, known_ids)
            qa_results.append(result)
            if result.passed:
                candidates.append(candidate)
                response_statuses.append(_status(response.unit_uid, request, "PASS", result.diagnostics))
            else:
                response_statuses.append(_status(response.unit_uid, request, "FAIL", result.diagnostics))
        for request in requests:
            if request.unit_uid not in response_ids:
                result = AnchorQAResult(unit_uid=request.unit_uid, passed=False, diagnostics=[_failure("MISSING_RESPONSE_UNIT", "Calibration request has no matching response record.")])
                qa_results.append(result)
                response_statuses.append(_status(request.unit_uid, request, "FAIL", result.diagnostics))
        if diagnostics:
            assert_unchanged(before, fingerprint(source_path), source_path)
            return CalibrationImportResult(source_document, None, source_coverage, None, qa_results, diagnostics, None, None, response_statuses, review_required, len(responses), 0, _fail_count(response_statuses))
        approved = {candidate.unit_uid for candidate in candidates}
        projections = [TaggedProjection(views_by_uid[candidate.unit_uid], candidate) for candidate in candidates]
        if not projections:
            assert_unchanged(before, fingerprint(source_path), source_path)
            return CalibrationImportResult(source_document, None, source_coverage, None, qa_results, [], None, None, response_statuses, review_required, len(responses), 0, _fail_count(response_statuses))
        patch_report = apply_tagged_projections(source_path, candidate_path, projections)
        assert_unchanged(before, fingerprint(source_path), source_path)
        preservation = verify_package_preservation(source_path, candidate_path, patch_report.touched_ooxml_parts, require_touched_xml_equivalence=False)
        candidate_snapshot = SourceSnapshot(path=str(candidate_path), sha256=fingerprint(candidate_path).sha256, size_bytes=candidate_path.stat().st_size, mtime_utc=None)
        candidate_document, candidate_uncertainty, _ = extractor.extract(candidate_path, candidate_snapshot)
        candidate_coverage = build_coverage_ledger(candidate_document, candidate_uncertainty, source_integrity_pass=True)
        expected = {candidate.unit_uid: candidate.target_text for candidate in candidates}
        mismatches = _expected_block_mismatches(source_document, candidate_document, expected)
        post = qa.verify_projections(source_path, candidate_path, [(views_by_uid[c.unit_uid], c) for c in candidates])
        qa_results.extend(post)
        # Rejected responses are intentionally absent from the candidate.  Their
        # diagnostics remain in the import report, but cannot make a correctly
        # projected PASS-only candidate fail its package/round-trip proof.
        blockers = [*patch_report.resolution_failures, *(item for result in post for item in result.diagnostics)]
        if mismatches: blockers.append(_failure("CALIBRATION_REEXTRACTION_MISMATCH", f"Candidate differs from approved target in {len(mismatches)} block(s)."))
        if not preservation.passed: blockers.append(_failure("PACKAGE_PRESERVATION_FAILED", "Candidate changed protected package payload."))
        if not candidate_coverage.coverage_complete: blockers.append(_failure("CANDIDATE_COVERAGE_INCOMPLETE", "Candidate coverage is incomplete."))
        report = TaggedRoundTripReport(
            source_sha256=before.sha256, candidate_sha256=fingerprint(candidate_path).sha256,
            source_integrity_pass=True, source_coverage_complete=source_coverage.coverage_complete,
            candidate_coverage_complete=candidate_coverage.coverage_complete,
            expected_block_mismatch_count=len(mismatches), expected_block_mismatches=mismatches,
            # This report proves the emitted candidate. Pre-patch rejected
            # responses remain auditable in calibration_qa_report.json but are
            # absent from the package, so only post-projection QA can fail it.
            anchor_qa_failure_count=sum(not result.passed for result in post),
            structural_anchor_count=sum(len(views_by_uid[c.unit_uid].structural_anchors) for c in candidates),
            structural_anchor_qa_failure_count=sum(any(d.code.startswith("STRUCTURAL_ANCHOR") for d in result.diagnostics) for result in post),
            preservation=preservation, blockers=blockers,
        )
        return CalibrationImportResult(
            source_document, candidate_document, source_coverage, candidate_coverage,
            qa_results, [], patch_report, report, response_statuses, review_required,
            len(responses), len(approved), _fail_count(response_statuses),
        )


def _failure(code: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="blocker", message=message)


def _status(
    unit_uid: str,
    request: CalibrationRequest | None,
    status: str,
    diagnostics: list[Diagnostic],
) -> CalibrationResponseStatus:
    return CalibrationResponseStatus(
        unit_uid=unit_uid,
        display_id=request.display_id if request else None,
        status=status,
        diagnostics=[f"{item.code}: {item.message}" for item in diagnostics],
        source_text=request.source_text if request else None,
    )


def _fail_count(statuses: list[CalibrationResponseStatus]) -> int:
    return sum(status.status == "FAIL" for status in statuses)
