"""Authoritative Architecture Explorer validation orchestration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Protocol
from xml.etree import ElementTree


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class ValidationContext:
    repository: Path
    python_executable: Path
    python_prefix: Path
    module_file: Path
    chrome_path: Path


@dataclass(frozen=True)
class StageResult:
    name: str
    status: ValidationStatus
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    stages: tuple[StageResult, ...]

    def stage(self, name: str) -> StageResult:
        return next(stage for stage in self.stages if stage.name == name)

    def status(self, name: str) -> ValidationStatus:
        return self.stage(name).status

    @property
    def first_reason(self) -> str:
        for stage in self.stages:
            if stage.status in {ValidationStatus.FAIL, ValidationStatus.BLOCKED}:
                return stage.detail
        return ""

    @property
    def exit_code(self) -> int:
        statuses = {stage.status for stage in self.stages}
        if ValidationStatus.BLOCKED in statuses:
            return 2
        if ValidationStatus.FAIL in statuses:
            return 1
        return 0


class CommandRunner(Protocol):
    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ): ...


class SubprocessCommandRunner:
    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))


_EXPLICIT_EXCLUSIONS = (
    StageResult("firefox", ValidationStatus.NOT_RUN, "Owner manual acceptance only"),
    StageResult("edge", ValidationStatus.NOT_RUN, "supplemental browser not requested"),
    StageResult(
        "non-explorer",
        ValidationStatus.NOT_RUN,
        "translation full-repository tests are outside this check",
    ),
)


def _report(*stages: StageResult) -> ValidationReport:
    return ValidationReport(stages + _EXPLICIT_EXCLUSIONS)


def render_report(report: ValidationReport) -> str:
    """Render the stable console summary consumed locally and in CI logs."""

    lines = ["ARCHITECTURE_EXPLORER_VALIDATION v1"]
    lines.extend(
        f"{stage.name}: {stage.status.value} - {stage.detail}"
        for stage in report.stages
    )
    if report.first_reason:
        lines.append(f"FIRST_REASON: {report.first_reason}")
    result = (
        ValidationStatus.BLOCKED
        if report.exit_code == 2
        else ValidationStatus.FAIL
        if report.exit_code == 1
        else ValidationStatus.PASS
    )
    lines.append(f"RESULT: {result.value} (exit {report.exit_code})")
    return "\n".join(lines)


def _blocked_report(reason: str) -> ValidationReport:
    return _report(
        StageResult("environment", ValidationStatus.BLOCKED, reason),
        StageResult("static", ValidationStatus.NOT_RUN, "environment blocked"),
        StageResult(
            "architecture-explorer",
            ValidationStatus.NOT_RUN,
            "environment blocked",
        ),
    )


def validate(
    context: ValidationContext,
    *,
    runner: CommandRunner,
    scratch_root: Path | None = None,
) -> ValidationReport:
    """Validate the environment, then run the Architecture Explorer checks."""

    expected_prefix = (context.repository / ".venv").resolve()
    actual_prefix = context.python_prefix.resolve()
    if actual_prefix != expected_prefix:
        reason = f"sys.prefix {actual_prefix} does not match {expected_prefix}"
        return _blocked_report(reason)

    if not context.python_executable.is_file():
        return _blocked_report(
            f"project Python is unavailable at {context.python_executable}"
        )

    source_root = (context.repository / "src").resolve()
    module_file = context.module_file.resolve()
    if not module_file.is_relative_to(source_root):
        return _blocked_report(
            f"booktrans.__file__ {module_file} is outside current Worktree {source_root}"
        )

    if not context.chrome_path.is_file():
        return _blocked_report(f"Chrome is unavailable at {context.chrome_path}")

    dependency_check = runner.run(
        (str(context.python_executable), "-m", "pip", "check"),
        cwd=context.repository,
    )
    if dependency_check.returncode != 0:
        detail = dependency_check.stderr.strip() or dependency_check.stdout.strip()
        return _blocked_report(
            f"pip check failed: {detail or f'exit {dependency_check.returncode}'}"
        )

    environment_stage = StageResult(
        "environment",
        ValidationStatus.PASS,
        f"python={context.python_executable}; browser=chrome:{context.chrome_path}",
    )
    static_commands = (
        (
            "Python compile",
            (
                str(context.python_executable),
                "-m",
                "compileall",
                "-q",
                "src/booktrans/architecture_explorer",
                "tests/architecture_explorer",
            ),
        ),
        ("app.js syntax", ("node", "--check", "architecture-explorer/web/app.js")),
        ("t4-state.js syntax", ("node", "--check", "architecture-explorer/web/t4-state.js")),
        ("working tree whitespace", ("git", "diff", "--check")),
        ("staged whitespace", ("git", "diff", "--cached", "--check")),
    )
    for label, command in static_commands:
        completed = runner.run(command, cwd=context.repository)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            status = (
                ValidationStatus.BLOCKED
                if completed.returncode == 127
                else ValidationStatus.FAIL
            )
            return _report(
                environment_stage,
                StageResult(
                    "static",
                    status,
                    f"{label} failed: {detail or f'exit {completed.returncode}'}",
                ),
                StageResult(
                    "architecture-explorer",
                    ValidationStatus.NOT_RUN,
                    "static checks failed",
                ),
            )

    try:
        if scratch_root is None:
            scratch_root = Path(
                tempfile.mkdtemp(prefix="architecture-explorer-validation-")
            )
        else:
            scratch_root.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                f"isolated temporary test directory is unavailable: {error}",
            ),
        )
    pytest_xml = scratch_root / "pytest.xml"
    pytest_temp = scratch_root / "pytest"
    test_environment = dict(os.environ)
    test_environment["PYTHONPATH"] = str((context.repository / "src").resolve())
    test_environment["ARCHITECTURE_EXPLORER_BROWSER"] = "chrome"
    pytest_command = (
        str(context.python_executable),
        "-m",
        "pytest",
        "tests/architecture_explorer",
        "-q",
        "--tb=short",
        f"--basetemp={pytest_temp}",
        f"--junitxml={pytest_xml}",
    )
    completed = runner.run(
        pytest_command,
        cwd=context.repository,
        env=test_environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        status = (
            ValidationStatus.FAIL
            if completed.returncode == 1
            else ValidationStatus.BLOCKED
        )
        outcome = "failed" if status is ValidationStatus.FAIL else "blocked"
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                status,
                f"pytest {outcome}: {detail or f'exit {completed.returncode}'}",
            ),
        )

    deselected = re.search(r"\b(\d+) deselected\b", completed.stdout)
    if deselected:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                f"complete directory check reported {deselected.group(1)} deselected tests",
            ),
        )

    if not pytest_xml.is_file():
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "pytest completed without a JUnit execution record",
            ),
        )

    test_cases = tuple(ElementTree.parse(pytest_xml).getroot().iter("testcase"))
    required_browser_cases = tuple(
        case
        for case in test_cases
        if case.attrib.get("name", "").startswith(
            ("test_real_browser_", "test_real_chrome_")
        )
    )
    continuous_resize = tuple(
        case
        for case in test_cases
        if case.attrib.get("name", "").startswith(
            "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip"
        )
    )
    if not required_browser_cases or not continuous_resize:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "required Chrome tests were not collected",
            ),
        )
    skipped_required = tuple(
        case.attrib.get("name", "<unknown>")
        for case in required_browser_cases
        if case.find("skipped") is not None
    )
    if skipped_required:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "required Chrome tests skipped: " + ", ".join(skipped_required),
            ),
        )

    return _report(
        environment_stage,
        StageResult("static", ValidationStatus.PASS, "all static checks passed"),
        StageResult(
            "architecture-explorer",
            ValidationStatus.PASS,
            (
                f"{len(test_cases)} tests executed; {len(required_browser_cases)} required "
                "Chrome tests passed; browser=chrome; actual viewports verified by suite: "
                "1440x900,1280x900,960x900 and continuous 1440->1280->960->1440"
            ),
        ),
    )


def _runtime_context(repository: Path) -> ValidationContext:
    import booktrans

    return ValidationContext(
        repository=repository,
        python_executable=Path(sys.executable),
        python_prefix=Path(sys.prefix),
        module_file=Path(booktrans.__file__),
        chrome_path=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the authoritative Architecture Explorer validation.",
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    repository = arguments.repository.resolve()
    required_files = (
        repository / "pyproject.toml",
        repository / "docs/architecture-explorer-acceptance-v1.md",
    )
    missing = [path for path in required_files if not path.is_file()]
    if missing:
        report = _blocked_report(
            "repository root is missing required files: "
            + ", ".join(str(path) for path in missing)
        )
    else:
        with tempfile.TemporaryDirectory(
            prefix="architecture-explorer-validation-"
        ) as temporary_directory:
            report = validate(
                _runtime_context(repository),
                runner=SubprocessCommandRunner(),
                scratch_root=Path(temporary_directory) / "run",
            )
    print(render_report(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
