from booktrans.ooxml.format_unit_audit import FormatTranslationUnitAudit


def test_format_audit_collapses_language_only_run_fragmentation(format_audit_docx):
    report = FormatTranslationUnitAudit().run(format_audit_docx)
    assert report.normalization_collapsible_blocks >= 1
    assert report.collapsible_categories["LANGUAGE_METADATA_ONLY"] >= 1


def test_format_audit_keeps_bold_and_italic_as_true_differences(format_audit_docx):
    report = FormatTranslationUnitAudit().run(format_audit_docx)
    assert report.true_format_difference_categories["BOLD_DIFFERENCE"] >= 1
    assert report.true_format_difference_categories["ITALIC_DIFFERENCE"] >= 1


def test_hyperlink_and_protected_boundaries_do_not_disappear_under_normalization(m2_docx, format_audit_docx):
    hyperlink_report = FormatTranslationUnitAudit().run(m2_docx)
    protected_report = FormatTranslationUnitAudit().run(format_audit_docx)
    assert hyperlink_report.remaining_complex_reasons["HYPERLINK_BOUNDARY"] >= 1
    assert protected_report.remaining_complex_reasons["PROTECTED_CONTENT"] >= 1


def test_short_heading_is_not_fragment_but_lowercase_sentence_piece_is(format_audit_docx):
    report = FormatTranslationUnitAudit().run(format_audit_docx)
    assert report.unit_audit.short_heading_or_title_blocks >= 1
    samples = report.unit_audit.suspicious_fragment_samples
    assert any(sample.source_text == "friends," for sample in samples)
    assert all(sample.source_text != "Short Title" for sample in samples)
