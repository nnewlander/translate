from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from booktrans.architecture_explorer.validation import (
    ValidationContext,
    ValidationStatus,
    render_report,
    validate,
)


REPOSITORY = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPOSITORY / "scripts/architecture_explorer/verify.ps1"
WORKFLOW = REPOSITORY / ".github/workflows/architecture-explorer.yml"


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
        if self.results:
            return self.results.pop(0)
        return subprocess.CompletedProcess(command, 0, "", "")


class JunitRunner(RecordingRunner):
    def __init__(self, junit_xml: str, *, pytest_stdout: str = "") -> None:
        super().__init__()
        self.junit_xml = junit_xml
        self.pytest_stdout = pytest_stdout

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
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
            return subprocess.CompletedProcess(command, 0, self.pytest_stdout, "")
        return completed


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
    runner = JunitRunner(
        """<testsuites><testsuite tests="1" skipped="1">
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip">
          <skipped message="required Chrome acceptance test" />
        </testcase></testsuite></testsuites>"""
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
        """<testsuites><testsuite tests="3">
        <testcase classname="tests.architecture_explorer.test_acceptance_contract"
          name="test_contract_trust_semantics_match_the_published_explorer" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase classname="tests.architecture_explorer.test_t7_top_level_graph"
          name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
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
    assert "browser=chrome" in report.stage("architecture-explorer").detail
    assert "actual viewports" in report.stage("architecture-explorer").detail


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
    runner = JunitRunner(
        """<testsuites><testsuite tests="2">
        <testcase
          name="test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]" />
        <testcase name="test_real_chrome_reconnects_edges_during_same_page_resize_round_trip" />
        </testsuite></testsuites>""",
        pytest_stdout="2 passed, 3 deselected",
    )

    report = validate(
        _context(),
        runner=runner,
        scratch_root=tmp_path / "validation-scratch",
    )

    assert report.status("architecture-explorer") is ValidationStatus.FAIL
    assert report.exit_code == 1
    assert "3 deselected" in report.first_reason


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


def test_powershell_entrypoint_delegates_the_single_validation_plan() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in script
    assert "PYTHONPATH" in script
    assert "-m booktrans.architecture_explorer.validation" in script
    assert "pytest" not in script
    assert "ARCHITECTURE_EXPLORER_BROWSER" not in script


def test_windows_pr_workflow_provisions_python_and_calls_only_the_entrypoint() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "windows-latest" in workflow
    assert "python -m venv .venv" in workflow
    assert '.[dev]' in workflow
    assert workflow.count("scripts\\architecture_explorer\\verify.ps1") == 1
    assert "pytest" not in workflow
    assert "ARCHITECTURE_EXPLORER_BROWSER" not in workflow
    assert "node --check" not in workflow
    assert "paths:" not in workflow
    assert "branch protection" not in workflow.lower()
