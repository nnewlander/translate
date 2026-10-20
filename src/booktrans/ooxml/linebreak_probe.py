"""Closed-world Word line-break probe for M5.2e.

The probe is an experiment, not a production candidate or a typography
engine.  It copies page/layout and body-run conditions from an existing
candidate, writes three explicitly described paragraph variants, and proves
that their visible test payload is identical.  Only a later desktop Word/PDF
comparison may promote a strategy beyond ``EXPERIMENT_ONLY``.
"""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.ooxml.namespaces import NS, qn


_TOKENS = ("Compiling", "fork", "Google", "2016", "15", "24")
_PAYLOAD_PREFIX = "这是用于验证中文正文中混合文字换行行为的说明；请在接近行尾的位置保留 token "
_PAYLOAD_SUFFIX = "，并以中文句号结束。"


@dataclass(frozen=True)
class ProbeVariant:
    variant_id: str
    word_wrap: str | None
    language_mode: str
    expected_behavior: str
    known_risk: str


_VARIANTS = (
    ProbeVariant(
        variant_id="A_BASELINE",
        word_wrap=None,
        language_mode="inherited",
        expected_behavior="Establish the current kinsoku-only mixed-script baseline.",
        known_risk="Word may still split Latin or numeric tokens inside a word/token.",
    ),
    ProbeVariant(
        variant_id="B_M5_2D_WORDWRAP",
        word_wrap="0",
        language_mode="inherited",
        expected_behavior="Reproduce the M5.2d wordWrap experiment for a controlled comparison.",
        known_risk="Real Chapter 1 PDF evidence already found this ineffective for mixed-script tokens.",
    ),
    ProbeVariant(
        variant_id="C_EXPLICIT_SCRIPT_LANGUAGE",
        word_wrap="0",
        language_mode="explicit_script_runs",
        expected_behavior="Test whether explicit Latin/East Asian language metadata changes Word line breaking.",
        known_risk="w:lang is documented for language/proofing; a line-break effect is a hypothesis pending Word/PDF evidence.",
    ),
)


class LineBreakProbeError(ValueError):
    pass


def build_linebreak_probe(
    template_docx: Path,
    output_docx: Path,
    manifest_path: Path,
    *,
    body_paragraph_ordinal: int,
) -> dict[str, object]:
    """Build one self-contained DOCX probe without mutating ``template_docx``."""
    if template_docx.resolve() == output_docx.resolve():
        raise LineBreakProbeError("PROBE_OUTPUT_EQUALS_TEMPLATE")
    template_before = _sha256_file(template_docx)
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(template_docx, "r") as package:
        try:
            document_root = etree.fromstring(package.read("word/document.xml"))
        except KeyError as exc:
            raise LineBreakProbeError("PROBE_TEMPLATE_DOCUMENT_PART_MISSING") from exc
        body = document_root.find("w:body", namespaces=NS)
        if body is None:
            raise LineBreakProbeError("PROBE_TEMPLATE_BODY_MISSING")
        source_paragraphs = list(document_root.xpath(".//w:p", namespaces=NS))
        if body_paragraph_ordinal < 1 or body_paragraph_ordinal > len(source_paragraphs):
            raise LineBreakProbeError(f"PROBE_TEMPLATE_PARAGRAPH_MISSING: {body_paragraph_ordinal}")
        body_template = source_paragraphs[body_paragraph_ordinal - 1]
        sect_pr = body.find("w:sectPr", namespaces=NS)
        new_document = _probe_document(document_root, body_template, sect_pr)

        with zipfile.ZipFile(output_docx, "w") as output:
            for info in package.infolist():
                payload = package.read(info.filename)
                if info.filename == "word/document.xml":
                    payload = etree.tostring(new_document, xml_declaration=True, encoding="UTF-8", standalone=True)
                output.writestr(info, payload)

    template_after = _sha256_file(template_docx)
    if template_before != template_after:
        raise LineBreakProbeError("PROBE_TEMPLATE_MUTATED")
    text_by_variant = extract_probe_payload_texts(output_docx)
    expected_payload = _payloads()
    payload_hashes = {
        variant_id: _text_sha256(texts)
        for variant_id, texts in text_by_variant.items()
    }
    if any(texts != expected_payload for texts in text_by_variant.values()):
        raise LineBreakProbeError("PROBE_VISIBLE_TEXT_CHANGED")
    if len(set(payload_hashes.values())) != 1:
        raise LineBreakProbeError("PROBE_VARIANT_VISIBLE_TEXT_MISMATCH")

    manifest: dict[str, object] = {
        "schema_version": "m5.2e",
        "artifact_kind": "WORD_LINEBREAK_EXPERIMENT",
        "template_docx": str(template_docx.resolve()),
        "template_sha256_before": template_before,
        "template_sha256_after": template_after,
        "template_immutable": True,
        "output_docx": str(output_docx.resolve()),
        "body_template": {
            "part_name": "word/document.xml",
            "paragraph_ordinal": body_paragraph_ordinal,
            "page_layout": "Copied unchanged from template word/document.xml/w:body/w:sectPr.",
            "body_font_and_size": "Copied from the template paragraph's first direct run properties.",
        },
        "tokens": list(_TOKENS),
        "expected_payload_visible_text": expected_payload,
        "visible_text_sha256": next(iter(payload_hashes.values())),
        "text_preservation_passed": True,
        "variants": [_variant_manifest(variant, payload_hashes[variant.variant_id]) for variant in _VARIANTS],
        "approval_boundary": "All variants are EXPERIMENT_ONLY. A strategy cannot become APPROVED without new real Word-to-PDF visual evidence.",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def extract_probe_payload_texts(docx_path: Path) -> dict[str, list[str]]:
    """Return the six visible payload strings per variant from a generated probe."""
    with zipfile.ZipFile(docx_path, "r") as package:
        root = etree.fromstring(package.read("word/document.xml"))
    found: dict[str, list[str]] = {variant.variant_id: [] for variant in _VARIANTS}
    current_variant: str | None = None
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
        if text.startswith("M5.2e PROBE — "):
            current_variant = text.removeprefix("M5.2e PROBE — ")
            continue
        if current_variant and text.startswith(_PAYLOAD_PREFIX):
            found[current_variant].append(text)
    return found


def _probe_document(
    template_root: etree._Element,
    body_template: etree._Element,
    sect_pr: etree._Element | None,
) -> etree._Element:
    root = etree.Element(qn("w", "document"), nsmap=template_root.nsmap)
    body = etree.SubElement(root, qn("w", "body"))
    for variant in _VARIANTS:
        body.append(_label_paragraph(variant.variant_id))
        for token in _TOKENS:
            body.append(_payload_paragraph(body_template, variant, token))
    if sect_pr is not None:
        body.append(deepcopy(sect_pr))
    else:
        body.append(etree.Element(qn("w", "sectPr")))
    return root


def _label_paragraph(variant_id: str) -> etree._Element:
    paragraph = etree.Element(qn("w", "p"))
    run = etree.SubElement(paragraph, qn("w", "r"))
    text = etree.SubElement(run, qn("w", "t"))
    text.text = f"M5.2e PROBE — {variant_id}"
    return paragraph


def _payload_paragraph(template: etree._Element, variant: ProbeVariant, token: str) -> etree._Element:
    paragraph = etree.Element(qn("w", "p"))
    template_ppr = template.find("w:pPr", namespaces=NS)
    ppr = deepcopy(template_ppr) if template_ppr is not None else etree.Element(qn("w", "pPr"))
    _remove_property(ppr, "wordWrap")
    _remove_property(ppr, "kinsoku")
    _set_property(ppr, "kinsoku", "1")
    if variant.word_wrap is not None:
        _set_property(ppr, "wordWrap", variant.word_wrap)
    paragraph.append(ppr)

    template_rpr = template.find(".//w:r/w:rPr", namespaces=NS)
    if variant.language_mode == "explicit_script_runs":
        paragraph.append(_run(_PAYLOAD_PREFIX, template_rpr, language=("zh-CN", "zh-CN")))
        paragraph.append(_run(token, template_rpr, language=("en-US", "zh-CN")))
        paragraph.append(_run(_PAYLOAD_SUFFIX, template_rpr, language=("zh-CN", "zh-CN")))
    else:
        paragraph.append(_run(_PAYLOAD_PREFIX + token + _PAYLOAD_SUFFIX, template_rpr))
    return paragraph


def _run(text_value: str, template_rpr: etree._Element | None, *, language: tuple[str, str] | None = None) -> etree._Element:
    run = etree.Element(qn("w", "r"))
    if template_rpr is not None:
        rpr = deepcopy(template_rpr)
        _remove_property(rpr, "spacing")
        _remove_property(rpr, "w")
        if language is not None:
            _set_language(rpr, latin=language[0], east_asia=language[1])
        run.append(rpr)
    elif language is not None:
        rpr = etree.Element(qn("w", "rPr"))
        _set_language(rpr, latin=language[0], east_asia=language[1])
        run.append(rpr)
    text = etree.SubElement(run, qn("w", "t"))
    if text_value.startswith(" ") or text_value.endswith(" "):
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = text_value
    return run


def _set_language(rpr: etree._Element, *, latin: str, east_asia: str) -> None:
    language = rpr.find("w:lang", namespaces=NS)
    if language is None:
        language = etree.SubElement(rpr, qn("w", "lang"))
    language.set(qn("w", "val"), latin)
    language.set(qn("w", "eastAsia"), east_asia)


def _set_property(ppr: etree._Element, local_name: str, value: str) -> None:
    element = ppr.find(f"w:{local_name}", namespaces=NS)
    if element is None:
        element = etree.SubElement(ppr, qn("w", local_name))
    element.set(qn("w", "val"), value)


def _remove_property(properties: etree._Element, local_name: str) -> None:
    element = properties.find(f"w:{local_name}", namespaces=NS)
    if element is not None:
        properties.remove(element)


def _payloads() -> list[str]:
    return [_PAYLOAD_PREFIX + token + _PAYLOAD_SUFFIX for token in _TOKENS]


def _variant_manifest(variant: ProbeVariant, text_hash: str) -> dict[str, object]:
    diff = ["w:pPr/w:kinsoku @w:val=1"]
    if variant.word_wrap is not None:
        diff.append(f"w:pPr/w:wordWrap @w:val={variant.word_wrap}")
    if variant.language_mode == "explicit_script_runs":
        diff.extend([
            "CJK source/suffix runs: w:rPr/w:lang @w:val=zh-CN @w:eastAsia=zh-CN",
            "Latin/numeric token run: w:rPr/w:lang @w:val=en-US @w:eastAsia=zh-CN",
        ])
    return {
        "variant_id": variant.variant_id,
        "approval_status": "EXPERIMENT_ONLY",
        "strategy": {
            "kinsoku": "1",
            "wordWrap": variant.word_wrap,
            "language_mode": variant.language_mode,
        },
        "exact_ooxml_diff": diff,
        "visible_text_sha256": text_hash,
        "expected_behavior": variant.expected_behavior,
        "known_risk": variant.known_risk,
    }


def _text_sha256(texts: list[str]) -> str:
    return sha256("\x1e".join(texts).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
