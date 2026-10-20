from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.ooxml.verified_footnotes import load_exact_verified_evidence_registry
from booktrans.providers.qwen_max import ReviewResult, ReviewerProviderResult
from booktrans.providers.translation_provider import TranslationCandidate, TranslationProviderRequest
from booktrans.production_worker.continuation import (
    canary_immutable_fingerprints,
    continue_canary_from_raw,
    recalculate_canary_from_persisted_evidence,
)
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.selection import (
    d050_paths_for_checkpoint,
    load_checkpoint_d050_registry,
    overlay_verified_d050_bindings,
)

LIVE_RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
UID_06959 = "54403747680bb2796579ff4dbd776bd921a0fb5ab4846cd17079a1435af88a9d"
UID_06973 = "1dda963c190dd4b0b82fadd5c9132f302f8aac6cc38b583ff8c8cce2b46c0c2f"
MARKER_ANCHOR = "fn-marker"


def _period_marker_request(
    *,
    uid: str = "uid-059",
    display: str = "body.p.06959",
    marker: str = "2",
    sha: str = "a" * 64,
) -> CalibrationRequest:
    prose = "some of those updates will be important."
    rest = " How do we manage this?"
    return CalibrationRequest(
        source_sha256=sha,
        unit_uid=uid,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text=prose + marker + rest,
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=prose, anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=marker, anchor_ref=MARKER_ANCHOR, island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=rest, anchor_ref="a2", island_ref="island.001"),
        ],
    )


def _period_marker_raw(request: CalibrationRequest, marker: str = "2") -> TranslationResult:
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="其中一些更新将非常重要。", kind="anchored", anchor_ref="a0", island_ref="island.001"),
            StructuredTargetSegment(text=marker, kind="anchored", anchor_ref=MARKER_ANCHOR, island_ref="island.001"),
            StructuredTargetSegment(text=" 我们该如何管理？", kind="anchored", anchor_ref="a2", island_ref="island.001"),
        ],
    )


def _d050_record(*, display: str, uid: str, marker: str) -> dict[str, object]:
    return {
        "verification_status": "VERIFIED",
        "unit_uid": uid,
        "logical_work_order_id": display,
        "pre_footnote_numeric_tokens": [],
        "exact_source_marker_token": marker,
        "footnote_number": marker,
        "source_segment_binding": [{"anchor_ref": MARKER_ANCHOR, "text": marker}],
    }


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


def _write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def _seed(tmp_path: Path, requests: list[CalibrationRequest], raws: list[TranslationResult], *, fail_ids: list[str] | None = None) -> Path:
    (tmp_path / "full-book-20260916T000200Z-decb09" / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "qwen-production-canary-d050"
    output.mkdir()
    _write_jsonl(output / "translation_request.jsonl", requests)
    _write_jsonl(output / "translation_raw_response.jsonl", raws)
    fail_ids = fail_ids or []
    records = []
    for request in requests:
        passed = request.display_id not in fail_ids
        records.append(
            {
                "unit_uid": request.unit_uid,
                "display_id": request.display_id,
                "passed": passed,
                "diagnostics": [] if passed else [{"code": "NUMBER_PRESERVATION", "message": "Unexpected target numeric token(s): ['2']"}],
            }
        )
    while len(records) < 25:
        display = f"body.p.{len(records) + 1:05d}"
        records.append({"unit_uid": f"pad-{len(records)}", "display_id": display, "passed": True, "diagnostics": []})
    (output / "translation_raw_qa_report.json").write_text(
        json.dumps({"pass_count": sum(row["passed"] for row in records), "fail_count": sum(not row["passed"] for row in records), "records": records}),
        encoding="utf-8",
    )
    (output / "translation_provider_provenance.jsonl").write_text("{}\n", encoding="utf-8")
    return output


class CountingMT:
    def translate(self, request):
        raise AssertionError("translation API must not be called")


class CountingMax:
    def translate_structured(self, request):
        raise AssertionError("structured translation API must not be called")


class ScriptedReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls += 1
        review = ReviewResult(
            schema_version="booktrans.structured-review.v1",
            unit_uid=request.request.unit_uid,
            verdict="ACCEPT",
            issues=[],
        )
        binding = TranslationProviderRequest(request=request.request)
        return ReviewerProviderResult(
            review=review,
            candidate=TranslationCandidate(
                provider="ALIYUN_DASHSCOPE_QWEN_MAX_REVIEWER",
                model="qwen3.8-max",
                request_binding_sha256=binding.request_binding_sha256,
                translation_options_sha256=binding.translation_options_sha256,
                source_sha256=request.request.source_sha256,
                unit_uid=request.request.unit_uid,
                display_id=request.request.display_id,
                response_text="review-json",
                requested_at="2026-09-17T00:00:00+00:00",
                responded_at="2026-09-17T00:00:02+00:00",
                usage={"total_tokens": 3},
            ),
        )


class CountingRepair:
    def repair(self, request, current, contract):
        raise AssertionError("repair API must not be called")


def test_exact_d050_marker_is_not_false_number_extra() -> None:
    request = _period_marker_request()
    raw = _period_marker_raw(request)
    target = "".join(segment.text for segment in raw.target_segments)
    without = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid=request.unit_uid, display_id=request.display_id, source_text=request.source_text, target_text=target),
        known_unit_ids={request.unit_uid},
    )
    assert not without.passed
    assert any(item.code == "NUMBER_PRESERVATION" and "['2']" in item.message for item in without.diagnostics)
    registry = load_exact_verified_evidence_registry_from_records(
        [_d050_record(display=request.display_id, uid=request.unit_uid, marker="2")]
    )
    overlaid = overlay_verified_d050_bindings(request, registry)
    with_d050 = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid=request.unit_uid, display_id=request.display_id, source_text=request.source_text, target_text=target),
        known_unit_ids={request.unit_uid},
        footnote_reference_numbers=overlaid.footnote_reference_numbers,
        verified_footnote_numeric_bindings=overlaid.verified_footnote_numeric_bindings,
    )
    assert with_d050.passed
    assert request.footnote_reference_numbers == []
    assert request.verified_footnote_numeric_bindings == []


def test_same_numeric_token_without_d050_still_fails() -> None:
    request = _period_marker_request()
    raw = _period_marker_raw(request)
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=request.unit_uid,
            display_id=request.display_id,
            source_text=request.source_text,
            target_text="".join(segment.text for segment in raw.target_segments),
        ),
        known_unit_ids={request.unit_uid},
    )
    assert not result.passed


def test_wrong_unit_uid_d050_evidence_cannot_leak() -> None:
    request = _period_marker_request()
    raw = _period_marker_raw(request)
    registry = load_exact_verified_evidence_registry_from_records(
        [_d050_record(display=request.display_id, uid="other-uid", marker="2")]
    )
    overlaid = overlay_verified_d050_bindings(request, registry)
    assert overlaid.verified_footnote_numeric_bindings == []
    assert overlaid.footnote_reference_numbers == []
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid=request.unit_uid,
            display_id=request.display_id,
            source_text=request.source_text,
            target_text="".join(segment.text for segment in raw.target_segments),
        ),
        known_unit_ids={request.unit_uid},
        footnote_reference_numbers=overlaid.footnote_reference_numbers,
        verified_footnote_numeric_bindings=overlaid.verified_footnote_numeric_bindings,
    )
    assert not result.passed


def test_cross_chapter_d050_cannot_leak() -> None:
    request = _period_marker_request()
    registry = load_exact_verified_evidence_registry_from_records(
        [_d050_record(display="body.p.01000", uid=request.unit_uid, marker="2")]
    )
    overlaid = overlay_verified_d050_bindings(request, registry)
    assert overlaid is request or (
        overlaid.footnote_reference_numbers == [] and overlaid.verified_footnote_numeric_bindings == []
    )


def test_compact_checkpoint03_filename_is_discovered(tmp_path: Path) -> None:
    compact = tmp_path / "chapter_21_batch022_checkpoint03_verified_footnote_evidence_v1.json"
    compact.write_text(json.dumps({"records": [_d050_record(display="body.p.06959", uid="uid-059", marker="2")]}), encoding="utf-8")
    underscored = tmp_path / "chapter_21_batch022_checkpoint_03_verified_footnote_evidence_v1.json"
    assert not underscored.exists()
    paths = d050_paths_for_checkpoint(tmp_path, "checkpoint_03")
    assert paths == [compact]
    registry = load_checkpoint_d050_registry(tmp_path, "checkpoint_03")
    assert ("body.p.06959", "uid-059") in registry


def test_canary_d050_readiness_feeds_actual_qa(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _period_marker_request(sha=sha)
    raw = _period_marker_raw(request)
    output = _seed(tmp_path, [request], [raw], fail_ids=[request.display_id])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    compact = production_run / "chapter_21_batch022_checkpoint03_verified_footnote_evidence_v1.json"
    compact.write_text(
        json.dumps({"records": [_d050_record(display=request.display_id, uid=request.unit_uid, marker="2")]}),
        encoding="utf-8",
    )
    registry = load_checkpoint_d050_registry(production_run, "checkpoint_03")
    assert registry
    reviewer = ScriptedReviewer()
    seen: list[list[str]] = []

    def capturing_qa(view, qa_request, response, known):
        seen.append(list(qa_request.footnote_reference_numbers))
        return _qa_fn(view, qa_request, response, known)

    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=capturing_qa,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id, *[f"body.p.{index:05d}" for index in range(2, 26)]],
        d050_registry=registry,
    )
    assert seen and seen[0] == ["2"]
    assert request.footnote_reference_numbers == []
    assert report["translation_api_calls"] == 0
    assert report["human_required"] == 0
    assert reviewer.calls == 1
    unit = json.loads((output / "continuation" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert unit["final_status"] == "ACCEPT"


def test_duplicate_run_cleanup_does_not_alter_immutable_evidence(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _period_marker_request(sha=sha)
    raw = _period_marker_raw(request)
    output = _seed(tmp_path, [request], [raw], fail_ids=[request.display_id])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    compact = production_run / "chapter_21_batch022_checkpoint03_verified_footnote_evidence_v1.json"
    compact.write_text(
        json.dumps({"records": [_d050_record(display=request.display_id, uid=request.unit_uid, marker="2")]}),
        encoding="utf-8",
    )
    before = canary_immutable_fingerprints(output)
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[request],
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        d050_registry=load_checkpoint_d050_registry(production_run, "checkpoint_03"),
    )
    assert canary_immutable_fingerprints(output) == before
    assert report["translation_api_calls"] == 0
    assert report["d050_aware_qa_pass"] == 1
    assert report["d050_aware_qa_fail"] == 0


def test_live_b022_checkpoint03_d050_registry_is_discoverable() -> None:
    paths = d050_paths_for_checkpoint(LIVE_RUN, "checkpoint_03")
    assert paths, "compact chapter_21 D050 registry must be discoverable"
    assert paths[0].name == "chapter_21_batch022_checkpoint03_verified_footnote_evidence_v1.json"
    registry = load_checkpoint_d050_registry(LIVE_RUN, "checkpoint_03")
    assert ( "body.p.06959", UID_06959) in registry
    assert ("body.p.06973", UID_06973) in registry


def load_exact_verified_evidence_registry_from_records(records: list[dict[str, object]]):
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        path = Path(folder) / "registry.json"
        path.write_text(json.dumps({"records": records}), encoding="utf-8")
        return load_exact_verified_evidence_registry([path])
