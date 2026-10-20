"""M4.7 synthetic structured-target proof; it is not a translator."""

from dataclasses import dataclass
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.calibration import (
    ExplicitNumericRepresentationEquivalence,
    VerifiedFootnoteNumericBinding,
)
from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ir import CoverageLedger, DocumentIR
from booktrans.domain.models import Diagnostic, SourceSnapshot
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import (
    AnchorQAResult,
    StructuredTargetSegment,
    TaggedCapabilityReport,
    TaggedRoundTripReport,
    TaggedSyntheticCandidate,
    StructuralCapabilityReport,
    TranslationView,
)
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.package_preservation import verify_package_preservation
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part
from booktrans.ooxml.structural_islands import analyze_structural_capability, inspect_paragraph
from booktrans.ooxml.tagged_projection import TaggedProjection, apply_tagged_projections
from booktrans.ooxml.translation_view import TaggedCapabilityAudit, TranslationViewBuilder


@dataclass(frozen=True)
class TaggedRoundTripResult:
    source_document: DocumentIR
    source_coverage: CoverageLedger
    candidate_document: DocumentIR
    candidate_coverage: CoverageLedger
    capability: TaggedCapabilityReport
    structural_capability: StructuralCapabilityReport
    views: list[TranslationView]
    candidates: list[TaggedSyntheticCandidate]
    qa_results: list[AnchorQAResult]
    patch_report: object
    validation_report: TaggedRoundTripReport
    candidate_path: Path


class FormatAnchorQA:
    """Small deterministic closed-world checks for anchor references and output."""

    def validate(
        self,
        view: TranslationView,
        candidate: TaggedSyntheticCandidate,
        known_unit_ids: set[str],
        *,
        footnote_reference_numbers: list[str] | None = None,
        verified_footnote_numeric_bindings: list[VerifiedFootnoteNumericBinding] | None = None,
        explicit_numeric_representation_equivalences: list[ExplicitNumericRepresentationEquivalence] | None = None,
    ) -> AnchorQAResult:
        diagnostics: list[Diagnostic] = []
        if candidate.unit_uid not in known_unit_ids:
            diagnostics.append(_failure("UNKNOWN_UNIT_ID", "Tagged candidate does not refer to a known BlockIR unit."))
        if candidate.source_text and not candidate.target_text:
            diagnostics.append(_failure("EMPTY_TARGET", "Source has translatable text but tagged target is empty."))
        if candidate.target_text != "".join(segment.text for segment in candidate.target_segments):
            diagnostics.append(_failure("TARGET_TEXT_SEGMENT_MISMATCH", "Tagged target_text is not the concatenation of its target segments."))
        expected_islands = [island.island_id for island in view.text_islands]
        actual_islands = _island_sequence(candidate.target_segments)
        if actual_islands != expected_islands:
            diagnostics.append(_failure("ISLAND_ORDER_OR_SET_MISMATCH", "Target must retain every editable text island in original order."))
        anchors = {anchor.anchor_id: anchor for anchor in view.anchors}
        seen: dict[str, int] = {}
        for segment in candidate.target_segments:
            if segment.kind == "plain":
                if segment.anchor_ref is not None:
                    diagnostics.append(_failure("PLAIN_SEGMENT_HAS_ANCHOR_REF", "A plain target segment cannot claim a format anchor."))
                continue
            if not segment.anchor_ref or segment.anchor_ref not in anchors:
                diagnostics.append(_failure("UNKNOWN_ANCHOR_REF", "Target references an anchor absent from its source TranslationView."))
                continue
            seen[segment.anchor_ref] = seen.get(segment.anchor_ref, 0) + 1
            anchor = anchors[segment.anchor_ref]
            if segment.kind == "protected":
                source_segment = next((item for item in view.segments if item.anchor_ref == segment.anchor_ref), None)
                if source_segment is None or segment.text != source_segment.text:
                    diagnostics.append(_failure("PROTECTED_TEXT", "A protected anchor must retain its exact source text."))
            if segment.kind == "anchored" and anchor.protected:
                diagnostics.append(_failure("PROTECTED_ANCHOR_KIND_MISMATCH", "A protected anchor cannot be emitted as an editable anchored segment."))
        for anchor in view.anchors:
            count = seen.get(anchor.anchor_id, 0)
            if count == 0:
                diagnostics.append(_failure("MISSING_ANCHOR", f"Required anchor is absent: {anchor.anchor_id}"))
            elif count > 1:
                code = "DUPLICATED_PROTECTED_ANCHOR" if anchor.protected else "DUPLICATED_ANCHOR"
                diagnostics.append(_failure(code, f"Anchor occurs {count} times: {anchor.anchor_id}"))
        protected_text = [segment.text for segment in view.segments if segment.kind == "protected"]
        deterministic = DeterministicCandidateQA().validate(
            SyntheticCandidate(
                unit_uid=candidate.unit_uid, display_id=candidate.display_id,
                source_text=candidate.source_text, target_text=candidate.target_text,
            ),
            known_unit_ids=known_unit_ids,
            protected_text=protected_text,
            footnote_reference_numbers=footnote_reference_numbers,
            verified_footnote_numeric_bindings=verified_footnote_numeric_bindings,
            explicit_numeric_representation_equivalences=explicit_numeric_representation_equivalences,
            source_segments=view.segments,
            target_segments=candidate.target_segments,
        )
        diagnostics.extend(deterministic.diagnostics)
        return AnchorQAResult(unit_uid=candidate.unit_uid, passed=not diagnostics, diagnostics=diagnostics)

    def verify_projection(self, source_path: Path, candidate_path: Path, view: TranslationView, candidate: TaggedSyntheticCandidate) -> AnchorQAResult:
        """Verify one fixture-scale projection through the same batched path."""
        return self.verify_projections(source_path, candidate_path, [(view, candidate)])[0]

    def verify_projections(
        self,
        source_path: Path,
        candidate_path: Path,
        projections: list[tuple[TranslationView, TaggedSyntheticCandidate]],
    ) -> list[AnchorQAResult]:
        """Verify all real-book anchors with one pair of ZIP handles per run."""
        source_roots: dict[str, etree._Element] = {}
        candidate_roots: dict[str, etree._Element] = {}
        source_indexes: dict[str, RootLocatorIndex] = {}
        candidate_indexes: dict[str, RootLocatorIndex] = {}
        results: list[AnchorQAResult] = []
        with zipfile.ZipFile(source_path, "r") as source_package, zipfile.ZipFile(candidate_path, "r") as candidate_package:
            for view, candidate in projections:
                part = view.base_template_locator.part_name
                if part not in source_roots:
                    source_root = read_xml_part(source_package, part)
                    candidate_root = read_xml_part(candidate_package, part)
                    if source_root is None or candidate_root is None:
                        results.append(AnchorQAResult(unit_uid=view.unit_uid, passed=False, diagnostics=[_failure("ANCHOR_PART_NOT_XML", "Anchor part is not readable XML in source or candidate.")]))
                        continue
                    source_roots[part] = source_root
                    candidate_roots[part] = candidate_root
                    source_indexes[part] = RootLocatorIndex(source_root)
                    candidate_indexes[part] = RootLocatorIndex(candidate_root)
                results.append(_verify_one_projection(
                    view, candidate, source_roots[part], candidate_roots[part], source_indexes[part], candidate_indexes[part],
                ))
        return results


def _verify_one_projection(
    view: TranslationView,
    candidate: TaggedSyntheticCandidate,
    source_root: etree._Element,
    candidate_root: etree._Element,
    source_index: RootLocatorIndex,
    candidate_index: RootLocatorIndex,
) -> AnchorQAResult:
    diagnostics: list[Diagnostic] = []
    source_paragraph = resolve_locator_in_root(source_root, _paragraph_locator(view), index=source_index)
    candidate_paragraph = resolve_locator_in_root(candidate_root, _paragraph_locator(view), index=candidate_index)
    source_layout = inspect_paragraph(source_paragraph, view.unit_uid)
    candidate_layout = inspect_paragraph(candidate_paragraph, view.unit_uid)
    if _anchor_signature(source_layout) != _anchor_signature(candidate_layout):
        diagnostics.append(_failure("STRUCTURAL_ANCHOR_SEQUENCE_CHANGED", "Structural anchor count, identity, type, or relative order changed."))
    candidate_nodes = [
        node for node in candidate_paragraph.iter()
        if node.tag == qn("w", "t") and _nearest_paragraph(node) is candidate_paragraph
    ]
    if len(candidate_nodes) != len(candidate.target_segments):
        diagnostics.append(_failure("ANCHOR_OUTPUT_NODE_COUNT", "Projected paragraph has a different text-node count than approved target segments."))
        return AnchorQAResult(unit_uid=view.unit_uid, passed=False, diagnostics=diagnostics)
    anchors = {anchor.anchor_id: anchor for anchor in view.anchors}
    for target, output_node in zip(candidate.target_segments, candidate_nodes, strict=True):
        if (output_node.text or "") != target.text:
            diagnostics.append(_failure("ANCHOR_OUTPUT_TEXT_MISMATCH", "Projected text node differs from approved tagged target text."))
            continue
        if target.kind != "protected" and _contains_cjk(target.text) and _has_micro_layout(output_node):
            diagnostics.append(_failure("CJK_MICRO_LAYOUT_COPIED", "Newly generated CJK text retained source w:spacing or w:w."))
        if target.kind == "plain":
            continue
        anchor = anchors.get(target.anchor_ref or "")
        if anchor is None:
            diagnostics.append(_failure("UNKNOWN_ANCHOR_REF", "Projected target contains an unknown anchor reference."))
            continue
        source_node = resolve_locator_in_root(source_root, anchor.template_locator, index=source_index)
        if _run_properties_bytes(source_node, strip_micro_layout=target.kind != "protected" and _contains_cjk(target.text)) != _run_properties_bytes(output_node):
            diagnostics.append(_failure("ANCHOR_STYLE_TEMPLATE_MISMATCH", f"Projected anchor did not retain source rPr template: {anchor.anchor_id}"))
        expected_rid = anchor.container_context.get("hyperlink_rid")
        if expected_rid:
            hyperlink = _ancestor(output_node, qn("w", "hyperlink"))
            if hyperlink is None or hyperlink.get(qn("r", "id")) != expected_rid:
                diagnostics.append(_failure("HYPERLINK_RELATIONSHIP_CHANGED", f"Projected hyperlink anchor lost relationship id {expected_rid}."))
    return AnchorQAResult(unit_uid=view.unit_uid, passed=not diagnostics, diagnostics=diagnostics)


class TaggedSyntheticRoundTrip:
    """Structured synthetic targets for blocks proven safe by the thin projector."""

    def run(self, source_path: Path, candidate_path: Path) -> TaggedRoundTripResult:
        before = fingerprint(source_path)
        snapshot = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
        extractor = DocumentExtractor()
        source_document, source_uncertainty, _ = extractor.extract(source_path, snapshot)
        source_coverage = build_coverage_ledger(source_document, source_uncertainty, source_integrity_pass=True)
        views = TranslationViewBuilder().build(source_path, source_document)
        capability = TaggedCapabilityAudit().audit(source_path, source_document, views)
        structural_capability = analyze_structural_capability(
            source_path,
            [block for block in source_document.blocks if block.unit_uid in {view.unit_uid for view in views}],
        )
        views_by_uid = {view.unit_uid: view for view in views}
        known_ids = {block.unit_uid for block in source_document.blocks}
        candidates = [_synthetic_candidate(views_by_uid[record.unit_uid]) for record in capability.records if record.status == "TAGGED_SUPPORTED"]
        qa = FormatAnchorQA()
        qa_results = [qa.validate(views_by_uid[candidate.unit_uid], candidate, known_ids) for candidate in candidates]
        passed_ids = {result.unit_uid for result in qa_results if result.passed}
        for candidate in candidates:
            if candidate.unit_uid not in passed_ids:
                candidate.status = "rejected"
        projections = [
            TaggedProjection(view=views_by_uid[candidate.unit_uid], candidate=candidate)
            for candidate in candidates if candidate.unit_uid in passed_ids
        ]
        patch_report = apply_tagged_projections(source_path, candidate_path, projections)
        assert_unchanged(before, fingerprint(source_path), source_path)
        preservation = verify_package_preservation(
            source_path, candidate_path, patch_report.touched_ooxml_parts, require_touched_xml_equivalence=False,
        )
        candidate_fingerprint = fingerprint(candidate_path)
        candidate_snapshot = SourceSnapshot(path=str(candidate_path), sha256=candidate_fingerprint.sha256, size_bytes=candidate_fingerprint.size_bytes, mtime_utc=candidate_fingerprint.mtime_utc)
        candidate_document, candidate_uncertainty, _ = extractor.extract(candidate_path, candidate_snapshot)
        candidate_coverage = build_coverage_ledger(candidate_document, candidate_uncertainty, source_integrity_pass=True)
        expected = {candidate.unit_uid: candidate.target_text for candidate in candidates if candidate.unit_uid in passed_ids}
        mismatches = _expected_block_mismatches(source_document, candidate_document, expected)
        post_qa = qa.verify_projections(
            source_path,
            candidate_path,
            [(views_by_uid[candidate.unit_uid], candidate) for candidate in candidates if candidate.unit_uid in passed_ids],
        )
        qa_results.extend(post_qa)
        blockers = list(patch_report.resolution_failures)
        for result in qa_results:
            blockers.extend(result.diagnostics)
        if not preservation.passed:
            blockers.append(_failure("PACKAGE_PRESERVATION_FAILED", "Tagged candidate changed protected package payload."))
        if not source_coverage.coverage_complete:
            blockers.append(_failure("SOURCE_COVERAGE_INCOMPLETE", "M4.7 cannot claim PASS when source coverage is incomplete."))
        if not candidate_coverage.coverage_complete:
            blockers.append(_failure("CANDIDATE_COVERAGE_INCOMPLETE", "Candidate coverage is incomplete after tagged projection."))
        if mismatches:
            blockers.append(_failure("TAGGED_REEXTRACTION_MISMATCH", f"Candidate text differs from approved tagged target in {len(mismatches)} block(s)."))
        report = TaggedRoundTripReport(
            source_sha256=before.sha256, candidate_sha256=candidate_fingerprint.sha256,
        source_integrity_pass=True, source_coverage_complete=source_coverage.coverage_complete,
            candidate_coverage_complete=candidate_coverage.coverage_complete,
            expected_block_mismatch_count=len(mismatches), expected_block_mismatches=mismatches,
            anchor_qa_failure_count=sum(not result.passed for result in qa_results),
            structural_anchor_count=sum(len(view.structural_anchors) for view in views if view.unit_uid in passed_ids),
            structural_anchor_qa_failure_count=sum(
                any(diagnostic.code.startswith("STRUCTURAL_ANCHOR") for diagnostic in result.diagnostics)
                for result in post_qa
            ),
            preservation=preservation,
            blockers=blockers,
        )
        return TaggedRoundTripResult(
            source_document=source_document, source_coverage=source_coverage,
            candidate_document=candidate_document, candidate_coverage=candidate_coverage,
            capability=capability, structural_capability=structural_capability,
            views=views, candidates=candidates, qa_results=qa_results,
            patch_report=patch_report, validation_report=report, candidate_path=candidate_path,
        )


def _synthetic_candidate(view: TranslationView) -> TaggedSyntheticCandidate:
    # Prefix only once at the block boundary.  Prefixing every physical/logical
    # segment can split a URL or number token that M2 correctly sees only when
    # the complete block text is concatenated.
    editable_index = next(index for index, segment in enumerate(view.segments) if segment.kind != "protected")
    target_segments = []
    for index, segment in enumerate(view.segments):
        text = segment.text
        if index == editable_index:
            text = f"【测试】{text}"
        target_segments.append(StructuredTargetSegment(
            text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref,
        ))
    return TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=target_segments, target_text="".join(segment.text for segment in target_segments),
    )


def _expected_block_mismatches(source: DocumentIR, candidate: DocumentIR, targets: dict[str, str]) -> list[Diagnostic]:
    source_by_key = {_block_key(block): block for block in source.blocks}
    candidate_by_key = {_block_key(block): block for block in candidate.blocks}
    mismatches: list[Diagnostic] = []
    for key in sorted(source_by_key.keys() | candidate_by_key.keys()):
        source_block = source_by_key.get(key)
        candidate_block = candidate_by_key.get(key)
        if source_block is None or candidate_block is None:
            mismatches.append(_failure("BLOCK_LOCATOR_SET_CHANGED", f"Block locator missing from source/candidate: {key}"))
            continue
        expected = targets.get(source_block.unit_uid, source_block.source_plain_text)
        if candidate_block.source_plain_text != expected:
            mismatches.append(Diagnostic(
                code="EXPECTED_BLOCK_TEXT_MISMATCH", severity="blocker",
                message="Re-extracted block text differs from approved tagged target or unchanged source text.",
                part=key[1], detail=f"locator={key[2]}; expected={expected[:100]!r}; actual={candidate_block.source_plain_text[:100]!r}",
            ))
    return mismatches


def _block_key(block) -> tuple[str, str, str, str]:
    return block.story, block.part_name, block.locator.xml_path, block.block_type


def _paragraph_locator(view: TranslationView):
    template = view.base_template_locator
    return template.model_copy(update={"xml_path": template.container_path})


def _run_properties_bytes(node: etree._Element, *, strip_micro_layout: bool = False) -> bytes:
    run = _ancestor(node, qn("w", "r"))
    properties = run.find("w:rPr", namespaces=NS) if run is not None else None
    if properties is None:
        return b""
    copied = etree.fromstring(etree.tostring(properties))
    if strip_micro_layout:
        for child in list(copied):
            if child.tag in {qn("w", "spacing"), qn("w", "w")}:
                copied.remove(child)
    return etree.tostring(copied, method="c14n")


def _nearest_paragraph(node: etree._Element) -> etree._Element | None:
    return _ancestor(node, qn("w", "p"))


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _failure(code: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="blocker", message=message)


def _island_sequence(segments) -> list[str]:
    result: list[str] = []
    for segment in segments:
        if not result or result[-1] != segment.island_ref:
            result.append(segment.island_ref)
    return result


def _anchor_signature(layout) -> list[tuple[str, str, int]]:
    return [(anchor.kind, anchor.fingerprint, anchor.relative_position) for anchor in layout.anchors]


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff" for character in text)


def _has_micro_layout(node: etree._Element) -> bool:
    run = _ancestor(node, qn("w", "r"))
    properties = run.find("w:rPr", namespaces=NS) if run is not None else None
    return properties is not None and any(child.tag in {qn("w", "spacing"), qn("w", "w")} for child in properties)
