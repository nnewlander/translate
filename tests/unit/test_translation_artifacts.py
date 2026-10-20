import json
from pathlib import Path

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness import translation_artifacts
from booktrans.harness.translation_artifacts import (
    normalize_or_validate_response_artifact,
    normalize_response_artifact,
    persist_raw_response,
)


def _request(uid: str, source: str, *, evidence: list[str] | None = None) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="fixture-sha", unit_uid=uid, display_id=f"body.p.{uid}", story="footnotes",
        content_role="paragraph", source_text=source,
        source_segments=[CalibrationSourceSegment(kind="plain", text=source, island_ref="island.001")],
        footnote_reference_numbers=evidence or [],
    )


def _response(uid: str, text: str, *, kind: str = "plain") -> TranslationResult:
    return TranslationResult(unit_uid=uid, target_segments=[StructuredTargetSegment(text=text, kind=kind, anchor_ref="p" if kind == "protected" else None)])


def _write_requests(path: Path, requests: list[CalibrationRequest]) -> None:
    path.write_text("".join(f"{request.model_dump_json()}\n" for request in requests), encoding="utf-8", newline="\n")


def test_raw_to_normalized_artifact_chain_is_immutable_and_auditable(tmp_path: Path) -> None:
    requests = [_request("safe", "1 footnote text", evidence=["1"])]
    request_path = tmp_path / "request.jsonl"
    raw_path = tmp_path / "translation_raw_response.jsonl"
    normalized_path = tmp_path / "translation_normalized_response.jsonl"
    report_path = tmp_path / "translation_normalization_report.json"
    _write_requests(request_path, requests)
    raw_digest = persist_raw_response(raw_path, [_response("safe", "¹ 脚注文本")])

    report = normalize_response_artifact(request_path, raw_path, normalized_path, report_path)

    assert report["raw_artifact"]["immutable"] is True
    assert report["raw_artifact"]["sha256_before"] == raw_digest == report["raw_artifact"]["sha256_after"]
    assert report["normalized_unit_count"] == 1
    assert report["normalization_rule_distribution"] == {"FOOTNOTE_SUPERSCRIPT_TO_ASCII": 1}
    assert report["changes"][0]["exact_changed_character_span"]["before"] == "¹"
    assert "source-evidenced ASCII footnote reference" in report["changes"][0]["semantic_value_unchanged_proof"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["raw_artifact"]["sha256_after"] == raw_digest
    normalized = TranslationResult.model_validate_json(normalized_path.read_text(encoding="utf-8").strip())
    assert normalized.target_segments[0].text == "1 脚注文本"
    with pytest.raises(ValueError, match="RAW_RESPONSE_ALREADY_EXISTS"):
        persist_raw_response(raw_path, [_response("safe", "different")])


def test_raw_mutation_during_normalization_is_a_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request("safe", "1 footnote text", evidence=["1"])
    request_path = tmp_path / "request.jsonl"
    raw_path = tmp_path / "translation_raw_response.jsonl"
    normalized_path = tmp_path / "translation_normalized_response.jsonl"
    report_path = tmp_path / "translation_normalization_report.json"
    _write_requests(request_path, [request])
    persist_raw_response(raw_path, [_response("safe", "¹ 脚注文本")])

    def corrupt_raw(_request: CalibrationRequest, response: TranslationResult):
        raw_path.write_text("corrupted\n", encoding="utf-8")
        return response, []

    monkeypatch.setattr(translation_artifacts, "normalize_footnote_superscripts", corrupt_raw)
    with pytest.raises(ValueError, match="RAW_RESPONSE_MUTATED"):
        normalize_response_artifact(request_path, raw_path, normalized_path, report_path)


def test_missing_and_semantic_numeric_cases_are_never_normalized_or_repaired() -> None:
    cases = [
        (_request("missing", "2 footnote text", evidence=["2"]), _response("missing", "缺少编号")),
        (_request("decade", "In 1990s, systems changed."), _response("decade", "在20世纪90年代，系统发生了变化。")),
        (_request("era", "mid-2000s systems"), _response("era", "21世纪初期的系统")),
    ]
    qa = DeterministicCandidateQA()
    for request, raw in cases:
        normalized, changes = normalize_footnote_superscripts(request, raw)
        assert normalized == raw
        assert changes == []
        result = qa.validate(
            SyntheticCandidate(unit_uid=request.unit_uid, display_id=request.display_id, source_text=request.source_text, target_text="".join(item.text for item in normalized.target_segments)),
            known_unit_ids={request.unit_uid},
        )
        assert not result.passed


def test_protected_number_never_normalizes() -> None:
    request = _request("protected", "1 protected footnote", evidence=["1"])
    raw = _response("protected", "¹", kind="protected")
    normalized, changes = normalize_footnote_superscripts(request, raw)
    assert normalized == raw
    assert changes == []


def test_artifact_report_records_no_change_and_rejects_path_collision(tmp_path: Path) -> None:
    request = _request("plain", "There are 2 options.")
    request_path = tmp_path / "request.jsonl"
    raw_path = tmp_path / "translation_raw_response.jsonl"
    output_path = tmp_path / "translation_normalized_response.jsonl"
    report_path = tmp_path / "translation_normalization_report.json"
    _write_requests(request_path, [request])
    persist_raw_response(raw_path, [_response("plain", "有²种选择。")])
    report = normalize_response_artifact(request_path, raw_path, output_path, report_path)
    assert report["normalized_unit_count"] == 0
    assert report["unchanged_unit_count"] == 1
    assert report["normalized_artifact"]["input_equals_output"] is True

    with pytest.raises(ValueError, match="ARTIFACT_PATH_COLLISION"):
        normalize_response_artifact(request_path, raw_path, raw_path, tmp_path / "other.json")


def test_normalized_artifact_resume_creates_once_then_reuses_valid_evidence(tmp_path: Path) -> None:
    request_path = tmp_path / "request.jsonl"
    raw_path = tmp_path / "translation_raw_response.jsonl"
    normalized_path = tmp_path / "translation_normalized_response.jsonl"
    report_path = tmp_path / "translation_normalization_report.json"
    _write_requests(request_path, [_request("safe", "1 footnote text", evidence=["1"])])
    persist_raw_response(raw_path, [_response("safe", "¹ 脚注文本")])

    created = normalize_or_validate_response_artifact(request_path, raw_path, normalized_path, report_path)
    normalized_before = normalized_path.read_bytes()
    report_before = report_path.read_bytes()
    reused = normalize_or_validate_response_artifact(request_path, raw_path, normalized_path, report_path)

    assert reused == created
    assert normalized_path.read_bytes() == normalized_before
    assert report_path.read_bytes() == report_before


def test_normalized_artifact_resume_rejects_existing_invalid_evidence(tmp_path: Path) -> None:
    request_path = tmp_path / "request.jsonl"
    raw_path = tmp_path / "translation_raw_response.jsonl"
    normalized_path = tmp_path / "translation_normalized_response.jsonl"
    report_path = tmp_path / "translation_normalization_report.json"
    _write_requests(request_path, [_request("safe", "1 footnote text", evidence=["1"])])
    persist_raw_response(raw_path, [_response("safe", "¹ 脚注文本")])
    normalize_or_validate_response_artifact(request_path, raw_path, normalized_path, report_path)
    normalized_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="NORMALIZED_ARTIFACT_CONTENT_MISMATCH"):
        normalize_or_validate_response_artifact(request_path, raw_path, normalized_path, report_path)
