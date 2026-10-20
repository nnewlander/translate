"""Production-shaped, provider-free B029 canonical recovery regression fixtures."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.continuation import (
    project_current_canonical_recovery,
    recalculate_canary_from_persisted_evidence,
    reconstruct_current_production_evidence,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.selection import load_checkpoint_d050_registry
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_029"
CHECKPOINT = "checkpoint_07"
UNIT = "body.p.09104"
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
SHORT_TEST_ROOT = Path(os.environ.get("BOOKTRANS_R3_TEST_ROOT", r"D:\bt-test"))


def _hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


def _clone(tmp_path: Path, name: str, *, incident_timeslice: bool = False) -> tuple[Path, Path]:
    root = tmp_path / name
    root.mkdir()
    batch = root / "full_book_batch_029"
    batch.mkdir()
    source = RUN / "full_book_batch_029" / CHECKPOINT
    shutil.copytree(source, batch / CHECKPOINT)
    shutil.copy2(RUN / "full_book_batch_029_selection.json", root / "full_book_batch_029_selection.json")
    shutil.copy2(RUN / "full_book_batch_029" / "translation_bible_snapshot.md", batch / "translation_bible_snapshot.md")
    checkpoint = batch / CHECKPOINT
    if incident_timeslice:
        # These are release-close descendants added after the R1/R2 incident.
        # Removing them only in the isolated clone reconstructs the exact
        # persisted state that required canonical recovery; the closed B029
        # source itself stays untouched.
        for stage in (
            "human_adjudications",
            "human_approved_candidates",
            "human_escalation_assessments",
            "human_escalation_packets",
            "release_boundary_adjudication_supersessions",
        ):
            (checkpoint / stage / f"{UNIT}.json").unlink(missing_ok=True)
    return root, checkpoint


@pytest.fixture(scope="module")
def r3_workspace() -> Path:
    """Use a compact, retained test-only root for deep immutable snapshots.

    checkpoint seal-generation paths already contain content-addressed names.
    Letting pytest add a per-test tmp_path and then remove it after every case
    can exceed Windows path limits and can block in recursive cleanup.  The
    directory is deliberately retained as a test cleanup debt; it is isolated
    from the production run and may be collected by a separate test-only GC.
    """
    root = SHORT_TEST_ROOT / f"r3-{uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root


def _inputs(root: Path) -> dict:
    return frozen_batch_checkpoint_inputs(root, batch_label=BATCH, checkpoint=CHECKPOINT, source_path=SOURCE)


def _kwargs(root: Path, checkpoint: Path) -> dict:
    inputs = _inputs(root)
    return {
        "output_dir": checkpoint,
        "requests": inputs["requests"],
        "production_run": root,
        "views": inputs["views"],
        "known_unit_ids": inputs["known_unit_ids"],
        "qa_fn": harness_qa_record,
        "frozen_order": inputs["order"],
        "selection_sha256": inputs["selection_sha256"],
        "source_sha256": inputs["source_sha256"],
        "translation_bible": inputs["bible"],
        "d050_registry": load_checkpoint_d050_registry(root, CHECKPOINT, batch_label=BATCH),
    }


def _r1_timeslice(checkpoint: Path) -> None:
    """Remove only post-R1 corrective evidence and restore the R1 CA head."""
    repair = json.loads((checkpoint / "semantic_rescue_repairs" / f"{UNIT}.json").read_text(encoding="utf-8"))
    unit = json.loads((checkpoint / "units" / f"{UNIT}.json").read_text(encoding="utf-8"))
    raw = next(json.loads(line) for line in (checkpoint / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(line)["unit_uid"] == unit["unit_uid"])
    unit["working"] = repair["working"]
    (checkpoint / "units" / f"{UNIT}.json").write_text(json.dumps(unit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mutation = {
        "artifact_type": "PRODUCTION_ACCEPTED_CANDIDATE_MUTATION", "schema_version": 1,
        "display_id": UNIT, "unit_uid": unit["unit_uid"], "mutation_kind": "SEMANTIC_PRIMARY_REPAIR",
        "parent_candidate_sha256": candidate_sha(__import__("booktrans.domain.calibration", fromlist=["TranslationResult"]).TranslationResult.model_validate(raw)),
        "candidate_binding_sha256": candidate_sha(__import__("booktrans.domain.calibration", fromlist=["TranslationResult"]).TranslationResult.model_validate(repair["working"])),
        "source_sha256": unit["source_sha256"], "selection_sha256": unit["selection_sha256"],
        "request_binding_sha256": repair["request_binding_sha256"], "working": repair["working"], "qa": unit["qa"],
        "semantic_terminal": "MODEL_REPAIR_PENDING", "production_candidate_disposition": "MODEL_REPAIR_PENDING", "accepted": True, "translation_api_calls": 0,
    }
    (checkpoint / "accepted_candidate_mutations" / f"{UNIT}.json").write_text(json.dumps(mutation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ("semantic_rescue", "semantic_rescue_completeness", "semantic_rescue_final_review_requests", "semantic_rescue_final_reviews", "semantic_rescue_corrective", "semantic_rescue_corrective_completeness", "semantic_rescue_corrective_eligibility", "semantic_rescue_corrective_final_review_requests", "semantic_rescue_corrective_final_reviews", "semantic_rescue_corrective_repairs", "semantic_rescue_rereviews", "semantic_rescue_rereview_requests"):
        (checkpoint / name / f"{UNIT}.json").unlink(missing_ok=True)


def _identity(root: Path, checkpoint: Path) -> dict | None:
    return project_current_canonical_recovery(**_kwargs(root, checkpoint), active_batch=BATCH, checkpoint=CHECKPOINT)


def _logical_identity(identity: dict | None, root: Path) -> dict | None:
    if identity is None:
        return None
    result = dict(identity)
    result["disposition_evidence_references"] = [str(Path(path).relative_to(root)) for path in identity["disposition_evidence_references"]]
    return result


def test_b029_current_human_supersession_makes_historical_recovery_inert(r3_workspace: Path) -> None:
    root, checkpoint = _clone(r3_workspace, "current-supersession")
    before = _hashes(checkpoint)
    assert _identity(root, checkpoint) is None
    assert _hashes(checkpoint) == before


def test_b029_r2_projection_is_pure_repeatable_and_does_not_mutate_source(r3_workspace: Path) -> None:
    root, checkpoint = _clone(r3_workspace, "r2", incident_timeslice=True)
    before = _hashes(checkpoint)
    first = _identity(root, checkpoint)
    second = _identity(root, checkpoint)
    assert _hashes(checkpoint) == before
    assert first == second
    assert first is not None
    assert first["recovery_family"] == "SEMANTIC_REREVIEW"
    assert first["disposition_reason"] == "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR"


def test_b029_r2_legacy_and_pure_reconstruction_have_identical_recovery_identity(r3_workspace: Path) -> None:
    legacy_root, legacy_checkpoint = _clone(r3_workspace, "r2-legacy", incident_timeslice=True)
    pure_root, pure_checkpoint = _clone(r3_workspace, "r2-pure", incident_timeslice=True)
    legacy_report, legacy_outcomes = recalculate_canary_from_persisted_evidence(
        **_kwargs(legacy_root, legacy_checkpoint), write_derived=True, include_outcomes=True,
    )
    pure_report, pure_outcomes = reconstruct_current_production_evidence(**_kwargs(pure_root, pure_checkpoint))
    assert legacy_report["gate"] == pure_report["gate"]
    assert [(x.request.display_id, x.final_status, x.semantic_terminal, x.qa) for x in legacy_outcomes] == [
        (x.request.display_id, x.final_status, x.semantic_terminal, x.qa) for x in pure_outcomes
    ]
    legacy_identity = _identity(legacy_root, legacy_checkpoint)
    pure_identity = _identity(pure_root, pure_checkpoint)
    assert _logical_identity(legacy_identity, legacy_root) == _logical_identity(pure_identity, pure_root)
    assert pure_identity is not None
    assert pure_identity["recovery_family"] == "SEMANTIC_REREVIEW"
    assert pure_identity["disposition_reason"] == "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR"
    assert pure_identity["failing_work_order_ids"] == [UNIT]
    assert pure_identity["recovery_fingerprint"]["affected_segment_indices"] == [0, 2]


def test_historical_review_hydration_cannot_override_newer_stage_evidence(r3_workspace: Path) -> None:
    root, checkpoint = _clone(r3_workspace, "hydration-precedence", incident_timeslice=True)
    unit_path = checkpoint / "units" / f"{UNIT}.json"
    unit = json.loads(unit_path.read_text(encoding="utf-8"))
    unit["review"] = {"schema_version": "booktrans.structured-review.v1", "unit_uid": unit["unit_uid"], "verdict": "ACCEPT", "issues": []}
    unit_path.write_text(json.dumps(unit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    identity = _identity(root, checkpoint)
    assert identity is not None
    assert identity["recovery_family"] == "SEMANTIC_REREVIEW"
    assert identity["disposition_reason"] == "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR"


def test_b029_r1_timeslice_projects_corrective_and_legacy_matches_pure(r3_workspace: Path) -> None:
    legacy_root, legacy_checkpoint = _clone(r3_workspace, "r1-legacy", incident_timeslice=True)
    pure_root, pure_checkpoint = _clone(r3_workspace, "r1-pure", incident_timeslice=True)
    _r1_timeslice(legacy_checkpoint); _r1_timeslice(pure_checkpoint)
    legacy_report, legacy_outcomes = recalculate_canary_from_persisted_evidence(**_kwargs(legacy_root, legacy_checkpoint), write_derived=True, include_outcomes=True)
    pure_report, pure_outcomes = reconstruct_current_production_evidence(**_kwargs(pure_root, pure_checkpoint))
    assert legacy_report["gate"] == pure_report["gate"]
    assert [(x.request.display_id, x.final_status, x.semantic_terminal) for x in legacy_outcomes] == [(x.request.display_id, x.final_status, x.semantic_terminal) for x in pure_outcomes]
    identity = _identity(pure_root, pure_checkpoint)
    assert identity is not None
    assert identity["recovery_family"] == "SEMANTIC_CORRECTIVE"
    assert identity["disposition_reason"] == "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE"
