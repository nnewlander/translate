from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from booktrans.architecture_explorer.candidate_scanner import (
    AUXILIARY_TEST_FILES,
    PRODUCTION_FILES,
    REQUIRED_EVIDENCE_KINDS,
    SCAN_FILES,
    scan_supervisor_worker_candidate,
)
from booktrans.architecture_explorer.candidate_report import render_candidate_report
from booktrans.architecture_explorer.generator import ExplorerInputError


REPOSITORY = Path(__file__).resolve().parents[2]
CLI = REPOSITORY / "scripts" / "architecture_explorer" / "generate_supervisor_worker_candidate.py"


SUPERVISOR = r'''$deterministicWorkerScript = Join-Path $Root "scripts\production\run_qwen_deterministic_worker.py"

function Start-DeterministicWorkerProcess([string]$FilePath, [string[]]$ArgumentVector, [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath, [ref]$LaunchDiagnostics) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new($FilePath, [string]::Join(' ', $ArgumentVector))
    $child = [System.Diagnostics.Process]::new()
    $child.StartInfo = $startInfo
    if (-not $child.Start()) { throw 'launch failed' }
    $child.WaitForExit()
    return $child
}

function After-Boundary { return }

$workerArgumentVector = @(
    $deterministicWorkerScript, '--root', $Root, '--run-path', $run,
    '--worker-id', $workerId, '--handoff-path', $workerHandoffPath,
    '--batch-label', $before.active_batch, '--checkpoint', $before.next_checkpoint,
    '--mode', 'production', '--authorize-production-checkpoint-write'
) + $workerArgs
$child = Start-DeterministicWorkerProcess $python $workerArgumentVector $Root $workerStdoutPath $workerStderrPath ([ref]$launchDiagnostics)
'''

WRAPPER = '''from booktrans.production_worker.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
'''

WORKER = '''import argparse

def run_production_checkpoint(**kwargs):
    return 0

def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--batch-label", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--mode", choices=("canary", "production"), default="canary")
    parser.add_argument("--authorize-production-checkpoint-write", action="store_true")
    return parser

def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode == "canary":
        return 0
    if not args.worker_id:
        raise SystemExit("WORKER_ID_REQUIRED")
    if bool(args.batch_label) != bool(args.checkpoint):
        raise SystemExit("WORKER_LAUNCH_BINDING_INCOMPLETE")
    return run_production_checkpoint(
        worker_id=args.worker_id,
        authorize=args.authorize_production_checkpoint_write,
        batch_label=args.batch_label,
        checkpoint=args.checkpoint,
    )
'''

LAUNCH_TEST = """$child = Start-DeterministicWorkerProcess $python @('-c', 'pass') $Root $out $err ([ref]$diagnostics)
Assert-True ($inheritChild.ExitCode -eq 0 -and $inheritDiagnostics.child_created) 'inherited-environment launch creates child'
'SUPERVISOR_LAUNCH_BOUNDARY_TESTS_PASS'
"""

BOOTSTRAP_TEST = '''def test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight():
    assert True
'''

PACKAGE_TEST = '''def test_production_worker_package_does_not_call_codex():
    assert "run_qwen_deterministic_worker.py"
'''


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def _commit(repository: Path, message: str) -> None:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=explorer-test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=NUL",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def _write(repository: Path, relative: str, content: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def source_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    _write(repository, PRODUCTION_FILES[0], SUPERVISOR)
    _write(repository, PRODUCTION_FILES[1], WRAPPER)
    _write(repository, PRODUCTION_FILES[2], WORKER)
    _write(repository, AUXILIARY_TEST_FILES[0], LAUNCH_TEST)
    _write(repository, AUXILIARY_TEST_FILES[1], BOOTSTRAP_TEST)
    _write(repository, AUXILIARY_TEST_FILES[2], PACKAGE_TEST)
    _write(repository, "large-production-output/must-not-be-read.bin", "not an input\n")
    _git(repository, "init", "--quiet")
    _commit(repository, "fixture")
    return repository


def _scan(repository: Path, hour: int = 0) -> dict:
    return scan_supervisor_worker_candidate(
        repository,
        scanned_at=f"2026-10-29T{hour:02d}:00:00Z",
    )


def test_complete_chain_is_candidate_not_approved(source_repository: Path) -> None:
    candidate = _scan(source_repository)
    relation = candidate["candidate_relation"]

    assert candidate["source_snapshot"]["worktree"] == {"state": "clean", "porcelain": []}
    assert [item["path"] for item in candidate["source_snapshot"]["input_files"]] == sorted(SCAN_FILES)
    assert relation["status"] == "candidate"
    assert relation["scan_outcome"] == "complete"
    assert relation["review_status"] == "PENDING_HUMAN_REVIEW"
    assert relation["runtime_state"] == "NOT_COLLECTED"
    assert relation["reachability"]["status"] == "STATIC_CONDITIONAL"
    assert relation["direction"] == "production-control -> translation-execution"
    assert all(candidate["required_findings"][kind]["status"] == "unique" for kind in REQUIRED_EVIDENCE_KINDS)
    assert len(relation["production_evidence_ids"]) == len(REQUIRED_EVIDENCE_KINDS)


def test_evidence_ids_and_candidate_judgment_ignore_line_moves(source_repository: Path) -> None:
    first = _scan(source_repository)
    for relative in SCAN_FILES:
        path = source_repository / relative
        path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    _commit(source_repository, "move lines only")

    second = _scan(source_repository, hour=1)

    assert {item["evidence_id"] for item in first["evidence"]} == {
        item["evidence_id"] for item in second["evidence"]
    }
    assert first["candidate_relation"]["scan_outcome"] == second["candidate_relation"]["scan_outcome"]
    assert first["candidate_relation"]["reachability"]["status"] == second["candidate_relation"]["reachability"]["status"]
    assert any(
        before["current_location"] != after["current_location"]
        for before, after in zip(first["evidence"], second["evidence"], strict=True)
    )


def test_same_source_has_stable_ids_decision_and_report_digest(source_repository: Path) -> None:
    first = _scan(source_repository)
    second = _scan(source_repository, hour=1)

    assert [item["evidence_id"] for item in first["evidence"]] == [
        item["evidence_id"] for item in second["evidence"]
    ]
    assert first["required_findings"] == second["required_findings"]
    assert first["candidate_relation"] == second["candidate_relation"]
    assert first["candidate_report_sha256"] == second["candidate_report_sha256"]


def test_missing_required_evidence_is_incomplete_and_unknown(source_repository: Path) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            ", '--authorize-production-checkpoint-write'", ""
        ),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["production_argument_vector"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"
    assert candidate["candidate_relation"]["review_status"] == "PENDING_HUMAN_REVIEW"


def test_multiple_required_matches_are_reported_as_ambiguous(source_repository: Path) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    text = path.read_text(encoding="utf-8")
    binding = text.splitlines()[0]
    path.write_text(f"{binding}\n{text}", encoding="utf-8")

    candidate = _scan(source_repository)
    finding = candidate["required_findings"]["worker_script_binding"]

    assert finding["status"] == "ambiguous"
    assert len(finding["evidence_ids"]) == 2
    assert candidate["candidate_relation"]["scan_outcome"] == "ambiguous"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


def test_unrecognized_static_branch_does_not_get_guessed(source_repository: Path) -> None:
    path = source_repository / PRODUCTION_FILES[2]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'if args.mode == "canary":', "if is_canary(args):"
        ),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["worker_production_branch"]["status"] == "missing"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"
    assert not candidate["candidate_relation"]["reachability"]["proven_conditions"]


@pytest.mark.parametrize(
    "replacement",
    [
        'if __name__ != "__main__":\n    raise SystemExit(main())',
        'if __name__ == "__main__":\n    raise SystemExit(other.main())',
    ],
)
def test_wrapper_requires_exact_main_guard_and_imported_function_identity(
    source_repository: Path,
    replacement: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[1]
    path.write_text(
        'from booktrans.production_worker.worker import main\n\n' + replacement + '\n',
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["worker_wrapper_main_call"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


def test_changed_wrapper_import_breaks_import_and_main_identity(source_repository: Path) -> None:
    path = source_repository / PRODUCTION_FILES[1]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "from booktrans.production_worker.worker import main",
            "from booktrans.production_worker.canary import main",
        ),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["worker_wrapper_import"]["status"] == "missing"
    assert candidate["required_findings"]["worker_wrapper_main_call"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"


@pytest.mark.parametrize(
    "rebinding",
    [
        "from unrelated.module import main",
        "main = unrelated_main",
    ],
)
def test_wrapper_main_rebinding_breaks_imported_function_identity(
    source_repository: Path,
    rebinding: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[1]
    path.write_text(
        "from booktrans.production_worker.worker import main\n\n"
        + rebinding
        + '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["worker_wrapper_main_call"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("$before.active_batch", "'WRONG_BATCH'"),
        ("$before.next_checkpoint", "'checkpoint_999'"),
    ],
)
def test_supervisor_state_binding_requires_exact_argument_values(
    source_repository: Path,
    original: str,
    replacement: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace(original, replacement),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["production_argument_vector"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"
    assert not candidate["candidate_relation"]["reachability"]["proven_conditions"]


@pytest.mark.parametrize(
    "decoy",
    [
        "    # '--batch-label', $before.active_batch\n"
        "    # '--checkpoint', $before.next_checkpoint\n",
        '    "\'--batch-label\', $before.active_batch", '
        '"\'--checkpoint\', $before.next_checkpoint",\n',
    ],
)
def test_supervisor_state_binding_ignores_comment_and_string_decoys(
    source_repository: Path,
    decoy: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    text = path.read_text(encoding="utf-8")
    text = text.replace("$before.active_batch", "'WRONG_BATCH'")
    text = text.replace("$before.next_checkpoint", "'WRONG_CHECKPOINT'")
    text = text.replace("    '--mode', 'production'", decoy + "    '--mode', 'production'")
    path.write_text(text, encoding="utf-8")

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["production_argument_vector"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "replacement",
    [
        "'--checkpoint', $before.next_checkpoint, '--batch-label', $before.active_batch",
        "'--batch-label', $before.active_batch, '--batch-label', $before.active_batch, "
        "'--checkpoint', $before.next_checkpoint",
    ],
)
def test_supervisor_argument_order_and_duplicates_are_not_proven(
    source_repository: Path,
    replacement: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "'--batch-label', $before.active_batch, '--checkpoint', $before.next_checkpoint",
            replacement,
        ),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["production_argument_vector"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


def test_supervisor_argument_vector_must_feed_the_launch_boundary(
    source_repository: Path,
) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Start-DeterministicWorkerProcess $python $workerArgumentVector",
            "Start-DeterministicWorkerProcess $python $otherArgumentVector",
        ),
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["production_argument_vector"]["status"] == "missing"
    assert candidate["required_findings"]["process_boundary_invocation"]["status"] == "missing"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


def test_process_boundary_definition_ignores_comment_decoys(
    source_repository: Path,
) -> None:
    path = source_repository / PRODUCTION_FILES[0]
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "if (-not $child.Start()) { throw 'launch failed' }",
        "if (-not $true) { throw 'launch failed' } # $child.Start()",
    )
    text = text.replace("    $child.WaitForExit()", "    # $child.WaitForExit()")
    path.write_text(text, encoding="utf-8")

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["process_boundary_definition"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


def test_production_call_inside_canary_branch_is_not_non_canary_evidence(
    source_repository: Path,
) -> None:
    path = source_repository / PRODUCTION_FILES[2]
    text = path.read_text(encoding="utf-8")
    start = text.index("def main(argv=None):")
    path.write_text(
        text[:start]
        + '''def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode == "canary":
        return run_production_checkpoint(
            authorize=args.authorize_production_checkpoint_write,
        )
    if not args.worker_id:
        raise SystemExit("WORKER_ID_REQUIRED")
    if bool(args.batch_label) != bool(args.checkpoint):
        raise SystemExit("WORKER_LAUNCH_BINDING_INCOMPLETE")
    return 0
''',
        encoding="utf-8",
    )

    candidate = _scan(source_repository)

    assert candidate["required_findings"]["worker_production_branch"]["status"] == "missing"
    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "guard",
    [
        '    if not args.worker_id:\n        raise SystemExit("WORKER_ID_REQUIRED")\n',
        '    if bool(args.batch_label) != bool(args.checkpoint):\n        raise SystemExit("WORKER_LAUNCH_BINDING_INCOMPLETE")\n',
    ],
)
def test_missing_worker_guard_cannot_prove_production_path(
    source_repository: Path,
    guard: str,
) -> None:
    path = source_repository / PRODUCTION_FILES[2]
    path.write_text(path.read_text(encoding="utf-8").replace(guard, ""), encoding="utf-8")

    candidate = _scan(source_repository)

    assert candidate["candidate_relation"]["scan_outcome"] == "incomplete"
    assert candidate["candidate_relation"]["reachability"]["status"] == "UNKNOWN"
    assert not candidate["candidate_relation"]["reachability"]["proven_conditions"]


def test_marker_without_launch_assertion_is_not_auxiliary_behavior_evidence(
    source_repository: Path,
) -> None:
    path = source_repository / AUXILIARY_TEST_FILES[0]
    path.write_text("'SUPERVISOR_LAUNCH_BOUNDARY_TESTS_PASS'\n", encoding="utf-8")

    candidate = _scan(source_repository)

    assert not any(
        item["evidence_kind"] == "auxiliary_launch_boundary_test"
        for item in candidate["evidence"]
    )


def test_allowlisted_path_is_validated_before_external_junction_is_read(
    source_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production = source_repository / "scripts/production"
    outside = tmp_path / "outside-production"
    outside.mkdir()
    for name in ("production_campaign_supervisor.ps1", "run_qwen_deterministic_worker.py"):
        shutil.copyfile(production / name, outside / name)
    shutil.rmtree(production)
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(production), str(outside)],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    else:
        production.symlink_to(outside, target_is_directory=True)

    original = Path.read_bytes
    outside_was_read = False

    def observed_read(path: Path) -> bytes:
        nonlocal outside_was_read
        if path.resolve().is_relative_to(outside.resolve()):
            outside_was_read = True
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", observed_read)

    with pytest.raises(ExplorerInputError, match="escapes|link|reparse"):
        _scan(source_repository)
    assert outside_was_read is False


def test_source_change_during_single_read_fails_consistency_check(
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = (source_repository / PRODUCTION_FILES[2]).resolve()
    original = Path.read_bytes
    changed = False

    def changing_read(path: Path) -> bytes:
        nonlocal changed
        raw = original(path)
        if path.resolve() == target and not changed:
            changed = True
            path.write_bytes(raw + b"\n# changed during scan\n")
        return raw

    monkeypatch.setattr(Path, "read_bytes", changing_read)

    with pytest.raises(ExplorerInputError, match="changed during scan|inconsistent"):
        _scan(source_repository)


def test_auxiliary_tests_are_never_production_evidence(source_repository: Path) -> None:
    candidate = _scan(source_repository)
    evidence_by_id = {item["evidence_id"]: item for item in candidate["evidence"]}
    relation = candidate["candidate_relation"]

    assert relation["auxiliary_test_evidence_ids"]
    assert all(
        evidence_by_id[evidence_id]["source_role"] == "auxiliary_test"
        for evidence_id in relation["auxiliary_test_evidence_ids"]
    )
    assert all(
        evidence_by_id[evidence_id]["source_role"] == "production"
        for evidence_id in relation["production_evidence_ids"]
    )
    assert set(relation["auxiliary_test_evidence_ids"]).isdisjoint(relation["production_evidence_ids"])


def test_markdown_report_is_rendered_from_same_candidate_record(source_repository: Path) -> None:
    candidate = _scan(source_repository)
    report = render_candidate_report(candidate)

    assert candidate["candidate_report_sha256"] in report
    assert "PENDING_HUMAN_REVIEW" in report
    assert "NOT_COLLECTED" in report
    assert "授权审核者" in report
    assert "实施 Agent 不得填写批准结论" in report
    assert "\r" not in report
    for item in candidate["evidence"]:
        assert item["evidence_id"] in report


def test_cli_writes_machine_record_and_human_report(source_repository: Path) -> None:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPOSITORY / "src") + (os.pathsep + existing if existing else "")
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--repository",
            str(source_repository),
            "--output-directory",
            "output/t2-candidate",
            "--scanned-at",
            "2026-10-29T00:00:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    machine = json.loads((source_repository / "output/t2-candidate/candidate.json").read_text(encoding="utf-8"))
    report = (source_repository / "output/t2-candidate/candidate-review.md").read_text(encoding="utf-8")
    assert machine["candidate_relation"]["scan_outcome"] == "complete"
    assert machine["candidate_report_sha256"] in report
    assert "scan_outcome=complete" in result.stdout
