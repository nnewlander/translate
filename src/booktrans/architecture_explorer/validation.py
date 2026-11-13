"""Authoritative Architecture Explorer validation orchestration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import json
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

_CRITICAL_NODEIDS = (
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_opens_candidate_evidence_from_every_node_context_menu",
    "tests/architecture_explorer/test_acceptance_contract.py::"
    "test_contract_trust_semantics_match_the_published_explorer",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_degraded_relationship_never_receives_reviewed_graph_treatment",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[1440-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[1280-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_browser_renders_production_candidates_in_the_shared_graph[960-900]",
    "tests/architecture_explorer/test_t7_top_level_graph.py::"
    "test_real_chrome_reconnects_edges_during_same_page_resize_round_trip",
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
_VIEWPORT_SEQUENCE = ((1440, 900), (1280, 900), (960, 900), (1440, 900))
_GRAPH_IDS = {
    "top-level",
    "document-understanding",
    "scope-and-request",
    "production-control",
    "translation-execution",
    "quality-and-repair",
    "production-evidence",
    "candidate-and-release",
}


def _report(*stages: StageResult) -> ValidationReport:
    return ValidationReport(stages + _EXPLICIT_EXCLUSIONS)


def _collected_nodeids(output: str) -> frozenset[str]:
    nodeids = set()
    for line in output.splitlines():
        value = line.strip()
        if not value.startswith(("tests/", "tests\\")) or "::" not in value:
            continue
        path, separator, test_id = value.partition("::")
        nodeids.add(path.replace("\\", "/") + separator + test_id)
    return frozenset(nodeids)


def _junit_nodeid(case: ElementTree.Element) -> str:
    class_name = case.attrib.get("classname", "")
    return class_name.replace(".", "/") + ".py::" + case.attrib.get("name", "")


def _validated_viewport_evidence(path: Path) -> str:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema") != "architecture-explorer.chrome-viewport-evidence.v1":
        raise ValueError("unexpected viewport evidence schema")
    if evidence.get("requested_browser") != "chrome":
        raise ValueError("viewport evidence did not request Chrome")

    browser = evidence.get("browser")
    if not isinstance(browser, dict):
        raise ValueError("viewport evidence has no browser identity")
    product = browser.get("product")
    user_agent = browser.get("user_agent")
    if not isinstance(product, str) or "Chrome/" not in product:
        raise ValueError(f"viewport evidence browser product is not Chrome: {product!r}")
    if not isinstance(user_agent, str) or "Chrome/" not in user_agent:
        raise ValueError("viewport evidence has no Chrome user agent")

    page_identity = evidence.get("page_identity")
    if not isinstance(page_identity, dict):
        raise ValueError("viewport evidence has no page identity")
    if page_identity.get("navigation_count") != 1:
        raise ValueError("viewport evidence did not preserve one page navigation")
    if not isinstance(page_identity.get("time_origin"), (int, float)):
        raise ValueError("viewport evidence has no time origin")

    graphs = evidence.get("graphs")
    if not isinstance(graphs, list):
        raise ValueError("viewport evidence has no graph observations")
    graph_ids = [graph.get("graph_id") for graph in graphs if isinstance(graph, dict)]
    if len(graph_ids) != len(set(graph_ids)) or set(graph_ids) != _GRAPH_IDS:
        raise ValueError(f"viewport evidence graph set changed: {graph_ids}")

    expected_sequence = [list(viewport) for viewport in _VIEWPORT_SEQUENCE]
    for graph in graphs:
        observations = graph.get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"{graph['graph_id']} has no viewport observations")
        requested = [observation.get("requested") for observation in observations]
        actual = [observation.get("actual") for observation in observations]
        if requested != expected_sequence:
            raise ValueError(
                f"{graph['graph_id']} requested viewport sequence changed: {requested}"
            )
        if actual != requested:
            raise ValueError(
                f"{graph['graph_id']} actual viewport sequence {actual} "
                f"did not match requested {requested}"
            )
        if any(
            observation.get("navigation_identity") != page_identity
            for observation in observations
        ):
            raise ValueError(
                f"{graph['graph_id']} navigation identity changed during resize"
            )

    actual_sequence = " -> ".join(
        f"{width}x{height}" for width, height in _VIEWPORT_SEQUENCE
    )
    return f"browser={product}; actual viewport sequence={actual_sequence} across 8 graphs"


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
    viewport_evidence = scratch_root / "chrome-viewport-evidence.json"
    test_environment = dict(os.environ)
    test_environment["PYTHONPATH"] = str((context.repository / "src").resolve())
    test_environment["ARCHITECTURE_EXPLORER_BROWSER"] = "chrome"
    test_environment["ARCHITECTURE_EXPLORER_VIEWPORT_EVIDENCE"] = str(
        viewport_evidence
    )
    collection_command = (
        str(context.python_executable),
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "tests/architecture_explorer",
    )
    collection = runner.run(
        collection_command,
        cwd=context.repository,
        env=test_environment,
    )
    if collection.returncode != 0:
        detail = collection.stderr.strip() or collection.stdout.strip()
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                f"pytest collection blocked: {detail or f'exit {collection.returncode}'}",
            ),
        )
    expected_nodeids = _collected_nodeids(collection.stdout)
    if not expected_nodeids:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "pytest collection returned no Architecture Explorer nodeids",
            ),
        )
    missing_critical_collection = tuple(
        nodeid for nodeid in _CRITICAL_NODEIDS if nodeid not in expected_nodeids
    )
    if missing_critical_collection:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                "missing critical test scenarios from collection: "
                + ", ".join(missing_critical_collection),
            ),
        )
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
    skipped_critical = tuple(
        case.attrib.get("name", "<unknown>")
        for case in test_cases
        if _junit_nodeid(case) in _CRITICAL_NODEIDS
        and case.find("skipped") is not None
    )
    if skipped_critical:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "required Chrome tests skipped: " + ", ".join(skipped_critical),
            ),
        )
    executed_nodeids = frozenset(_junit_nodeid(case) for case in test_cases)
    missing_executions = tuple(sorted(expected_nodeids - executed_nodeids))
    unexpected_executions = tuple(sorted(executed_nodeids - expected_nodeids))
    if missing_executions or unexpected_executions:
        details = []
        if missing_executions:
            details.append("not executed: " + ", ".join(missing_executions))
        if unexpected_executions:
            details.append("not collected: " + ", ".join(unexpected_executions))
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                "; ".join(details),
            ),
        )
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

    if not viewport_evidence.is_file():
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "real Chrome viewport evidence was not produced",
            ),
        )
    try:
        viewport_summary = _validated_viewport_evidence(viewport_evidence)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                f"invalid Chrome viewport evidence: {error}",
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
                f"Chrome tests passed; requested browser=chrome; {viewport_summary}"
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
