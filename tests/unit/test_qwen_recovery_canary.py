from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.ooxml.verified_footnotes import load_exact_verified_evidence_registry
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy
from booktrans.production_worker.deterministic_recovery import (
    CodexMustNotRun,
    dispatch_allowlisted_recovery,
    execute_recovery_decision,
    restore_exact_source_urls,
)
from booktrans.providers.translation_provider import TranslationProviderRequest


DOI = "https://doi.org/10.1145/2786805.2786855"
MARKER_ANCHOR = "fn-marker"


def _qa_fn(view, request, response, known):
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=request.unit_uid,
            display_id=request.display_id,
            source_text=request.source_text,
            target_text="".join(segment.text for segment in response.target_segments),
        ),
        known_unit_ids=known,
        footnote_reference_numbers=request.footnote_reference_numbers,
        verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,
    )
    return {
        "unit_uid": request.unit_uid,
        "display_id": request.display_id,
        "passed": result.passed,
        "diagnostics": [item.model_dump() for item in result.diagnostics],
    }


def _result(request: CalibrationRequest, texts: tuple[str, ...]) -> TranslationResult:
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment, text in zip(request.source_segments, texts, strict=True)
        ],
    )


@dataclass
class FakeNarrowRepair:
    text_by_index: dict[int, str]
    calls: int = 0
    contracts: list = field(default_factory=list)

    def repair(self, request: TranslationProviderRequest, current: TranslationResult, contract) -> TranslationResult:
        self.calls += 1
        self.contracts.append(contract)
        texts = [segment.text for segment in current.target_segments]
        for index in contract.permitted_segment_indices:
            texts[index] = self.text_by_index[index]
        return _result(request.request, tuple(texts))


@dataclass
class CountingTranslate:
    calls: int = 0

    def translate(self, request):
        self.calls += 1
        raise AssertionError("translation API must not be called")


def _number_fixture() -> tuple[CalibrationRequest, TranslationResult, TranslationResult]:
    request = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="uid-number",
        display_id="body.p.05675",
        story="body",
        content_role="body_paragraph",
        source_text="Alpha 2 quality.",
        source_segments=[CalibrationSourceSegment(kind="anchored", text="Alpha 2 quality.", anchor_ref="a0", island_ref="island.001")],
    )
    broken = _result(request, ("甲质量。",))
    fixed = _result(request, ("甲 2 质量。",))
    return request, broken, fixed


def _doi_fixture() -> tuple[CalibrationRequest, TranslationResult, TranslationResult]:
    parts = (
        CalibrationSourceSegment(kind="anchored", text="See ", anchor_ref="a0", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text="the paper ", anchor_ref="a1", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text="at ", anchor_ref="a2", island_ref="island.001"),
        CalibrationSourceSegment(kind="protected", text=DOI, anchor_ref="a3", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text=".", anchor_ref="a4", island_ref="island.001"),
    )
    request = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="uid-doi",
        display_id="body.p.05951",
        story="body",
        content_role="body_paragraph",
        source_text="See the paper at " + DOI + ".",
        source_segments=list(parts),
    )
    broken = _result(request, ("见", "论文", "于", DOI, "。"))
    fixed = _result(request, ("见", "论文", "于", DOI, "."))
    return request, broken, fixed


def _d050_fixture() -> tuple[CalibrationRequest, TranslationResult, dict[tuple[str, str], tuple[dict[str, object], ...]]]:
    prose = "some of those updates will be important."
    rest = " How do we manage this?"
    marker = "2"
    request = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="uid-d050",
        display_id="body.p.06959",
        story="body",
        content_role="body_paragraph",
        source_text=prose + marker + rest,
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=prose, anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=marker, anchor_ref=MARKER_ANCHOR, island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=rest, anchor_ref="a2", island_ref="island.001"),
        ],
    )
    raw = _result(request, ("其中一些更新将非常重要。", marker, " 我们该如何管理？"))
    record = {
        "verification_status": "VERIFIED",
        "unit_uid": request.unit_uid,
        "logical_work_order_id": request.display_id,
        "pre_footnote_numeric_tokens": [],
        "exact_source_marker_token": marker,
        "footnote_number": marker,
        "source_segment_binding": [{"anchor_ref": MARKER_ANCHOR, "text": marker}],
    }
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        path = Path(folder) / "registry.json"
        path.write_text(__import__("json").dumps({"records": [record]}), encoding="utf-8")
        registry = load_exact_verified_evidence_registry([path])
    return request, raw, registry


def _run(decision, request, raw, *, repair=None, registry=None, sha_ok=True, raw_ok=True, raw_bytes=None, expected_raw=None, probe=None):
    probe = probe or CodexMustNotRun()
    translator = CountingTranslate()
    result = execute_recovery_decision(
        decision,
        request=request,
        raw=raw,
        view=SimpleNamespace(),
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        repair_provider=repair,
        d050_registry=registry,
        source_sha_valid=sha_ok,
        raw_sha_valid=raw_ok,
        raw_bytes=raw_bytes,
        expected_raw_sha256=expected_raw,
        codex=probe,
    )
    assert translator.calls == 0
    assert result["codex_exec_count"] == 0
    assert result["translation_api_calls"] == 0
    assert probe.exec_count == 0
    return result


def test_number_known_failure_uses_qwen_narrow_repair_and_qa_pass() -> None:
    request, broken, _fixed = _number_fixture()
    before = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid=request.unit_uid, display_id=request.display_id, source_text=request.source_text, target_text="甲质量。"),
        known_unit_ids={request.unit_uid},
    )
    assert not before.passed
    probe = CodexMustNotRun()
    evidence = {
        "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED",
        "source_sha_valid": True,
        "raw_required": True,
        "raw_sha_valid": True,
        "sparse_local": True,
        "qa_codes": ["NUMBER_PRESERVATION"],
        "repair_contract": {"permitted_segment_indices": [0], "units": [request.display_id]},
    }
    decision = decide_recovery_policy(evidence)
    assert (decision["policy_id"], decision["handler_kind"]) == ("NUMERIC_REPRESENTATION", "QWEN_NARROW_REPAIR")
    repair = FakeNarrowRepair({0: "甲 2 质量。"})
    result = _run(decision, request, broken, repair=repair, probe=probe)
    assert result["status"] == "PASS"
    assert result["qa"]["passed"] is True
    assert repair.calls == 1
    assert repair.contracts[0].policy_id == "NUMERIC_REPRESENTATION"
    assert broken.target_segments[0].text == "甲质量。"
    assert result["codex_exec_count"] == 0


def test_doi_punctuation_boundary_uses_qwen_narrow_repair_and_qa_pass() -> None:
    request, broken, _fixed = _doi_fixture()
    before = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=request.unit_uid,
            display_id=request.display_id,
            source_text=request.source_text,
            target_text="".join(segment.text for segment in broken.target_segments),
        ),
        known_unit_ids={request.unit_uid},
    )
    assert not before.passed
    assert any(item.code == "DOI_PRESERVATION" for item in before.diagnostics)
    evidence = {
        "failure_classification": "DOI_PRESERVATION",
        "source_sha_valid": True,
        "raw_required": True,
        "raw_sha_valid": True,
        "sparse_local": True,
        "qa_codes": ["DOI_PRESERVATION"],
        "repair_contract": {"repairs": [{"logical_work_order_id": request.display_id, "target_segment_index": 4}]},
    }
    decision = decide_recovery_policy(evidence)
    assert (decision["policy_id"], decision["handler_kind"]) == ("DOI_PRESERVATION", "QWEN_NARROW_REPAIR")
    repair = FakeNarrowRepair({4: "."})
    probe = CodexMustNotRun()
    result = _run(decision, request, broken, repair=repair, probe=probe)
    assert result["status"] == "PASS"
    assert result["qa"]["passed"] is True
    assert repair.calls == 1
    assert list(repair.contracts[0].permitted_segment_indices) == [4]
    assert broken.target_segments[4].text == "。"
    assert result["codex_exec_count"] == 0


def test_d050_exact_evidence_is_deterministic_and_qa_pass() -> None:
    request, raw, registry = _d050_fixture()
    before = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=request.unit_uid,
            display_id=request.display_id,
            source_text=request.source_text,
            target_text="".join(segment.text for segment in raw.target_segments),
        ),
        known_unit_ids={request.unit_uid},
    )
    assert not before.passed
    evidence = {
        "failure_classification": "D050_PREFLIGHT_REQUIRED",
        "source_sha_valid": True,
        "exact_d050_binding": True,
        "repair_contract": {"logical_work_order_id": request.display_id, "unit_uid": request.unit_uid},
    }
    decision = decide_recovery_policy(evidence)
    assert (decision["policy_id"], decision["handler_kind"]) == ("D050_EXACT_EVIDENCE", "DETERMINISTIC_EVIDENCE")
    probe = CodexMustNotRun()
    result = _run(decision, request, raw, registry=registry, probe=probe)
    assert result["status"] == "PASS"
    assert result["qa"]["passed"] is True
    assert result["overlay_applied"] is True
    assert result["codex_exec_count"] == 0


def test_unknown_failure_is_blocked_without_codex() -> None:
    request, broken, _fixed = _number_fixture()
    decision = decide_recovery_policy({"failure_classification": "UNSEEN", "source_sha_valid": True})
    assert decision["eligible"] is False
    probe = CodexMustNotRun()
    repair = FakeNarrowRepair({0: "甲 2 质量。"})
    result = _run(decision, request, broken, repair=repair, probe=probe)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "CLASSIFICATION_NOT_ALLOWLISTED"
    assert repair.calls == 0
    assert result["codex_exec_count"] == 0


def test_wrong_sha_and_ambiguous_scope_fail_closed() -> None:
    request, broken, _fixed = _number_fixture()
    sha_decision = decide_recovery_policy(
        {
            "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED",
            "source_sha_valid": False,
            "raw_required": True,
            "raw_sha_valid": True,
            "sparse_local": True,
            "qa_codes": ["NUMBER_PRESERVATION"],
            "repair_contract": {"permitted_segment_indices": [0]},
        }
    )
    assert sha_decision["eligible"] is False
    probe = CodexMustNotRun()
    sha_result = _run(sha_decision, request, broken, repair=FakeNarrowRepair({0: "甲 2 质量。"}), probe=probe)
    assert sha_result["status"] == "BLOCKED"
    assert sha_result["reason"] == "SOURCE_SHA_MISMATCH"

    raw_bytes = b"immutable-raw"
    forged = {
        "eligible": True,
        "handler_kind": "QWEN_NARROW_REPAIR",
        "policy_id": "NUMERIC_REPRESENTATION",
        "repair_contract": {"permitted_segment_indices": [0]},
        "escalation_code": "AUTO_RECOVERY_EXHAUSTED",
    }
    drift = _run(
        forged,
        request,
        broken,
        repair=FakeNarrowRepair({0: "甲 2 质量。"}),
        raw_bytes=raw_bytes,
        expected_raw=sha256(b"other").hexdigest(),
        probe=CodexMustNotRun(),
    )
    assert drift["status"] == "BLOCKED"
    assert drift["reason"] == "IMMUTABLE_RAW_SHA_MISMATCH"

    doi_request, doi_broken, _ = _doi_fixture()
    ambiguous = decide_recovery_policy(
        {
            "failure_classification": "DOI_PRESERVATION",
            "source_sha_valid": True,
            "raw_required": True,
            "raw_sha_valid": True,
            "sparse_local": True,
            "qa_codes": ["DOI_PRESERVATION"],
            "repair_contract": {"note": "no indices"},
        }
    )
    assert ambiguous["eligible"] is True
    probe = CodexMustNotRun()
    result = _run(ambiguous, doi_request, doi_broken, repair=FakeNarrowRepair({4: "."}), probe=probe)
    assert result["status"] == "HUMAN_REQUIRED"
    assert result["reason"] == "REPAIR_SCOPE_AMBIGUOUS"
    assert result["codex_exec_count"] == 0


def test_wrong_d050_binding_fail_closed() -> None:
    request, raw, registry = _d050_fixture()
    decision = {
        "eligible": True,
        "handler_kind": "DETERMINISTIC_EVIDENCE",
        "policy_id": "D050_EXACT_EVIDENCE",
        "repair_contract": {"logical_work_order_id": request.display_id, "unit_uid": "other-uid"},
    }
    probe = CodexMustNotRun()
    result = _run(decision, request, raw, registry=registry, probe=probe)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "D050_BINDING_MISMATCH"
    assert result["codex_exec_count"] == 0


def test_allowlisted_recovery_never_invokes_codex_probe() -> None:
    request, broken, _ = _number_fixture()
    probe = CodexMustNotRun()
    result = dispatch_allowlisted_recovery(
        handler_kind="UNKNOWN_HANDLER",
        policy_id="UNKNOWN",
        repair_contract={},
        request=request,
        raw=broken,
        view=SimpleNamespace(),
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        codex=probe,
    )
    assert result["status"] == "HUMAN_REQUIRED"
    assert result["codex_exec_count"] == 0
    try:
        probe.exec("forbidden")
    except RuntimeError as error:
        assert "CODEX_EXEC_FORBIDDEN" in str(error)
    else:
        raise AssertionError("codex probe must fail closed")
    assert probe.exec_count == 1


def test_exact_source_url_restoration_is_local_noop_safe_and_raw_immutable() -> None:
    url = "https://ieeexplore.ieee.org/abstract/document/8443579"
    request = CalibrationRequest(
        source_sha256="a" * 64, unit_uid="uid-url", display_id="footer.354.p.0001", story="footers", content_role="story_paragraph",
        source_text="5 See " + url + ".",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="5 See ", anchor_ref="a0", island_ref="i"),
            CalibrationSourceSegment(kind="protected", text="https://ieeexplore.ieee.org/", anchor_ref="a1", island_ref="i"),
            CalibrationSourceSegment(kind="anchored", text="abstract/document/8443579", anchor_ref="a2", island_ref="i"),
            CalibrationSourceSegment(kind="anchored", text=".", anchor_ref="a3", island_ref="i"),
        ],
    )
    raw = _result(request, ("5 参见 ", "https://ieeexplore.ieee.org/", "https://ieeexplore.ieee.org/", "摘要/文档/8443579"))
    before = raw.model_dump()
    restored = restore_exact_source_urls(request, raw)
    assert restored is not None
    assert "".join(segment.text for segment in restored.target_segments).count(url) == 1
    assert raw.model_dump() == before
    assert restore_exact_source_urls(request, restored) == restored
