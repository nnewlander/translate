"""Read-only rendered-differential facts for DOCX visual-gate diagnosis.

The comparison deliberately does *not* align PDFs by physical page number.
DOCX reflow may shift pages while preserving content.  Instead it records
content-anchor locations from two PDFs rendered by the same renderer and
separately proves whether the candidate's OOXML changed anything other than
``w:t`` values.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from booktrans.ooxml.namespaces import NS, qn


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_rendered_text(value: str) -> str:
    """Normalize extraction-only differences without changing document facts."""
    # A renderer may extract a line-end discretionary hyphen as either a soft
    # hyphen or a visible hyphen followed by whitespace.  Join only that
    # line-break form; ordinary in-line hyphenated words remain intact.
    value = re.sub(r"(?<=\w)[\u00ad\u2010\u2011-]\s+(?=\w)", "", value)
    value = value.replace("\u00ad", "").replace("\u2010", "-").replace("\u2011", "-")
    value = value.replace("\ufb01", "fi").replace("\ufb02", "fl")
    return re.sub(r"\s+", " ", value).strip()


def _document_root(path: Path) -> etree._Element:
    with ZipFile(path) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def _canonical_sha(element: etree._Element) -> str:
    return sha256(etree.tostring(element, method="c14n")).hexdigest()


def _paragraph_property_hashes(root: etree._Element, xpath: str) -> list[str]:
    values: list[str] = []
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        node = paragraph.find(xpath, namespaces=NS)
        values.append(_canonical_sha(node) if node is not None else "")
    return values


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff" for character in value)


def _run_property_delta_is_approved_cjk_micro_layout(source_properties: etree._Element | None, candidate_properties: etree._Element | None, candidate_run: etree._Element) -> bool:
    """Recognize the existing tagged-projection CJK micro-layout contract.

    The writeback contract deliberately removes only ``w:spacing``/``w:w``
    from a copied run when it writes generated CJK.  Keeping source Latin
    micro-spacing is already rejected by tagged-roundtrip QA.
    """
    if source_properties is None or candidate_properties is None:
        return False
    source_children = list(source_properties)
    candidate_children = list(candidate_properties)
    removable = {qn("w", "spacing"), qn("w", "w")}
    source_non_micro = [child for child in source_children if child.tag not in removable]
    candidate_non_micro = [child for child in candidate_children if child.tag not in removable]
    removed = [child for child in source_children if child.tag in removable]
    candidate_added_micro = [child for child in candidate_children if child.tag in removable]
    candidate_text = "".join(candidate_run.xpath(".//w:t/text()", namespaces=NS))
    return bool(removed) and not candidate_added_micro and _contains_cjk(candidate_text) and [
        _canonical_sha(child) for child in source_non_micro
    ] == [_canonical_sha(child) for child in candidate_non_micro]


def _run_property_deltas(source: etree._Element, candidate: etree._Element) -> dict[str, object]:
    approved: list[int] = []
    unexpected: list[int] = []
    source_paragraphs = source.xpath(".//w:p", namespaces=NS)
    candidate_paragraphs = candidate.xpath(".//w:p", namespaces=NS)
    for index, (source_paragraph, candidate_paragraph) in enumerate(zip(source_paragraphs, candidate_paragraphs, strict=False)):
        source_runs = source_paragraph.xpath(".//w:r", namespaces=NS)
        candidate_runs = candidate_paragraph.xpath(".//w:r", namespaces=NS)
        if len(source_runs) != len(candidate_runs):
            unexpected.append(index)
            continue
        changed = False
        paragraph_approved = True
        for source_run, candidate_run in zip(source_runs, candidate_runs, strict=True):
            source_properties = source_run.find("w:rPr", namespaces=NS)
            candidate_properties = candidate_run.find("w:rPr", namespaces=NS)
            source_hash = _canonical_sha(source_properties) if source_properties is not None else ""
            candidate_hash = _canonical_sha(candidate_properties) if candidate_properties is not None else ""
            if source_hash == candidate_hash:
                continue
            changed = True
            paragraph_approved = paragraph_approved and _run_property_delta_is_approved_cjk_micro_layout(
                source_properties, candidate_properties, candidate_run,
            )
        if changed:
            (approved if paragraph_approved else unexpected).append(index)
    if len(source_paragraphs) != len(candidate_paragraphs):
        unexpected.extend(range(min(len(source_paragraphs), len(candidate_paragraphs)), max(len(source_paragraphs), len(candidate_paragraphs))))
    return {
        "approved_cjk_micro_layout_run_property_changed_indices": approved,
        "unexpected_run_property_changed_indices": sorted(set(unexpected)),
    }


def _nontext_structure_sha(root: etree._Element) -> str:
    """Hash OOXML structure without copying a book-scale XML tree.

    A deep copy/c14n of large DOCX XML can consume several GB.  This streaming
    digest preserves tags, attributes, child ordering, and non-``w:t`` text;
    only mutable text-node content is excluded.
    """
    digest = sha256()
    text_tag = qn("w", "t")
    for node in root.iter():
        digest.update(node.tag.encode("utf-8"))
        for key, value in sorted(node.attrib.items()):
            digest.update(key.encode("utf-8"))
            digest.update(value.encode("utf-8"))
        if node.tag != text_tag and node.text and not node.text.isspace():
            digest.update(node.text.encode("utf-8"))
        if node.tail and not node.tail.isspace():
            digest.update(node.tail.encode("utf-8"))
    return digest.hexdigest()


def compare_docx_nontext_structure(source_docx: Path, candidate_docx: Path) -> dict[str, object]:
    """Prove whether a narrow text writer changed layout-affecting OOXML.

    ``w:t`` values are the only intended differences for the current patcher.
    Any other structural mismatch is surfaced as an explicit fact, including
    paragraph/run properties, section properties, and page-break constructs.
    """
    source = _document_root(source_docx)
    candidate = _document_root(candidate_docx)
    source_ppr = _paragraph_property_hashes(source, "w:pPr")
    candidate_ppr = _paragraph_property_hashes(candidate, "w:pPr")

    def changed_indices(left: list[str], right: list[str]) -> list[int]:
        common = [index for index, (a, b) in enumerate(zip(left, right, strict=False)) if a != b]
        if len(left) != len(right):
            common.extend(range(min(len(left), len(right)), max(len(left), len(right))))
        return common

    def section_hashes(root: etree._Element) -> list[str]:
        return [_canonical_sha(node) for node in root.xpath(".//w:sectPr", namespaces=NS)]

    def page_break_facts(root: etree._Element) -> dict[str, object]:
        section_types = [node.get(qn("w", "val"), "nextPage") for node in root.xpath(".//w:sectPr/w:type", namespaces=NS)]
        return {
            "explicit_page_break_runs": len(root.xpath(".//w:br[@w:type='page']", namespaces=NS)),
            "page_break_before": len(root.xpath(".//w:pPr/w:pageBreakBefore", namespaces=NS)),
            "keep_next": len(root.xpath(".//w:pPr/w:keepNext", namespaces=NS)),
            "keep_lines": len(root.xpath(".//w:pPr/w:keepLines", namespaces=NS)),
            "section_types": section_types,
        }

    source_nontext_sha = _nontext_structure_sha(source)
    candidate_nontext_sha = _nontext_structure_sha(candidate)
    run_property_deltas = _run_property_deltas(source, candidate)
    return {
        "source_docx_sha256": sha256_file(source_docx),
        "candidate_docx_sha256": sha256_file(candidate_docx),
        "source_nontext_structure_sha256": source_nontext_sha,
        "candidate_nontext_structure_sha256": candidate_nontext_sha,
        "nontext_structure_equal": source_nontext_sha == candidate_nontext_sha,
        "paragraph_property_changed_indices": changed_indices(source_ppr, candidate_ppr),
        **run_property_deltas,
        "section_properties_equal": section_hashes(source) == section_hashes(candidate),
        "source_page_break_facts": page_break_facts(source),
        "candidate_page_break_facts": page_break_facts(candidate),
        "page_break_facts_equal": page_break_facts(source) == page_break_facts(candidate),
    }


def rendered_differential_facts(
    *,
    source_docx: Path,
    candidate_docx: Path,
    source_rendered_pdf: Path,
    candidate_rendered_pdf: Path,
    official_source_pdf: Path | None,
    anchors: list[dict[str, str]],
    source_render_observed: dict[str, object],
    candidate_render_observed: dict[str, object],
    official_render_observed: dict[str, object] | None,
) -> dict[str, object]:
    """Return serializable bounded visual-gate facts; never renders or writes.

    ``*_render_observed`` is an explicit, renderer-produced page observation
    (page count plus the reviewed page facts).  This avoids an unsafe whole-book
    PDF text scan merely to discover anchors.  The caller supplies anchors only
    after deriving them from immutable request/response/IR evidence.
    """
    def observed(pdf_path: Path, value: dict[str, object]) -> dict[str, object]:
        page_count = value.get("page_count")
        pages = value.get("pages")
        if not isinstance(page_count, int) or page_count < 1 or not isinstance(pages, list):
            raise ValueError("RENDER_OBSERVATION_SCHEMA_INVALID")
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("page_number"), int) or not isinstance(page.get("text_character_count"), int):
                raise ValueError("RENDER_OBSERVATION_PAGE_FACT_INVALID")
            if page["page_number"] < 1 or page["page_number"] > page_count:
                raise ValueError("RENDER_OBSERVATION_PAGE_OUT_OF_RANGE")
        return {
            "path": str(pdf_path),
            "sha256": sha256_file(pdf_path),
            "page_count": page_count,
            "examined_page_numbers": [page["page_number"] for page in pages],
            "blank_pages_by_text": [page["page_number"] for page in pages if page.get("blank_by_text") is True],
            "pages": pages,
        }

    return {
        "artifact_type": "RENDERED_DIFFERENTIAL_VISUAL_GATE",
        "schema_version": 1,
        "source_render": observed(source_rendered_pdf, source_render_observed),
        "candidate_render": observed(candidate_rendered_pdf, candidate_render_observed),
        "official_source_render": observed(official_source_pdf, official_render_observed) if official_source_pdf and official_render_observed else None,
        "content_anchor_locations": anchors,
        "ooxml_nontext_structure": compare_docx_nontext_structure(source_docx, candidate_docx),
    }
