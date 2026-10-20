from booktrans.ooxml.format_fragment_decision_audit import FormatFragmentDecisionAudit


def _report(docx):
    return FormatFragmentDecisionAudit().run(docx)


def test_other_direct_property_is_decomposed_to_qname(format_audit_docx):
    report = _report(format_audit_docx)
    evidence = {item.property_name: item for item in report.direct_property_evidence}
    assert evidence["w:vertAlign"].block_count >= 1
    assert evidence["w:vertAlign"].evidence_category == "CLEARLY_VISUAL_OR_SEMANTIC"


def test_logical_region_distribution_is_repeatable(format_audit_docx):
    first = _report(format_audit_docx)
    second = _report(format_audit_docx)
    assert first.logical_region_distribution == second.logical_region_distribution


def test_list_item_is_not_forced_into_true_prose_fragment(format_audit_docx):
    report = _report(format_audit_docx)
    standalone = report.likely_legitimate_standalone.samples
    assert any(sample.source_text == "item" and "LIST_PARAGRAPH" in sample.structural_evidence for sample in standalone)


def test_cross_block_sentence_is_likely_fragment_and_ambiguous_case_stays_uncertain(format_audit_docx):
    report = _report(format_audit_docx)
    true_samples = report.likely_true_prose_fragment.samples
    uncertain_samples = report.uncertain.samples
    assert any(sample.source_text == "across blocks." for sample in true_samples)
    assert any(sample.source_text == "perhaps" for sample in uncertain_samples)
