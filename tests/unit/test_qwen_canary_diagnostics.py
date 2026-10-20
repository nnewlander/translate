from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult, ReviewerProviderResult
from booktrans.providers.translation_provider import TranslationCandidate, TranslationProviderRequest
from booktrans.production_worker.canary import run_isolated_canary
from booktrans.production_worker.diagnostics import (
    CanaryDiagnostics,
    emit_milestone,
    run_bounded_subprocess,
)
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.selection import FrozenSelectionError, load_selected_jsonl_by_display_id
from booktrans.production_worker.worker import run_canary_command


class _FlushStream:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.flush_count = 0

    def write(self, text: str) -> int:
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        self.flush_count += 1


def _request(display: str = "body.p.06939", unit: str = "u1") -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid=unit,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text="A unit test improves quality.",
        source_segments=[CalibrationSourceSegment(kind="anchored", text="A unit test improves quality.", anchor_ref="a0", island_ref="island.001")],
    )


def _candidate(request: CalibrationRequest) -> TranslationCandidate:
    now = "2026-09-17T00:00:00+00:00"
    binding = TranslationProviderRequest(request=request)
    return TranslationCandidate(
        provider="FAKE",
        model="fake",
        request_binding_sha256=binding.request_binding_sha256,
        translation_options_sha256=binding.translation_options_sha256,
        source_sha256=request.source_sha256,
        unit_uid=request.unit_uid,
        display_id=request.display_id,
        response_text="译文",
        requested_at=now,
        responded_at=now,
        usage={"total_tokens": 1},
    )


class FakeMT:
    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        return _candidate(request.request)


class FakeMax:
    def translate_structured(self, request: TranslationProviderRequest):
        raise AssertionError("structured route not expected")


class FakeReviewer:
    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        review = ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid=request.request.unit_uid, verdict="ACCEPT", issues=[])
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request))


class FakeRepair:
    def repair(self, request, current, contract):
        raise AssertionError("repair not expected")


def _providers() -> WorkerProviders:
    return WorkerProviders(mt=FakeMT(), structured=FakeMax(), reviewer=FakeReviewer(), repair=FakeRepair())


def _pass_qa(view, request, response, known):
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}


def test_startup_milestones_flush() -> None:
    stream = _FlushStream()
    line = emit_milestone("CANARY_START", stream=stream)
    assert line == "CANARY_START"
    assert stream.flush_count >= 1
    assert "CANARY_START" in "".join(stream.lines)


def test_fake_stage_hang_dumps_traceback(tmp_path: Path) -> None:
    output = tmp_path / "canary"
    output.mkdir()
    fault = tmp_path / "fault.log"
    hung = {"done": False}

    def hang() -> None:
        hung["done"] = True
        time.sleep(2.5)

    with fault.open("w", encoding="utf-8") as handle:
        diagnostics = CanaryDiagnostics(
            output,
            stream=_FlushStream(),
            watchdog_seconds=1,
            watchdog_file=handle,
            stage_hooks={"CANARY_START": hang},
        )
        diagnostics.arm_watchdog()
        try:
            diagnostics.emit("CANARY_START")
        finally:
            diagnostics.cancel_watchdog()
    text = fault.read_text(encoding="utf-8", errors="replace")
    assert hung["done"] is True
    assert "Timeout" in text or "dump_traceback" in text or "File " in text


def test_subprocess_timeout_does_not_hang() -> None:
    started = time.perf_counter()
    with pytest.raises(TimeoutError, match="SUBPROCESS_TIMEOUT"):
        run_bounded_subprocess([sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1)
    assert time.perf_counter() - started < 8


def test_provider_call_milestone_visible(tmp_path: Path) -> None:
    production_run = tmp_path / "run"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary-out"
    stream = _FlushStream()
    diagnostics = CanaryDiagnostics(output, stream=stream, watchdog_seconds=0)
    request = _request()
    run_isolated_canary(
        output_dir=output,
        requests=[request],
        providers=_providers(),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_pass_qa,
        frozen_order=[request.display_id],
        diagnostics=diagnostics,
    )
    text = "".join(stream.lines)
    assert "UNIT_START" in text
    assert "ROUTE QWEN_MT_PLUS" in text
    assert "PROVIDER_CALL_START" in text
    assert "PROVIDER_CALL_PASS" in text
    assert "api_key" not in text.casefold()


def test_failure_before_first_api_writes_session(tmp_path: Path) -> None:
    isolated = tmp_path / "canary-fail"
    run = tmp_path / "full-book-run"
    (run / "full_book_batch_022").mkdir(parents=True)
    source = tmp_path / "not-the-book.docx"
    source.write_bytes(b"not-the-source")
    with pytest.raises(FrozenSelectionError, match="SOURCE_MUTATED"):
        run_canary_command(
            root=tmp_path,
            run=run,
            isolated_output=isolated,
            source_path=source,
            watchdog_seconds=0,
            require_provider_env=False,
        )
    session = json.loads((isolated / "canary_session.json").read_text(encoding="utf-8"))
    assert session["status"] == "FAILED"
    assert session["current_stage"] == "CANARY_FAILED"
    assert "api_key" not in json.dumps(session).casefold()
    assert not (run / "full_book_batch_022" / "checkpoint_03").exists()


def test_milestone_never_prints_api_key() -> None:
    stream = _FlushStream()
    line = emit_milestone("CONFIG", stream=stream, token="api_key=secret")
    text = "".join(stream.lines).casefold()
    assert "api_key" not in text
    assert "secret" not in text
    assert "<redacted>" in line


def test_canary_crash_does_not_create_production_checkpoint(tmp_path: Path) -> None:
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    batch = production_run / "full_book_batch_022"
    batch.mkdir(parents=True)
    output = tmp_path / "isolated"
    output.mkdir()

    def boom() -> None:
        raise RuntimeError("injected-canary-crash")

    diagnostics = CanaryDiagnostics(output, stream=_FlushStream(), watchdog_seconds=0, stage_hooks={"UNIT_START": boom})
    request = _request()
    with pytest.raises(RuntimeError, match="injected-canary-crash"):
        run_isolated_canary(
            output_dir=output,
            requests=[request],
            providers=_providers(),
            production_run=production_run,
            views={request.display_id: SimpleNamespace()},
            known_unit_ids={request.unit_uid},
            qa_fn=_pass_qa,
            diagnostics=diagnostics,
        )
    assert not (batch / "checkpoint_03").exists()
    assert (production_run / "production_campaign_worker.lock").exists() is False


def test_selected_jsonl_load_stops_after_requested_rows(tmp_path: Path) -> None:
    path = tmp_path / "blocks.jsonl"
    rows = [
        {"display_id": "body.p.00001", "unit_uid": "a", "story": "body", "part_name": "word/document.xml", "block_type": "p", "content_role": "body_paragraph", "locator": {"part_name": "word/document.xml", "story": "body", "xml_path": "/w:document[1]"}, "source_plain_text": "one", "source_hash": "h1", "structure_fingerprint": "s1"},
        {"display_id": "body.p.06939", "unit_uid": "b", "story": "body", "part_name": "word/document.xml", "block_type": "p", "content_role": "body_paragraph", "locator": {"part_name": "word/document.xml", "story": "body", "xml_path": "/w:document[1]"}, "source_plain_text": "two", "source_hash": "h2", "structure_fingerprint": "s2"},
        {"display_id": "body.p.99999", "unit_uid": "c", "story": "body", "part_name": "word/document.xml", "block_type": "p", "content_role": "body_paragraph", "locator": {"part_name": "word/document.xml", "story": "body", "xml_path": "/w:document[1]"}, "source_plain_text": "three", "source_hash": "h3", "structure_fingerprint": "s3"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    from booktrans.domain.ir import BlockIR

    found = load_selected_jsonl_by_display_id(path, BlockIR, ["body.p.06939"])
    assert list(found) == ["body.p.06939"]
    assert found["body.p.06939"].unit_uid == "b"
