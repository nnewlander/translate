from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from booktrans.architecture_explorer import validation
from booktrans.architecture_explorer.validation import (
    ChromeValidationEvidence,
    SuiteStatistics,
    ValidationEvidence,
    ValidationContext,
    ValidationReport,
    ValidationStatus,
    StageResult,
    render_report,
    validate,
    write_candidate_acceptance_record,
)


REPOSITORY = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPOSITORY / "scripts/architecture_explorer/verify.ps1"
WORKFLOW = REPOSITORY / ".github/workflows/architecture-explorer.yml"
DEFAULT_COLLECTED_NODEIDS = (
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
    "tests/architecture_explorer/test_acceptance_contract.py::"
    "test_contract_trust_semantics_match_the_published_explorer",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_degraded_relationship_never_receives_reviewed_graph_treatment",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
)
TICKET_02_REQUIRED_NODEIDS = (
    "tests/architecture_explorer/test_t8_security_repairs.py::"
    "test_github_import_flows_through_offline_build_to_generated_site[approved]",
    "tests/architecture_explorer/test_t8_security_repairs.py::"
    "test_github_import_flows_through_offline_build_to_generated_site[rejected]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_evidence_content_change_records_material_invalidation",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_recorded_node_invalidation_survives_source_restoration",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_explicit_update_never_overwrites_unusable_state[corrupt]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_explicit_update_never_overwrites_unusable_state[inconsistent]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_node_review_requires_the_t3_relationship_own_current_gate",
    "tests/architecture_explorer/test_t8_node_review.py::"
    "test_no_node_decision_projects_only_the_exact_target_as_pending",
    "tests/architecture_explorer/test_t8_node_review.py::"
    "test_trusted_approved_decision_requires_current_exact_state_before_projection",
    "tests/architecture_explorer/test_t8_node_review.py::"
    "test_trusted_rejected_decision_is_current_but_never_pending_or_approved",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_unusable_state_projects_unknown_not_pending[corrupt]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_real_browser_renders_historical_and_unknown_node_review_states[1440-historical-approved]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_real_browser_renders_historical_and_unknown_node_review_states[1280-historical-rejected]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_real_browser_renders_historical_and_unknown_node_review_states[960-unknown]",
    "tests/architecture_explorer/test_t8_node_review_continuity.py::"
    "test_real_chrome_preserves_historical_node_review_during_resize_round_trip",
)
DEFAULT_COLLECTED_NODEIDS += TICKET_02_REQUIRED_NODEIDS
VIEWPORT_SEQUENCE = ((1440, 900), (1280, 900), (960, 900), (1440, 900))
GRAPH_IDS = (
    "top-level",
    "document-understanding",
    "scope-and-request",
    "production-control",
    "translation-execution",
    "quality-and-repair",
    "production-evidence",
    "candidate-and-release",
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _isolated_candidate_repository(tmp_path: Path) -> tuple[Path, str, Path]:
    repository = tmp_path / "candidate-repository"
    files = {
        ".gitignore": "/output/\n",
        "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = ['src']\n",
        "docs/architecture-explorer-acceptance-v1.md": "# fixture contract\n",
        "tests/conftest.py": "# candidate pytest configuration\n",
        "src/booktrans/__init__.py": "# candidate package\n",
        "src/booktrans/production_worker/pipeline.py": "# scanned source fact\n",
    }
    for relative_path, content in files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repository, "init")
    _git(repository, "config", "user.email", "candidate@example.test")
    _git(repository, "config", "user.name", "Candidate Test")
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", "candidate fixture")
    return (
        repository,
        _git(repository, "rev-parse", "HEAD"),
        repository / "src/booktrans/__init__.py",
    )


def _clean_candidate_checkout(tmp_path: Path, head: str) -> tuple[Path, Path]:
    repository = tmp_path / "clean-candidate-checkout"
    subprocess.run(
        (
            "git",
            "clone",
            "--shared",
            "--no-checkout",
            "--quiet",
            str(REPOSITORY),
            str(repository),
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(repository, "checkout", "--detach", head)
    return repository, repository / "src/booktrans/__init__.py"


def _include_ticket02_junit_cases(junit_xml: str) -> str:
    root = ElementTree.fromstring(junit_xml)
    existing = {
        case.attrib.get("classname", "").replace(".", "/")
        + ".py::"
        + case.attrib["name"]
        for case in root.iter("testcase")
    }
    suite = next(root.iter("testsuite"))
    for nodeid in TICKET_02_REQUIRED_NODEIDS:
        if nodeid in existing:
            continue
        path, name = nodeid.split("::", 1)
        ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": path.removesuffix(".py").replace("/", "."),
                "name": name,
            },
        )
    return ElementTree.tostring(root, encoding="unicode")


def _valid_viewport_evidence() -> dict:
    identity = {"time_origin": 1234.5, "navigation_count": 1}
    return {
        "schema": "architecture-explorer.chrome-viewport-evidence.v1",
        "requested_browser": "chrome",
        "browser": {
            "product": "Chrome/142.0.0.0",
            "user_agent": "HeadlessChrome/142.0.0.0",
        },
        "page_identity": identity,
        "graphs": [
            {
                "graph_id": graph_id,
                "observations": [
                    {
                        "requested": [width, height],
                        "actual": [width, height],
                        "navigation_identity": identity,
                    }
                    for width, height in VIEWPORT_SEQUENCE
                ],
            }
            for graph_id in GRAPH_IDS
        ],
        "source_trail": {
            "keyboard_entry": True,
            "escape_focus_restored": True,
            "close_focus_restored": True,
            "observations": [
                {
                    "requested": [width, height],
                    "actual": [width, height],
                    "navigation_identity": identity,
                    "page_overflow": 0,
                    "dialog_overflow": 0,
                    "detail_overflow": 0,
                }
                for width, height in VIEWPORT_SEQUENCE
            ],
        },
    }


def _valid_joint_view_evidence() -> dict:
    evidence = _valid_viewport_evidence()
    widths = ([480, 900], [960, 900], [1280, 900], [1440, 900])
    evidence["graphs"] = [
        {
            "graph_id": f"view-{index}",
            "observations": [
                {
                    "requested": viewport,
                    "actual": viewport,
                    "navigation_identity": evidence["page_identity"],
                    "page_overflow": 0,
                    "relationship_ids": [],
                }
                for viewport in widths
            ],
        }
        for index in range(12)
    ]
    evidence.pop("source_trail")
    return evidence


class RecordingRunner:
    def __init__(
        self,
        results: list[subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str] | None] = []
        self.results = list(results or [])

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        self.environments.append(env)
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "\n".join(DEFAULT_COLLECTED_NODEIDS),
                "",
            )
        if self.results:
            return self.results.pop(0)
        return subprocess.CompletedProcess(command, 0, "", "")


class JunitRunner(RecordingRunner):
    def __init__(
        self,
        junit_xml: str,
        *,
        pytest_stdout: str = "",
        write_viewport_evidence: bool = True,
        viewport_evidence: dict | None = None,
        joint_view_evidence: dict | None = None,
        write_joint_view_evidence: bool = True,
        include_ticket02_required: bool = True,
    ) -> None:
        super().__init__()
        self.junit_xml = (
            _include_ticket02_junit_cases(junit_xml)
            if include_ticket02_required
            else junit_xml
        )
        self.pytest_stdout = pytest_stdout
        self.write_viewport_evidence = write_viewport_evidence
        self.viewport_evidence = viewport_evidence or _valid_viewport_evidence()
        self.joint_view_evidence = joint_view_evidence or _valid_joint_view_evidence()
        self.write_joint_view_evidence = write_joint_view_evidence

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "--collect-only" in command:
            root = ElementTree.fromstring(self.junit_xml)
            nodeids = []
            for case in root.iter("testcase"):
                class_name = case.attrib.setdefault(
                    "classname",
                    "tests.architecture_explorer.test_t7_top_level_graph",
                )
                nodeids.append(
                    class_name.replace(".", "/")
                    + ".py::"
                    + case.attrib["name"]
                )
            self.junit_xml = ElementTree.tostring(root, encoding="unicode")
            self.commands.append(command)
            self.environments.append(env)
            return subprocess.CompletedProcess(command, 0, "\n".join(nodeids), "")
        completed = super().run(command, cwd=cwd, env=env)
        if command[1:3] == ("-m", "pytest"):
            xml_path = Path(
                next(
                    value.split("=", 1)[1]
                    for value in command
                    if value.startswith("--junitxml=")
                )
            )
            xml_path.write_text(self.junit_xml, encoding="utf-8")
            if self.write_viewport_evidence and env is not None:
                evidence_path = Path(
                    env["ARCHITECTURE_EXPLORER_VIEWPORT_EVIDENCE"]
                )
                evidence_path.write_text(
                    json.dumps(self.viewport_evidence),
                    encoding="utf-8",
                )
            if self.write_joint_view_evidence and env is not None:
                joint_evidence_path = Path(
                    env["ARCHITECTURE_EXPLORER_JOINT_VIEW_EVIDENCE"]
                )
                joint_evidence_path.write_text(
                    json.dumps(self.joint_view_evidence),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(command, 0, self.pytest_stdout, "")
        return completed


class CollectedJunitRunner(JunitRunner):
    def __init__(
        self,
        junit_xml: str,
        collected_nodeids: tuple[str, ...],
        *,
        include_ticket02_required: bool = True,
    ) -> None:
        super().__init__(
            junit_xml,
            include_ticket02_required=include_ticket02_required,
        )
        self.collected_nodeids = collected_nodeids
        if include_ticket02_required:
            self.collected_nodeids += tuple(
                nodeid
                for nodeid in TICKET_02_REQUIRED_NODEIDS
                if nodeid not in self.collected_nodeids
            )

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "--collect-only" in command:
            self.commands.append(command)
            self.environments.append(env)
            return subprocess.CompletedProcess(
                command,
                0,
                "\n".join(self.collected_nodeids),
                "",
            )
        return super().run(command, cwd=cwd, env=env)


def _context(**changes: Path) -> ValidationContext:
    values = {
        "repository": REPOSITORY,
        "python_executable": REPOSITORY / ".venv/Scripts/python.exe",
        "python_prefix": REPOSITORY / ".venv",
        "module_file": REPOSITORY / "src/booktrans/__init__.py",
        "chrome_path": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    }
    values.update(changes)
    return ValidationContext(**values)


class FailingTemporaryDirectory:
    def __init__(self, errors: list[OSError]) -> None:
        self.errors = list(errors)
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        if self.errors:
            raise self.errors.pop(0)


def _windows_error(code: int, message: str) -> OSError:
    error = OSError(code, message)
    error.winerror = code
    return error


def test_windows_directory_not_empty_cleanup_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_directory = FailingTemporaryDirectory(
        [
            _windows_error(145, "directory is not empty"),
            _windows_error(145, "directory is not empty"),
        ]
    )
    delays: list[float] = []
    monkeypatch.setattr(validation.time, "sleep", delays.append)

    validation._cleanup_temporary_directory(temporary_directory)

    assert temporary_directory.cleanup_calls == 3
    assert delays == [0.1, 0.1]


def test_cleanup_does_not_retry_an_unrelated_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _windows_error(5, "access denied")
    temporary_directory = FailingTemporaryDirectory([error])
    delays: list[float] = []
    monkeypatch.setattr(validation.time, "sleep", delays.append)

    with pytest.raises(OSError) as caught:
        validation._cleanup_temporary_directory(temporary_directory)

    assert caught.value is error
    assert temporary_directory.cleanup_calls == 1
    assert delays == []


def test_directory_not_empty_cleanup_fails_after_the_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = [
        _windows_error(145, f"directory is not empty {index}")
        for index in range(5)
    ]
    temporary_directory = FailingTemporaryDirectory(errors)
    delays: list[float] = []
    monkeypatch.setattr(validation.time, "sleep", delays.append)

    with pytest.raises(OSError) as caught:
        validation._cleanup_temporary_directory(temporary_directory)

    assert caught.value is errors[-1]
    assert temporary_directory.cleanup_calls == 5
    assert delays == [0.1, 0.1, 0.1, 0.1]


def test_wrong_python_prefix_blocks_later_stages() -> None:
    runner = RecordingRunner()

    report = validate(
        _context(python_prefix=REPOSITORY.parent / "other/.venv"),
        runner=runner,
    )

    assert report.status("environment") is ValidationStatus.BLOCKED
    assert report.status("static") is ValidationStatus.NOT_RUN
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 2
    assert "sys.prefix" in report.first_reason
    assert runner.commands == []


def test_source_imported_from_another_worktree_blocks_later_stages() -> None:
    runner = RecordingRunner()

    report = validate(
        _context(module_file=REPOSITORY.parent / "other/src/booktrans/__init__.py"),
        runner=runner,
    )

    assert report.status("environment") is ValidationStatus.BLOCKED
    assert report.status("static") is ValidationStatus.NOT_RUN
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 2
    assert "booktrans.__file__" in report.first_reason
    assert runner.commands == []


def test_clean_candidate_repository_inputs_match_the_full_head(tmp_path: Path) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)
    ignored_output = repository / "output/validation/evidence.json"
    ignored_output.parent.mkdir(parents=True)
    ignored_output.write_text("{}\n", encoding="utf-8")

    validation.validate_candidate_inputs(
        repository,
        head_revision=head,
        module_file=module_file,
    )


def test_assume_unchanged_candidate_input_is_rejected(tmp_path: Path) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)
    relative_path = "tests/conftest.py"
    _git(repository, "update-index", "--assume-unchanged", "--", relative_path)
    (repository / relative_path).write_text(
        "# hidden candidate pytest configuration\n",
        encoding="utf-8",
    )
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""

    with pytest.raises(ValueError, match=r"assume-unchanged.*tests/conftest\.py"):
        validation.validate_candidate_inputs(
            repository,
            head_revision=head,
            module_file=module_file,
        )


def test_skip_worktree_candidate_input_is_rejected(tmp_path: Path) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)
    relative_path = "tests/conftest.py"
    _git(repository, "update-index", "--skip-worktree", "--", relative_path)
    (repository / relative_path).write_text(
        "# hidden candidate pytest configuration\n",
        encoding="utf-8",
    )
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""

    with pytest.raises(ValueError, match=r"skip-worktree.*tests/conftest\.py"):
        validation.validate_candidate_inputs(
            repository,
            head_revision=head,
            module_file=module_file,
        )


@pytest.mark.parametrize(
    ("relative_path", "staged"),
    (
        ("tests/conftest.py", True),
        ("pyproject.toml", False),
        ("src/booktrans/production_worker/pipeline.py", False),
        ("untracked-validation-input.py", False),
    ),
)
def test_dirty_candidate_repository_inputs_are_rejected(
    tmp_path: Path,
    relative_path: str,
    staged: bool,
) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)
    dirty_path = repository / relative_path
    dirty_path.parent.mkdir(parents=True, exist_ok=True)
    dirty_path.write_text("# dirty candidate validation input\n", encoding="utf-8")
    if staged:
        _git(repository, "add", "--", relative_path)

    with pytest.raises(ValueError, match="working tree is not clean"):
        validation.validate_candidate_inputs(
            repository,
            head_revision=head,
            module_file=module_file,
        )


def test_candidate_input_binding_rejects_an_abbreviated_head(tmp_path: Path) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)

    with pytest.raises(ValueError, match="full 40-character"):
        validation.validate_candidate_inputs(
            repository,
            head_revision=head[:12],
            module_file=module_file,
        )


def test_candidate_input_binding_rejects_source_imported_elsewhere(
    tmp_path: Path,
) -> None:
    repository, head, _ = _isolated_candidate_repository(tmp_path)

    with pytest.raises(ValueError, match="booktrans.__file__"):
        validation.validate_candidate_inputs(
            repository,
            head_revision=head,
            module_file=tmp_path / "other/src/booktrans/__init__.py",
        )


def test_candidate_cli_rejects_dirty_inputs_before_validation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    repository, head, _ = _isolated_candidate_repository(tmp_path)
    conftest = repository / "tests/conftest.py"
    conftest.write_text("# dirty candidate pytest configuration\n", encoding="utf-8")

    exit_code = validation.main(
        [
            "--repository",
            str(repository),
            "--candidate-head",
            head,
            "--target-contract",
            "architecture-explorer/v3",
            "--manifest-path",
            "architecture-explorer/manifests/v3-ticket-06.v1.json",
            "--candidate-record",
            str(repository / "output/candidate/evidence.json"),
        ]
    )

    output = capfd.readouterr().out
    assert exit_code == 1
    assert "tests/conftest.py" in output
    assert "candidate validation inputs rejected" in output
    assert "RESULT: FAIL (exit 1)" in output


@pytest.mark.parametrize(
    ("index_flag", "reason"),
    (
        ("--assume-unchanged", "assume-unchanged"),
        ("--skip-worktree", "skip-worktree"),
    ),
)
def test_candidate_cli_rejects_hidden_index_flags_before_validation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    index_flag: str,
    reason: str,
) -> None:
    repository, head, _ = _isolated_candidate_repository(tmp_path)
    relative_path = "tests/conftest.py"
    _git(repository, "update-index", index_flag, "--", relative_path)
    (repository / relative_path).write_text(
        "# hidden candidate pytest configuration\n",
        encoding="utf-8",
    )
    record_path = repository / "output/candidate/evidence.json"

    exit_code = validation.main(
        [
            "--repository",
            str(repository),
            "--candidate-head",
            head,
            "--target-contract",
            "architecture-explorer/v3",
            "--manifest-path",
            "architecture-explorer/manifests/v3-ticket-06.v1.json",
            "--candidate-record",
            str(record_path),
        ]
    )

    output = capfd.readouterr().out
    assert exit_code == 1
    assert reason in output
    assert relative_path in output
    assert "candidate validation inputs rejected" in output
    assert "RESULT: FAIL (exit 1)" in output
    assert not record_path.exists()


def test_missing_chrome_blocks_instead_of_skipping(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()

    report = validate(
        _context(chrome_path=tmp_path / "missing-chrome.exe"),
        runner=runner,
    )

    assert report.status("environment") is ValidationStatus.BLOCKED
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 2
    assert "Chrome" in report.first_reason
    assert runner.commands == []


def test_broken_project_dependencies_block_later_stages() -> None:
    runner = RecordingRunner(
        [subprocess.CompletedProcess((), 1, "", "broken dependency")]
    )

    report = validate(_context(), runner=runner)

    assert runner.commands[0][1:] == ("-m", "pip", "check")
    assert report.status("environment") is ValidationStatus.BLOCKED
    assert report.status("static") is ValidationStatus.NOT_RUN
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 2
    assert "broken dependency" in report.first_reason


def test_static_check_failure_fails_and_does_not_run_explorer() -> None:
    runner = RecordingRunner(
        [
            subprocess.CompletedProcess((), 0, "No broken requirements found.", ""),
            subprocess.CompletedProcess((), 1, "", "compile failed"),
        ]
    )

    report = validate(_context(), runner=runner)

    assert runner.commands[1][1:4] == ("-m", "compileall", "-q")
    assert report.status("environment") is ValidationStatus.PASS
    assert report.status("static") is ValidationStatus.FAIL
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 1
    assert "compile failed" in report.first_reason


def test_missing_required_static_tool_is_blocked_not_failed() -> None:
    passed = subprocess.CompletedProcess((), 0, "", "")
    runner = RecordingRunner(
        [
            passed,
            passed,
            subprocess.CompletedProcess((), 127, "", "node was not found"),
        ]
    )

    report = validate(_context(), runner=runner)

    assert runner.commands[2][:2] == ("node", "--check")
    assert report.status("static") is ValidationStatus.BLOCKED
    assert report.status("architecture-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 2
    assert "node was not found" in report.first_reason


def test_unsafe_existing_test_scratch_is_blocked(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "already-exists"
    scratch.mkdir()

    report = validate(
        _context(),
        runner=RecordingRunner(),
        scratch_root=scratch,
    )

    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "temporary" in report.first_reason


@pytest.mark.parametrize(
    "failure_reason",
    ["browser viewport mismatch", "chrome returned empty DOM"],
)
def test_pytest_failure_is_reported_as_fail_with_explicit_chrome(
    tmp_path: Path,
    failure_reason: str,
) -> None:
    passed = subprocess.CompletedProcess((), 0, "", "")
    runner = RecordingRunner(
        [
            passed,
            passed,
            passed,
            passed,
            passed,
            passed,
            subprocess.CompletedProcess((), 1, "", failure_reason),
        ]
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    pytest_command = runner.commands[-1]
    assert pytest_command[1:4] == ("-m", "pytest", "tests/architecture_explorer")
    assert runner.environments[-1]["ARCHITECTURE_EXPLORER_BROWSER"] == "chrome"
    assert report.status("environment") is ValidationStatus.PASS
    assert report.status("static") is ValidationStatus.PASS
    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert failure_reason in report.first_reason


def test_required_chrome_skip_blocks_an_otherwise_green_pytest_run(
    tmp_path: Path,
) -> None:
    runner = CollectedJunitRunner(
        """<testsuites><testsuite tests="1" skipped="1">
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip">
          <skipped message="required Chrome acceptance test" />
        </testcase></testsuite></testsuites>""",
        DEFAULT_COLLECTED_NODEIDS,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("environment") is ValidationStatus.PASS
    assert report.status("static") is ValidationStatus.PASS
    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "required Chrome" in report.first_reason


def test_success_reports_executed_chrome_scope_and_explicit_exclusions(
    tmp_path: Path,
) -> None:
    runner = JunitRunner(
        """<testsuites><testsuite tests="9">
        <testcase classname="tests.architecture_explorer.test_acceptance_contract"
          name="test_contract_trust_semantics_match_the_published_explorer" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_browser_opens_candidate_evidence_from_every_node_context_menu" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_degraded_relationship_never_receives_reviewed_graph_treatment" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        <testcase classname="tests.architecture_explorer.test_supervisor_worker_candidate"
          name="test_parameterized_guard[if\\nline]" />
        </testsuite></testsuites>"""
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("environment") is ValidationStatus.PASS
    assert report.status("static") is ValidationStatus.PASS
    assert report.status("architecture-explorer") is ValidationStatus.PASS
    assert report.status("firefox") is ValidationStatus.NOT_RUN
    assert report.status("edge") is ValidationStatus.NOT_RUN
    assert report.status("non-explorer") is ValidationStatus.NOT_RUN
    assert report.exit_code == 0
    assert report.evidence is not None
    assert report.evidence.complete_suite.as_dict() == {
        "collected": report.evidence.complete_suite.collected,
        "selected": report.evidence.complete_suite.collected,
        "executed": report.evidence.complete_suite.collected,
        "passed": report.evidence.complete_suite.collected,
        "skipped": 0,
        "deselected": 0,
        "failures": 0,
        "errors": 0,
    }
    assert report.evidence.required_chrome.passed == len(
        report.evidence.chrome.required_nodeids
    )
    assert "browser=chrome" in report.stage("architecture-explorer").detail
    assert "actual viewport sequence" in report.stage("architecture-explorer").detail


def test_no_tests_collected_is_blocked_not_failed(
    tmp_path: Path,
) -> None:
    passed = subprocess.CompletedProcess((), 0, "", "")
    runner = RecordingRunner(
        [
            passed,
            passed,
            passed,
            passed,
            passed,
            passed,
            subprocess.CompletedProcess((), 5, "no tests ran", ""),
        ]
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "no tests" in report.first_reason


def test_deselected_tests_fail_the_complete_directory_check(
    tmp_path: Path,
) -> None:
    runner = CollectedJunitRunner(
        """<testsuites><testsuite tests="2">
        <testcase
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        </testsuite></testsuites>""",
        DEFAULT_COLLECTED_NODEIDS,
    )
    runner.pytest_stdout = "2 passed, 3 deselected"

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "3 deselected" in report.first_reason


def test_missing_required_viewport_parameter_cannot_pass(
    tmp_path: Path,
) -> None:
    runner = JunitRunner(
        """<testsuites><testsuite tests="7">
        <testcase
          name="test_real_browser_opens_candidate_evidence_from_every_node_context_menu" />
        <testcase classname="tests.architecture_explorer.test_acceptance_contract"
          name="test_contract_trust_semantics_match_the_published_explorer" />
        <testcase name="test_degraded_relationship_never_receives_reviewed_graph_treatment" />
        <testcase
          name="test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids" />
        <testcase
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]" />
        <testcase name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        </testsuite></testsuites>"""
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "1440-900" in report.first_reason


def test_collected_required_test_missing_from_execution_cannot_pass(
    tmp_path: Path,
) -> None:
    chrome_class = "tests.architecture_explorer.test_t7_top_level_graph"
    executed = (
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
    )
    junit_cases = "".join(
        f'<testcase classname="{chrome_class}" name="{name}" />'
        for name in executed
    )
    junit_cases += (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer" />'
    )
    collected = tuple(
        f"tests/architecture_explorer/test_t7_top_level_graph.py::{name}"
        for name in executed
    ) + (
        "tests/architecture_explorer/test_acceptance_contract.py::"
        "test_contract_trust_semantics_match_the_published_explorer",
        "tests/architecture_explorer/test_t7_top_level_graph.py::"
        "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
    )
    runner = CollectedJunitRunner(
        f'<testsuites><testsuite tests="7">{junit_cases}</testsuite></testsuites>',
        collected,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "not executed" in report.first_reason
    assert "candidate_evidence" in report.first_reason


def test_deleted_evidence_scenario_cannot_hide_from_collection_comparison(
    tmp_path: Path,
) -> None:
    chrome_class = "tests.architecture_explorer.test_t7_top_level_graph"
    cases = (
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
    )
    junit_cases = "".join(
        f'<testcase classname="{chrome_class}" name="{name}" />'
        for name in cases
    ) + (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer" />'
    )
    runner = JunitRunner(
        f'<testsuites><testsuite tests="7">{junit_cases}</testsuite></testsuites>'
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "candidate_evidence" in report.first_reason


def test_missing_ticket02_critical_nodeid_cannot_pass(tmp_path: Path) -> None:
    missing = (
        "tests/architecture_explorer/test_t8_node_review_continuity.py::"
        "test_recorded_node_invalidation_survives_source_restoration"
    )
    collected = tuple(
        nodeid for nodeid in DEFAULT_COLLECTED_NODEIDS if nodeid != missing
    )
    cases = []
    for nodeid in collected:
        path, name = nodeid.split("::", 1)
        class_name = path.removesuffix(".py").replace("/", ".")
        cases.append(f'<testcase classname="{class_name}" name="{name}" />')
    runner = JunitRunner(
        "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>",
        include_ticket02_required=False,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "missing critical test scenarios" in report.first_reason
    assert "recorded_node_invalidation" in report.first_reason


def test_skipped_trust_scenario_cannot_pass(
    tmp_path: Path,
) -> None:
    t7_class = "tests.architecture_explorer.test_t7_top_level_graph"
    names = (
        "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
    )
    junit_cases = "".join(
        f'<testcase classname="{t7_class}" name="{name}" />'
        for name in names
    ) + (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer">'
        '<skipped message="trust check was not executed" /></testcase>'
    )
    runner = JunitRunner(
        f'<testsuites><testsuite tests="8" skipped="1">{junit_cases}'
        "</testsuite></testsuites>"
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "trust_semantics" in report.first_reason


def test_preset_viewport_text_cannot_replace_browser_observation_evidence(
    tmp_path: Path,
) -> None:
    t7_class = "tests.architecture_explorer.test_t7_top_level_graph"
    names = (
        "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
    )
    junit_cases = "".join(
        f'<testcase classname="{t7_class}" name="{name}" />'
        for name in names
    ) + (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer" />'
    )
    runner = JunitRunner(
        f'<testsuites><testsuite tests="8">{junit_cases}</testsuite></testsuites>',
        write_viewport_evidence=False,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "viewport evidence" in report.first_reason


def test_missing_joint_view_evidence_cannot_produce_a_passing_validation(
    tmp_path: Path,
) -> None:
    t7_class = "tests.architecture_explorer.test_t7_top_level_graph"
    names = (
        "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
        "test_real_chrome_preserves_every_projected_relationship_at_mobile_width",
    )
    junit_cases = "".join(
        f'<testcase classname="{t7_class}" name="{name}" />'
        for name in names
    ) + (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer" />'
    )
    runner = JunitRunner(
        f'<testsuites><testsuite tests="9">{junit_cases}</testsuite></testsuites>',
        write_joint_view_evidence=False,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.BLOCKED
    assert report.exit_code == 2
    assert "joint 12-View" in report.first_reason


@pytest.mark.parametrize("corruption", ("graph", "observation"))
def test_non_object_in_joint_view_evidence_is_reported_as_fail(
    tmp_path: Path,
    corruption: str,
) -> None:
    joint_evidence = _valid_joint_view_evidence()
    if corruption == "graph":
        joint_evidence["graphs"].append("not-a-view")
    else:
        joint_evidence["graphs"][0]["observations"][0] = "not-an-observation"
    runner = JunitRunner(
        """<testsuites><testsuite tests="9">
        <testcase classname="tests.architecture_explorer.test_acceptance_contract"
          name="test_contract_trust_semantics_match_the_published_explorer" />
        <testcase name="test_real_browser_opens_candidate_evidence_from_every_node_context_menu" />
        <testcase name="test_degraded_relationship_never_receives_reviewed_graph_treatment" />
        <testcase name="test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]" />
        <testcase name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        <testcase name="test_real_chrome_preserves_every_projected_relationship_at_mobile_width" />
        </testsuite></testsuites>""",
        joint_view_evidence=joint_evidence,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "non-object" in report.first_reason


@pytest.mark.parametrize("location", ("graphs", "graph-observations", "source-trail"))
@pytest.mark.parametrize("invalid_value", ("not-an-object", None, []))
def test_non_object_in_primary_viewport_evidence_is_reported_as_fail(
    tmp_path: Path,
    location: str,
    invalid_value: object,
) -> None:
    evidence = _valid_viewport_evidence()
    if location == "graphs":
        evidence["graphs"].append(invalid_value)
    elif location == "graph-observations":
        evidence["graphs"][0]["observations"].append(invalid_value)
    else:
        evidence["source_trail"]["observations"].append(invalid_value)
    runner = JunitRunner(
        """<testsuites><testsuite tests="9">
        <testcase classname="tests.architecture_explorer.test_acceptance_contract"
          name="test_contract_trust_semantics_match_the_published_explorer" />
        <testcase name="test_real_browser_opens_candidate_evidence_from_every_node_context_menu" />
        <testcase name="test_degraded_relationship_never_receives_reviewed_graph_treatment" />
        <testcase name="test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]" />
        <testcase name="test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]" />
        <testcase name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        <testcase name="test_real_chrome_preserves_every_projected_relationship_at_mobile_width" />
        </testsuite></testsuites>""",
        viewport_evidence=evidence,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "non-object" in report.first_reason
    assert "RESULT: FAIL (exit 1)" in render_report(report)


def test_contradictory_actual_viewport_evidence_cannot_pass(
    tmp_path: Path,
) -> None:
    evidence = _valid_viewport_evidence()
    evidence["graphs"][0]["observations"][1]["actual"] = [1279, 900]
    t7_class = "tests.architecture_explorer.test_t7_top_level_graph"
    names = (
        "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
        "test_degraded_relationship_never_receives_reviewed_graph_treatment",
        "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
        "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
        "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
    )
    junit_cases = "".join(
        f'<testcase classname="{t7_class}" name="{name}" />'
        for name in names
    ) + (
        '<testcase classname="tests.architecture_explorer.test_acceptance_contract" '
        'name="test_contract_trust_semantics_match_the_published_explorer" />'
    )
    runner = JunitRunner(
        f'<testsuites><testsuite tests="8">{junit_cases}</testsuite></testsuites>',
        viewport_evidence=evidence,
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "1279" in report.first_reason
    assert "did not match requested" in report.first_reason


def test_report_renders_each_status_and_first_reason() -> None:
    report = validate(
        _context(python_prefix=REPOSITORY.parent / "other/.venv"),
        runner=RecordingRunner(),
    )

    rendered = render_report(report)

    assert "environment: BLOCKED" in rendered
    assert "static: NOT_RUN" in rendered
    assert "architecture-explorer: NOT_RUN" in rendered
    assert "firefox: NOT_RUN" in rendered
    assert "edge: NOT_RUN" in rendered
    assert "non-explorer: NOT_RUN" in rendered
    assert f"FIRST_REASON: {report.first_reason}" in rendered
    assert "RESULT: BLOCKED (exit 2)" in rendered


def test_validation_cli_writes_utf8_result_under_gbk_with_chinese_and_replacement(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "中文-\ufffd"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY / "src")
    environment["PYTHONIOENCODING"] = "cp936"
    environment.pop("PYTHONUTF8", None)

    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "booktrans.architecture_explorer.validation",
            "--repository",
            str(repository),
        ),
        cwd=REPOSITORY,
        env=environment,
        check=False,
        capture_output=True,
    )

    output = completed.stdout.decode("utf-8")
    assert completed.returncode == 2
    assert str(repository) in output
    assert "RESULT: BLOCKED (exit 2)" in output
    assert b"UnicodeEncodeError" not in completed.stderr


def test_subprocess_runner_rejects_replacement_character_in_child_output() -> None:
    completed = validation.SubprocessCommandRunner().run(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write('child \\ufffd'.encode('utf-8'))",
        ),
        cwd=REPOSITORY,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "U+FFFD" in completed.stderr


def test_subprocess_runner_preserves_chinese_failure_detail() -> None:
    completed = validation.SubprocessCommandRunner().run(
        (
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stderr.buffer.write('中文异常'.encode('utf-8')); "
                "raise SystemExit(1)"
            ),
        ),
        cwd=REPOSITORY,
    )

    assert completed.returncode == 1
    assert completed.stderr == "中文异常"


def test_replacement_output_from_dependency_check_is_an_explicit_fail() -> None:
    runner = RecordingRunner(
        [
            subprocess.CompletedProcess(
                (),
                1,
                "",
                (
                    "subprocess output integrity failure: output contained "
                    "replacement character U+FFFD"
                ),
            )
        ]
    )

    report = validate(_context(), runner=runner)

    assert report.status("environment") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "U+FFFD" in report.first_reason
    assert "RESULT: FAIL (exit 1)" in render_report(report)


def test_replacement_output_from_pytest_collection_is_an_explicit_fail(
    tmp_path: Path,
) -> None:
    class ReplacementCollectionRunner(RecordingRunner):
        def run(
            self,
            command: tuple[str, ...],
            *,
            cwd: Path,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            if "--collect-only" in command:
                self.commands.append(command)
                self.environments.append(env)
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    (
                        "subprocess output integrity failure: output contained "
                        "replacement character U+FFFD"
                    ),
                )
            return super().run(command, cwd=cwd, env=env)

    report = validate(
        _context(),
        runner=ReplacementCollectionRunner(),
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "U+FFFD" in report.first_reason
    assert "RESULT: FAIL (exit 1)" in render_report(report)


def _passing_candidate_report() -> tuple[ValidationReport, str]:
    joint_view_nodeid = (
        "tests/architecture_explorer/test_ticket_61_semantic_routing_view.py::"
        "test_real_chrome_preserves_every_projected_relationship_at_mobile_width"
    )
    report = ValidationReport(
        stages=(
            StageResult("environment", ValidationStatus.PASS, "environment passed"),
            StageResult("static", ValidationStatus.PASS, "static passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.PASS,
                "583 tests executed; 69 required Chrome tests passed",
            ),
            StageResult("firefox", ValidationStatus.NOT_RUN, "manual only"),
            StageResult("edge", ValidationStatus.NOT_RUN, "not requested"),
            StageResult("non-explorer", ValidationStatus.NOT_RUN, "outside scope"),
        ),
        evidence=ValidationEvidence(
            complete_suite=SuiteStatistics(
                collected=583,
                selected=583,
                executed=583,
                passed=583,
                skipped=0,
                deselected=0,
                failures=0,
                errors=0,
            ),
            required_chrome=SuiteStatistics(
                collected=69,
                selected=69,
                executed=69,
                passed=69,
                skipped=0,
                deselected=0,
                failures=0,
                errors=0,
            ),
            chrome=ChromeValidationEvidence(
                product="Chrome/158.0.8127.36",
                user_agent="HeadlessChrome/158.0.8127.36",
                required_nodeids=(joint_view_nodeid,)
                + tuple(f"tests/example.py::test_required_chrome_{index}" for index in range(68)),
                joint_view_nodeid=joint_view_nodeid,
                joint_view_ids=tuple(f"view-{index}" for index in range(12)),
                joint_viewport_widths=(480, 960, 1280, 1440),
            ),
        ),
    )
    return report, joint_view_nodeid


def test_candidate_record_binds_one_head_and_exact_validation_statistics(
    tmp_path: Path,
) -> None:
    head = _git(REPOSITORY, "rev-parse", "HEAD")
    candidate_repository, module_file = _clean_candidate_checkout(tmp_path, head)
    report, joint_view_nodeid = _passing_candidate_report()
    record_path = candidate_repository / "output/candidate-acceptance/evidence.json"

    digest = write_candidate_acceptance_record(
        candidate_repository,
        report=report,
        head_revision=head,
        module_file=module_file,
        target_contract="architecture-explorer/v3",
        manifest_path="architecture-explorer/manifests/v3-ticket-06.v1.json",
        output_path=record_path,
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "CANDIDATE_ACCEPTANCE_PASS"
    assert record["head_revision"] == head
    assert len(record["head_tree"]) == 40
    assert record["head_tree"].isalnum()
    assert record["base_revision"] == "6a239f6919cbb791e69e56383b91dbb634aeaf0f"
    assert record["spec"] == {
        "issue": 58,
        "approved_sha256": (
            "0eb5f5650897fc0ba15b129a2782c09eeceda551e087cb3ff7e51ca5afbf1283"
        ),
    }
    assert record["manifest_sha256"] == (
        "fb6146bfac310170330b1ccda4d0d1822fe13c40b1b82fa7e5fb090a38a83b08"
    )
    assert record["base_content_sha256"] == (
        "af7fcc843f306765b0135390fc6b184a2d47af8d14322a11de4ad26bdfcd1be7"
    )
    assert record["head_content_sha256"] == (
        "518c26a6115d9ce5243b0b071f33a551deb50114c32c9e5ff5935f41c7c9e2cb"
    )
    assert record["complete_suite"] == report.evidence.complete_suite.as_dict()
    assert record["required_chrome"]["statistics"] == (
        report.evidence.required_chrome.as_dict()
    )
    assert record["required_chrome"]["joint_view_validation"] == {
        "test_nodeid": joint_view_nodeid,
        "views": 12,
        "view_ids": [f"view-{index}" for index in range(12)],
        "viewport_widths": [480, 960, 1280, 1440],
    }
    assert record["not_run"] == ["firefox", "edge", "non-explorer"]
    assert digest == validation.hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert record_path.with_suffix(".json.sha256").read_text(encoding="ascii") == (
        f"{digest}  {record_path.name}\n"
    )


def test_candidate_record_rechecks_the_complete_worktree_before_writing(
    tmp_path: Path,
) -> None:
    head = _git(REPOSITORY, "rev-parse", "HEAD")
    candidate_repository, module_file = _clean_candidate_checkout(tmp_path, head)
    report, _ = _passing_candidate_report()
    pipeline = candidate_repository / "src/booktrans/production_worker/pipeline.py"
    pipeline.write_text("# changed after validation\n", encoding="utf-8")
    record_path = candidate_repository / "output/candidate-acceptance/evidence.json"

    with pytest.raises(ValueError, match="working tree is not clean"):
        write_candidate_acceptance_record(
            candidate_repository,
            report=report,
            head_revision=head,
            module_file=module_file,
            target_contract="architecture-explorer/v3",
            manifest_path="architecture-explorer/manifests/v3-ticket-06.v1.json",
            output_path=record_path,
        )

    assert not record_path.exists()


@pytest.mark.parametrize(
    ("index_flag", "reason"),
    (
        ("--assume-unchanged", "assume-unchanged"),
        ("--skip-worktree", "skip-worktree"),
    ),
)
def test_candidate_record_rechecks_hidden_index_flags_before_writing(
    tmp_path: Path,
    index_flag: str,
    reason: str,
) -> None:
    repository, head, module_file = _isolated_candidate_repository(tmp_path)
    relative_path = "tests/conftest.py"
    _git(repository, "update-index", index_flag, "--", relative_path)
    (repository / relative_path).write_text(
        "# changed after validation\n",
        encoding="utf-8",
    )
    report, _ = _passing_candidate_report()
    record_path = repository / "output/candidate-acceptance/evidence.json"

    with pytest.raises(ValueError, match=rf"{reason}.*tests/conftest\.py"):
        write_candidate_acceptance_record(
            repository,
            report=report,
            head_revision=head,
            module_file=module_file,
            target_contract="architecture-explorer/v3",
            manifest_path="architecture-explorer/manifests/v3-ticket-06.v1.json",
            output_path=record_path,
        )

    assert not record_path.exists()


def test_candidate_record_refuses_a_non_passing_validation(tmp_path: Path) -> None:
    report = ValidationReport(
        stages=(StageResult("architecture-explorer", ValidationStatus.FAIL, "失败 \ufffd"),)
    )

    with pytest.raises(ValueError, match="passing validation"):
        write_candidate_acceptance_record(
            REPOSITORY,
            report=report,
            head_revision=(
                subprocess.run(
                    ("git", "rev-parse", "HEAD"),
                    cwd=REPOSITORY,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                ).stdout.strip()
            ),
            module_file=REPOSITORY / "src/booktrans/__init__.py",
            target_contract="architecture-explorer/v3",
            manifest_path="architecture-explorer/manifests/v3-ticket-06.v1.json",
            output_path=tmp_path / "must-not-exist" / "evidence.json",
        )


def test_powershell_entrypoint_delegates_the_single_validation_plan() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in script
    assert "PYTHONPATH" in script
    assert "PYTHONIOENCODING" in script
    assert "PYTHONUTF8" in script
    assert "'booktrans.architecture_explorer.validation'" in script
    assert "'-m'" in script
    assert "pytest" not in script
    assert "ARCHITECTURE_EXPLORER_BROWSER" not in script


def test_windows_pr_workflow_provisions_python_and_calls_only_the_entrypoint() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "windows-latest" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert "actions/checkout@v4" not in workflow
    assert "actions/setup-python@v5" not in workflow
    assert "python -m venv .venv" in workflow
    assert '.[dev]' in workflow
    assert workflow.count("scripts\\architecture_explorer\\verify.ps1") == 1
    assert "pytest" not in workflow
    assert "ARCHITECTURE_EXPLORER_BROWSER" not in workflow
    assert "node --check" not in workflow
    assert "paths:" not in workflow
    assert "branch protection" not in workflow.lower()
