import json

import pytest

from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker.candidate_authority import CandidateAuthorityError
from booktrans.production_worker.production_checkpoint import _candidate_binding, _resolve_checkpoint_authoritative_candidate


def _candidate(text: str) -> TranslationResult:
    return TranslationResult(
        unit_uid="unit.legacy-human",
        target_segments=[StructuredTargetSegment(text=text, kind="plain", island_ref="island.001")],
    )


def test_unbound_schema_v1_human_record_is_advisory_not_a_current_mutation(tmp_path):
    raw = _candidate("current")
    directory = tmp_path / "human_approved_candidates"
    directory.mkdir()
    (directory / "unit.json").write_text(json.dumps({
        "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE", "schema_version": 1,
        "current_display_id": "unit", "decision": "HUMAN_APPROVED",
        "semantic_terminal": "HUMAN_APPROVED", "working": _candidate("legacy").model_dump(),
        "qa": {"passed": True},
    }), encoding="utf-8")
    resolved = _resolve_checkpoint_authoritative_candidate(tmp_path, "unit", raw)
    assert resolved.candidate_sha256 == _candidate_binding(raw)


def test_bound_human_candidate_with_wrong_sha_still_fails_closed(tmp_path):
    raw = _candidate("current")
    directory = tmp_path / "human_approved_candidates"
    directory.mkdir()
    (directory / "unit.json").write_text(json.dumps({
        "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE", "schema_version": 2,
        "current_display_id": "unit", "decision": "HUMAN_APPROVED",
        "semantic_terminal": "HUMAN_APPROVED", "working": _candidate("bound").model_dump(),
        "candidate_binding_sha256": "0" * 64, "parent_candidate_sha256": _candidate_binding(raw),
    }), encoding="utf-8")
    with pytest.raises(CandidateAuthorityError, match="HUMAN_APPROVED_CANDIDATE_BINDING_MISMATCH"):
        _resolve_checkpoint_authoritative_candidate(tmp_path, "unit", raw)
