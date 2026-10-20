from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from booktrans.domain.ir import SourceLocator
from booktrans.domain.translation_view import TranslationSegment, TranslationView
from booktrans.production_worker.selection import (
    D050PreflightError,
    _PDF_NATIVE_NOTE_BODY,
    _PDF_NATIVE_REFERENCE,
    _contains_token_sequence,
    _normalize_pdf_text,
    d050_paths_for_checkpoint,
    ensure_checkpoint_d050_preflight,
)


def _locator(paragraph: int, text: int = 1) -> SourceLocator:
    return SourceLocator(
        part_name="word/document.xml",
        story="body",
        xml_path=f"/word/document.xml/paragraph[{paragraph}]/text[{text}]",
        container_path=f"/word/document.xml/paragraph[{paragraph}]",
        paragraph_ordinal=paragraph,
    )


def test_pdf_text_normalization_joins_only_u2010_line_wrap() -> None:
    assert _normalize_pdf_text("com\u2010\npatibility") == "compatibility"
    assert _normalize_pdf_text("com \u2010 patibility") == "compatibility"
    assert _normalize_pdf_text("well\u2010known") == "well‐known"


def test_pdf_exact_token_comparison_ignores_extraction_only_terminal_period() -> None:
    # DOCX and the PDF extraction retain the same words, but the latter drops
    # a terminal full stop.  This is representation normalization, not a
    # relaxed marker/body association.
    assert _contains_token_sequence("At Google, release automation is managed by TAP", "At Google, release automation is managed by TAP.")


def test_pdf_exact_token_comparison_accepts_only_declared_terminal_soft_hyphen_prefix() -> None:
    """A DOCX visual continuation can bind its one unsplit PDF word."""
    assert _contains_token_sequence("13 See Proceedings of the conference.", "13 See Pro \u2010")
    assert _contains_token_sequence("workloads are isolated", "work\u2010")
    assert not _contains_token_sequence("13 See Proceedings of the conference.", "13 See Pro")


def test_pdf_footnote_binding_uses_complete_token_sequence_when_pdf_quotes_differ(monkeypatch) -> None:
    from booktrans.production_worker.selection import _pdf_page_for_exact_footnote

    monkeypatch.setattr(
        "booktrans.production_worker.selection._source_pdf_text_pages",
        lambda _path: [(7, "7 | 12 In a world of tests This is a behavioral change.")],
    )
    page, _printed, _text = _pdf_page_for_exact_footnote(
        source_pdf=Path("ignored.pdf"),
        marker_context="in a world of tests",
        footnote_body="12 In a world of tests ‘This is a behavioral change.’",
    )
    assert page == 7


def test_pdf_footnote_binding_accepts_only_a_terminal_standalone_page_footer(monkeypatch) -> None:
    from booktrans.production_worker.selection import _pdf_page_for_exact_footnote

    monkeypatch.setattr(
        "booktrans.production_worker.selection._source_pdf_text_pages",
        lambda _path: [(545, "A unique source context. 1 Exact footnote body.\n517\n")],
    )
    page, printed, _text = _pdf_page_for_exact_footnote(
        source_pdf=Path("ignored.pdf"), marker_context="A unique source context",
        footnote_body="1 Exact footnote body.",
    )
    assert (page, printed) == (545, 517)


def _views(*, duplicate_body_bookmark: bool = False) -> dict[str, TranslationView]:
    marker = TranslationView(
        unit_uid="marker-uid",
        display_id="body.p.marker",
        source_text="Semantic versioning.6 Next sentence.",
        segments=[
            TranslationSegment(segment_id="before", text="Semantic versioning.", kind="anchored", anchor_ref="before", template_locator=_locator(1)),
            TranslationSegment(segment_id="marker", text="6", kind="anchored", anchor_ref="marker-anchor", template_locator=_locator(1, 2), container_context={"hyperlink_rid": ""}),
            TranslationSegment(segment_id="after", text=" Next sentence.", kind="anchored", anchor_ref="after", template_locator=_locator(1, 3)),
        ],
        base_template_locator=_locator(1),
    )
    footnote = TranslationView(
        unit_uid="footnote-uid",
        display_id="body.p.footnote",
        source_text="6 Exact matching footnote body.",
        segments=[TranslationSegment(segment_id="body", text="6 Exact matching footnote body.", kind="anchored", anchor_ref="body-anchor", template_locator=_locator(2))],
        base_template_locator=_locator(2),
    )
    return {marker.display_id: marker, footnote.display_id: footnote}


def _pdf_native_views(*, duplicate_note: bool = False) -> dict[str, TranslationView]:
    marker = TranslationView(
        unit_uid="native-marker-uid",
        display_id="body.p.native-marker",
        source_text="The project authors could identify19 the shared API subset.",
        segments=[
            TranslationSegment(
                segment_id="paragraph",
                text="The project authors could identify19 the shared API subset.",
                kind="anchored",
                anchor_ref="native-marker-anchor",
                template_locator=_locator(1),
            )
        ],
        base_template_locator=_locator(1),
    )
    note = TranslationView(
        unit_uid="native-note-uid",
        display_id="body.p.native-note",
        source_text="19 Exact PDF-native note body.",
        segments=[TranslationSegment(segment_id="body", text="19 Exact PDF-native note body.", kind="anchored", anchor_ref="native-note-anchor", template_locator=_locator(2))],
        base_template_locator=_locator(2),
    )
    result = {marker.display_id: marker, note.display_id: note}
    if duplicate_note:
        result["body.p.native-note-duplicate"] = note.model_copy(
            update={"unit_uid": "native-note-duplicate-uid", "display_id": "body.p.native-note-duplicate", "base_template_locator": _locator(3)}
        )
    return result


def _pdf_native_terminal_marker_views() -> dict[str, TranslationView]:
    marker = TranslationView(
        unit_uid="native-terminal-marker-uid",
        display_id="body.p.native-terminal-marker",
        source_text="The operation becomes more complex.5",
        segments=[
            TranslationSegment(
                segment_id="paragraph",
                text="The operation becomes more complex.5",
                kind="anchored",
                anchor_ref="native-terminal-marker-anchor",
                template_locator=_locator(1),
            )
        ],
        base_template_locator=_locator(1),
    )
    note = TranslationView(
        unit_uid="native-terminal-note-uid",
        display_id="footer.native-terminal-note",
        source_text="5 Exact PDF-native terminal-marker note body.",
        segments=[TranslationSegment(segment_id="body", text="5 Exact PDF-native terminal-marker note body.", kind="anchored", anchor_ref="native-terminal-note-anchor", template_locator=_locator(2))],
        base_template_locator=_locator(2),
    )
    return {marker.display_id: marker, note.display_id: note}


def _docx(path: Path, *, duplicate_body_bookmark: bool = False) -> None:
    extra = '<w:p><w:bookmarkStart w:id="2" w:name="bookmark1"/><w:r><w:t>6 Other body.</w:t></w:r></w:p>' if duplicate_body_bookmark else ""
    xml = f'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Semantic versioning.</w:t></w:r><w:hyperlink w:anchor="bookmark1"><w:r><w:t>6</w:t></w:r></w:hyperlink><w:r><w:t> Next sentence.</w:t></w:r></w:p>
      <w:p><w:bookmarkStart w:id="1" w:name="bookmark1"/><w:r><w:t>6 Exact matching footnote body.</w:t></w:r></w:p>
      {extra}</w:body></w:document>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def _patch_pdf(monkeypatch) -> None:
    monkeypatch.setattr("booktrans.production_worker.selection._source_pdf_text_pages", lambda *_: [(100, "exact source facts")])
    monkeypatch.setattr("booktrans.production_worker.selection._pdf_page_for_exact_footnote", lambda **_: (100, 72, "exact source facts"))
    monkeypatch.setattr(
        "booktrans.production_worker.selection.get_pdf_evidence",
        lambda *args, **kwargs: SimpleNamespace(provider_id="POPPLER_PDFTOPPM", provider_version="test", representation_sha256="a" * 64),
    )


def _preflight(tmp_path: Path, monkeypatch) -> dict[str, object]:
    _patch_pdf(monkeypatch)
    docx = tmp_path / "source.docx"
    _docx(docx)
    return ensure_checkpoint_d050_preflight(
        run=tmp_path,
        checkpoint="checkpoint_06",
        order=["body.p.marker"],
        views=_views(),
        source_docx=docx,
        source_pdf=tmp_path / "source.pdf",
        source_sha256="b" * 64,
        source_pdf_sha256="c" * 64,
    )


def test_cp06_like_marker_is_generated_before_translation_and_reused(tmp_path: Path, monkeypatch) -> None:
    created = _preflight(tmp_path, monkeypatch)
    assert created["status"] == "CREATED"
    reused = _preflight(tmp_path, monkeypatch)
    assert reused["status"] == "REUSED"


def test_active_d050_paths_include_prior_checkpoint_evidence_for_context_lanes(tmp_path: Path) -> None:
    prior = tmp_path / "chapter_21_batch022_checkpoint_06_verified_footnote_evidence_v1.json"
    current = tmp_path / "chapter_21_batch022_checkpoint_07_verified_footnote_evidence_v1.json"
    prior.write_text("{}", encoding="utf-8")
    current.write_text("{}", encoding="utf-8")
    assert d050_paths_for_checkpoint(tmp_path, "checkpoint_07") == [prior, current]


def test_wrong_uid_in_existing_registry_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _preflight(tmp_path, monkeypatch)
    evidence = tmp_path / "chapter_21_batch022_checkpoint_06_verified_footnote_evidence_v1.json"
    text = evidence.read_text(encoding="utf-8").replace("marker-uid", "wrong-uid")
    evidence.unlink()
    evidence.write_text(text, encoding="utf-8")
    with pytest.raises(D050PreflightError, match="D050_EXISTING_REGISTRY_INCOMPLETE"):
        _preflight(tmp_path, monkeypatch)


def test_ambiguous_bookmark_target_hard_blocks_before_pdf_provider(tmp_path: Path, monkeypatch) -> None:
    _patch_pdf(monkeypatch)
    docx = tmp_path / "source.docx"
    _docx(docx, duplicate_body_bookmark=True)
    with pytest.raises(D050PreflightError, match="D050_BOOKMARK_TARGET_AMBIGUOUS"):
        ensure_checkpoint_d050_preflight(
            run=tmp_path,
            checkpoint="checkpoint_06",
            order=["body.p.marker"],
            views=_views(),
            source_docx=docx,
            source_pdf=tmp_path / "source.pdf",
            source_sha256="b" * 64,
            source_pdf_sha256="c" * 64,
        )


def test_pdf_native_exact_fallback_requires_unique_full_paragraph_and_visible_note(tmp_path: Path, monkeypatch) -> None:
    _patch_pdf(monkeypatch)
    monkeypatch.setattr(
        "booktrans.production_worker.selection._source_pdf_text_pages",
        lambda *_: [(100, "100 | The project authors could identify19 the shared API subset. 19 Exact PDF-native note body.")],
    )
    docx = tmp_path / "source.docx"
    _docx(docx)
    result = ensure_checkpoint_d050_preflight(
        run=tmp_path,
        checkpoint="checkpoint_06",
        order=["body.p.native-marker"],
        views=_pdf_native_views(),
        source_docx=docx,
        source_pdf=tmp_path / "source.pdf",
        source_sha256="b" * 64,
        source_pdf_sha256="c" * 64,
    )
    assert result["status"] == "CREATED"
    assert result["structural_exact"] == 0
    assert result["pdf_native_exact"] == 1
    payload = __import__("json").loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["records"][0]["verification_method"] == "PDF_NATIVE_EXACT"
    assert payload["records"][0]["corresponding_footnote_body"]["display_id"] == "body.p.native-note"


def test_pdf_native_exact_accepts_only_explicit_marker_spacing_representation() -> None:
    """PDF extraction may space an OOXML-inline marker without changing identity."""
    from booktrans.production_worker.selection import _pdf_native_exact_matches

    source = "Most teams run small tests on presubmit8—these are fast."
    note = "8 Exact note body."
    matches = _pdf_native_exact_matches(
        source_paragraph=source,
        marker_token="presubmit8",
        footnote_body=note,
        pages=[(514, "514 | Most teams run small tests on presubmit 8—these are fast. 8 Exact note body.")],
    )
    assert [(page, printed) for page, printed, _text in matches] == [(514, 514)]


def test_pdf_native_reference_does_not_treat_decimal_fraction_as_footnote_marker() -> None:
    """A decimal is source content, not an inline D050 marker candidate."""
    assert list(_PDF_NATIVE_REFERENCE.finditer("Set a 99.9% availability target.")) == []


def test_pdf_native_reference_rejects_uppercase_acronym_numeric_suffix() -> None:
    """EC2 is an identifier, whereas presubmit8 remains a possible marker."""
    from booktrans.production_worker.selection import _pdf_native_word_marker_is_eligible

    assert _pdf_native_word_marker_is_eligible("presubmit8", "8")
    assert not _pdf_native_word_marker_is_eligible("EC2", "2")


def test_pdf_native_word_form_requires_a_terminal_numeric_suffix() -> None:
    """Digits inside an identifier cannot enter D050 association routing."""
    assert _PDF_NATIVE_REFERENCE.search("g3doc") is None
    assert _PDF_NATIVE_REFERENCE.search("abc3def") is None
    assert _PDF_NATIVE_REFERENCE.search("v2beta") is None
    assert _PDF_NATIVE_REFERENCE.search("foo12bar") is None
    for text, marker in (("foo3", "3"), ("foo12", "12"), ("foo3,", "3"), ("foo3)", "3")):
        match = _PDF_NATIVE_REFERENCE.search(text)
        assert match is not None
        assert marker in match.groups()


def test_pdf_native_note_body_does_not_treat_page_header_as_footnote() -> None:
    """A page header cannot make an identifier suffix into a D050 marker."""
    assert _PDF_NATIVE_NOTE_BODY.fullmatch("100      |      Chapter 5: How to Lead a Team") is None
    assert _PDF_NATIVE_NOTE_BODY.fullmatch("5 Exact footer-resident note body.")


def test_pdf_native_duplicate_note_body_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _patch_pdf(monkeypatch)
    docx = tmp_path / "source.docx"
    _docx(docx)
    with pytest.raises(D050PreflightError, match="D050_PDF_NATIVE_NOTE_BODY_AMBIGUOUS"):
        ensure_checkpoint_d050_preflight(
            run=tmp_path,
            checkpoint="checkpoint_06",
            order=["body.p.native-marker"],
            views=_pdf_native_views(duplicate_note=True),
            source_docx=docx,
            source_pdf=tmp_path / "source.pdf",
            source_sha256="b" * 64,
            source_pdf_sha256="c" * 64,
        )


def test_pdf_native_terminal_marker_requires_the_same_closed_world_proof(tmp_path: Path, monkeypatch) -> None:
    _patch_pdf(monkeypatch)
    monkeypatch.setattr(
        "booktrans.production_worker.selection._source_pdf_text_pages",
        lambda *_: [(100, "100 | The operation becomes more complex.5 5 Exact PDF-native terminal-marker note body.")],
    )
    docx = tmp_path / "source.docx"
    _docx(docx)
    result = ensure_checkpoint_d050_preflight(
        run=tmp_path,
        checkpoint="checkpoint_06",
        order=["body.p.native-terminal-marker"],
        views=_pdf_native_terminal_marker_views(),
        source_docx=docx,
        source_pdf=tmp_path / "source.pdf",
        source_sha256="b" * 64,
        source_pdf_sha256="c" * 64,
    )
    payload = __import__("json").loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["records"][0]["exact_source_marker_token"] == "5"
    assert payload["records"][0]["corresponding_footnote_body"]["display_id"] == "footer.native-terminal-note"
