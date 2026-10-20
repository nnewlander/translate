"""Read-only M4.6 evidence audit for format semantics and fragment root causes."""

from collections import Counter, defaultdict
from pathlib import Path
import re
import zipfile

from lxml import etree

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.format_fragment_audit import (
    DirectPropertyEvidence,
    FormatFragmentDecisionAuditReport,
    FragmentBucket,
    FragmentDecisionSample,
    PropertySample,
)
from booktrans.domain.ir import BlockIR, DocumentIR, SpanIR
from booktrans.domain.models import SourceSnapshot
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.capability import NonIdentityCapabilityAudit
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.format_unit_audit import (
    FormatTranslationUnitAudit,
    NUMBER_OR_PAGE,
    WORD,
    _attrs,
    _is_fragment_candidate,
    _neighborhoods,
    _relevant_signature,
    _signature_differences,
)
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part


PHONE_OR_CONTACT = re.compile(r"(?:\d[\d() .-]{6,}\d|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://)")
PAGE_HEADER = re.compile(r"^\s*(?:[ivxlcdm]+|\d+)\s*(?:\||[-—])\s*\S+", re.IGNORECASE)
KNOWN_RPR = {
    "b", "bCs", "i", "iCs", "u", "rFonts", "sz", "szCs", "color", "highlight", "rStyle", "lang", "noProof",
}
CLEARLY_VISUAL_OR_SEMANTIC = {
    "vertAlign", "strike", "dstrike", "caps", "smallCaps", "position", "spacing", "kern", "shd", "outline",
    "shadow", "emboss", "imprint", "vanish", "webHidden", "effect", "border", "fitText", "rtl", "w",
}
LIKELY_TECHNICAL_OR_NON_SEMANTIC = {"dirty", "noProof", "lang", "cs"}


class FormatFragmentDecisionAudit:
    """Collect facts needed to decide whether a small next writeback layer pays rent."""

    def run(self, source_path: Path) -> FormatFragmentDecisionAuditReport:
        before = fingerprint(source_path)
        source = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
        document, uncertainty, _ = DocumentExtractor().extract(source_path, source)
        ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
        capability = NonIdentityCapabilityAudit().audit(source_path, document)
        report = self._analyze(source_path, document, ledger.coverage_complete, capability)
        assert_unchanged(before, fingerprint(source_path), source_path)
        return report

    def _analyze(self, source_path: Path, document: DocumentIR, coverage_complete: bool, capability) -> FormatFragmentDecisionAuditReport:
        roots: dict[str, etree._Element] = {}
        indexes: dict[str, RootLocatorIndex] = {}
        by_uid = {block.unit_uid: block for block in document.blocks}
        property_blocks: dict[str, set[str]] = defaultdict(set)
        property_cooccurrences: dict[str, Counter[str]] = defaultdict(Counter)
        property_samples: dict[str, list[PropertySample]] = defaultdict(list)
        region_counts: Counter[str] = Counter()
        other_direct_blocks = 0
        simple_inline = hyperlinks = protected = multi_factor = 0
        m45_classifier = FormatTranslationUnitAudit()
        with zipfile.ZipFile(source_path, "r") as package:
            for record in capability.records:
                block = by_uid[record.unit_uid]
                spans = _translatable_spans(block)
                raw = [_raw_span(package, roots, indexes, span) for span in spans]
                regions, differences = _logical_regions(raw)
                region_counts[_region_bucket(regions)] += 1
                has_hyperlink = any("hyperlink_rid" in span.container_context for span in spans)
                has_protected = any(
                    span.kind == "text" and span.text and span.classification != CoverageClassification.TRANSLATABLE
                    for span in block.spans
                )
                hyperlinks += int(has_hyperlink)
                protected += int(has_protected)
                factors = set()
                if regions > 1:
                    factors.add("format_regions")
                if has_hyperlink:
                    factors.add("hyperlink")
                if has_protected:
                    factors.add("protected")
                if len(factors) >= 2:
                    multi_factor += 1
                if regions > 1 and not has_hyperlink and not has_protected and differences and differences <= {
                    "BOLD_DIFFERENCE", "ITALIC_DIFFERENCE", "UNDERLINE_DIFFERENCE",
                }:
                    simple_inline += 1
                # Reuse M4.5's exact primary-category function. This prevents
                # M4.6 from widening OTHER when it coexists with color/bold or
                # a container difference.
                if m45_classifier._format_category(package, roots, indexes, block) != "OTHER_DIRECT_FORMAT_DIFFERENCE":
                    continue
                other_direct_blocks += 1
                for property_name in _other_property_differences(raw):
                    property_blocks[property_name].add(block.unit_uid)
                    for difference in differences:
                        if difference != "OTHER_DIRECT_FORMAT_DIFFERENCE":
                            property_cooccurrences[property_name][difference] += 1
                    if len(property_samples[property_name]) < 10:
                        property_samples[property_name].append(PropertySample(
                            display_id=block.display_id, part_name=block.part_name, locator=block.locator,
                            source_text=block.source_plain_text[:200], cooccurring_differences=sorted(differences),
                        ))
            fragment_buckets, root_causes = _fragment_decisions(package, roots, indexes, document)
        evidence = [
            DirectPropertyEvidence(
                property_name=name,
                evidence_category=_property_evidence_category(name),
                block_count=len(property_blocks[name]),
                cooccurring_difference_counts=dict(sorted(property_cooccurrences[name].items())),
                samples=property_samples[name],
            )
            for name in sorted(property_blocks, key=lambda item: (-len(property_blocks[item]), item))
        ]
        total_fragments = sum(bucket.count for bucket in fragment_buckets.values())
        facts, inferences, recommendations = _decision_text(
            capability.total_translatable_blocks, len(property_blocks), region_counts, fragment_buckets, root_causes,
        )
        return FormatFragmentDecisionAuditReport(
            source_sha256=document.document_sha256, source_integrity_pass=True, coverage_complete=coverage_complete,
            other_direct_format_blocks=other_direct_blocks,
            direct_property_evidence=evidence,
            logical_region_distribution=dict(region_counts), simple_inline_emphasis_blocks=simple_inline,
            hyperlink_blocks=hyperlinks, protected_content_blocks=protected, multi_complex_factor_blocks=multi_factor,
            fragment_candidates_total=total_fragments,
            likely_true_prose_fragment=fragment_buckets["LIKELY_TRUE_PROSE_FRAGMENT"],
            likely_legitimate_standalone=fragment_buckets["LIKELY_LEGITIMATE_STANDALONE"],
            uncertain=fragment_buckets["UNCERTAIN"], true_fragment_root_causes=dict(sorted(root_causes.items())),
            facts=facts, inferences=inferences, recommendations=recommendations,
        )


def _translatable_spans(block: BlockIR) -> list[SpanIR]:
    return [span for span in block.spans if span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE]


def _raw_span(package, roots, indexes, span: SpanIR) -> tuple[tuple[tuple[str, str], ...], dict[str, tuple], dict[str, tuple]]:
    locator = span.locator
    root = roots.get(locator.part_name)
    if root is None:
        root = read_xml_part(package, locator.part_name)
        if root is None:
            raise ValueError(f"AUDIT_LOCATOR_PART_NOT_XML: {locator.part_name}")
        roots[locator.part_name] = root
        indexes[locator.part_name] = RootLocatorIndex(root)
    node = resolve_locator_in_root(root, locator, index=indexes[locator.part_name])
    run = _ancestor(node, qn("w", "r"))
    properties = run.find("w:rPr", namespaces=NS) if run is not None else None
    context = tuple(sorted((key, value) for key, value in span.container_context.items() if key != "merge_key"))
    return context, _relevant_signature(properties), _other_properties(properties)


def _other_properties(properties: etree._Element | None) -> dict[str, tuple]:
    if properties is None:
        return {}
    return {
        f"w:{etree.QName(item).localname}": _attrs(item)
        for item in properties
        if etree.QName(item).localname not in KNOWN_RPR
    }


def _logical_regions(raw: list[tuple[tuple[tuple[str, str], ...], dict[str, tuple], dict[str, tuple]]]) -> tuple[int, set[str]]:
    if not raw:
        return 0, set()
    keys = [(context, tuple(sorted(signature.items()))) for context, signature, _ in raw]
    regions = 1 + sum(keys[index] != keys[index - 1] for index in range(1, len(keys)))
    differences = set(_signature_differences([signature for _, signature, _ in raw]))
    if len({context for context, _, _ in raw}) > 1:
        differences.add("CONTAINER_DIFFERENCE")
    if any(dict(context).get("hyperlink_rid") for context, _, _ in raw):
        differences.add("HYPERLINK_CONTAINER_DIFFERENCE")
    return regions, differences


def _other_property_differences(raw: list[tuple[tuple[tuple[str, str], ...], dict[str, tuple], dict[str, tuple]]]) -> list[str]:
    properties = {name for _, _, values in raw for name in values}
    result = []
    for name in properties:
        values = {values.get(name, ()) for _, _, values in raw}
        if len(values) > 1:
            result.append(name)
    return sorted(result)


def _region_bucket(regions: int) -> str:
    if regions <= 1:
        return "1"
    if regions == 2:
        return "2"
    if regions == 3:
        return "3"
    if regions <= 5:
        return "4-5"
    return "6+"


def _property_evidence_category(property_name: str) -> str:
    local = property_name.removeprefix("w:")
    if local in CLEARLY_VISUAL_OR_SEMANTIC:
        return "CLEARLY_VISUAL_OR_SEMANTIC"
    if local in LIKELY_TECHNICAL_OR_NON_SEMANTIC:
        return "LIKELY_TECHNICAL_OR_NON_SEMANTIC"
    return "UNKNOWN"


def _fragment_decisions(package, roots, indexes, document: DocumentIR) -> tuple[dict[str, FragmentBucket], Counter[str]]:
    neighborhoods = _neighborhoods(document)
    windows = _neighbor_windows(document)
    buckets: dict[str, list[FragmentDecisionSample]] = defaultdict(list)
    counts: Counter[str] = Counter()
    root_causes: Counter[str] = Counter()
    for block in document.blocks:
        if not _is_translatable_block(block) or not _is_fragment_candidate(block, block.source_plain_text, len(WORD.findall(block.source_plain_text))):
            continue
        paragraph, evidence = _paragraph_evidence(package, roots, indexes, block)
        previous, following = neighborhoods[block.unit_uid]
        previous2, next2 = windows[block.unit_uid]
        decision, reason = _classify_fragment(block.source_plain_text, previous, following, previous2, next2, evidence)
        counts[decision] += 1
        if decision == "LIKELY_TRUE_PROSE_FRAGMENT":
            root_causes[_fragment_root_cause(evidence)] += 1
        if len(buckets[decision]) < 20:
            buckets[decision].append(FragmentDecisionSample(
                display_id=block.display_id, story=block.story, part_name=block.part_name, locator=block.locator,
                source_text=block.source_plain_text, previous_text=previous, next_text=following,
                structural_evidence=evidence, classification_reason=reason,
            ))
    total = sum(counts.values())
    return {
        label: FragmentBucket(count=counts[label], percentage=round((counts[label] / total * 100) if total else 0.0, 2), samples=buckets[label])
        for label in ("LIKELY_TRUE_PROSE_FRAGMENT", "LIKELY_LEGITIMATE_STANDALONE", "UNCERTAIN")
    }, root_causes


def _paragraph_evidence(package, roots, indexes, block: BlockIR) -> tuple[etree._Element, list[str]]:
    locator = block.locator
    root = roots.get(locator.part_name)
    if root is None:
        root = read_xml_part(package, locator.part_name)
        if root is None:
            raise ValueError(f"AUDIT_LOCATOR_PART_NOT_XML: {locator.part_name}")
        roots[locator.part_name] = root
        indexes[locator.part_name] = RootLocatorIndex(root)
    paragraph = resolve_locator_in_root(root, locator, index=indexes[locator.part_name])
    evidence = ["ORDINARY_BODY_PARAGRAPH"]
    if paragraph.find("w:pPr/w:numPr", namespaces=NS) is not None:
        evidence.append("LIST_PARAGRAPH")
    style = paragraph.find("w:pPr/w:pStyle", namespaces=NS)
    if style is not None:
        evidence.append(f"PARAGRAPH_STYLE={style.get(qn('w', 'val'), '')}")
    checks = (
        (qn("w", "tc"), "TABLE_CELL"), (qn("w", "txbxContent"), "TEXTBOX"),
        (qn("w", "sdt"), "CONTENT_CONTROL"), (qn("mc", "AlternateContent"), "ALTERNATE_CONTENT"),
    )
    for tag, label in checks:
        if _ancestor(paragraph, tag) is not None:
            evidence.append(label)
    if paragraph.xpath(".//w:hyperlink", namespaces=NS):
        evidence.append("HYPERLINK")
    return paragraph, evidence


def _classify_fragment(
    text: str,
    previous: str | None,
    following: str | None,
    previous2: str | None,
    next2: str | None,
    evidence: list[str],
) -> tuple[str, str]:
    compact = text.strip()
    if PHONE_OR_CONTACT.search(compact):
        return "LIKELY_LEGITIMATE_STANDALONE", "CONTACT_OR_PHONE_PATTERN"
    if NUMBER_OR_PAGE.fullmatch(compact) or PAGE_HEADER.match(compact):
        return "LIKELY_LEGITIMATE_STANDALONE", "PAGE_NUMBER_OR_HEADER_PATTERN"
    if "LIST_PARAGRAPH" in evidence or _looks_like_short_list_item(compact, previous, following, previous2, next2):
        return "LIKELY_LEGITIMATE_STANDALONE", "LIST_OR_REPEATED_SHORT_ITEM_PATTERN"
    prev_continues = bool(previous and previous.rstrip()[-1:] not in ".!?" and previous.rstrip())
    next_continues = bool(following and following.lstrip()[:1].islower())
    starts_lower = next((character for character in compact if character.isalpha()), "").islower()
    ends_mid = compact.rstrip().endswith((",", ";", ":")) or (compact.rstrip()[-1:].isalnum() if compact else False)
    if (starts_lower and prev_continues) or (ends_mid and next_continues):
        return "LIKELY_TRUE_PROSE_FRAGMENT", "ADJACENT_PARAGRAPHS_HAVE_CONTINUATION_PUNCTUATION_OR_CASE"
    return "UNCERTAIN", "SHORT_BODY_TEXT_WITHOUT_SUFFICIENT_CONTINUATION_OR_STANDALONE_EVIDENCE"


def _looks_like_short_list_item(
    text: str,
    previous: str | None,
    following: str | None,
    previous2: str | None,
    next2: str | None,
) -> bool:
    words = WORD.findall(text)
    if len(words) > 1:
        return False
    near = [item.strip() for item in (previous, following) if item]
    if len(near) == 2 and all(len(WORD.findall(item)) <= 1 or item == "..." for item in near):
        return True
    return (
        (previous is not None and previous2 is not None and len(WORD.findall(previous)) <= 1 and len(WORD.findall(previous2)) <= 1)
        or (following is not None and next2 is not None and len(WORD.findall(following)) <= 1 and len(WORD.findall(next2)) <= 1)
    )


def _neighbor_windows(document: DocumentIR) -> dict[str, tuple[str | None, str | None]]:
    grouped: dict[tuple[str, str], list[BlockIR]] = defaultdict(list)
    for block in document.blocks:
        grouped[(block.story, block.part_name)].append(block)
    result: dict[str, tuple[str | None, str | None]] = {}
    for blocks in grouped.values():
        for index, block in enumerate(blocks):
            before = [item.source_plain_text for item in reversed(blocks[:index]) if item.source_plain_text]
            after = [item.source_plain_text for item in blocks[index + 1:] if item.source_plain_text]
            result[block.unit_uid] = (before[1] if len(before) > 1 else None, after[1] if len(after) > 1 else None)
    return result


def _fragment_root_cause(evidence: list[str]) -> str:
    for label in ("ALTERNATE_CONTENT", "TEXTBOX", "CONTENT_CONTROL", "TABLE_CELL", "LIST_PARAGRAPH"):
        if label in evidence:
            return label
    return "ORDINARY_BODY_PARAGRAPH_SEPARATION"


def _is_translatable_block(block: BlockIR) -> bool:
    return any(span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE for span in block.spans)


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _decision_text(total_blocks, property_count, regions, buckets, root_causes) -> tuple[list[str], list[str], list[str]]:
    true_count = buckets["LIKELY_TRUE_PROSE_FRAGMENT"].count
    ordinary = root_causes.get("ORDINARY_BODY_PARAGRAPH_SEPARATION", 0)
    facts = [
        f"FACT: {property_count} distinct non-core w:rPr property names caused observed OTHER_DIRECT_FORMAT differences.",
        f"FACT: logical format-region distribution is {dict(regions)} across {total_blocks} translatable blocks.",
        f"FACT: {true_count} of {sum(bucket.count for bucket in buckets.values())} fragment candidates meet the conservative continuation test.",
    ]
    inferences = [
        "INFERENCE: format differences that remain after M4.5 are not proven to be harmless run fragmentation; any future normalization must be property-specific.",
        f"INFERENCE: {ordinary} likely prose fragments have no special OOXML container evidence and are recorded as ordinary paragraph separation.",
    ]
    recommendations = [
        "RECOMMENDATION: evidence supports considering a thin tagged format-anchor layer only for observed direct properties and hyperlink boundaries; it does not support a full style engine.",
        "RECOMMENDATION: do not introduce global automatic TranslationUnit grouping. If pursued, scope it first to the audited continuation signals/root causes and require explicit reviewable evidence.",
    ]
    return facts, inferences, recommendations
