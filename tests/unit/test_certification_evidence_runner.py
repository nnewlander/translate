from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path
import pytest

SCRIPT = Path("scripts/certification/run_certification_evidence.py").resolve()
SPEC = importlib.util.spec_from_file_location("certification_evidence_runner", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = runner; SPEC.loader.exec_module(runner)

def inputs(tmp_path: Path):
    book, selection, manifest, code = (tmp_path / name for name in ("book.docx", "selection.json", "batch_manifest.json", "source_manifest.json"))
    book.write_bytes(b"immutable book"); selection.write_text('{"selected_logical_work_order_ids":["one"]}', encoding="utf-8"); manifest.write_text('{"batch":"FULL_BOOK_BATCH_036","target_count":1}', encoding="utf-8")
    code.write_text(json.dumps([{"relative_path":"src/a.py","size":1,"sha256":"a" * 64}]), encoding="utf-8")
    return book, selection, manifest, code

def execute(tmp_path: Path, command: list[str], *, timeout_seconds: float | None = None):
    book, selection, manifest, code = inputs(tmp_path)
    return runner.run_evidence(receipt=tmp_path / "receipt.json", suite="existing-suite", command=command, cwd=tmp_path, book_source=book, selection=selection, batch_manifest=manifest, code_manifest=code, timeout_seconds=timeout_seconds)

def test_success_archives_real_exit_and_reuses_identical_receipt(tmp_path: Path):
    first = execute(tmp_path, [sys.executable, "-c", "print('2 passed')"])
    assert first["verdict"] == "PASS" and first["receipt"]["real_child_exit_code"] == 0 and first["receipt"]["test_counts"]["passed"] == 2
    assert Path(first["receipt"]["stdout_path"]).read_text(encoding="utf-8").strip() == "2 passed"
    assert execute(tmp_path, [sys.executable, "-c", "print('2 passed')"])["reused"] is True

def test_nonzero_child_cannot_become_pass(tmp_path: Path):
    result = execute(tmp_path, [sys.executable, "-c", "import sys; print('99 passed'); sys.exit(7)"])
    assert result["verdict"] == "FAIL" and result["receipt"]["real_child_exit_code"] == 7

def test_launch_failure_is_explicit(tmp_path: Path):
    result = execute(tmp_path, [str(tmp_path / "does-not-exist")])
    assert result["verdict"] == "FAIL" and result["receipt"]["real_child_exit_code"] == 127 and result["receipt"]["child_launch_error"]

def test_conflicting_receipt_fails_closed(tmp_path: Path):
    execute(tmp_path, [sys.executable, "-c", "pass"])
    with pytest.raises(RuntimeError, match="CERTIFICATION_RECEIPT_CONFLICT"): execute(tmp_path, [sys.executable, "-c", "print('different')"])

def test_book_and_code_digest_domains_are_distinct(tmp_path: Path):
    result = execute(tmp_path, [sys.executable, "-c", "pass"])
    assert result["receipt"]["book_source_binding"]["book_source_sha256"] != result["receipt"]["code_baseline_digest"]

def test_timeout_verifies_root_and_descendant_cleanup(tmp_path: Path):
    command = [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"]
    result = execute(tmp_path, command, timeout_seconds=1)
    receipt = result["receipt"]
    assert receipt["verdict"] == "FAIL" and receipt["terminal_state"] == "TIMEOUT"
    assert receipt["process_tree_cleanup"] == "PASS" and receipt["cleanup_verified"] is True
    assert receipt["surviving_pids"] == [] and len(receipt["tracked_pids"]) >= 2
