from booktrans.production_worker.review_issue_policy import AUTO_POLISH, BLOCKING_SEMANTIC, COSMETIC_DEBT, review_issue_severity


def test_final_review_issue_severity_is_small_and_fail_closed() -> None:
    assert review_issue_severity("fluency") == AUTO_POLISH
    assert review_issue_severity("SPACING") == AUTO_POLISH
    assert review_issue_severity("SPACING_ERROR") == AUTO_POLISH
    assert review_issue_severity("PUNCTUATION_ERROR") == AUTO_POLISH
    assert review_issue_severity("PUNCTUATION_SPACING") == AUTO_POLISH
    assert review_issue_severity("CONTINUATION_PUNCTUATION_ERROR") == AUTO_POLISH
    assert review_issue_severity("FOOTNOTE_PLACEMENT") == AUTO_POLISH
    assert review_issue_severity("style_preference") == COSMETIC_DEBT
    assert review_issue_severity("INCOMPLETE_SOURCE_SPAN") == COSMETIC_DEBT
    assert review_issue_severity("MISALIGNMENT") == COSMETIC_DEBT
    assert review_issue_severity("PUNCTUATION_SPACE") == AUTO_POLISH
    assert review_issue_severity("SEGMENT_MISALIGNMENT_DUPLICATION") == BLOCKING_SEMANTIC
    assert review_issue_severity("SEMANTIC_MISMATCH") == BLOCKING_SEMANTIC
    assert review_issue_severity("unrecognized_future_code") == BLOCKING_SEMANTIC
