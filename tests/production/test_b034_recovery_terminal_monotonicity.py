"""Provider-free regression for the B034 current-bound corrective terminal."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

from booktrans.production_worker.continuation import (
    project_current_canonical_recovery,
    reconstruct_current_production_evidence,
)
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.production_checkpoint import (
    canonical_production_disposition,
    run_production_checkpoint,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.selection import load_checkpoint_d050_registry
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_034"
CHECKPOINT = "checkpoint_08"
UNIT = "body.p.10318"
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _clone(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "b034-terminal"
    batch = root / "full_book_batch_034"
    batch.mkdir(parents=True)
    shutil.copytree(RUN / BATCH / CHECKPOINT, batch / CHECKPOINT)
    shutil.copy2(RUN / "full_book_batch_034_selection.json", root / "full_book_batch_034_selection.json")
    shutil.copy2(RUN / BATCH / "translation_bible_snapshot.md", batch / "translation_bible_snapshot.md")
    shutil.copy2(RUN / BATCH / "batch_manifest.json", batch / "batch_manifest.json")
    shutil.copy2(RUN / "production_campaign_state.json", root / "production_campaign_state.json")
    return root, batch / CHECKPOINT


def _clone_historical_numeric_hard_stop(tmp_path: Path) -> tuple[Path, Path]:
    """Restore the immutable pre-Human, pre-seal B034 hard-stop timeslice.

    The live checkpoint legitimately acquired a Human descendant and seal after
    this incident.  R1 must continue to exercise the historical hard-stop
    handoff, so remove only those later *clone-local* successors.
    """
    root, checkpoint = _clone(tmp_path)
    for relative in (
        "checkpoint_evidence.json",
        "human_adjudications/body.p.10311.json",
        "human_approved_candidates/body.p.10311.json",
        "current_candidate_qa_reconciliations/body.p.10311.json",
    ):
        path = checkpoint / relative
        if path.exists():
            path.unlink()
    # The live manifest/state are now terminal.  Rebase only the clone's
    # derived projection to the historical checkpoint_08 partial state.
    manifest_path = root / BATCH.lower() / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"completed_count": 175, "checkpoint_count": 7, "remaining_in_batch": 25, "status": "OPEN_PROGRESS", "next_checkpoint": CHECKPOINT})
    manifest["checkpoints"] = [item for item in manifest["checkpoints"] if item["id"] != CHECKPOINT]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_path = root / "production_campaign_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "active_batch": BATCH, "completed_count": 175,
        "completed_checkpoints": [f"checkpoint_{number:02d}" for number in range(1, 8)],
        "next_checkpoint": CHECKPOINT, "next_logical_work_order_id": "body.p.10303",
        "campaign_status": "CONTINUE", "stop_reason": None,
    })
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root, checkpoint


def _kwargs(root: Path, checkpoint: Path) -> dict:
    inputs = frozen_batch_checkpoint_inputs(
        root, batch_label=BATCH, checkpoint=CHECKPOINT, source_path=SOURCE,
    )
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


def test_b034_terminal_corrective_is_consumed_without_provider_replay(tmp_path: Path) -> None:
    root, checkpoint = _clone(tmp_path)
    before = _hashes(checkpoint)
    kwargs = _kwargs(root, checkpoint)

    _, outcomes = reconstruct_current_production_evidence(**kwargs)
    item = next(item for item in outcomes if item.request.display_id == UNIT)
    decision = canonical_production_disposition(item, checkpoint_dir=checkpoint)
    identity = project_current_canonical_recovery(
        **kwargs, active_batch=BATCH, checkpoint=CHECKPOINT,
    )

    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "SAFE_CURRENT_HEAD_RELEASE_DEBT"
    assert identity is None
    assert _hashes(checkpoint) == before


class _NoProviderCalls:
    provider_name = "TEST_NO_PROVIDER"
    model = "test-no-provider"

    def translate(self, *_args, **_kwargs):
        raise AssertionError("unexpected translation provider call")

    def review(self, *_args, **_kwargs):
        raise AssertionError("unexpected reviewer provider call")

    def repair(self, *_args, **_kwargs):
        raise AssertionError("unexpected repair provider call")


def test_b034_numeric_hard_stop_handoff_is_blocked_not_recovery(tmp_path: Path) -> None:
    """A canonical numeric hard stop cannot be replayed as a recovery handoff."""
    root, _checkpoint = _clone_historical_numeric_hard_stop(tmp_path)
    inputs = frozen_batch_checkpoint_inputs(
        root, batch_label=BATCH, checkpoint=CHECKPOINT, source_path=SOURCE,
    )
    provider = _NoProviderCalls()
    handoff = root / "worker-handoff.json"
    assert run_production_checkpoint(
        root=root, run=root, worker_id="b034-hard-stop", handoff_path=handoff,
        authorize=True,
        providers=WorkerProviders(mt=provider, structured=provider, reviewer=provider, repair=provider),
        payload=inputs, qa_fn=harness_qa_record,
        launch_batch_label=BATCH, launch_checkpoint=CHECKPOINT,
    ) == 3
    result = __import__("json").loads(handoff.read_text(encoding="utf-8"))
    assert result["status"] == "BLOCKED"
    assert result["production_disposition"] == "HARD_STOP"
    assert result["disposition_reason"] == "CORE_QA_OR_PROTECTED_INTEGRITY_FAILED"


