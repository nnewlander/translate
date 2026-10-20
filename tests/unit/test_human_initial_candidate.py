"""Closed-world regression coverage for exhausted acquisition recovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from booktrans.production_worker.human_adjudication import (
    HumanAdjudicationError, apply_human_initial_candidate_to_production_checkpoint,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import _resolve_checkpoint_authoritative_candidate
from booktrans.production_worker.unit_store import append_raw_response_line
from tests.unit.test_qwen_deterministic_worker import _request, _raw


def _seed_exhausted(path: Path, request) -> None:
    (path / "provider_contract_failures").mkdir(parents=True)
    binding = enrich_request(request, translation_bible=None).request_binding_sha256
    for attempt in (0, 1):
        (path / "provider_contract_failures" / f"{request.display_id}.attempt_{attempt:02d}.json").write_text(
            json.dumps({"unit_uid": request.unit_uid, "source_sha256": request.source_sha256,
                        "request_binding_sha256": binding}), encoding="utf-8"
        )
    (path / "candidate_acquisition_terminals").mkdir()
    (path / "candidate_acquisition_terminals" / f"{request.display_id}.json").write_text(
        json.dumps({"terminal_class": "NO_SAFE_AUTHORITATIVE_CANDIDATE", "root_cause_class": "PROVIDER_ATTEMPTS_EXHAUSTED",
                    "unit_uid": request.unit_uid, "source_sha256": request.source_sha256,
                    "request_binding_sha256": binding}), encoding="utf-8"
    )


def _apply(path: Path, request, *, texts=("甲", "乙"), passed=True):
    return apply_human_initial_candidate_to_production_checkpoint(
        checkpoint_dir=path, request=request, provider_request=enrich_request(request, translation_bible=None),
        target_texts=texts, view=object(), known_unit_ids={request.unit_uid},
        qa_fn=lambda *_: {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": passed, "diagnostics": []},
    )


def test_initial_candidate_requires_exhaustion_and_no_raw_or_head(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two."))
    with pytest.raises(HumanAdjudicationError, match="ACQUISITION_NOT_EXHAUSTED"):
        _apply(tmp_path, request)
    _seed_exhausted(tmp_path, request)
    append_raw_response_line(tmp_path / "translation_raw_response.jsonl", _raw(request, ("甲", "乙")))
    with pytest.raises(HumanAdjudicationError, match="RAW_ALREADY_EXISTS"):
        _apply(tmp_path, request)


@pytest.mark.parametrize("texts,code", [(("甲",), "TARGET_TEXT_COUNT_MISMATCH"), (("甲", " "), "EMPTY_TARGET_TEXT")])
def test_initial_candidate_rejects_bad_segment_contract(tmp_path: Path, texts, code: str) -> None:
    request = _request(texts=("One.", "Two.")); _seed_exhausted(tmp_path, request)
    with pytest.raises(HumanAdjudicationError, match=code):
        _apply(tmp_path, request, texts=texts)
    assert not (tmp_path / "human_approved_candidates" / f"{request.display_id}.json").exists()


def test_initial_candidate_qa_failure_never_enters_authority(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two.")); _seed_exhausted(tmp_path, request)
    with pytest.raises(HumanAdjudicationError, match="QA_FAILED"):
        _apply(tmp_path, request, passed=False)
    assert not (tmp_path / "human_approved_candidates" / f"{request.display_id}.json").exists()


def test_initial_candidate_is_exact_bound_unique_head_and_preserves_failures(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two.")); _seed_exhausted(tmp_path, request)
    before = [(path.read_bytes()) for path in sorted((tmp_path / "provider_contract_failures").glob("*.json"))]
    result = _apply(tmp_path, request)
    head = _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, result["candidate"])
    assert head.candidate_sha256
    assert head.lineage[0]["artifact"] == "HUMAN_INITIAL_CANDIDATE"
    assert [path.read_bytes() for path in sorted((tmp_path / "provider_contract_failures").glob("*.json"))] == before
