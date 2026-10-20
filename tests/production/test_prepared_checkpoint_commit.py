"""Integration coverage for recovery-safe checkpoint commit/rebuild."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Software Engineering at Google.docx"
REBUILD = ROOT / "scripts" / "production" / "rebuild_production_campaign_state.py"


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_prepared_checkpoint_commit_rebuilds_cursor_and_replays_as_noop(tmp_path):
    run = tmp_path / "run"; batch = run / "full_book_batch_001"; checkpoint = batch / "checkpoint_01"
    source_sha = sha256(SOURCE.read_bytes()).hexdigest()
    _write(run / "full_book_progress_v1.json", {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": source_sha,
        "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_000", "unresolved_deterministic_fail": 0, "human_required": 0, "recovery_queue": []})
    _write(run / "full_book_batch_001_selection.json", {"batch": "FULL_BOOK_BATCH_001", "selected_logical_work_order_ids": ["one", "two"]})
    _write(batch / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_001", "target_count": 2, "completed_count": 0,
        "checkpoint_count": 0, "next_checkpoint": "checkpoint_01", "status": "OPEN_PROGRESS", "checkpoints": []})
    checkpoint.mkdir(parents=True)
    request = [{"unit_uid": "provider"}, {"unit_uid": "human"}]
    _jsonl(checkpoint / "translation_request.jsonl", request)
    _jsonl(checkpoint / "translation_raw_response.jsonl", request[:1])
    _jsonl(checkpoint / "translation_normalized_response.jsonl", request[:1])
    _jsonl(checkpoint / "translation_repaired_response.jsonl", request)
    raw_sha = sha256((checkpoint / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    _write(checkpoint / "translation_normalization_report.json", {"raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha}, "input_unit_count": 1, "normalized_unit_count": 0})
    qa = {"pass_count": 2, "fail_count": 0, "records": [{"passed": True}, {"passed": True}]}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json", "translation_final_qa_report.json"):
        _write(checkpoint / name, qa)
    _write(checkpoint / "semantic_review_report.json", {"accept_count": 2})
    _write(checkpoint / "checkpoint_evidence.json", {"logical_work_orders": ["one", "two"], "d050_evidence_artifacts": []})
    _write(checkpoint / "human_approved_candidates" / "two.json", {"artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE", "schema_version": 3,
        "origin": "HUMAN_INITIAL_CANDIDATE", "semantic_terminal": "HUMAN_APPROVED", "unit_uid": "human", "working": {"unit_uid": "human"}, "qa": {"passed": True}})
    command = [sys.executable, str(REBUILD), "--run", str(run), "--source", str(SOURCE), "--commit-prepared-checkpoint", "checkpoint_01"]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout)["committed"] is True
    state = json.loads((run / "production_campaign_state.json").read_text(encoding="utf-8"))
    assert state["completed_count"] == 2 and state["next_checkpoint"] is None
    before = (batch / "batch_manifest.json").read_bytes()
    for _ in range(10):
        replay = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
        assert json.loads(replay.stdout)["committed"] is False
        assert (batch / "batch_manifest.json").read_bytes() == before
