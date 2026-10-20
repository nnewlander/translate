"""Read-only M4.5 format and paragraph-granularity audit."""

from collections import Counter, defaultdict
from pathlib import Path
import re
import zipfile

from lxml import etree

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.format_audit import FormatUnitAuditReport, FragmentSample, TranslationUnitAudit
from booktrans.domain.ir import BlockIR, DocumentIR, SpanIR
from booktrans.domain.models import SourceSnapshot
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.capability import NonIdentityCapabilityAudit
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part


WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
NUMBER_OR_PAGE = re.compile(r"\s*(?:\d+(?:\.\d+)?|[ivxlcdm]+)\s*", re.IGNORECASE)


class FormatTranslationUnitAudit:
    """Classifies only observed raw run differences; it makes no write decision."""

    def run(self, source_path: Path) -> FormatUnitAuditReport:
        before = fingerprint(source_path)
        snapshot = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
        document, uncertainty, _ = DocumentExtractor().extract(source_path, snapshot)
        ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
        capability = NonIdentityCapabilityAudit().audit(source_path, document)
        report = self._analyze(source_path, document, ledger.coverage_complete, capability)
        assert_unchanged(before, fingerprint(source_path), source_path)
        return report

    def _analyze(self, source_path: Path, document: DocumentIR, coverage_complete: bool, capability) -> FormatUnitAuditReport:
        records = {record.unit_uid: record for record in capability.records}
        raw_multi = [record for record in capability.records if "MULTI_FORMAT_CONTEXT" in record.reasons]
        collapsible: set[str] = set()
        collapse_categories: Counter[str] = Counter()
        true_categories: Counter[str] = Counter()
        samples: dict[str, list[dict[str, str]]] = defaultdict(list)
        with zipfile.ZipFile(source_path, "r") as package:
            roots: dict[str, etree._Element] = {}
            indexes: dict[str, RootLocatorIndex] = {}
            by_uid = {block.unit_uid: block for block in document.blocks}
            for record in raw_multi:
                block = by_uid[record.unit_uid]
                category = self._format_category(package, roots, indexes, block)
                sample = {
                    "display_id": block.display_id,
                    "story": block.story,
                    "part_name": block.part_name,
                    "locator": block.locator.xml_path,
                    "source_text": block.source_plain_text[:180],
                }
                if len(samples[category]) < 5:
                    samples[category].append(sample)
                if category in {"LANGUAGE_METADATA_ONLY", "WHITESPACE_RUN_FRAGMENTATION", "EQUIVALENT_RELEVANT_SIGNATURE"}:
                    # A block can flip only when format fragmentation is its sole
                    # M4 capability constraint; this keeps protected/hyperlink
                    # structures conservative even if their formatting matches.
                    if records[record.unit_uid].reasons == ["MULTI_FORMAT_CONTEXT"]:
                        collapsible.add(record.unit_uid)
                        collapse_categories[category] += 1
                    else:
                        true_categories[category] += 1
                else:
                    true_categories[category] += 1
        remaining: Counter[str] = Counter()
        for record in capability.records:
            if record.status == "SAFE_SIMPLE" or record.unit_uid in collapsible:
                continue
            for reason in record.reasons:
                if reason != "MULTI_FORMAT_CONTEXT" or record.unit_uid not in collapsible:
                    remaining[reason] += 1
        normalized_safe = capability.safe_simple_blocks + len(collapsible)
        return FormatUnitAuditReport(
            source_sha256=document.document_sha256,
            source_integrity_pass=True,
            coverage_complete=coverage_complete,
            total_translatable_blocks=capability.total_translatable_blocks,
            raw_safe_simple_blocks=capability.safe_simple_blocks,
            raw_multi_format_context_blocks=len(raw_multi),
            normalization_collapsible_blocks=len(collapsible),
            true_multi_format_blocks=len(raw_multi) - len(collapsible),
            normalized_safe_blocks=normalized_safe,
            normalized_safe_percentage=round((normalized_safe / capability.total_translatable_blocks * 100) if capability.total_translatable_blocks else 0.0, 2),
            collapsible_categories=dict(sorted(collapse_categories.items())),
            true_format_difference_categories=dict(sorted(true_categories.items())),
            remaining_complex_reasons=dict(sorted(remaining.items())),
            format_samples=dict(sorted(samples.items())),
            unit_audit=_unit_audit(document),
        )

    def _format_category(self, package, roots, indexes, block: BlockIR) -> str:
        spans = [span for span in block.spans if span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE]
        infos = [_span_format(package, roots, indexes, span) for span in spans]
        contexts = {info[0] for info in infos}
        if any("hyperlink_rid" in span.container_context for span in spans):
            return "HYPERLINK_CONTAINER_DIFFERENCE"
        if len(contexts) > 1:
            return "CONTAINER_DIFFERENCE"
        signatures = [info[1] for info in infos]
        categories = _signature_differences(signatures)
        if categories:
            # Keep the first deterministically ordered category per block.  The
            # report counts one block once; samples remain interpretable.
            return categories[0]
        metadata = {info[2] for info in infos}
        if len(metadata) > 1:
            return "LANGUAGE_METADATA_ONLY"
        if any(span.text.isspace() for span in spans):
            return "WHITESPACE_RUN_FRAGMENTATION"
        return "EQUIVALENT_RELEVANT_SIGNATURE"


def _span_format(package, roots, indexes, span: SpanIR) -> tuple[tuple[tuple[str, str], ...], dict[str, tuple], tuple[tuple[str, str], ...]]:
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
    return context, _relevant_signature(properties), _metadata_signature(properties)


def _relevant_signature(properties: etree._Element | None) -> dict[str, tuple]:
    if properties is None:
        return {
            "bold": (False,), "italic": (False,), "underline": ("none",), "font_family": (), "font_size": (),
            "color_highlight": (), "character_style": (), "other_direct": (),
        }
    child = {etree.QName(item).localname: item for item in properties}
    known = {"b", "bCs", "i", "iCs", "u", "rFonts", "sz", "szCs", "color", "highlight", "rStyle", "lang", "noProof"}
    return {
        "bold": (_bool(child.get("b")) or _bool(child.get("bCs")),),
        "italic": (_bool(child.get("i")) or _bool(child.get("iCs")),),
        "underline": _attrs(child.get("u")),
        "font_family": _attrs(child.get("rFonts")),
        "font_size": _attrs(child.get("sz")) + _attrs(child.get("szCs")),
        "color_highlight": _attrs(child.get("color")) + _attrs(child.get("highlight")),
        "character_style": _attrs(child.get("rStyle")),
        "other_direct": tuple(
            (etree.QName(item).localname, _attrs(item)) for item in properties
            if etree.QName(item).localname not in known
        ),
    }


def _metadata_signature(properties: etree._Element | None) -> tuple[tuple[str, str], ...]:
    if properties is None:
        return ()
    metadata: list[tuple[str, str]] = []
    for item in properties:
        local = etree.QName(item).localname
        if local in {"lang", "noProof"}:
            metadata.extend((local, f"{key}={value}") for key, value in _attrs(item))
            if local == "noProof" and not _attrs(item):
                metadata.append((local, "true"))
    return tuple(metadata)


def _signature_differences(signatures: list[dict[str, tuple]]) -> list[str]:
    labels = {
        "bold": "BOLD_DIFFERENCE", "italic": "ITALIC_DIFFERENCE", "underline": "UNDERLINE_DIFFERENCE",
        "font_family": "FONT_FAMILY_DIFFERENCE", "font_size": "FONT_SIZE_DIFFERENCE",
        "color_highlight": "COLOR_HIGHLIGHT_DIFFERENCE", "character_style": "CHARACTER_STYLE_DIFFERENCE",
        "other_direct": "OTHER_DIRECT_FORMAT_DIFFERENCE",
    }
    return [labels[field] for field in labels if len({signature[field] for signature in signatures}) > 1]


def _attrs(element: etree._Element | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((etree.QName(key).localname, value) for key, value in element.attrib.items())) if element is not None else ()


def _bool(element: etree._Element | None) -> bool:
    if element is None:
        return False
    value = element.get(qn("w", "val"), "true").lower()
    return value not in {"0", "false", "off", "none"}


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _is_translatable(block: BlockIR) -> bool:
    return any(span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE for span in block.spans)


def _unit_audit(document: DocumentIR) -> TranslationUnitAudit:
    blocks = [block for block in document.blocks if _is_translatable(block)]
    characters: Counter[str] = Counter()
    words: Counter[str] = Counter()
    punctuation_only = number_like = short_heading = 0
    samples: list[FragmentSample] = []
    neighborhoods = _neighborhoods(document)
    for block in blocks:
        text = block.source_plain_text
        length = len(text)
        word_count = len(WORD.findall(text))
        characters[_bucket(length, ((0, "0"), (1, "1"), (3, "2-3"), (10, "4-10"), (40, "11-40")), "41+")] += 1
        words[_bucket(word_count, ((0, "0"), (1, "1"), (3, "2-3"), (10, "4-10")), "11+")] += 1
        punctuation_only += int(bool(text.strip()) and not any(character.isalnum() for character in text))
        number_like += int(bool(NUMBER_OR_PAGE.fullmatch(text)))
        short_heading += int(block.content_role == "heading" and word_count <= 3)
        if _is_fragment_candidate(block, text, word_count):
            previous, following = neighborhoods[block.unit_uid]
            samples.append(FragmentSample(
                display_id=block.display_id, story=block.story, part_name=block.part_name, locator=block.locator,
                source_text=text, previous_text=previous, next_text=following,
                reason="SHORT_BODY_PROSE_WITH_LOWERCASE_OR_MID_SENTENCE_PUNCTUATION",
            ))
    return TranslationUnitAudit(
        total_translatable_blocks=len(blocks),
        character_length_distribution=dict(characters), word_count_distribution=dict(words),
        blocks_leq_one_word=sum(len(WORD.findall(block.source_plain_text)) <= 1 for block in blocks),
        blocks_leq_three_words=sum(len(WORD.findall(block.source_plain_text)) <= 3 for block in blocks),
        punctuation_only_blocks=punctuation_only, number_or_page_like_blocks=number_like,
        short_heading_or_title_blocks=short_heading, suspicious_fragment_candidates=len(samples),
        suspicious_fragment_samples=samples[:20],
    )


def _bucket(value: int, limits: tuple[tuple[int, str], ...], otherwise: str) -> str:
    for limit, label in limits:
        if value <= limit:
            return label
    return otherwise


def _neighborhoods(document: DocumentIR) -> dict[str, tuple[str | None, str | None]]:
    grouped: dict[tuple[str, str], list[BlockIR]] = defaultdict(list)
    for block in document.blocks:
        grouped[(block.story, block.part_name)].append(block)
    result: dict[str, tuple[str | None, str | None]] = {}
    for group in grouped.values():
        for index, block in enumerate(group):
            previous = next((item.source_plain_text for item in reversed(group[:index]) if item.source_plain_text), None)
            following = next((item.source_plain_text for item in group[index + 1:] if item.source_plain_text), None)
            result[block.unit_uid] = (previous, following)
    return result


def _is_fragment_candidate(block: BlockIR, text: str, word_count: int) -> bool:
    if block.story != "body" or block.block_type != "body_paragraph" or block.content_role != "paragraph":
        return False
    if not text.strip() or NUMBER_OR_PAGE.fullmatch(text) or word_count > 3 or not re.search(r"[A-Za-z]", text):
        return False
    first_letter = next((character for character in text if character.isalpha()), "")
    return first_letter.islower() or text.rstrip().endswith((",", ";", ":"))
