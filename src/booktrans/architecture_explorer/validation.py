"""Authoritative Architecture Explorer validation orchestration."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Iterator, Protocol
from xml.etree import ElementTree

from .acceptance import APPROVED_PROPOSED_V3_METADATA, validate_acceptance_target
from .content_delta import read_git


_OUTPUT_INTEGRITY_FAILURE = "subprocess output integrity failure:"


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
    evidence: ValidationEvidence | None = None

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


@dataclass(frozen=True)
class SuiteStatistics:
    collected: int
    selected: int
    executed: int
    passed: int
    skipped: int
    deselected: int
    failures: int
    errors: int

    def as_dict(self) -> dict[str, int]:
        return {
            "collected": self.collected,
            "selected": self.selected,
            "executed": self.executed,
            "passed": self.passed,
            "skipped": self.skipped,
            "deselected": self.deselected,
            "failures": self.failures,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class ChromeValidationEvidence:
    product: str
    user_agent: str
    required_nodeids: tuple[str, ...]
    joint_view_nodeid: str
    joint_view_ids: tuple[str, ...]
    joint_viewport_widths: tuple[int, ...]


@dataclass(frozen=True)
class ValidationEvidence:
    complete_suite: SuiteStatistics
    required_chrome: SuiteStatistics
    chrome: ChromeValidationEvidence


@dataclass(frozen=True)
class TestDuration:
    nodeid: str
    seconds: float
    chrome: bool


@dataclass
class ValidationObservability:
    """Best-effort timing diagnostics that never affect validation status."""

    stage_seconds: dict[str, float] = field(default_factory=dict)
    test_durations: tuple[TestDuration, ...] = ()

    def record_duration(self, name: str, seconds: float) -> None:
        self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + seconds

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record_duration(name, time.perf_counter() - started)

    def capture_junit(self, path: Path) -> None:
        try:
            cases = tuple(ElementTree.parse(path).getroot().iter("testcase"))
            self.test_durations = tuple(
                TestDuration(
                    nodeid=_junit_nodeid(case),
                    seconds=float(case.attrib.get("time", "0")),
                    chrome=_is_chrome_case(case),
                )
                for case in cases
            )
        except (OSError, ValueError, TypeError, ElementTree.ParseError):
            self.test_durations = ()


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
        command_environment = dict(os.environ if env is None else env)
        command_environment["PYTHONIOENCODING"] = "utf-8"
        command_environment["PYTHONUTF8"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=command_environment,
                check=False,
                capture_output=True,
            )
        except OSError as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))
        try:
            stdout = completed.stdout.decode("utf-8")
            stderr = completed.stderr.decode("utf-8")
        except UnicodeDecodeError as error:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                f"{_OUTPUT_INTEGRITY_FAILURE} non-UTF-8 bytes: {error}",
            )
        if "\ufffd" in stdout or "\ufffd" in stderr:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                f"{_OUTPUT_INTEGRITY_FAILURE} output contained replacement character U+FFFD",
            )
        return subprocess.CompletedProcess(
            command,
            completed.returncode,
            stdout,
            stderr,
        )


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
_JOINT_VIEW_NODEID = (
    "tests/architecture_explorer/test_ticket_61_semantic_routing_view.py::"
    "test_real_chrome_preserves_every_projected_relationship_at_mobile_width"
)
_HISTORY_PREFLIGHT_NODEIDS = (
    "tests/architecture_explorer/test_acceptance_contract.py::"
    "test_repository_contract_status_matrix_is_valid",
    "tests/architecture_explorer/test_ticket_61_semantic_routing_view.py::"
    "test_ticket_61_contribution_has_the_exact_canonical_add_set",
    "tests/architecture_explorer/test_v3_ticket_06_integration.py::"
    "test_ticket_06_real_git_delta_indexes_both_v2_review_definitions",
)


def _report(
    *stages: StageResult,
    evidence: ValidationEvidence | None = None,
) -> ValidationReport:
    return ValidationReport(stages + _EXPLICIT_EXCLUSIONS, evidence=evidence)


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


def _is_chrome_case(case: ElementTree.Element) -> bool:
    return case.attrib.get("name", "").startswith(
        ("test_real_browser_", "test_real_chrome_")
    )


def render_observability(observer: ValidationObservability) -> str:
    """Render best-effort timings without assigning acceptance meaning to them."""

    lines = ["ARCHITECTURE_EXPLORER_OBSERVABILITY v1"]
    for name in (
        "environment/static",
        "collection",
        "full pytest",
        "Chrome evidence validation",
        "cleanup",
    ):
        if name in observer.stage_seconds:
            lines.append(f"timing {name}: {observer.stage_seconds[name]:.3f}s")
    durations = observer.test_durations
    if durations:
        chrome = tuple(item for item in durations if item.chrome)
        non_chrome = tuple(item for item in durations if not item.chrome)
        lines.append(
            f"test-time Chrome tests: {sum(item.seconds for item in chrome):.3f}s "
            f"across {len(chrome)}"
        )
        lines.append(
            "test-time non-Chrome tests: "
            f"{sum(item.seconds for item in non_chrome):.3f}s across {len(non_chrome)}"
        )
        lines.append(
            "test-time note: Chrome/non-Chrome is JUnit classification only; "
            "it does not prove a specific bottleneck"
        )
        lines.append("slowest tests (top 20):")
        for index, item in enumerate(
            sorted(durations, key=lambda value: value.seconds, reverse=True)[:20],
            start=1,
        ):
            lines.append(f"{index:02d}. {item.seconds:.3f}s {item.nodeid}")
    else:
        lines.append("test-time summary: unavailable (no readable JUnit record)")
    return "\n".join(lines)


def _validated_viewport_evidence(path: Path) -> tuple[str, str, str]:
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
    if any(not isinstance(graph, dict) for graph in graphs):
        raise ValueError("viewport evidence contains a non-object graph")
    graph_ids = [graph.get("graph_id") for graph in graphs]
    if len(graph_ids) != len(set(graph_ids)) or set(graph_ids) != _GRAPH_IDS:
        raise ValueError(f"viewport evidence graph set changed: {graph_ids}")

    expected_sequence = [list(viewport) for viewport in _VIEWPORT_SEQUENCE]
    for graph in graphs:
        observations = graph.get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"{graph['graph_id']} has no viewport observations")
        if any(not isinstance(observation, dict) for observation in observations):
            raise ValueError(
                f"{graph['graph_id']} contains a non-object viewport observation"
            )
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

    source_trail = evidence.get("source_trail")
    if not isinstance(source_trail, dict) or any(
        source_trail.get(field) is not True
        for field in (
            "keyboard_entry",
            "escape_focus_restored",
            "close_focus_restored",
        )
    ):
        raise ValueError("viewport evidence has no complete Source Trail keyboard result")
    source_trail_observations = source_trail.get("observations")
    if not isinstance(source_trail_observations, list):
        raise ValueError("viewport evidence has no Source Trail observations")
    if any(
        not isinstance(observation, dict)
        for observation in source_trail_observations
    ):
        raise ValueError("Source Trail evidence contains a non-object observation")
    requested = [
        observation.get("requested")
        for observation in source_trail_observations
    ]
    actual = [
        observation.get("actual")
        for observation in source_trail_observations
    ]
    if requested != expected_sequence or actual != requested:
        raise ValueError("Source Trail viewport sequence did not complete")
    if any(
        observation.get("navigation_identity") != page_identity
        or any(
            not isinstance(observation.get(field), (int, float))
            or observation[field] > 1
            for field in ("page_overflow", "dialog_overflow", "detail_overflow")
        )
        for observation in source_trail_observations
    ):
        raise ValueError("Source Trail navigation or overflow evidence is invalid")

    actual_sequence = " -> ".join(
        f"{width}x{height}" for width, height in _VIEWPORT_SEQUENCE
    )
    return (
        (
            f"browser={product}; actual viewport sequence={actual_sequence} across "
            "8 graphs and an open Source Trail"
        ),
        product,
        user_agent,
    )


def _statistics(
    cases: tuple[ElementTree.Element, ...],
    *,
    collected: int,
    deselected: int = 0,
) -> SuiteStatistics:
    skipped = sum(case.find("skipped") is not None for case in cases)
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    executed = len(cases)
    return SuiteStatistics(
        collected=collected,
        selected=collected - deselected,
        executed=executed,
        passed=executed - skipped - failures - errors,
        skipped=skipped,
        deselected=deselected,
        failures=failures,
        errors=errors,
    )


def validate_candidate_inputs(
    repository: Path,
    *,
    head_revision: str,
    module_file: Path,
) -> None:
    """Bind one candidate validation run to its complete checked-out Git state."""

    repository = repository.resolve()
    if re.fullmatch(r"[0-9a-f]{40}", head_revision) is None:
        raise ValueError("candidate Head must be a full 40-character lowercase SHA")
    repository_root = Path(
        str(read_git(repository, "rev-parse", "--show-toplevel")).strip()
    ).resolve()
    if repository_root != repository:
        raise ValueError(
            f"candidate repository root {repository_root} does not match {repository}"
        )
    object_type = str(
        read_git(repository, "cat-file", "-t", head_revision)
    ).strip()
    if object_type != "commit":
        raise ValueError("candidate Head is not a Git commit object")
    checked_out_head = str(read_git(repository, "rev-parse", "HEAD")).strip()
    if checked_out_head != head_revision:
        raise ValueError(
            f"candidate Head {head_revision} does not match checked out HEAD "
            f"{checked_out_head}"
        )
    worktree_status = str(
        read_git(
            repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    ).strip()
    if worktree_status:
        raise ValueError(
            "candidate working tree is not clean: "
            + worktree_status.replace("\n", "; ")
        )
    tracked_entries = str(read_git(repository, "ls-files", "-v", "-z")).split("\0")
    assume_unchanged = [
        entry[2:]
        for entry in tracked_entries
        if len(entry) > 2 and entry[0].islower()
    ]
    if assume_unchanged:
        raise ValueError(
            "candidate tracked files use assume-unchanged: "
            + ", ".join(assume_unchanged)
        )
    skip_worktree = [
        entry[2:]
        for entry in tracked_entries
        if len(entry) > 2 and entry[0].upper() == "S"
    ]
    if skip_worktree:
        raise ValueError(
            "candidate tracked files use skip-worktree: "
            + ", ".join(skip_worktree)
        )
    expected_module = (repository / "src/booktrans/__init__.py").resolve()
    actual_module = module_file.resolve()
    if actual_module != expected_module:
        raise ValueError(
            f"booktrans.__file__ {actual_module} does not match {expected_module}"
        )
    read_git(
        repository,
        "ls-files",
        "--error-unmatch",
        "--",
        "src/booktrans/__init__.py",
    )


def _validated_joint_view_evidence(
    path: Path,
    *,
    chrome_product: str,
    chrome_user_agent: str,
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema") != "architecture-explorer.chrome-viewport-evidence.v1":
        raise ValueError("unexpected joint View evidence schema")
    browser = evidence.get("browser")
    if not isinstance(browser, dict) or (
        browser.get("product") != chrome_product
        or browser.get("user_agent") != chrome_user_agent
    ):
        raise ValueError("joint View evidence came from a different Chrome identity")
    graphs = evidence.get("graphs")
    if not isinstance(graphs, list):
        raise ValueError("joint View evidence has no graph observations")
    if len(graphs) != 12 or any(not isinstance(graph, dict) for graph in graphs):
        raise ValueError(
            "joint View evidence must contain exactly 12 graphs and no non-object entries"
        )
    view_ids = tuple(graph.get("graph_id") for graph in graphs)
    if len(view_ids) != 12 or len(set(view_ids)) != 12:
        raise ValueError(f"joint View evidence did not contain 12 unique Views: {view_ids}")
    expected = ([480, 900], [960, 900], [1280, 900], [1440, 900])
    for graph in graphs:
        observations = graph.get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"joint View {graph['graph_id']} has no observations")
        if any(not isinstance(observation, dict) for observation in observations):
            raise ValueError(
                f"joint View {graph['graph_id']} contains a non-object observation"
            )
        requested = tuple(observation.get("requested") for observation in observations)
        actual = tuple(observation.get("actual") for observation in observations)
        if requested != expected or actual != expected:
            raise ValueError(
                f"joint View {graph['graph_id']} did not complete all viewport widths"
            )
    return view_ids, tuple(viewport[0] for viewport in expected)


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


def _write_utf8_report(
    report: ValidationReport,
    observer: ValidationObservability | None = None,
) -> None:
    """Write the final report independently of the Windows console code page."""

    rendered = render_report(report)
    if observer is not None:
        rendered += "\n" + render_observability(observer)
    payload = (rendered + "\n").encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(payload.decode("utf-8"))
        return
    buffer.write(payload)
    buffer.flush()


def _with_candidate_stage(
    report: ValidationReport,
    stage: StageResult,
) -> ValidationReport:
    explicit_names = {item.name for item in _EXPLICIT_EXCLUSIONS}
    core_stages = tuple(
        item for item in report.stages if item.name not in explicit_names
    )
    return ValidationReport(
        core_stages + (stage,) + _EXPLICIT_EXCLUSIONS,
        evidence=report.evidence,
    )


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


def _environment_failure_report(reason: str) -> ValidationReport:
    return _report(
        StageResult("environment", ValidationStatus.FAIL, reason),
        StageResult("static", ValidationStatus.NOT_RUN, "environment failed"),
        StageResult(
            "architecture-explorer",
            ValidationStatus.NOT_RUN,
            "environment failed",
        ),
    )


def _has_output_integrity_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    return _OUTPUT_INTEGRITY_FAILURE in completed.stderr


def _repository_history_preflight(
    context: ValidationContext,
    *,
    runner: CommandRunner,
    expected_head: str,
) -> StageResult | None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
        return StageResult(
            "static",
            ValidationStatus.FAIL,
            "expected PR Head must be a full 40-character lowercase SHA",
        )

    def run_git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return runner.run(("git", *arguments), cwd=context.repository)

    actual = run_git("rev-parse", "HEAD")
    if actual.returncode != 0:
        detail = actual.stderr.strip() or actual.stdout.strip()
        status = (
            ValidationStatus.BLOCKED
            if actual.returncode == 127
            else ValidationStatus.FAIL
        )
        return StageResult(
            "static",
            status,
            "repository history preflight could not read HEAD: "
            + (detail or f"exit {actual.returncode}"),
        )
    actual_head = actual.stdout.strip()
    if actual_head != expected_head:
        return StageResult(
            "static",
            ValidationStatus.FAIL,
            f"CI checkout HEAD {actual_head} does not equal expected PR Head {expected_head}",
        )

    shallow = run_git("rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0:
        detail = shallow.stderr.strip() or shallow.stdout.strip()
        status = (
            ValidationStatus.BLOCKED
            if shallow.returncode == 127
            else ValidationStatus.FAIL
        )
        return StageResult(
            "static",
            status,
            "repository history preflight could not determine shallow state: "
            + (detail or f"exit {shallow.returncode}"),
        )
    if shallow.stdout.strip() != "false":
        return StageResult(
            "static",
            ValidationStatus.FAIL,
            "CI checkout is a shallow repository; complete trusted history is required",
        )

    parent = run_git("cat-file", "-e", "HEAD^^{commit}")
    if parent.returncode != 0:
        detail = parent.stderr.strip() or parent.stdout.strip()
        status = (
            ValidationStatus.BLOCKED
            if parent.returncode == 127
            else ValidationStatus.FAIL
        )
        return StageResult(
            "static",
            status,
            "repository history preflight could not read HEAD^: "
            + (detail or f"exit {parent.returncode}"),
        )

    history_tests = runner.run(
        (
            str(context.python_executable),
            "-m",
            "pytest",
            *_HISTORY_PREFLIGHT_NODEIDS,
            "-q",
            "--tb=short",
        ),
        cwd=context.repository,
    )
    if history_tests.returncode != 0:
        detail = history_tests.stderr.strip() or history_tests.stdout.strip()
        status = (
            ValidationStatus.FAIL
            if history_tests.returncode == 1
            else ValidationStatus.BLOCKED
        )
        return StageResult(
            "static",
            status,
            "trusted Base and historical baseline preflight failed: "
            + (detail or f"exit {history_tests.returncode}"),
        )
    return None


def validate(
    context: ValidationContext,
    *,
    runner: CommandRunner,
    scratch_root: Path | None = None,
    expected_head: str | None = None,
    observer: ValidationObservability | None = None,
) -> ValidationReport:
    """Validate the environment, then run the Architecture Explorer checks."""

    observer = observer or ValidationObservability()
    environment_static_started = time.perf_counter()

    def finish_environment_static(report: ValidationReport) -> ValidationReport:
        if "environment/static" not in observer.stage_seconds:
            observer.record_duration(
                "environment/static",
                time.perf_counter() - environment_static_started,
            )
        return report

    expected_prefix = (context.repository / ".venv").resolve()
    actual_prefix = context.python_prefix.resolve()
    if actual_prefix != expected_prefix:
        reason = f"sys.prefix {actual_prefix} does not match {expected_prefix}"
        return finish_environment_static(_blocked_report(reason))

    if not context.python_executable.is_file():
        return finish_environment_static(_blocked_report(
            f"project Python is unavailable at {context.python_executable}"
        ))

    source_root = (context.repository / "src").resolve()
    module_file = context.module_file.resolve()
    if not module_file.is_relative_to(source_root):
        return finish_environment_static(_blocked_report(
            f"booktrans.__file__ {module_file} is outside current Worktree {source_root}"
        ))

    if not context.chrome_path.is_file():
        return finish_environment_static(
            _blocked_report(f"Chrome is unavailable at {context.chrome_path}")
        )

    dependency_check = runner.run(
        (str(context.python_executable), "-m", "pip", "check"),
        cwd=context.repository,
    )
    if dependency_check.returncode != 0:
        detail = dependency_check.stderr.strip() or dependency_check.stdout.strip()
        if _has_output_integrity_failure(dependency_check):
            return finish_environment_static(_environment_failure_report(
                f"pip check failed: {detail or f'exit {dependency_check.returncode}'}"
            ))
        return finish_environment_static(_blocked_report(
            f"pip check failed: {detail or f'exit {dependency_check.returncode}'}"
        ))

    environment_stage = StageResult(
        "environment",
        ValidationStatus.PASS,
        f"python={context.python_executable}; browser=chrome:{context.chrome_path}",
    )
    if expected_head is not None:
        preflight_failure = _repository_history_preflight(
            context,
            runner=runner,
            expected_head=expected_head,
        )
        if preflight_failure is not None:
            return finish_environment_static(
                _report(
                    environment_stage,
                    preflight_failure,
                    StageResult(
                        "architecture-explorer",
                        ValidationStatus.NOT_RUN,
                        "repository history preflight failed",
                    ),
                )
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
            return finish_environment_static(_report(
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
            ))

    observer.record_duration(
        "environment/static",
        time.perf_counter() - environment_static_started,
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
    joint_view_evidence = scratch_root / "joint-view-evidence.json"
    test_environment = dict(os.environ)
    test_environment["PYTHONPATH"] = str((context.repository / "src").resolve())
    test_environment["ARCHITECTURE_EXPLORER_BROWSER"] = "chrome"
    test_environment["ARCHITECTURE_EXPLORER_VIEWPORT_EVIDENCE"] = str(
        viewport_evidence
    )
    test_environment["ARCHITECTURE_EXPLORER_JOINT_VIEW_EVIDENCE"] = str(
        joint_view_evidence
    )
    collection_command = (
        str(context.python_executable),
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "tests/architecture_explorer",
    )
    with observer.measure("collection"):
        collection = runner.run(
            collection_command,
            cwd=context.repository,
            env=test_environment,
        )
    if collection.returncode != 0:
        detail = collection.stderr.strip() or collection.stdout.strip()
        status = (
            ValidationStatus.FAIL
            if _has_output_integrity_failure(collection)
            else ValidationStatus.BLOCKED
        )
        outcome = "failed" if status is ValidationStatus.FAIL else "blocked"
        return _report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                status,
                f"pytest collection {outcome}: {detail or f'exit {collection.returncode}'}",
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
    with observer.measure("full pytest"):
        completed = runner.run(
            pytest_command,
            cwd=context.repository,
            env=test_environment,
        )
    observer.capture_junit(pytest_xml)
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
        if _is_chrome_case(case)
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

    chrome_evidence_started = time.perf_counter()

    def finish_chrome_evidence(report: ValidationReport) -> ValidationReport:
        if "Chrome evidence validation" not in observer.stage_seconds:
            observer.record_duration(
                "Chrome evidence validation",
                time.perf_counter() - chrome_evidence_started,
            )
        return report

    if not viewport_evidence.is_file():
        return finish_chrome_evidence(_report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "real Chrome viewport evidence was not produced",
            ),
        ))
    try:
        viewport_summary, chrome_product, chrome_user_agent = (
            _validated_viewport_evidence(viewport_evidence)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return finish_chrome_evidence(_report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                f"invalid Chrome viewport evidence: {error}",
            ),
        ))
    if not joint_view_evidence.is_file():
        return finish_chrome_evidence(_report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.BLOCKED,
                "joint 12-View Chrome evidence was not produced",
            ),
        ))
    try:
        joint_view_ids, joint_viewport_widths = _validated_joint_view_evidence(
            joint_view_evidence,
            chrome_product=chrome_product,
            chrome_user_agent=chrome_user_agent,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return finish_chrome_evidence(_report(
            environment_stage,
            StageResult("static", ValidationStatus.PASS, "all static checks passed"),
            StageResult(
                "architecture-explorer",
                ValidationStatus.FAIL,
                f"invalid joint 12-View Chrome evidence: {error}",
            ),
        ))

    observer.record_duration(
        "Chrome evidence validation",
        time.perf_counter() - chrome_evidence_started,
    )

    complete_statistics = _statistics(
        test_cases,
        collected=len(expected_nodeids),
    )
    required_chrome_statistics = _statistics(
        required_browser_cases,
        collected=len(required_browser_cases),
    )
    validation_evidence = ValidationEvidence(
        complete_suite=complete_statistics,
        required_chrome=required_chrome_statistics,
        chrome=ChromeValidationEvidence(
            product=chrome_product,
            user_agent=chrome_user_agent,
            required_nodeids=tuple(
                sorted(_junit_nodeid(case) for case in required_browser_cases)
            ),
            joint_view_nodeid=_JOINT_VIEW_NODEID,
            joint_view_ids=joint_view_ids,
            joint_viewport_widths=joint_viewport_widths,
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
        evidence=validation_evidence,
    )


def write_candidate_acceptance_record(
    repository: Path,
    *,
    report: ValidationReport,
    head_revision: str,
    module_file: Path,
    target_contract: str,
    manifest_path: str,
    output_path: Path,
) -> str:
    """Bind one passing validation run to one candidate Git commit."""

    if report.exit_code != 0 or report.evidence is None:
        raise ValueError("candidate record requires one passing validation with evidence")
    validate_candidate_inputs(
        repository,
        head_revision=head_revision,
        module_file=module_file,
    )
    complete = report.evidence.complete_suite
    chrome = report.evidence.required_chrome
    for label, statistics in (("complete suite", complete), ("required Chrome", chrome)):
        if statistics.selected != statistics.collected - statistics.deselected:
            raise ValueError(f"{label} selected statistics are inconsistent")
        if statistics.executed != (
            statistics.passed
            + statistics.skipped
            + statistics.failures
            + statistics.errors
        ):
            raise ValueError(f"{label} execution statistics are inconsistent")
    if complete.selected != complete.executed:
        raise ValueError("complete suite statistics contain unexecuted selected tests")
    if (
        chrome.collected != chrome.selected
        or chrome.selected != chrome.executed
        or chrome.executed != chrome.passed
        or chrome.skipped
        or chrome.deselected
        or chrome.failures
        or chrome.errors
    ):
        raise ValueError("required Chrome statistics are not an all-passing execution")
    if len(report.evidence.chrome.required_nodeids) != chrome.collected:
        raise ValueError("required Chrome nodeids do not match the recorded statistics")
    repository = repository.resolve()
    if _JOINT_VIEW_NODEID not in report.evidence.chrome.required_nodeids:
        raise ValueError("passing validation did not execute the joint 12-View Chrome matrix")

    acceptance = validate_acceptance_target(
        repository,
        head_revision=head_revision,
        target_contract=target_contract,
        manifest_path=manifest_path,
    )
    manifest = acceptance.manifest
    if manifest.head_revision != head_revision:
        raise ValueError("Manifest validation returned a different candidate Head")
    spec_issue = int(
        APPROVED_PROPOSED_V3_METADATA["authority_issue"].rsplit("/", 1)[-1]
    )
    not_run_stages = tuple(
        stage for stage in report.stages if stage.status is ValidationStatus.NOT_RUN
    )
    record = {
        "schema": "architecture-explorer.candidate-acceptance.v1",
        "status": "CANDIDATE_ACCEPTANCE_PASS",
        "head_revision": head_revision,
        "head_tree": str(
            read_git(repository, "rev-parse", f"{head_revision}^{{tree}}")
        ).strip(),
        "base_revision": manifest.base_revision,
        "spec": {
            "issue": spec_issue,
            "approved_sha256": APPROVED_PROPOSED_V3_METADATA[
                "approved_spec_sha256"
            ],
        },
        "target_contract": target_contract,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest.manifest_sha256,
        "base_content_sha256": manifest.base_content_sha256,
        "head_content_sha256": manifest.head_content_sha256,
        "delta_change_count": len(manifest.changes),
        "object_counts": manifest.object_counts,
        "complete_suite": report.evidence.complete_suite.as_dict(),
        "required_chrome": {
            "statistics": report.evidence.required_chrome.as_dict(),
            "browser": {
                "product": report.evidence.chrome.product,
                "user_agent": report.evidence.chrome.user_agent,
            },
            "test_nodeids": list(report.evidence.chrome.required_nodeids),
            "joint_view_validation": {
                "test_nodeid": report.evidence.chrome.joint_view_nodeid,
                "views": len(report.evidence.chrome.joint_view_ids),
                "view_ids": list(report.evidence.chrome.joint_view_ids),
                "viewport_widths": list(
                    report.evidence.chrome.joint_viewport_widths
                ),
            },
        },
        "not_run": [stage.name for stage in not_run_stages],
        "not_run_details": {
            stage.name: stage.detail for stage in not_run_stages
        },
        "validation_report": render_report(report),
    }
    payload = (
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    output_path = output_path.resolve()
    output_directory = output_path.parent
    digest_name = output_path.name + ".sha256"
    if output_directory.exists():
        raise FileExistsError("candidate acceptance record already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.staging-",
            dir=output_directory.parent,
        )
    )
    with (staging_directory / output_path.name).open("xb") as record_file:
        record_file.write(payload)
    with (staging_directory / digest_name).open(
        "x", encoding="ascii", newline="\n"
    ) as digest_file:
        digest_file.write(f"{digest}  {output_path.name}\n")
    staging_directory.replace(output_directory)
    return digest


def _runtime_context(repository: Path) -> ValidationContext:
    import booktrans

    return ValidationContext(
        repository=repository,
        python_executable=Path(sys.executable),
        python_prefix=Path(sys.prefix),
        module_file=Path(booktrans.__file__),
        chrome_path=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )


def _cleanup_temporary_directory(
    temporary_directory: tempfile.TemporaryDirectory,
) -> None:
    attempts = 5
    for attempt in range(attempts):
        try:
            temporary_directory.cleanup()
            return
        except OSError as error:
            directory_not_empty = (
                os.name == "nt" and getattr(error, "winerror", None) == 145
            )
            if not directory_not_empty or attempt == attempts - 1:
                raise
            time.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the authoritative Architecture Explorer validation.",
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-head")
    parser.add_argument("--target-contract")
    parser.add_argument("--manifest-path")
    parser.add_argument("--candidate-record", type=Path)
    arguments = parser.parse_args(argv)
    repository = arguments.repository.resolve()
    observer = ValidationObservability()
    candidate_values = (
        arguments.candidate_head,
        arguments.target_contract,
        arguments.manifest_path,
        arguments.candidate_record,
    )
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
        runtime_context = _runtime_context(repository)
        try:
            if all(value is not None for value in candidate_values):
                validate_candidate_inputs(
                    repository,
                    head_revision=arguments.candidate_head,
                    module_file=runtime_context.module_file,
                )
        except (OSError, ValueError) as error:
            report = _environment_failure_report(
                f"candidate validation inputs rejected: {error}"
            )
        else:
            temporary_directory = tempfile.TemporaryDirectory(prefix="ae-v-")
            try:
                report = validate(
                    runtime_context,
                    runner=SubprocessCommandRunner(),
                    scratch_root=Path(temporary_directory.name) / "run",
                    expected_head=os.environ.get(
                        "ARCHITECTURE_EXPLORER_EXPECTED_HEAD"
                    ),
                    observer=observer,
                )
            finally:
                with observer.measure("cleanup"):
                    _cleanup_temporary_directory(temporary_directory)
    if any(value is not None for value in candidate_values):
        if not all(value is not None for value in candidate_values):
            report = _with_candidate_stage(
                report,
                StageResult(
                    "candidate-acceptance",
                    ValidationStatus.BLOCKED,
                    "candidate Head, target, Manifest, and record path are all required",
                ),
            )
        elif report.exit_code == 0:
            try:
                validate_candidate_inputs(
                    repository,
                    head_revision=arguments.candidate_head,
                    module_file=runtime_context.module_file,
                )
                passing_report = _with_candidate_stage(
                    report,
                    StageResult(
                        "candidate-acceptance",
                        ValidationStatus.PASS,
                        f"bound to candidate Head {arguments.candidate_head}",
                    ),
                )
                digest = write_candidate_acceptance_record(
                    repository,
                    report=passing_report,
                    head_revision=arguments.candidate_head,
                    module_file=runtime_context.module_file,
                    target_contract=arguments.target_contract,
                    manifest_path=arguments.manifest_path,
                    output_path=arguments.candidate_record,
                )
                report = _with_candidate_stage(
                    report,
                    StageResult(
                        "candidate-acceptance",
                        ValidationStatus.PASS,
                        (
                            f"record={arguments.candidate_record.resolve()}; "
                            f"sha256={digest}"
                        ),
                    ),
                )
            except (OSError, ValueError) as error:
                report = _with_candidate_stage(
                    report,
                    StageResult(
                        "candidate-acceptance",
                        ValidationStatus.FAIL,
                        str(error),
                    ),
                )
        else:
            report = _with_candidate_stage(
                report,
                StageResult(
                    "candidate-acceptance",
                    ValidationStatus.NOT_RUN,
                    "complete validation did not pass",
                ),
            )
    _write_utf8_report(report, observer)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
