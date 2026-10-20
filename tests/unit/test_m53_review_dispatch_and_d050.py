from booktrans.domain.calibration import (
    CalibrationRequest,
    CalibrationSourceSegment,
    ExplicitNumericRepresentationEquivalence,
    VerifiedFootnoteNumericBinding,
    VerifiedFootnoteSourceSegment,
)
from booktrans.ooxml.verified_footnotes import verified_numeric_bindings_for_request
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.semantic_review import (
    partition_deterministic_review_dispatch,
    semantic_review_summary,
)


def _binding(*, unit_uid: str = "unit", marker: str = "11-2.4") -> VerifiedFootnoteNumericBinding:
    return VerifiedFootnoteNumericBinding(
        unit_uid=unit_uid,
        exact_source_marker_token=marker,
        footnote_number="4",
        source_segment_binding=[
            VerifiedFootnoteSourceSegment(anchor_ref="figure", text="Figure 11-2"),
            VerifiedFootnoteSourceSegment(anchor_ref="footnote", text=".4"),
        ],
        pre_footnote_numeric_tokens=["11", "2"],
    )


def _candidate(source: str, target: str) -> SyntheticCandidate:
    return SyntheticCandidate(unit_uid="unit", display_id="body.p.03718", source_text=source, target_text=target)


def _equivalence(*, unit_uid: str = "unit", source_span: str = "25 million", target: str = "两千五百万", target_value: str = "25000000") -> ExplicitNumericRepresentationEquivalence:
    return ExplicitNumericRepresentationEquivalence(
        unit_uid=unit_uid,
        exact_source_span=source_span,
        source_numeric_tokens=["25"],
        approved_target_representation=target,
        reviewed_source_semantic_numeric_value="25000000",
        reviewed_target_semantic_numeric_value=target_value,
        semantic_unit="lines",
    )


def test_d050_exact_figure_and_verified_footnote_do_not_form_false_decimal() -> None:
    result = DeterministicCandidateQA().validate(
        _candidate("Figure 11-2.4", "图 11-2所示。4"),
        known_unit_ids={"unit"},
        footnote_reference_numbers=["4"],
        verified_footnote_numeric_bindings=[_binding()],
    )
    assert result.passed


def test_d050_without_verified_evidence_keeps_generic_behavior() -> None:
    result = DeterministicCandidateQA().validate(
        _candidate("Figure 11-2.4", "图 11-2所示。4"),
        known_unit_ids={"unit"},
        footnote_reference_numbers=["4"],
    )
    assert not result.passed
    assert any("2.4" in diagnostic.message for diagnostic in result.diagnostics)


def test_d050_wrong_unit_or_marker_does_not_apply() -> None:
    qa = DeterministicCandidateQA()
    for binding in (_binding(unit_uid="other"), _binding(marker="11-3.4")):
        result = qa.validate(
            _candidate("Figure 11-2.4", "图 11-2所示。4"),
            known_unit_ids={"unit"},
            footnote_reference_numbers=["4"],
            verified_footnote_numeric_bindings=[binding],
        )
        assert not result.passed


def test_d050_keeps_real_decimal_and_verified_marker_preservation() -> None:
    qa = DeterministicCandidateQA()
    decimal = qa.validate(
        _candidate("Measured value is 2.4.", "测得数值为 2。"),
        known_unit_ids={"unit"},
        verified_footnote_numeric_bindings=[_binding()],
    )
    assert not decimal.passed
    footnote_missing = qa.validate(
        _candidate("Figure 11-2.4", "图 11-2所示。"),
        known_unit_ids={"unit"},
        footnote_reference_numbers=["4"],
        verified_footnote_numeric_bindings=[_binding()],
    )
    assert not footnote_missing.passed
    assert any("4" in diagnostic.message for diagnostic in footnote_missing.diagnostics)


def test_d050_requires_exact_source_segment_binding() -> None:
    request = CalibrationRequest(
        source_sha256="sha", unit_uid="unit", display_id="body.p.03718", story="body",
        content_role="body_paragraph", source_text="Figure 11-2.4",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="Figure 11-2", anchor_ref="figure", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=".4", anchor_ref="footnote", island_ref="island.001"),
        ],
    )
    evidence = [{
        "verification_status": "VERIFIED", "unit_uid": "unit", "exact_source_marker_token": "11-2.4",
        "footnote_number": "4", "source_segment_binding": [
            {"anchor_ref": "figure", "text": "Figure 11-2"},
            {"anchor_ref": "footnote", "text": ".4"},
        ],
        "pre_footnote_numeric_tokens": ["11", "2"],
    }]
    assert verified_numeric_bindings_for_request(evidence, request) == [_binding()]
    mismatched = request.model_copy(update={"source_segments": [*request.source_segments[:-1], request.source_segments[-1].model_copy(update={"text": ".5"})]})
    assert verified_numeric_bindings_for_request(evidence, mismatched) == []


def test_deterministic_pass_is_the_only_reviewer_input() -> None:
    records = [
        {"unit_uid": "pass", "display_id": "body.p.1", "passed": True},
        {"unit_uid": "fail", "display_id": "body.p.2", "passed": False},
    ]
    dispatched, exclusions = partition_deterministic_review_dispatch(records)
    reviewer_records = [{"unit_uid": item["unit_uid"], "status": "ACCEPT"} for item in dispatched]
    summary = semantic_review_summary(reviewer_records, exclusions)
    assert [item["unit_uid"] for item in dispatched] == ["pass"]
    assert summary["accept_count"] == 1
    assert summary["not_reviewed_deterministic_fail_count"] == 1
    assert summary["dispatch_exclusions"] == [{"unit_uid": "fail", "display_id": "body.p.2", "status": "NOT_REVIEWED_DETERMINISTIC_FAIL", "reason": "normalized_deterministic_qa_failed"}]


def test_review_summary_is_derived_from_dispatched_records() -> None:
    summary = semantic_review_summary(
        [{"unit_uid": "one", "status": "ACCEPT"}, {"unit_uid": "two", "status": "HUMAN_REQUIRED"}],
        [{"unit_uid": "three", "display_id": "body.p.3", "status": "NOT_REVIEWED_DETERMINISTIC_FAIL", "reason": "normalized_deterministic_qa_failed"}],
    )
    assert summary["accept_count"] == 1
    assert summary["human_required_count"] == 1
    assert summary["not_reviewed_deterministic_fail_count"] == 1


def test_d051_exact_reviewed_representation_equivalence_passes() -> None:
    result = DeterministicCandidateQA().validate(
        _candidate("25 million lines", "两千五百万行"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_equivalence()],
    )
    assert result.passed


def test_d051_exact_250_million_to_2_5_yi_equivalence_passes_only_with_bound_evidence() -> None:
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid="unit", exact_source_span="250 million lines", source_numeric_tokens=["250"],
        approved_target_representation="2.5 亿行", reviewed_source_semantic_numeric_value="250000000",
        reviewed_target_semantic_numeric_value="250000000", semantic_unit="lines",
    )
    qa = DeterministicCandidateQA()
    assert qa.validate(_candidate("250 million lines", "2.5 亿行"), known_unit_ids={"unit"}, explicit_numeric_representation_equivalences=[equivalence]).passed
    assert not qa.validate(_candidate("250 million lines", "2.5 亿行"), known_unit_ids={"unit"}).passed


def test_d051_exact_english_number_phrase_to_chinese_numeric_representation_passes() -> None:
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid="unit", exact_source_span="two billion", source_numeric_tokens=[],
        approved_target_representation="20 亿", reviewed_source_semantic_numeric_value="2000000000",
        reviewed_target_semantic_numeric_value="2000000000", semantic_unit="device count",
    )
    qa = DeterministicCandidateQA()
    assert qa.validate(
        _candidate("more than two billion devices", "超过 20 亿台设备"),
        known_unit_ids={"unit"}, explicit_numeric_representation_equivalences=[equivalence],
    ).passed
    assert not qa.validate(
        _candidate("more than two billion devices", "超过 20 亿台设备"), known_unit_ids={"unit"},
    ).passed


def test_d051_without_explicit_evidence_keeps_literal_contract() -> None:
    result = DeterministicCandidateQA().validate(
        _candidate("25 million lines", "两千五百万行"), known_unit_ids={"unit"},
    )
    assert not result.passed
    assert any("25" in diagnostic.message for diagnostic in result.diagnostics)


def test_d051_does_not_normalize_exact_source_span_whitespace() -> None:
    qa = DeterministicCandidateQA()
    source = "25  million lines"
    target = "两千五百万行"
    exact = qa.validate(
        _candidate(source, target), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_equivalence(source_span="25  million")],
    )
    stale = qa.validate(
        _candidate(source, target), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_equivalence()],
    )
    assert exact.passed
    assert not stale.passed


def test_d051_wrong_unit_target_or_semantic_evidence_does_not_apply() -> None:
    qa = DeterministicCandidateQA()
    for equivalence in (_equivalence(unit_uid="other"), _equivalence(target="两千四百万"), _equivalence(target_value="25000001")):
        result = qa.validate(
            _candidate("25 million lines", "两千五百万行"), known_unit_ids={"unit"},
            explicit_numeric_representation_equivalences=[equivalence],
        )
        assert not result.passed


def test_d051_keeps_other_numeric_tokens_strict() -> None:
    result = DeterministicCandidateQA().validate(
        _candidate("25 million lines (Chapter 22)", "两千五百万行（第 23 章）"),
        known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_equivalence()],
    )
    assert not result.passed
    assert any("22" in diagnostic.message or "23" in diagnostic.message for diagnostic in result.diagnostics)


def _chinese_integer_equivalence(*, source: str, target: str, value: str = "20000", operator: str = "MORE_THAN", counter: str = "USAGES") -> ExplicitNumericRepresentationEquivalence:
    return ExplicitNumericRepresentationEquivalence(
        unit_uid="unit", exact_source_span=source, source_numeric_tokens=["20000"],
        approved_target_representation=target, reviewed_source_semantic_numeric_value=value,
        reviewed_target_semantic_numeric_value=value, semantic_unit=counter,
        source_comparison_operator=operator, target_comparison_operator=operator,
        source_counter_context=counter, target_counter_context=counter,
    )


def test_d051_chinese_integer_numeric_equivalence_requires_value_operator_and_counter() -> None:
    qa = DeterministicCandidateQA()
    assert qa.validate(
        _candidate("more than 20,000 usages", "调用次数超过两万次"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_chinese_integer_equivalence(source="more than 20,000 usages", target="调用次数超过两万次")],
    ).passed
    assert not qa.validate(
        _candidate("more than 20,000 usages", "调用次数超过两千次"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_chinese_integer_equivalence(source="more than 20,000 usages", target="调用次数超过两千次")],
    ).passed
    assert not qa.validate(
        _candidate("less than 20,000 usages", "调用次数超过两万次"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_chinese_integer_equivalence(source="less than 20,000 usages", target="调用次数超过两万次", operator="LESS_THAN")],
    ).passed
    assert not qa.validate(
        _candidate("more than 20,000 users", "用户超过两万次"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_chinese_integer_equivalence(source="more than 20,000 users", target="用户超过两万次", counter="USERS")],
    ).passed
    assert not qa.validate(
        _candidate("more than 20,000 usages", "调用次数超过两万多次"), known_unit_ids={"unit"},
        explicit_numeric_representation_equivalences=[_chinese_integer_equivalence(source="more than 20,000 usages", target="调用次数超过两万多次")],
    ).passed
