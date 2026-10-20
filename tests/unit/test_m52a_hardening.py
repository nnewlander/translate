from pathlib import Path

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.bible_snapshot import assert_snapshot_unchanged, freeze_translation_bible
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.semantic_review import review_status_for_repair


def _request(text: str, *, footnote_refs: list[str] | None = None) -> CalibrationRequest:
    return CalibrationRequest(source_sha256="sha", unit_uid="unit", display_id="body.p.00001", story="body", content_role="paragraph", source_text=text, source_segments=[CalibrationSourceSegment(kind="plain", text=text, island_ref="island.001")], footnote_reference_numbers=footnote_refs or [])


def _response(text: str, *, kind: str = "plain") -> TranslationResult:
    return TranslationResult(unit_uid="unit", target_segments=[StructuredTargetSegment(text=text, kind=kind)])


def test_observed_footnote_superscript_normalizes_but_ordinary_prose_does_not() -> None:
    normalized, changes = normalize_footnote_superscripts(_request("life span1 of code", footnote_refs=["1"]), _response("代码寿命¹很重要"))
    assert normalized.target_segments[0].text == "代码寿命1很重要"
    assert len(changes) == 1
    unchanged, no_changes = normalize_footnote_superscripts(_request("There are 2 options."), _response("有²种选择。"))
    assert unchanged.target_segments[0].text == "有²种选择。"
    assert not no_changes


def test_explicit_marker_evidence_supports_punctuation_adjacent_footnote_only() -> None:
    normalized, changes = normalize_footnote_superscripts(
        _request("You risk reinventing wheels.2", footnote_refs=["2"]), _response("你可能会重复造轮子。²"),
    )
    assert normalized.target_segments[0].text == "你可能会重复造轮子。2"
    assert len(changes) == 1


def test_normalizer_never_rewrites_protected_literal_or_numeric_semantic_mutation() -> None:
    protected = TranslationResult(unit_uid="unit", target_segments=[StructuredTargetSegment(text="¹", kind="protected", anchor_ref="p")])
    normalized, changes = normalize_footnote_superscripts(_request("1 protected", footnote_refs=["1"]), protected)
    assert normalized == protected and not changes
    qa = DeterministicCandidateQA().validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="In 1987, usage reached 37%.", target_text="1989年，使用率达到37%。"), known_unit_ids={"unit"})
    assert not qa.passed


def test_missing_or_extra_number_fails() -> None:
    qa = DeterministicCandidateQA()
    assert not qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="1 item", target_text="项目"), known_unit_ids={"unit"}).passed
    assert not qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="1 item", target_text="2 个项目"), known_unit_ids={"unit"}).passed


def test_explicit_verified_footnote_marker_is_preserved_without_global_inference() -> None:
    qa = DeterministicCandidateQA()
    candidate = SyntheticCandidate(
        unit_uid="unit", display_id="x", source_text="You risk reinventing wheels.2", target_text="你可能会重复造轮子。2",
    )
    assert not qa.validate(candidate, known_unit_ids={"unit"}).passed
    assert qa.validate(candidate, known_unit_ids={"unit"}, footnote_reference_numbers=["2"]).passed


def test_no_op_repair_is_contract_error_but_material_repair_is_not() -> None:
    original = _response("原译文")
    assert review_status_for_repair(original, _response("原译文")) == "REVIEWER_CONTRACT_ERROR"
    assert review_status_for_repair(original, _response("修订译文")) == "REPAIR_REQUIRED"


def test_bible_snapshot_is_immutable_for_a_run(tmp_path: Path) -> None:
    canonical = tmp_path / "bible.md"
    canonical.write_text("canonical", encoding="utf-8")
    snapshot = tmp_path / "run" / "snapshot.md"
    digest = freeze_translation_bible(canonical, snapshot)
    canonical.write_text("next canonical", encoding="utf-8")
    assert_snapshot_unchanged(snapshot, digest)
    snapshot.write_text("mutated", encoding="utf-8")
    with pytest.raises(ValueError, match="BIBLE_SNAPSHOT_MUTATED"):
        assert_snapshot_unchanged(snapshot, digest)
