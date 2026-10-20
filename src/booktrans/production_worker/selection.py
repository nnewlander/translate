"""Read-only frozen-selection helpers.  This module never writes selection files."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from lxml import etree

from booktrans.domain.calibration import (
    CalibrationAnchor,
    CalibrationRequest,
    CalibrationSourceSegment,
)
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView
from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    footnote_numbers_for_unit,
    load_exact_verified_evidence_registry,
    verified_numeric_bindings_for_request,
)
from booktrans.production_control.pdf_evidence_provider import PdfEvidenceRequirement, get_pdf_evidence
from booktrans.production_worker.artifacts import write_json_once


CHECKPOINT_SIZE = 25
# Batch membership is a policy limit, not a lifecycle shape.  The frozen
# selection remains the authority for the actual target of a particular batch.
BATCH_TARGET_MAX = 1000
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
DEFAULT_VIEWS = Path(r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl")
DEFAULT_BLOCKS = Path(r"D:\translate\runs\extract-20260914T050300Z-decb09-094777\blocks.jsonl")


class FrozenSelectionError(RuntimeError):
    """The worker cannot invent or mutate a frozen selection."""


class D050PreflightError(FrozenSelectionError):
    """An exact D050 marker/body/PDF proof could not be established."""


def checkpoint_number(checkpoint: str) -> int:
    try:
        value = int(checkpoint.rsplit("_", 1)[1])
    except (IndexError, ValueError) as exc:
        raise FrozenSelectionError("CHECKPOINT_IDENTIFIER_INVALID") from exc
    if value < 1:
        raise FrozenSelectionError("CHECKPOINT_IDENTIFIER_INVALID")
    return value


def checkpoint_count_for_target(target_count: int) -> int:
    """Return the number of 25-unit checkpoints required by a frozen target."""
    if not isinstance(target_count, int) or target_count < 1 or target_count > BATCH_TARGET_MAX:
        raise FrozenSelectionError("FROZEN_SELECTION_TARGET_COUNT_INVALID")
    return (target_count + CHECKPOINT_SIZE - 1) // CHECKPOINT_SIZE


def frozen_checkpoint_order(selection: Mapping[str, Any], checkpoint: str) -> list[str]:
    """Return one frozen checkpoint slice; never reorder or pad it.

    All non-terminal checkpoints contain ``CHECKPOINT_SIZE`` IDs.  The sole
    terminal checkpoint may be shorter when the immutable selection target is
    not divisible by that size.
    """
    number = checkpoint_number(checkpoint)
    ids = selection.get("selected_logical_work_order_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
        raise FrozenSelectionError("FROZEN_SELECTION_INVALID")
    if number > checkpoint_count_for_target(len(ids)):
        raise FrozenSelectionError("FROZEN_SELECTION_MISMATCH")
    start = (number - 1) * CHECKPOINT_SIZE
    order = [str(item) for item in ids[start : start + CHECKPOINT_SIZE]]
    if not order:
        raise FrozenSelectionError("FROZEN_SELECTION_MISMATCH")
    return order


def validate_frozen_checkpoint_members(
    *, target_count: int, selection_count: int, checkpoint: str, member_count: int
) -> bool:
    """Validate the exact frozen slice cardinality for one production checkpoint.

    The immutable selection and manifest must name the same target.  Every
    non-terminal slice is full; only the final derived slice can be shorter.
    """
    if (
        not isinstance(target_count, int)
        or target_count < 1
        or selection_count != target_count
        or not isinstance(member_count, int)
    ):
        return False
    try:
        number = checkpoint_number(checkpoint)
        total = checkpoint_count_for_target(target_count)
    except FrozenSelectionError:
        return False
    if number < 1 or number > total:
        return False
    expected = CHECKPOINT_SIZE if number < total else target_count - CHECKPOINT_SIZE * (total - 1)
    return member_count == expected and 1 <= expected <= CHECKPOINT_SIZE


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FrozenSelectionError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def load_jsonl_models(path: Path, model):
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_selected_jsonl_by_display_id(path: Path, model, display_ids: Sequence[str]) -> dict[str, Any]:
    """Load only the requested display IDs, stopping once the set is complete."""
    wanted = list(display_ids)
    remaining = set(wanted)
    found: dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not remaining:
                break
            text = line.strip()
            if not text:
                continue
            item = model.model_validate_json(text)
            display_id = getattr(item, "display_id", None)
            if display_id in remaining:
                found[str(display_id)] = item
                remaining.remove(display_id)
    return {display_id: found[display_id] for display_id in wanted if display_id in found}


def load_unit_uids_from_jsonl(path: Path) -> set[str]:
    found: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            unit_uid = payload.get("unit_uid")
            if isinstance(unit_uid, str) and unit_uid:
                found.add(unit_uid)
    return found


def selection_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def production_checkpoint_path(run: Path, batch_label: str, checkpoint: str) -> Path:
    suffix = batch_label.replace("FULL_BOOK_BATCH_", "").zfill(3)
    return run / f"full_book_batch_{suffix}" / checkpoint


def d050_paths_for_checkpoint(
    run: Path, checkpoint: str, *, batch_label: str = "FULL_BOOK_BATCH_022"
) -> list[Path]:
    """Return exact D050 registries for one active batch through this checkpoint.

    Historical chapter-21 artifacts use ``checkpoint03`` rather than
    ``checkpoint_03``.  Both names are accepted; missing files are not invented.
    """
    match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", batch_label)
    if not match:
        raise FrozenSelectionError("ACTIVE_BATCH_ID_INVALID")
    batch_suffix = match.group(1)
    number = checkpoint_number(checkpoint)
    found: list[Path] = []
    seen: set[Path] = set()
    # Semantic context can cross a checkpoint boundary. Its lane classifier
    # must retain already verified marker-to-footnote identities from the
    # active batch, rather than classifying a prior footnote body as prose.
    # Records remain exact-key scoped and do not authorize another work order.
    for prior_number in range(1, number + 1):
        canonical = f"checkpoint_{prior_number:02d}"
        compact = f"checkpoint{prior_number:02d}"
        patterns = (
            f"chapter_21_batch{batch_suffix}_{canonical}_verified_footnote_evidence_v*.json",
            f"chapter_21_batch{batch_suffix}_{compact}_verified_footnote_evidence_v*.json",
        )
        for pattern in patterns:
            paths = sorted(
                run.glob(pattern),
                key=lambda path: int(re.search(r"_v(\d+)\.json$", path.name).group(1)),
            )
            for path in paths:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                found.append(path)
    return found


def d050_canonical_evidence_path(
    run: Path, checkpoint: str, *, batch_label: str = "FULL_BOOK_BATCH_022"
) -> Path:
    """The create-once D050 registry path for one active-batch checkpoint."""
    match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", batch_label)
    if not match:
        raise FrozenSelectionError("ACTIVE_BATCH_ID_INVALID")
    return run / f"chapter_21_batch{match.group(1)}_{checkpoint}_verified_footnote_evidence_v1.json"


def _next_d050_evidence_path(run: Path, checkpoint: str, *, batch_label: str) -> Path:
    """Return the next create-once version for one checkpoint's D050 registry."""
    match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", batch_label)
    if not match:
        raise FrozenSelectionError("ACTIVE_BATCH_ID_INVALID")
    prefix = f"chapter_21_batch{match.group(1)}_{checkpoint}_verified_footnote_evidence_v"
    versions = [
        int(version.group(1))
        for path in run.glob(f"{prefix}*.json")
        if (version := re.search(r"_v(\d+)\.json$", path.name))
    ]
    return run / f"{prefix}{max(versions, default=0) + 1}.json"


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _WORD_NS}
_PARAGRAPH_RE = re.compile(r"/paragraph\[(\d+)\]/text\[(\d+)\]$")
_INTEGER = re.compile(r"^[0-9]+$")
_PRINTED_PAGE_RE = re.compile(r"(?m)(?:^(\d+)\s*\|\s*|\|\s*(\d+)\s*$)")
# A PDF-native candidate still needs a unique complete source paragraph, a
# distinct exact note body, and a unique PDF co-occurrence below.  In addition
# to the legacy ``identify19`` form, admit the literal sentence-terminal
# ``.5``/``!5``/``?5`` form exported by this DOCX.  This is not a generic
# numeric rule: the subsequent closed-world proof decides whether the suffix is
# an actual reference marker.
# A sentence-ending marker may follow a word (``presubmit8``) or punctuation
# (``change.8``).  The punctuation form must not consume the fractional part
# of a decimal such as ``99.9``: decimal digits are ordinary source content,
# never a D050 marker.
# Word-form candidates must end at the numeric suffix.  Without this lexical
# boundary ``g3doc`` is truncated to ``g3`` and can be falsely paired with an
# unrelated visible note numbered 3.  Punctuation-form candidates retain the
# established terminal-marker behavior and are still subject to full proof.
_PDF_NATIVE_REFERENCE = re.compile(r"(?:\b[A-Za-z][A-Za-z]*?(\d+)(?![A-Za-z0-9_])|(?<!\d)[.!?](\d+)(?!\d))")


def _pdf_native_word_marker_is_eligible(marker_token: str, marker: str) -> bool:
    """Reject alphanumeric technical identifiers from PDF-native D050 routing.

    The PDF fallback may prove an inline word-plus-number marker such as
    ``presubmit8``.  An all-uppercase acronym suffix such as ``EC2`` is a
    technical identifier, not a source footnote marker; treating it as one
    creates a false closed-world ambiguity against every note numbered 2.
    Structural OOXML links remain independently eligible and authoritative.
    """
    if not marker_token.endswith(marker):
        return False
    prefix = marker_token[:len(marker_token) - len(marker)]
    return bool(prefix) and any(character.islower() for character in prefix)
# A visible running header/footer such as ``100 | Chapter 5: ...`` is not a
# footnote body.  Retain normal footer-resident notes (``5 Note body...``),
# which do not carry the page-header pipe convention.  Without this boundary
# a source identifier such as ``replica100`` can be paired with an unrelated
# page header and falsely become a D050 candidate.
_PDF_NATIVE_NOTE_BODY = re.compile(r"^(?!\d+\s*\|)(\d+)\s+.+", re.DOTALL)


def _normalize_pdf_text(value: str) -> str:
    """Normalize extraction-only line wrapping; never infer source content."""
    # Poppler/pypdf expose the same visual line wrap in two forms across the
    # source PDF: a replacement glyph and U+2010 followed by a newline.  Both
    # exact pairs are extraction artifacts; ordinary hyphenated content stays
    # untouched.
    unwrapped = value.replace("\ufffd\\\n", "").replace("\u2010\n", "").replace("\ufffd", "")
    # The DOCX-derived IR represents that same visual wrap as a U+2010 token
    # surrounded by whitespace.  Limit this join to alphabetic fragments so a
    # real in-word hyphen (or any punctuation delimiter) remains untouched.
    unwrapped = re.sub(r"(?<=[A-Za-z])\s+\u2010\s+(?=[A-Za-z])", "", unwrapped)
    return " ".join(unwrapped.split())


def _word_tokens(value: str) -> list[str]:
    # PDF extraction can drop sentence-terminal punctuation (for example
    # ``TAP.`` in DOCX becomes ``TAP`` in the extracted page).  A period is
    # therefore a token separator, while identifiers whose spelling is
    # semantically material (``C++``, ``foo_bar`` and hyphenated terms) remain
    # intact.  This normalizes only the representation of the same source
    # fact; marker/body uniqueness is still required below.
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9+_-]*", _normalize_pdf_text(value).casefold())


def _contains_token_sequence(haystack: str, needle: str) -> bool:
    wanted = _word_tokens(needle)
    available = _word_tokens(haystack)
    if not wanted:
        return False
    width = len(wanted)
    if any(available[index : index + width] == wanted for index in range(len(available) - width + 1)):
        return True
    # DOCX may split an exact footnote body at a visual soft-hyphen boundary
    # (``Pro ‐``), while the PDF has the unsplit word (``Proceedings``).  The
    # prefix is admitted only when the source itself records that terminal
    # continuation marker; every preceding token remains exact and the caller
    # still requires a unique OOXML bookmark plus PDF page association.
    partial = re.search(r"([A-Za-z]+)\s*\u2010\s*$", needle)
    if partial is None or len(wanted) < 1:
        return False
    prefix = partial.group(1).casefold()
    exact_prefix = wanted[:-1]
    return any(
        available[index : index + len(exact_prefix)] == exact_prefix
        and len(available[index + len(exact_prefix)]) > len(prefix)
        and available[index + len(exact_prefix)].startswith(prefix)
        for index in range(len(available) - len(wanted) + 1)
    )


def _paragraph_and_text_index(xml_path: str) -> tuple[int, int]:
    match = _PARAGRAPH_RE.search(xml_path)
    if not match:
        raise D050PreflightError("D050_SOURCE_LOCATOR_INVALID")
    return int(match.group(1)), int(match.group(2))


def _hyperlink_bookmark_for_segment(paragraph: etree._Element, text_index: int, marker: str) -> str:
    texts = paragraph.xpath(".//w:t", namespaces=_NS)
    if not 1 <= text_index <= len(texts):
        raise D050PreflightError("D050_SOURCE_TEXT_INDEX_INVALID")
    text = texts[text_index - 1]
    if (text.text or "") != marker:
        raise D050PreflightError("D050_SOURCE_MARKER_TEXT_MISMATCH")
    current = text.getparent()
    while current is not None and current.tag != f"{{{_WORD_NS}}}hyperlink":
        current = current.getparent()
    anchor = current.get(f"{{{_WORD_NS}}}anchor") if current is not None else None
    if not anchor:
        raise D050PreflightError("D050_SOURCE_HYPERLINK_BOOKMARK_MISSING")
    return anchor


def _view_for_paragraph(views: Mapping[str, TranslationView], paragraph_ordinal: int) -> TranslationView:
    matches = [
        view
        for view in views.values()
        if view.base_template_locator
        and view.base_template_locator.part_name == "word/document.xml"
        and view.base_template_locator.paragraph_ordinal == paragraph_ordinal
    ]
    if len(matches) != 1:
        raise D050PreflightError("D050_FOOTNOTE_BODY_VIEW_AMBIGUOUS")
    return matches[0]


def _source_pdf_text_pages(source_pdf: Path) -> list[tuple[int, str]]:
    """Read page text from the immutable PDF; absence is a hard preflight block."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pypdf is already used by the source-registration workflow.
        raise D050PreflightError("D050_PDF_TEXT_EVIDENCE_UNAVAILABLE") from exc
    try:
        return [(index + 1, page.extract_text() or "") for index, page in enumerate(PdfReader(str(source_pdf)).pages)]
    except Exception as exc:
        raise D050PreflightError(f"D050_PDF_TEXT_EVIDENCE_INVALID:{type(exc).__name__}") from exc


def _printed_pdf_page(text: str) -> int | None:
    """Read only an explicit PDF page-footer representation.

    The source PDF uses both ``517 | Chapter`` and a standalone terminal
    ``517`` footer form.  The latter is accepted only when it is the final
    nonblank extracted line; it is not a search for arbitrary numbers in
    paragraph or footnote content.
    """
    printed = _PRINTED_PAGE_RE.search(text)
    if printed is not None:
        return int(printed.group(1) or printed.group(2))
    nonblank_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if nonblank_lines and re.fullmatch(r"\d+", nonblank_lines[-1]):
        return int(nonblank_lines[-1])
    return None


def _pdf_page_for_exact_footnote(
    *, source_pdf: Path, marker_context: str, footnote_body: str, pages: Sequence[tuple[int, str]] | None = None
) -> tuple[int, int, object]:
    """Return the sole PDF page containing both exact source facts, or fail closed."""
    pages = list(pages) if pages is not None else _source_pdf_text_pages(source_pdf)
    body = _normalize_pdf_text(footnote_body)
    context_tokens = _word_tokens(marker_context)
    # The source IR can preserve a visual line-break/soft-hyphen split inside
    # a word.  A short leading token sequence is sufficient only in addition
    # to the exact full footnote body and OOXML bookmark proof; ambiguity still
    # fails closed.
    context_probe = " ".join(context_tokens[:5])
    matches = [
        (page, text)
        for page, text in pages
        # PDF extraction changes quotation glyphs and soft-hyphen rendering.
        # The complete normalized source-token sequence is still an exact
        # footnote-body proof; retain the independent local marker context and
        # fail closed unless exactly one PDF page contains both facts.
        if _contains_token_sequence(text, body) and _contains_token_sequence(text, context_probe)
    ]
    if not matches:
        raise D050PreflightError("D050_PDF_MARKER_BODY_NOT_FOUND")
    if len(matches) > 1:
        raise D050PreflightError("D050_PDF_MARKER_BODY_AMBIGUOUS")
    page, text = matches[0]
    printed_page = _printed_pdf_page(text)
    if printed_page is None:
        raise D050PreflightError("D050_PDF_PRINTED_PAGE_MISSING")
    return page, printed_page, text


def _d050_exact_candidate_records(
    *,
    order: Sequence[str],
    views: Mapping[str, TranslationView],
    source_docx: Path,
    source_pdf: Path,
    source_sha256: str,
    source_pdf_sha256: str,
    run: Path,
) -> list[dict[str, object]]:
    """Build records only from explicit OOXML hyperlink/bookmark source facts.

    This deliberately does not infer from punctuation, typography, or arbitrary
    numbers.  A candidate exists only for an IR digit segment located inside an
    OOXML hyperlink with a concrete bookmark target.
    """
    try:
        from zipfile import ZipFile

        with ZipFile(source_docx) as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
    except Exception as exc:
        raise D050PreflightError(f"D050_SOURCE_OOXML_UNAVAILABLE:{type(exc).__name__}") from exc
    paragraphs = document.xpath(".//w:p", namespaces=_NS)
    bookmarks: dict[str, list[int]] = {}
    for index, paragraph in enumerate(paragraphs, start=1):
        for bookmark in paragraph.xpath(".//w:bookmarkStart/@w:name", namespaces=_NS):
            bookmarks.setdefault(str(bookmark), []).append(index)

    candidates: list[tuple[str, TranslationView, int, object]] = []
    for logical_id in order:
        view = views.get(logical_id)
        if view is None:
            raise D050PreflightError("D050_FROZEN_VIEW_MISSING")
        for segment_index, segment in enumerate(view.segments):
            if not _INTEGER.fullmatch(segment.text):
                continue
            # This is the exact structural footprint exported for a Word
            # hyperlink without an external rid, not a numeric heuristic.
            if segment.container_context.get("hyperlink_rid") != "":
                continue
            candidates.append((logical_id, view, segment_index, segment))

    pdf_pages = _source_pdf_text_pages(source_pdf) if candidates else []
    records: list[dict[str, object]] = []
    for logical_id, view, segment_index, segment in candidates:
        paragraph_ordinal, text_index = _paragraph_and_text_index(segment.template_locator.xml_path)
        if not 1 <= paragraph_ordinal <= len(paragraphs):
            raise D050PreflightError("D050_SOURCE_PARAGRAPH_INVALID")
        bookmark = _hyperlink_bookmark_for_segment(paragraphs[paragraph_ordinal - 1], text_index, segment.text)
        body_paragraphs = bookmarks.get(bookmark, [])
        if len(body_paragraphs) != 1:
            raise D050PreflightError("D050_BOOKMARK_TARGET_AMBIGUOUS")
        body_view = _view_for_paragraph(views, body_paragraphs[0])
        if not re.match(rf"^{re.escape(segment.text)}\s+", body_view.source_text):
            raise D050PreflightError("D050_FOOTNOTE_BODY_MARKER_MISMATCH")
        before = "".join(item.text for item in view.segments[:segment_index])
        after = "".join(item.text for item in view.segments[segment_index + 1 :])
        # Keep the PDF probe on source-token boundaries.  A character-count
        # tail can begin midway through a word (for example ``API`` → ``I``),
        # turning an otherwise unique exact PDF association into a false
        # ambiguity.  This supplies adjacent source context only; it does not
        # infer or alter a marker/body identity.
        after_tokens = _word_tokens(after)
        before_tokens = _word_tokens(before)
        marker_context = " ".join(after_tokens[:20] if after_tokens else before_tokens[-20:])
        page, printed_page, page_text = _pdf_page_for_exact_footnote(
            source_pdf=source_pdf,
            marker_context=marker_context,
            footnote_body=body_view.source_text,
            pages=pdf_pages,
        )
        pdf_evidence = get_pdf_evidence(
            source_pdf,
            PdfEvidenceRequirement(pdf_sha256=source_pdf_sha256, page_number=page),
            cache_root=run / "d050_pdf_evidence_cache",
        )
        records.append(
            {
                "verification_status": "VERIFIED",
                "unit_uid": view.unit_uid,
                "logical_work_order_id": logical_id,
                "pre_footnote_numeric_tokens": [],
                "original_pdf_page": page,
                "original_pdf_printed_page": printed_page,
                "pdf_corroboration": (
                    f"Original English PDF page {page} (printed page {printed_page}) contains the OOXML-linked "
                    f"marker {segment.text} in its source context and the bookmark-linked footnote body: {body_view.source_text!r}."
                ),
                "pdf_evidence": {
                    "provider_id": pdf_evidence.provider_id,
                    "provider_version": pdf_evidence.provider_version,
                    "page_number": page,
                    "representation_sha256": pdf_evidence.representation_sha256,
                },
                "exact_source_marker_token": segment.text,
                "footnote_number": segment.text,
                "source_segment_binding": [
                    {
                        "anchor_ref": segment.anchor_ref,
                        "text": segment.text,
                        "source_run_locator": segment.template_locator.xml_path,
                        "paragraph_ordinal": paragraph_ordinal,
                        "ooxml_hyperlink_anchor": bookmark,
                    }
                ],
                "corresponding_footnote_body": {
                    "display_id": body_view.display_id,
                    "unit_uid": body_view.unit_uid,
                    "source_run_locators": [body_view.base_template_locator.xml_path],
                    "source_text": body_view.source_text,
                    "ooxml_bookmark": bookmark,
                },
            }
        )
    return records


def _pdf_page_for_native_exact_reference(
    *,
    source_paragraph: str,
    marker_token: str,
    footnote_body: str,
    pages: Sequence[tuple[int, str]],
) -> tuple[int, int, str]:
    """Prove one PDF-native marker/body association without positional inference."""
    matches = _pdf_native_exact_matches(
        source_paragraph=source_paragraph,
        marker_token=marker_token,
        footnote_body=footnote_body,
        pages=pages,
    )
    if len(matches) != 1:
        raise D050PreflightError("D050_PDF_NATIVE_MARKER_BODY_AMBIGUOUS")
    return matches[0]


def _pdf_native_exact_matches(
    *,
    source_paragraph: str,
    marker_token: str,
    footnote_body: str,
    pages: Sequence[tuple[int, str]],
) -> list[tuple[int, int, str]]:
    """Return every complete source paragraph/marker/note PDF match."""
    # OOXML can keep an inline marker adjacent to its preceding word
    # (``presubmit8``), while PDF text extraction exposes the same proven
    # marker as a separately spaced token (``presubmit 8``).  The marker is
    # already an exact, syntactically detected source fact; accepting only
    # these two physical renderings preserves closed-world identity without
    # guessing a footnote association.
    marker_digits = re.search(r"\d+$", marker_token)
    if marker_digits is None:
        boundary_normalized_paragraph = source_paragraph
    else:
        prefix = marker_token[:marker_digits.start()]
        boundary_normalized_paragraph = source_paragraph.replace(
            marker_token, f"{prefix} {marker_digits.group(0)}", 1,
        )
    matches: list[tuple[int, int, str]] = []
    for page, text in pages:
        if not (
            (
                _contains_token_sequence(text, source_paragraph)
                or _contains_token_sequence(text, boundary_normalized_paragraph)
            )
            and _contains_token_sequence(text, footnote_body)
        ):
            continue
        printed_page = _printed_pdf_page(text)
        if printed_page is None:
            raise D050PreflightError("D050_PDF_PRINTED_PAGE_MISSING")
        matches.append((page, printed_page, text))
    return matches


def _d050_pdf_native_exact_candidate_records(
    *,
    order: Sequence[str],
    views: Mapping[str, TranslationView],
    source_pdf: Path,
    source_docx_sha256: str,
    source_pdf_sha256: str,
    run: Path,
    structural_records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Build D050 fallback records only when the PDF and source views prove one link.

    This is deliberately not a typography or punctuation detector.  A fallback
    begins only with an inline marker whose number has exactly one distinct,
    visible source note body.  It then requires one PDF page containing the
    complete source paragraph, that exact marker token, and that complete note
    body.  Any duplicate candidate fails closed.
    """
    structural_keys = {
        (str(record.get("logical_work_order_id")), str(record.get("exact_source_marker_token")))
        for record in structural_records
    }
    notes: dict[str, list[TranslationView]] = {}
    for view in views.values():
        match = _PDF_NATIVE_NOTE_BODY.fullmatch(view.source_text.strip())
        if match and view.base_template_locator and view.segments:
            notes.setdefault(match.group(1), []).append(view)

    candidates: dict[str, list[tuple[str, TranslationView, object, str]]] = {}
    for logical_id in order:
        view = views.get(logical_id)
        if view is None:
            raise D050PreflightError("D050_FROZEN_VIEW_MISSING")
        for segment in view.segments:
            for match in _PDF_NATIVE_REFERENCE.finditer(segment.text):
                marker = match.group(1) or match.group(2)
                marker_token = match.group(0)
                if marker_token[0].isalpha() and not _pdf_native_word_marker_is_eligible(marker_token, marker):
                    continue
                if marker not in notes or (logical_id, marker) in structural_keys:
                    continue
                if segment.text.count(marker_token) != 1 or view.source_text.count(marker_token) != 1:
                    raise D050PreflightError("D050_PDF_NATIVE_SOURCE_MARKER_AMBIGUOUS")
                candidates.setdefault(marker, []).append((logical_id, view, segment, marker_token))

    pages = _source_pdf_text_pages(source_pdf) if candidates else []
    records: list[dict[str, object]] = []
    for marker, values in sorted(candidates.items()):
        if len(values) != 1:
            raise D050PreflightError("D050_PDF_NATIVE_MARKER_SCOPE_AMBIGUOUS")
        logical_id, view, segment, marker_token = values[0]
        if not view.base_template_locator or view.base_template_locator.part_name != "word/document.xml":
            raise D050PreflightError("D050_PDF_NATIVE_SOURCE_BLOCK_INVALID")
        proven: list[tuple[TranslationView, int, int, str]] = []
        for note_view in notes[marker]:
            if note_view.display_id == view.display_id or note_view.base_template_locator == view.base_template_locator:
                continue
            matches = _pdf_native_exact_matches(
                source_paragraph=view.source_text,
                marker_token=marker_token,
                footnote_body=note_view.source_text,
                pages=pages,
            )
            if len(matches) > 1:
                raise D050PreflightError("D050_PDF_NATIVE_MARKER_BODY_AMBIGUOUS")
            if matches:
                page, printed_page, page_text = matches[0]
                proven.append((note_view, page, printed_page, page_text))
        if len(proven) != 1:
            raise D050PreflightError("D050_PDF_NATIVE_NOTE_BODY_AMBIGUOUS")
        note_view, page, printed_page, _page_text = proven[0]
        pdf_evidence = get_pdf_evidence(
            source_pdf,
            PdfEvidenceRequirement(pdf_sha256=source_pdf_sha256, page_number=page),
            cache_root=run / "d050_pdf_evidence_cache",
        )
        records.append(
            {
                "verification_status": "VERIFIED",
                "verification_method": "PDF_NATIVE_EXACT",
                "unit_uid": view.unit_uid,
                "logical_work_order_id": logical_id,
                "source_docx_sha256": source_docx_sha256,
                "source_pdf_sha256": source_pdf_sha256,
                "pre_footnote_numeric_tokens": [],
                "original_pdf_page": page,
                "original_pdf_printed_page": printed_page,
                "pdf_corroboration": (
                    f"Source PDF page {page} (printed page {printed_page}) uniquely contains the complete "
                    f"source paragraph with marker token {marker_token!r} and the distinct exact note body "
                    f"{note_view.source_text!r}."
                ),
                "pdf_evidence": {
                    "provider_id": pdf_evidence.provider_id,
                    "provider_version": pdf_evidence.provider_version,
                    "page_number": page,
                    "representation_sha256": pdf_evidence.representation_sha256,
                },
                "exact_source_marker_token": marker,
                "footnote_number": marker,
                "source_segment_binding": [
                    {
                        "anchor_ref": segment.anchor_ref,
                        "text": segment.text,
                        "source_run_locator": segment.template_locator.xml_path,
                        "paragraph_ordinal": view.base_template_locator.paragraph_ordinal,
                    }
                ],
                "corresponding_footnote_body": {
                    "display_id": note_view.display_id,
                    "unit_uid": note_view.unit_uid,
                    "source_run_locators": [note_view.base_template_locator.xml_path],
                    "source_text": note_view.source_text,
                },
            }
        )
    return records


def ensure_checkpoint_d050_preflight(
    *,
    run: Path,
    checkpoint: str,
    order: Sequence[str],
    views: Mapping[str, TranslationView],
    source_docx: Path,
    source_pdf: Path,
    source_sha256: str,
    source_pdf_sha256: str,
    batch_label: str = "FULL_BOOK_BATCH_022",
) -> dict[str, object]:
    """Create or reuse exact D050 evidence before any Translation call.

    Existing records are immutable.  DOCX structural proof remains preferred;
    PDF-native proof is admitted only when its complete closed-world binding is
    unique.  Later discoveries create a new immutable registry version rather
    than extending or overwriting an earlier one.
    """
    structural_records = _d050_exact_candidate_records(
        order=order,
        views=views,
        source_docx=source_docx,
        source_pdf=source_pdf,
        source_sha256=source_sha256,
        source_pdf_sha256=source_pdf_sha256,
        run=run,
    )
    native_records = _d050_pdf_native_exact_candidate_records(
        order=order,
        views=views,
        source_pdf=source_pdf,
        source_docx_sha256=source_sha256,
        source_pdf_sha256=source_pdf_sha256,
        run=run,
        structural_records=structural_records,
    )
    records = [*structural_records, *native_records]
    existing_paths = d050_paths_for_checkpoint(run, checkpoint, batch_label=batch_label)
    registry = load_exact_verified_evidence_registry(tuple(existing_paths)) if existing_paths else {}
    candidate_keys = {(record["logical_work_order_id"], record["unit_uid"], record["exact_source_marker_token"]) for record in records}
    existing_keys = {
        (logical_id, unit_uid, str(record.get("exact_source_marker_token")))
        for (logical_id, unit_uid), items in registry.items()
        for record in items
    }
    existing_logical_marker_keys = {
        (logical_id, str(record.get("exact_source_marker_token")), unit_uid)
        for (logical_id, unit_uid), items in registry.items()
        for record in items
    }
    for logical_id, unit_uid, marker in candidate_keys:
        if any(
            known_logical == logical_id and known_marker == marker and known_uid != unit_uid
            for known_logical, known_marker, known_uid in existing_logical_marker_keys
        ):
            raise D050PreflightError("D050_EXISTING_REGISTRY_INCOMPLETE")
    counts = {
        "structural_exact": len(structural_records),
        "pdf_native_exact": len(native_records),
    }
    if candidate_keys <= existing_keys:
        return {
            "status": "REUSED",
            "candidate_count": len(records),
            "path": str(existing_paths[-1]) if existing_paths else None,
            **counts,
        }
    new_records = [
        record
        for record in records
        if (record["logical_work_order_id"], record["unit_uid"], record["exact_source_marker_token"]) not in existing_keys
    ]
    target = _next_d050_evidence_path(run, checkpoint, batch_label=batch_label)
    payload: dict[str, object] = {
        "artifact_type": f"CHAPTER_21_{batch_label}_{checkpoint.upper()}_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD_V2",
        "source_docx": str(source_docx),
        "source_sha256": source_sha256,
        "source_pdf": str(source_pdf),
        "source_pdf_sha256": source_pdf_sha256,
        "pdf_evidence_provider": "POPPLER_PDFTOPPM",
        "candidate_count": len(new_records),
        "verified_count": len(new_records),
        "ambiguous_count": 0,
        "unverified_candidates": [],
        "records": new_records,
        "scope_guard": (
            "This checkpoint-bounded artifact authorizes only each listed exact logical_work_order_id, unit_uid, "
            "marker, and either an OOXML hyperlink/bookmark binding or a unique PDF-native complete-paragraph plus "
            "distinct visible-note-body binding. It does not "
            "authorize typographic, punctuation, positional, or generic numeric inference."
        ),
    }
    try:
        write_json_once(target, payload)
    except ValueError as exc:
        raise D050PreflightError("D050_EVIDENCE_CREATE_ONCE_CONFLICT") from exc
    return {"status": "CREATED", "candidate_count": len(records), "path": str(target), **counts}


def overlay_verified_d050_bindings(
    request: CalibrationRequest,
    registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
) -> CalibrationRequest:
    """Attach exact D050 bindings for QA.  Does not rewrite persisted request files."""
    evidence = exact_evidence_for_work_order(
        registry, logical_work_order_id=request.display_id, unit_uid=request.unit_uid
    )
    if not evidence:
        return request
    numbers = footnote_numbers_for_unit(evidence, unit_uid=request.unit_uid, source_text=request.source_text)
    updated = request.model_copy(update={"footnote_reference_numbers": numbers})
    return updated.model_copy(
        update={"verified_footnote_numeric_bindings": verified_numeric_bindings_for_request(evidence, updated)}
    )


def load_checkpoint_d050_registry(
    run: Path, checkpoint: str, *, batch_label: str = "FULL_BOOK_BATCH_022"
) -> dict[tuple[str, str], tuple[dict[str, object], ...]]:
    paths = d050_paths_for_checkpoint(run, checkpoint, batch_label=batch_label)
    return load_exact_verified_evidence_registry(tuple(paths)) if paths else {}


def assert_not_production_checkpoint(output: Path, *, run: Path, batch_label: str, checkpoint: str) -> None:
    batch_dir = production_checkpoint_path(run, batch_label, checkpoint).parent.resolve()
    resolved = output.resolve()
    try:
        resolved.relative_to(batch_dir)
    except ValueError:
        pass
    else:
        raise FrozenSelectionError(f"ISOLATED_OUTPUT_COLLIDES_WITH_PRODUCTION:{batch_dir}")
    parts = [part.lower() for part in resolved.parts]
    if "full_book_batch_022" in parts and checkpoint.lower() in parts:
        raise FrozenSelectionError(f"ISOLATED_OUTPUT_COLLIDES_WITH_PRODUCTION:{resolved}")


def build_calibration_request(
    view: TranslationView,
    block: BlockIR,
    *,
    source_sha256: str,
    evidence: Sequence[Mapping[str, object]] = (),
    selection_categories: Sequence[str] | None = None,
) -> CalibrationRequest:
    """Exporter-equivalent request.  D050 evidence is routed only by exact identity."""
    by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    request = CalibrationRequest(
        source_sha256=source_sha256,
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        story=block.story,
        content_role=block.block_type,
        source_text=view.source_text,
        source_segments=[
            CalibrationSourceSegment(kind=segment.kind, text=segment.text, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment in view.segments
        ],
        available_anchors=[
            CalibrationAnchor(
                anchor_id=anchor.anchor_id,
                semantics=list(anchor.kinds),
                protected=anchor.protected,
                protected_text=by_anchor.get(anchor.anchor_id) if anchor.protected else None,
                container="hyperlink" if "hyperlink_rid" in anchor.container_context else None,
            )
            for anchor in view.anchors
        ],
        continuation_hint=view.continuation_hint,
        previous_text=view.previous_text,
        next_text=view.next_text,
        selection_categories=list(selection_categories or []),
        footnote_reference_numbers=footnote_numbers_for_unit(evidence, unit_uid=view.unit_uid, source_text=view.source_text),
    )
    return request.model_copy(
        update={"verified_footnote_numeric_bindings": verified_numeric_bindings_for_request(evidence, request)}
    )


def load_checkpoint_requests(
    *,
    selection: Mapping[str, Any],
    checkpoint: str,
    views: Mapping[str, TranslationView],
    blocks: Mapping[str, BlockIR],
    source_sha256: str,
    d050_paths: Sequence[Path] = (),
    selection_categories: Sequence[str] | None = None,
) -> tuple[list[str], list[CalibrationRequest]]:
    order = frozen_checkpoint_order(selection, checkpoint)
    if any(item not in views or item not in blocks for item in order):
        raise FrozenSelectionError("FROZEN_SOURCE_UNITS_MISSING")
    registry = load_exact_verified_evidence_registry(tuple(d050_paths)) if d050_paths else {}
    requests: list[CalibrationRequest] = []
    for item in order:
        view = views[item]
        evidence = exact_evidence_for_work_order(registry, logical_work_order_id=item, unit_uid=view.unit_uid)
        requests.append(
            build_calibration_request(
                view,
                blocks[item],
                source_sha256=source_sha256,
                evidence=evidence,
                selection_categories=selection_categories,
            )
        )
    return order, requests
