"""M5.2f positive-control probe built from actual Chapter 1 paragraphs.

This module deliberately copies the complete candidate paragraph payload rather
than inventing a similarly long sentence.  It is an experiment only: no result
from this writer may become a production line-break policy without a separate
real Word/PDF verdict satisfying the positive-control contract.
"""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.ooxml.namespaces import NS, qn


@dataclass(frozen=True)
class PositiveControlCase:
    display_id: str
    paragraph_ordinal: int
    token: str
    observed_failure: str


@dataclass(frozen=True)
class PositiveControlVariant:
    variant_id: str
    word_wrap: str | None
    explicit_language: bool


_VARIANTS = (
    PositiveControlVariant("A_REAL_BASELINE", None, False),
    PositiveControlVariant("B_REAL_WORDWRAP", "0", False),
    PositiveControlVariant("C_REAL_EXPLICIT_LANGUAGE", "0", True),
    PositiveControlVariant("D_REAL_LANGUAGE_NO_WORDWRAP", None, True),
)


class PositiveControlProbeError(ValueError):
    pass


def build_positive_control_probe(
    template_docx: Path,
    output_docx: Path,
    manifest_path: Path,
    cases: list[PositiveControlCase],
    *,
    width_deltas_twips: tuple[int, ...] = (0, 72, 144),
) -> dict[str, object]:
    """Build A/B/C/D controls from complete existing candidate paragraphs.

    ``width_deltas_twips`` is probe-only right-indent pressure.  It is not a
    production layout policy and it never changes the copied visible text.
    """
    if template_docx.resolve() == output_docx.resolve():
        raise PositiveControlProbeError("PROBE_OUTPUT_EQUALS_TEMPLATE")
    if not cases:
        raise PositiveControlProbeError("PROBE_CASES_EMPTY")
    if not width_deltas_twips or width_deltas_twips[0] != 0 or any(delta < 0 for delta in width_deltas_twips):
        raise PositiveControlProbeError("PROBE_WIDTH_CONDITIONS_INVALID")

    before_sha = _sha256_file(template_docx)
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template_docx, "r") as source:
        try:
            source_root = etree.fromstring(source.read("word/document.xml"))
        except KeyError as exc:
            raise PositiveControlProbeError("PROBE_TEMPLATE_DOCUMENT_PART_MISSING") from exc
        body = source_root.find("w:body", namespaces=NS)
        if body is None:
            raise PositiveControlProbeError("PROBE_TEMPLATE_BODY_MISSING")
        source_paragraphs = list(source_root.xpath(".//w:p", namespaces=NS))
        _validate_cases(cases, source_paragraphs)
        probe_root, fixture_rows = _build_probe_document(source_root, source_paragraphs, cases, width_deltas_twips)
        with zipfile.ZipFile(output_docx, "w") as output:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "word/document.xml":
                    payload = etree.tostring(probe_root, xml_declaration=True, encoding="UTF-8", standalone=True)
                output.writestr(info, payload)
    after_sha = _sha256_file(template_docx)
    if before_sha != after_sha:
        raise PositiveControlProbeError("PRODUCTION_CANDIDATE_MUTATED")

    groups = _visible_text_groups(output_docx)
    _validate_visible_text_groups(groups, fixture_rows)
    for fixture in fixture_rows:
        fixture["visible_text_sha256"] = _text_sha256(groups[fixture["fixture_id"]])
    manifest: dict[str, object] = {
        "schema_version": "m5.2f",
        "artifact_kind": "POSITIVE_CONTROL_MIXED_SCRIPT_WRAP_PROBE",
        "status_before_external_visual_review": "READY_FOR_WORD_PDF",
        "template_docx": str(template_docx.resolve()),
        "template_sha256_before": before_sha,
        "template_sha256_after": after_sha,
        "template_immutable": True,
        "output_docx": str(output_docx.resolve()),
        "cases": [
            {
                "display_id": case.display_id,
                "paragraph_ordinal": case.paragraph_ordinal,
                "token": case.token,
                "real_observed_failure": case.observed_failure,
            }
            for case in cases
        ],
        "width_conditions": [
            {
                "width_id": f"W{index}",
                "right_indent_delta_twips": delta,
                "meaning": "actual paragraph width" if delta == 0 else "probe-only reduced available width",
            }
            for index, delta in enumerate(width_deltas_twips)
        ],
        "variants": [_variant_manifest(item) for item in _VARIANTS],
        "fixtures": fixture_rows,
        "visible_text_contract": "Within each case and width condition, A/B/C/D visible paragraph text must have identical SHA-256.",
        "positive_control_contract": "The experiment is diagnostic only if A_REAL_BASELINE or B_REAL_WORDWRAP reproduces at least one TOKEN_INTERNAL_SPLIT. No C/D strategy is approved by this artifact.",
        "approval_status": "EXPERIMENT_ONLY",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def extract_positive_control_payloads(docx_path: Path) -> dict[str, list[str]]:
    """Extract visible copied-paragraph text keyed by fixture id from a probe."""
    with zipfile.ZipFile(docx_path, "r") as package:
        root = etree.fromstring(package.read("word/document.xml"))
    payloads: dict[str, list[str]] = {}
    active: tuple[str, str] | None = None
    variant_index = 0
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
        if text.startswith("M5.2f POSITIVE CONTROL — "):
            display_id, _token, width_id = text.removeprefix("M5.2f POSITIVE CONTROL — ").split(" / ")
            active = (display_id, width_id)
            variant_index = 0
            continue
        if active is not None and variant_index < len(_VARIANTS):
            display_id, width_id = active
            fixture_id = f"{display_id}:{width_id}:{_VARIANTS[variant_index].variant_id}"
            payloads.setdefault(fixture_id, []).append(text)
            variant_index += 1
    return payloads


def _build_probe_document(
    template_root: etree._Element,
    source_paragraphs: list[etree._Element],
    cases: list[PositiveControlCase],
    width_deltas_twips: tuple[int, ...],
) -> tuple[etree._Element, list[dict[str, object]]]:
    root = etree.Element(qn("w", "document"), nsmap=template_root.nsmap)
    for key, value in template_root.attrib.items():
        root.set(key, value)
    body = etree.SubElement(root, qn("w", "body"))
    rows: list[dict[str, object]] = []
    for case in cases:
        source_paragraph = source_paragraphs[case.paragraph_ordinal - 1]
        source_text = _paragraph_text(source_paragraph)
        paragraph_sha = sha256(etree.tostring(source_paragraph)).hexdigest()
        for width_index, delta in enumerate(width_deltas_twips):
            width_id = f"W{width_index}"
            body.append(_label_paragraph(f"{case.display_id} / {case.token} / {width_id}"))
            for variant in _VARIANTS:
                fixture_id = f"{case.display_id}:{width_id}:{variant.variant_id}"
                paragraph, exact_diff = _variant_paragraph(source_paragraph, case.token, variant, delta)
                body.append(paragraph)
                rows.append({
                    "fixture_id": fixture_id,
                    "source_display_id": case.display_id,
                    "token": case.token,
                    "real_observed_failure": case.observed_failure,
                    "exact_source_candidate_paragraph_sha256": paragraph_sha,
                    "variant": variant.variant_id,
                    "width_condition": width_id,
                    "right_indent_delta_twips": delta,
                    "exact_ooxml_diff_from_real_paragraph": exact_diff,
                    "expected_diagnostic_outcome": "Report WHOLE_TOKEN_SAME_LINE, WHOLE_TOKEN_MOVED_TO_NEXT_LINE, TOKEN_INTERNAL_SPLIT, or OVERFLOW_OR_CLIPPING.",
                    "source_visible_text": source_text,
                })
    source_body = template_root.find("w:body", namespaces=NS)
    sect_pr = source_body.find("w:sectPr", namespaces=NS) if source_body is not None else None
    body.append(deepcopy(sect_pr) if sect_pr is not None else etree.Element(qn("w", "sectPr")))
    return root, rows


def _variant_paragraph(
    source_paragraph: etree._Element,
    token: str,
    variant: PositiveControlVariant,
    right_indent_delta: int,
) -> tuple[etree._Element, list[str]]:
    paragraph = deepcopy(source_paragraph)
    diff: list[str] = []
    _set_property(paragraph, "kinsoku", "1")
    if variant.word_wrap is None:
        if _remove_property(paragraph, "wordWrap"):
            diff.append("remove w:pPr/w:wordWrap")
    else:
        _set_property(paragraph, "wordWrap", variant.word_wrap)
        diff.append(f"set w:pPr/w:wordWrap @w:val={variant.word_wrap}")
    if right_indent_delta:
        _increase_right_indent(paragraph, right_indent_delta)
        diff.append(f"increase w:pPr/w:ind @w:right by {right_indent_delta} twips (probe-only width pressure)")
    if variant.explicit_language:
        split_count = _apply_explicit_language(paragraph, token)
        diff.append(
            "set CJK fragments w:rPr/w:lang val=zh-CN eastAsia=zh-CN; "
            "set token fragments w:rPr/w:lang val=en-US eastAsia=zh-CN; "
            f"split {split_count} source run(s) only where needed to isolate the audited token"
        )
    if not diff:
        diff.append("none: real candidate paragraph copied unchanged")
    return paragraph, diff


def _apply_explicit_language(paragraph: etree._Element, token: str) -> int:
    """Set script language while preserving formatting and existing token boundaries."""
    runs = list(paragraph.xpath(".//w:r", namespaces=NS))
    values = ["".join(run.xpath("w:t/text()", namespaces=NS)) for run in runs]
    joined = "".join(values)
    matches = _all_token_ranges(joined, token)
    if not matches:
        raise PositiveControlProbeError(f"TOKEN_NOT_FOUND_FOR_LANGUAGE_VARIANT: {token}")
    split_count = 0
    cursor = 0
    for run, value in zip(runs, values):
        text_nodes = run.xpath("w:t", namespaces=NS)
        run_start, run_end = cursor, cursor + len(value)
        cursor = run_end
        overlaps = [(max(run_start, start) - run_start, min(run_end, end) - run_start) for start, end in matches if start < run_end and end > run_start]
        if len(text_nodes) != 1:
            if overlaps:
                raise PositiveControlProbeError(f"TOKEN_IN_COMPLEX_RUN_UNSUPPORTED: {token}")
            _set_run_language(run, latin="zh-CN", east_asia="zh-CN")
            continue
        if not overlaps:
            _set_run_language(run, latin="zh-CN", east_asia="zh-CN")
            continue
        cut_points = {0, len(value)}
        for start, end in overlaps:
            cut_points.update((start, end))
        replacement: list[etree._Element] = []
        points = sorted(cut_points)
        for start, end in zip(points, points[1:]):
            piece = value[start:end]
            is_token_piece = any(start >= token_start and end <= token_end for token_start, token_end in overlaps)
            replacement.append(_clone_run_with_text(
                run,
                piece,
                latin="en-US" if is_token_piece else "zh-CN",
                east_asia="zh-CN",
            ))
        parent = run.getparent()
        insert_at = parent.index(run)
        parent.remove(run)
        for offset, item in enumerate(replacement):
            parent.insert(insert_at + offset, item)
        split_count += 1
    return split_count


def _all_token_ranges(value: str, token: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        found = value.find(token, start)
        if found < 0:
            return ranges
        ranges.append((found, found + len(token)))
        start = found + len(token)


def _clone_run_with_text(source_run: etree._Element, text_value: str, *, latin: str, east_asia: str) -> etree._Element:
    new_run = deepcopy(source_run)
    text_nodes = new_run.xpath("w:t", namespaces=NS)
    text_nodes[0].text = text_value
    if text_value.startswith(" ") or text_value.endswith(" "):
        text_nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    _set_run_language(new_run, latin=latin, east_asia=east_asia)
    return new_run


def _set_run_language(run: etree._Element, *, latin: str, east_asia: str) -> None:
    rpr = run.find("w:rPr", namespaces=NS)
    if rpr is None:
        rpr = etree.Element(qn("w", "rPr"))
        run.insert(0, rpr)
    lang = rpr.find("w:lang", namespaces=NS)
    if lang is None:
        lang = etree.SubElement(rpr, qn("w", "lang"))
    lang.set(qn("w", "val"), latin)
    lang.set(qn("w", "eastAsia"), east_asia)


def _set_property(paragraph: etree._Element, local_name: str, value: str) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    if ppr is None:
        ppr = etree.Element(qn("w", "pPr"))
        paragraph.insert(0, ppr)
    node = ppr.find(f"w:{local_name}", namespaces=NS)
    if node is None:
        node = etree.SubElement(ppr, qn("w", local_name))
    node.set(qn("w", "val"), value)


def _remove_property(paragraph: etree._Element, local_name: str) -> bool:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    node = ppr.find(f"w:{local_name}", namespaces=NS) if ppr is not None else None
    if node is None:
        return False
    ppr.remove(node)
    return True


def _increase_right_indent(paragraph: etree._Element, delta: int) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    if ppr is None:
        ppr = etree.Element(qn("w", "pPr"))
        paragraph.insert(0, ppr)
    ind = ppr.find("w:ind", namespaces=NS)
    if ind is None:
        ind = etree.SubElement(ppr, qn("w", "ind"))
    existing = int(ind.get(qn("w", "right"), "0"))
    ind.set(qn("w", "right"), str(existing + delta))


def _label_paragraph(label: str) -> etree._Element:
    paragraph = etree.Element(qn("w", "p"))
    run = etree.SubElement(paragraph, qn("w", "r"))
    text = etree.SubElement(run, qn("w", "t"))
    text.text = f"M5.2f POSITIVE CONTROL — {label}"
    return paragraph


def _validate_cases(cases: list[PositiveControlCase], paragraphs: list[etree._Element]) -> None:
    seen: set[int] = set()
    for case in cases:
        if case.paragraph_ordinal < 1 or case.paragraph_ordinal > len(paragraphs):
            raise PositiveControlProbeError(f"CASE_PARAGRAPH_MISSING: {case.display_id}")
        if case.paragraph_ordinal in seen:
            raise PositiveControlProbeError(f"DUPLICATE_CASE_PARAGRAPH: {case.display_id}")
        seen.add(case.paragraph_ordinal)
        if case.token not in _paragraph_text(paragraphs[case.paragraph_ordinal - 1]):
            raise PositiveControlProbeError(f"CASE_TOKEN_NOT_IN_REAL_PARAGRAPH: {case.display_id} token={case.token}")


def _visible_text_groups(docx_path: Path) -> dict[str, list[str]]:
    return extract_positive_control_payloads(docx_path)


def _validate_visible_text_groups(groups: dict[str, list[str]], rows: list[dict[str, object]]) -> None:
    expected = {row["fixture_id"] for row in rows}
    if set(groups) != expected or any(len(value) != 1 for value in groups.values()):
        raise PositiveControlProbeError("PROBE_FIXTURE_TEXT_MISSING")
    by_case_width: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (str(row["source_display_id"]), str(row["width_condition"]))
        by_case_width.setdefault(key, set()).add(_text_sha256(groups[str(row["fixture_id"])]))
    if any(len(hashes) != 1 for hashes in by_case_width.values()):
        raise PositiveControlProbeError("PROBE_VARIANT_VISIBLE_TEXT_MISMATCH")


def _variant_manifest(variant: PositiveControlVariant) -> dict[str, object]:
    return {
        "variant_id": variant.variant_id,
        "kinsoku": "1",
        "wordWrap": variant.word_wrap,
        "explicit_script_language": variant.explicit_language,
        "approval_status": "EXPERIMENT_ONLY",
    }


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))


def _text_sha256(texts: list[str]) -> str:
    return sha256("\x1e".join(texts).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
