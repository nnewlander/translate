import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from booktrans.config import ProjectPaths, discover_project_root
from booktrans.domain.ids import new_run_id
from booktrans.domain.ids import new_calibration_run_id, new_extract_run_id, new_format_audit_run_id, new_format_fragment_audit_run_id, new_identity_run_id, new_synthetic_run_id, new_structural_tagged_synthetic_run_id, new_tagged_synthetic_run_id
from booktrans.domain.ir import CoverageLedger
from booktrans.domain.models import Diagnostic, InspectionReport, SourceSnapshot
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.identity_roundtrip import IdentityRoundTrip
from booktrans.harness.synthetic_roundtrip import SyntheticRoundTrip
from booktrans.harness.tagged_roundtrip import TaggedSyntheticRoundTrip
from booktrans.harness.calibration import CalibrationImporter, load_requests, load_responses
from booktrans.ooxml.calibration import select_calibration_requests, visual_review_manifest
from booktrans.reports.translation_policy import CALIBRATION_TRANSLATION_POLICY
from booktrans.ooxml.format_unit_audit import FormatTranslationUnitAudit
from booktrans.ooxml.format_fragment_decision_audit import FormatFragmentDecisionAudit
from booktrans.harness.source_integrity import SourceMutatedError, assert_unchanged, fingerprint
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.inspector import OOXMLInspector
from booktrans.ooxml.package import PackageValidationError
from booktrans.ooxml.patcher import PatchWriteError
from booktrans.ooxml.tagged_projection import TaggedProjectionError
from booktrans.ooxml.translation_view import TaggedCapabilityAudit, TranslationViewBuilder
from booktrans.storage.database import connect, initialize
from booktrans.storage.repositories import ExtractionRepository, InspectionRepository

app = typer.Typer(help="DOCX/OOXML inspection, M2-M5.2e translation contracts, and structural validation.", no_args_is_help=True)
console = Console()


@app.callback()
def root_command() -> None:
    """booktrans command group."""


def _render_report(report: InspectionReport, json_path: Path) -> None:
    table = Table(title="booktrans OOXML Inspection", show_header=True)
    table.add_column("Area", style="cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for area, model in (("Package", report.package), ("Main content", report.content), ("Other stories", report.other_stories), ("Special", report.special_constructs)):
        for key, value in model.model_dump().items():
            if isinstance(value, (dict, list)):
                continue
            table.add_row(area, key, str(value))
    console.print(table)
    console.print(f"[green]Source SHA-256 unchanged:[/green] {report.source.sha256}")
    console.print(f"[green]JSON report:[/green] {json_path}")
    for diagnostic in report.warnings:
        console.print(f"[yellow]{diagnostic.code}[/yellow] {diagnostic.message}" + (f" ({diagnostic.part})" if diagnostic.part else ""))


def _render_coverage(ledger: CoverageLedger, run_dir: Path, unit_count: int) -> None:
    table = Table(title="booktrans M2 Coverage Ledger", show_header=True)
    table.add_column("Story/group", style="cyan")
    table.add_column("Units", justify="right")
    table.add_column("Visible chars", justify="right")
    table.add_column("Translatable", justify="right")
    table.add_column("Protected", justify="right")
    table.add_column("Excluded", justify="right")
    table.add_column("Unclassified", justify="right")
    table.add_column("Unknown", justify="right")
    for group in ledger.groups:
        table.add_row(group.name, str(group.units), str(group.visible_characters), str(group.translatable_characters), str(group.protected_characters), str(group.excluded_characters), str(group.unclassified_visible_text), str(group.unknown_potential_visible_content))
    console.print(table)
    console.print(f"Units/blocks: {unit_count}; visible characters: {ledger.visible_characters}")
    console.print(f"Translatable blocks/chars: {ledger.translatable_blocks}/{ledger.translatable_characters}; protected: {ledger.protected_blocks}/{ledger.protected_characters}; excluded: {ledger.excluded_blocks}/{ledger.excluded_characters}")
    console.print(f"Unclassified visible text: {ledger.unclassified_visible_text}; unknown potential visible content: {ledger.unknown_potential_visible_content}")
    status = "YES" if ledger.coverage_complete else "NO"
    color = "green" if ledger.coverage_complete else "red"
    console.print(f"[{color}]Coverage complete: {status}[/{color}]")
    console.print(f"[bold {color}]{'COMPLETE' if ledger.coverage_complete else 'INCOMPLETE'}[/bold {color}]")
    console.print(f"[green]Source SHA-256 unchanged:[/green] {ledger.document_sha256}")
    console.print(f"[green]Run artifacts:[/green] {run_dir}")
    for blocker in ledger.blockers:
        console.print(f"[bold red]{blocker.code}[/bold red] {blocker.message}")


def _render_roundtrip(result, run_dir: Path) -> None:
    patch = result.patch_report
    report = result.roundtrip_report
    table = Table(title="booktrans M3 Identity Round-trip", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for metric, value in (
        ("Planned blocks", patch.planned_blocks), ("Planned spans", patch.planned_spans),
        ("Planned text nodes", patch.planned_text_nodes), ("Locators resolved", patch.locators_resolved),
        ("Writes attempted", patch.writes_attempted), ("Writes verified", patch.writes_verified),
        ("Resolution failures", len(patch.resolution_failures)), ("Touched OOXML parts", len(patch.touched_ooxml_parts)),
        ("Unchanged payload entries", report.preservation.untouched_entries_checked),
        ("Media hash checks", report.preservation.media_entries_checked),
        ("Relationship checks", report.preservation.relationship_entries_checked),
        ("Logical mismatches", report.logical_mismatch_count),
    ):
        table.add_row(metric, str(value))
    console.print(table)
    console.print(f"Source SHA-256: {report.source_sha256}")
    console.print(f"Candidate SHA-256: {report.candidate_sha256}")
    console.print(f"Candidate: {result.candidate_path}")
    status = "PASS" if report.passed else "INCOMPLETE/FAILED"
    color = "green" if report.passed else "red"
    console.print(f"[bold {color}]M3 IDENTITY ROUND-TRIP {status}[/bold {color}]")
    console.print(f"[green]Run artifacts:[/green] {run_dir}")
    for blocker in report.blockers:
        console.print(f"[bold red]{blocker.code}[/bold red] {blocker.message}" + (f" ({blocker.part})" if blocker.part else ""))


def _render_synthetic(result, run_dir: Path) -> None:
    capability = result.capability
    qa_pass = sum(item.passed for item in result.qa_results)
    table = Table(title="booktrans M4 Synthetic Non-Identity", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for metric, value in (
        ("Translatable blocks", capability.total_translatable_blocks),
        ("SAFE_SIMPLE", capability.safe_simple_blocks),
        ("COMPLEX_UNSUPPORTED", capability.complex_unsupported_blocks),
        ("SAFE_SIMPLE %", f"{capability.safe_simple_percentage:.2f}"),
        ("Synthetic candidates", len(result.candidates)),
        ("QA pass / total", f"{qa_pass}/{len(result.qa_results)}"),
        ("Patched blocks", result.patch_report.planned_blocks),
        ("Writes verified", result.patch_report.writes_verified),
        ("Re-extraction mismatches", result.validation_report.expected_block_mismatch_count),
    ):
        table.add_row(metric, str(value))
    console.print(table)
    if capability.reason_counts:
        console.print("[yellow]Complexity reasons:[/yellow] " + ", ".join(f"{code}={count}" for code, count in capability.reason_counts.items()))
    report = result.validation_report
    color = "green" if report.passed else "red"
    console.print(f"Source SHA-256: {report.source_sha256}")
    console.print(f"Candidate SHA-256: {report.candidate_sha256}")
    console.print(f"Candidate: {result.candidate_path}")
    console.print(f"[bold {color}]M4 SYNTHETIC ROUND-TRIP {'PASS' if report.passed else 'INCOMPLETE/FAILED'}[/bold {color}]")
    console.print(f"[green]Run artifacts:[/green] {run_dir}")
    for blocker in report.blockers:
        console.print(f"[bold red]{blocker.code}[/bold red] {blocker.message}" + (f" ({blocker.part})" if blocker.part else ""))


def _render_tagged_synthetic(result, run_dir: Path, *, stage: str = "M4.8") -> None:
    capability = result.capability
    qa_failures = sum(not item.passed for item in result.qa_results)
    anchor_counts: dict[str, int] = {}
    fragment_hints = 0
    for view in result.views:
        fragment_hints += int(view.continuation_hint)
        for anchor in view.anchors:
            for kind in anchor.kinds:
                anchor_counts[kind] = anchor_counts.get(kind, 0) + 1
    structural = result.structural_capability
    table = Table(title=f"booktrans {stage} Structural Tagged Synthetic Projection", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for metric, value in (
        ("Translatable blocks", capability.total_translatable_blocks),
        ("TAGGED_SUPPORTED", capability.tagged_supported_blocks),
        ("TAGGED_UNSUPPORTED", capability.tagged_unsupported_blocks),
        ("TAGGED_SUPPORTED %", f"{capability.tagged_supported_percentage:.2f}"),
        ("Previous TAGGED_SUPPORTED", structural.previous_tagged_supported_blocks),
        ("Newly supported", capability.tagged_supported_blocks - structural.previous_tagged_supported_blocks),
        ("Fragment-hint blocks", fragment_hints),
        ("Synthetic candidates", len(result.candidates)),
        ("Patched blocks", result.patch_report.planned_blocks),
        ("Writes verified", result.patch_report.writes_verified),
        ("Anchor QA failures", qa_failures),
        ("Structural anchors", result.validation_report.structural_anchor_count),
        ("Structural anchor QA failures", result.validation_report.structural_anchor_qa_failure_count),
        ("Unresolved locators", len(result.patch_report.resolution_failures)),
        ("Re-extraction mismatches", result.validation_report.expected_block_mismatch_count),
    ):
        table.add_row(metric, str(value))
    console.print(table)
    if anchor_counts:
        console.print("[yellow]Semantic anchors:[/yellow] " + ", ".join(f"{kind}={count}" for kind, count in sorted(anchor_counts.items())))
    if capability.reason_counts:
        console.print("[yellow]Unsupported reasons:[/yellow] " + ", ".join(f"{code}={count}" for code, count in capability.reason_counts.items()))
    report = result.validation_report
    color = "green" if report.passed else "red"
    console.print(f"Source SHA-256: {report.source_sha256}")
    console.print(f"Candidate SHA-256: {report.candidate_sha256}")
    console.print(f"Candidate: {result.candidate_path}")
    console.print(f"[bold {color}]{stage} STRUCTURAL TAGGED SYNTHETIC ROUND-TRIP {'PASS' if report.passed else 'INCOMPLETE/FAILED'}[/bold {color}]")
    console.print(f"[green]Run artifacts:[/green] {run_dir}")
    for blocker in report.blockers:
        console.print(f"[bold red]{blocker.code}[/bold red] {blocker.message}" + (f" ({blocker.part})" if blocker.part else ""))


def _render_format_audit(report, run_dir: Path) -> None:
    table = Table(title="booktrans M4.5 Format & Translation-Unit Audit", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for metric, value in (
        ("Total translatable blocks", report.total_translatable_blocks),
        ("Raw SAFE_SIMPLE", report.raw_safe_simple_blocks),
        ("Raw MULTI_FORMAT_CONTEXT", report.raw_multi_format_context_blocks),
        ("Collapsible format noise", report.normalization_collapsible_blocks),
        ("True/non-collapsible multi-context", report.true_multi_format_blocks),
        ("Normalized-safe estimate", f"{report.normalized_safe_blocks} ({report.normalized_safe_percentage:.2f}%)"),
        ("Suspicious prose fragments", report.unit_audit.suspicious_fragment_candidates),
    ):
        table.add_row(metric, str(value))
    console.print(table)
    if report.true_format_difference_categories:
        console.print("[yellow]Top non-collapsible differences:[/yellow] " + ", ".join(f"{code}={count}" for code, count in report.true_format_difference_categories.items()))
    console.print(f"[green]Source SHA-256 unchanged:[/green] {report.source_sha256}")
    console.print(f"[green]Audit artifact:[/green] {run_dir / 'format_unit_audit.json'}")


def _render_format_fragment_decision(report, run_dir: Path) -> None:
    table = Table(title="booktrans M4.6 Format Semantics & Fragment Root-Cause", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for metric, value in (
        ("OTHER_DIRECT_FORMAT blocks", report.other_direct_format_blocks),
        ("Distinct causal properties", len(report.direct_property_evidence)),
        ("1 logical region", report.logical_region_distribution.get("1", 0)),
        ("2 logical regions", report.logical_region_distribution.get("2", 0)),
        ("3 logical regions", report.logical_region_distribution.get("3", 0)),
        ("4+ logical regions", report.logical_region_distribution.get("4-5", 0) + report.logical_region_distribution.get("6+", 0)),
        ("Likely prose fragments", report.likely_true_prose_fragment.count),
        ("Likely standalone", report.likely_legitimate_standalone.count),
        ("Uncertain fragments", report.uncertain.count),
    ):
        table.add_row(metric, str(value))
    console.print(table)
    if report.direct_property_evidence:
        top = report.direct_property_evidence[:5]
        console.print("[yellow]Top direct properties:[/yellow] " + ", ".join(f"{item.property_name}={item.block_count}" for item in top))
    if report.true_fragment_root_causes:
        console.print("[yellow]True-fragment root causes:[/yellow] " + ", ".join(f"{name}={count}" for name, count in report.true_fragment_root_causes.items()))
    console.print(f"[green]Source SHA-256 unchanged:[/green] {report.source_sha256}")
    console.print(f"[green]Decision audit artifact:[/green] {run_dir / 'format_fragment_decision_audit.json'}")


@app.command()
def inspect(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="DOCX input. It is opened read-only and never overwritten."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ and data/."),
) -> None:
    """Inspect a DOCX package without modifying the input."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = (project_root.resolve() if project_root else discover_project_root())
    paths = ProjectPaths(root)
    before = fingerprint(source_path)
    inspector = OOXMLInspector()
    try:
        package_stats, content, stories, special, warnings, part_sizes = inspector.inspect(source_path)
    except PackageValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        try:
            after = fingerprint(source_path)
        except OSError as exc:
            console.print(
                "[bold red]BLOCKER SOURCE_MUTATED:[/bold red] "
                f"Source became unavailable during inspect: path={source_path}; detail={exc}"
            )
            raise typer.Exit(code=3) from exc
        try:
            assert_unchanged(before, after, source_path)
        except SourceMutatedError as exc:
            console.print(f"[bold red]BLOCKER {exc.code}:[/bold red] {exc}")
            raise typer.Exit(code=3) from exc

    run_id = new_run_id(source_path)
    report = InspectionReport(
        run_id=run_id,
        inspected_at_utc=datetime.now(UTC),
        source=SourceSnapshot(
            path=str(source_path),
            sha256=before.sha256,
            size_bytes=before.size_bytes,
            mtime_utc=before.mtime_utc,
            source_mutated=False,
        ),
        package=package_stats,
        content=content,
        other_stories=stories,
        special_constructs=special,
        warnings=warnings,
        blockers=[],
    )
    run_dir = paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    json_path = run_dir / "inspect.json"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    initialize(paths.database_path)
    with connect(paths.database_path) as connection:
        InspectionRepository(connection).record_inspection(report, json_path, part_sizes)
    _render_report(report, json_path)


@app.command()
def extract(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="DOCX input. It is opened read-only and never overwritten."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ and data/."),
) -> None:
    """Build M2 Document IR and Coverage Ledger without translating or writing DOCX."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    paths = ProjectPaths(root)
    before = fingerprint(source_path)
    source = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
    try:
        document, uncertainty, _ = DocumentExtractor().extract(source_path, source)
    except (PackageValidationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        try:
            after = fingerprint(source_path)
        except OSError as exc:
            console.print(f"[bold red]BLOCKER SOURCE_MUTATED:[/bold red] Source became unavailable during extract: path={source_path}; detail={exc}")
            raise typer.Exit(code=3) from exc
        try:
            assert_unchanged(before, after, source_path)
        except SourceMutatedError as exc:
            console.print(f"[bold red]BLOCKER {exc.code}:[/bold red] {exc}")
            raise typer.Exit(code=3) from exc
    ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
    run_id = new_extract_run_id(source_path)
    run_dir = paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    document_path = run_dir / "document.json"
    blocks_path = run_dir / "blocks.jsonl"
    coverage_path = run_dir / "coverage.json"
    report_path = run_dir / "extract_report.json"
    document_summary = {
        "schema_version": document.schema_version,
        "document_sha256": document.document_sha256,
        "source": document.source.model_dump(mode="json"),
        "extracted_at_utc": document.extracted_at_utc.isoformat(),
        "stories": [
            {"story": story.story, "part_name": story.part_name, "block_count": len(story.blocks)}
            for story in document.stories
        ],
        "diagnostics": [diagnostic.model_dump() for diagnostic in document.diagnostics],
    }
    document_path.write_text(json.dumps(document_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with blocks_path.open("w", encoding="utf-8", newline="\n") as stream:
        for story in document.stories:
            for block in story.blocks:
                stream.write(block.model_dump_json() + "\n")
    coverage_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    extract_report = {
        "run_id": run_id,
        "source": source.model_dump(mode="json"),
        "units": len(document.blocks),
        "spans": sum(len(block.spans) for block in document.blocks),
        "coverage": ledger.model_dump(mode="json"),
    }
    report_path.write_text(json.dumps(extract_report, ensure_ascii=False, indent=2), encoding="utf-8")
    initialize(paths.database_path)
    with connect(paths.database_path) as connection:
        ExtractionRepository(connection).record_extraction(run_id, document, ledger, document_path, coverage_path)
    _render_coverage(ledger, run_dir, len(document.blocks))
    if not ledger.coverage_complete:
        raise typer.Exit(code=4)


@app.command("identity")
def identity_roundtrip(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. A candidate is written under runs/ only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Execute M3's real identity text-node write path and validate the candidate."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_identity_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = run_dir / "candidate" / "identity.docx"
    try:
        result = IdentityRoundTrip().run(source_path, candidate_path)
    except (PackageValidationError, PatchWriteError, ValueError) as exc:
        console.print(f"[bold red]M3_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "patch_report.json").write_text(result.patch_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "roundtrip_report.json").write_text(result.roundtrip_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "source_coverage.json").write_text(result.source_coverage.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "candidate_coverage.json").write_text(result.candidate_coverage.model_dump_json(indent=2), encoding="utf-8")
    _render_roundtrip(result, run_dir)
    if not result.roundtrip_report.passed:
        raise typer.Exit(code=4)


@app.command("synthetic")
def synthetic_roundtrip(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. Synthetic candidate is written below runs/ only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Run M4 capability audit, deterministic synthetic QA, and safe non-identity writes."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_synthetic_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = SyntheticRoundTrip().run(source_path, run_dir / "candidate" / "synthetic.docx")
    except (PackageValidationError, PatchWriteError, ValueError) as exc:
        console.print(f"[bold red]M4_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "capability_report.json").write_text(result.capability.model_dump_json(indent=2), encoding="utf-8")
    with (run_dir / "synthetic_candidates.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for candidate in result.candidates:
            stream.write(candidate.model_dump_json() + "\n")
    (run_dir / "qa_report.json").write_text(json.dumps([item.model_dump() for item in result.qa_results], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "synthetic_patch_report.json").write_text(result.patch_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "synthetic_roundtrip_report.json").write_text(result.validation_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "source_coverage.json").write_text(result.source_coverage.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "candidate_coverage.json").write_text(result.candidate_coverage.model_dump_json(indent=2), encoding="utf-8")
    _render_synthetic(result, run_dir)
    if not result.validation_report.passed:
        raise typer.Exit(code=4)


@app.command("tagged-synthetic")
def tagged_synthetic_roundtrip(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. Tagged synthetic candidate is written below runs/ only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Compatibility command for tagged synthetic projection; no LLM or real translation is used."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_tagged_synthetic_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = TaggedSyntheticRoundTrip().run(source_path, run_dir / "candidate" / "tagged-synthetic.docx")
    except (PackageValidationError, TaggedProjectionError, ValueError) as exc:
        console.print(f"[bold red]M4.7_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "tagged_capability_report.json").write_text(result.capability.model_dump_json(indent=2), encoding="utf-8")
    with (run_dir / "translation_views.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for view in result.views:
            stream.write(view.model_dump_json() + "\n")
    with (run_dir / "tagged_synthetic_candidates.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for candidate in result.candidates:
            stream.write(candidate.model_dump_json() + "\n")
    (run_dir / "anchor_qa_report.json").write_text(json.dumps([item.model_dump() for item in result.qa_results], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "tagged_patch_report.json").write_text(result.patch_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "tagged_roundtrip_report.json").write_text(result.validation_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "source_coverage.json").write_text(result.source_coverage.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "candidate_coverage.json").write_text(result.candidate_coverage.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "structural_capability_report.json").write_text(result.structural_capability.model_dump_json(indent=2), encoding="utf-8")
    _render_tagged_synthetic(result, run_dir)
    if not result.validation_report.passed:
        raise typer.Exit(code=4)


@app.command("structural-tagged-synthetic")
def structural_tagged_synthetic_roundtrip(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. Structural tagged candidate is written below runs/ only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Run M4.8 text-island projection and CJK micro-layout policy checks."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_structural_tagged_synthetic_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = TaggedSyntheticRoundTrip().run(source_path, run_dir / "candidate" / "structural-tagged-synthetic.docx")
    except (PackageValidationError, TaggedProjectionError, ValueError) as exc:
        console.print(f"[bold red]M4.8_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "tagged_capability_report.json").write_text(result.capability.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "structural_capability_report.json").write_text(result.structural_capability.model_dump_json(indent=2), encoding="utf-8")
    with (run_dir / "translation_views.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for view in result.views:
            stream.write(view.model_dump_json() + "\n")
    with (run_dir / "tagged_synthetic_candidates.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for candidate in result.candidates:
            stream.write(candidate.model_dump_json() + "\n")
    (run_dir / "anchor_qa_report.json").write_text(json.dumps([item.model_dump() for item in result.qa_results], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "tagged_patch_report.json").write_text(result.patch_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "tagged_roundtrip_report.json").write_text(result.validation_report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "source_coverage.json").write_text(result.source_coverage.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "candidate_coverage.json").write_text(result.candidate_coverage.model_dump_json(indent=2), encoding="utf-8")
    _render_tagged_synthetic(result, run_dir, stage="M4.8")
    if not result.validation_report.passed:
        raise typer.Exit(code=4)


@app.command("calibration-export")
def calibration_export(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. Export is read-only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
    sample_size: int = typer.Option(40, "--sample-size", min=30, max=50, help="Deterministic representative sample size."),
) -> None:
    """Export M5.0 translator-facing calibration JSONL; no model is called."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    before = fingerprint(source_path)
    snapshot = SourceSnapshot(path=str(source_path), sha256=before.sha256, size_bytes=before.size_bytes, mtime_utc=before.mtime_utc)
    try:
        document, uncertainty, _ = DocumentExtractor().extract(source_path, snapshot)
        views = TranslationViewBuilder().build(source_path, document)
        capability = TaggedCapabilityAudit().audit(source_path, document, views)
        requests = select_calibration_requests(document, views, capability, sample_size=sample_size)
    except (PackageValidationError, ValueError) as exc:
        console.print(f"[bold red]M5.0_EXPORT_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    finally:
        assert_unchanged(before, fingerprint(source_path), source_path)
    run_dir = ProjectPaths(root).runs_dir / new_calibration_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    request_path = run_dir / "calibration_request.jsonl"
    with request_path.open("w", encoding="utf-8", newline="\n") as stream:
        for request in requests:
            stream.write(request.model_dump_json() + "\n")
    (run_dir / "translation_policy.md").write_text(CALIBRATION_TRANSLATION_POLICY, encoding="utf-8")
    manifest = visual_review_manifest(requests, views)
    (run_dir / "visual_review_manifest.json").write_text(json.dumps([entry.model_dump() for entry in manifest], ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "source_sha256": before.sha256, "sample_size": len(requests),
        "category_distribution": {category: sum(category in request.selection_categories for request in requests) for category in sorted({item for request in requests for item in request.selection_categories})},
        "known_tagged_unsupported": capability.reason_counts,
    }
    (run_dir / "calibration_export_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]M5.0 calibration export complete:[/green] {len(requests)} selected TAGGED_SUPPORTED units")
    console.print(f"Request: {request_path}")
    console.print(f"Policy: {run_dir / 'translation_policy.md'}")
    console.print(f"Visual review manifest: {run_dir / 'visual_review_manifest.json'}")
    console.print(f"Source SHA-256 unchanged: {before.sha256}")


@app.command("calibration-import")
def calibration_import(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input."),
    request: Path = typer.Option(..., "--request", exists=False, readable=True, help="M5.0 calibration_request.jsonl."),
    response: Path = typer.Option(..., "--response", exists=False, readable=True, help="Translator calibration_response.jsonl."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Import responses; patch only PASS units and hold translator notes for review."""
    source_path = input.resolve()
    if not source_path.is_file() or not request.is_file() or not response.is_file():
        console.print("[red]INPUT_NOT_FOUND:[/red] input, request, and response must exist.")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_calibration_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = CalibrationImporter().run(source_path, request.resolve(), response.resolve(), run_dir / "candidate" / "calibration.docx")
    except (PackageValidationError, ValueError) as exc:
        console.print(f"[bold red]M5.0_IMPORT_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    status_counts = {status: sum(item.status == status for item in result.response_statuses) for status in ("PASS", "FAIL", "REVIEW_REQUIRED")}
    (run_dir / "calibration_qa_report.json").write_text(
        json.dumps([item.model_dump() for item in result.response_statuses], ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (run_dir / "review_required.json").write_text(
        json.dumps([item.model_dump() for item in result.review_required], ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (run_dir / "import_diagnostics.json").write_text(json.dumps([item.model_dump() for item in result.import_diagnostics], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "calibration_import_report.json").write_text(json.dumps({
        "source_sha256_before": result.source_document.source.sha256,
        "source_sha256_after": fingerprint(source_path).sha256,
        "response_count": result.response_count,
        "deterministic_pass_count": status_counts["PASS"],
        "deterministic_fail_count": status_counts["FAIL"],
        "review_required_count": status_counts["REVIEW_REQUIRED"],
        "patched_count": result.approved_count,
        "candidate_created": result.candidate_document is not None,
        "candidate_harness_passed": result.validation_report.passed if result.validation_report else False,
        "calibration_findings": ["INDEX_LIKE_TRANSLATION_POLICY_NEEDED"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    request_records = load_requests(request.resolve())
    response_records = load_responses(response.resolve())
    manifest_path = request.resolve().parent / "visual_review_manifest.json"
    imported_manifest = _import_visual_manifest(manifest_path, request_records, result.response_statuses)
    (run_dir / "visual_review_manifest.json").write_text(
        json.dumps(imported_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "calibration_review_checklist.md").write_text(
        _calibration_review_checklist(request_records, response_records, imported_manifest), encoding="utf-8",
    )
    if result.patch_report is not None:
        (run_dir / "calibration_patch_report.json").write_text(result.patch_report.model_dump_json(indent=2), encoding="utf-8")
    if result.validation_report is not None:
        (run_dir / "calibration_roundtrip_report.json").write_text(result.validation_report.model_dump_json(indent=2), encoding="utf-8")
    console.print(
        "Responses: "
        f"PASS={status_counts['PASS']}; FAIL={status_counts['FAIL']}; "
        f"REVIEW_REQUIRED={status_counts['REVIEW_REQUIRED']}; patched={result.approved_count}"
    )
    console.print(f"Source SHA-256 before/after: {result.source_document.source.sha256} / {fingerprint(source_path).sha256}")
    if result.validation_report is None:
        console.print("[yellow]No candidate created: no import unit cleared closed-world QA.[/yellow]")
        raise typer.Exit(code=4)
    color = "green" if result.validation_report.passed else "red"
    console.print(f"[bold {color}]M5.1c CALIBRATION IMPORT {'PASS' if result.validation_report.passed else 'FAILED'}[/bold {color}]")
    console.print(f"Candidate: {run_dir / 'candidate' / 'calibration.docx'}")
    if not result.validation_report.passed:
        raise typer.Exit(code=4)


def _import_visual_manifest(manifest_path: Path, requests, statuses) -> list[dict]:
    """Annotate the existing smoke manifest; visual priority is not a quality verdict."""
    status_by_uid = {item.unit_uid: item.status for item in statuses}
    if manifest_path.is_file():
        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"VISUAL_MANIFEST_INVALID: path={manifest_path}; detail={exc}") from exc
    else:
        entries = [
            {
                "unit_uid": item.unit_uid,
                "display_id": item.display_id,
                "content_role": item.content_role,
                "story": item.story,
                "selection_categories": item.selection_categories,
                "nearby_drawing_or_textbox": False,
                "structural_island": False,
                "continuation_hint": item.continuation_hint,
                "review_reason": "Fallback manifest: original export manifest was unavailable.",
            }
            for item in requests
        ]
    for entry in entries:
        categories = set(entry.get("selection_categories", []))
        reasons = []
        if entry.get("nearby_drawing_or_textbox") or entry.get("story") == "textboxes": reasons.append("nearby_drawing_or_textbox")
        if entry.get("structural_island"): reasons.append("structural_island")
        if entry.get("content_role") == "table_cell_paragraph" or "table" in categories: reasons.append("table")
        if "caption" in categories: reasons.append("caption")
        if entry.get("story") == "footers": reasons.append("footer")
        if "multi_anchor" in categories: reasons.append("multi_anchor")
        if "long_body" in categories: reasons.append("long_body")
        entry["patch_status"] = status_by_uid.get(entry.get("unit_uid"), "FAIL")
        entry["patched"] = entry["patch_status"] == "PASS"
        entry["continuation_context_status"] = "provided" if entry.get("continuation_hint") else "not_provided"
        entry["visual_priority"] = "HIGH" if entry["patched"] and reasons else "NORMAL"
        entry["priority_reasons"] = reasons
    return entries


def _calibration_review_checklist(requests, responses, manifest: list[dict]) -> str:
    """Human navigation only: exact approved text, never an automated review."""
    request_by_uid = {item.unit_uid: item for item in requests}
    response_by_uid = {item.unit_uid: item for item in responses}
    sections = {"HIGH": [], "NORMAL": []}
    for entry in manifest:
        if not entry.get("patched"):
            continue
        request = request_by_uid.get(entry.get("unit_uid"))
        response = response_by_uid.get(entry.get("unit_uid"))
        if request is None or response is None:
            continue
        target = "".join(segment.text for segment in response.target_segments)
        priority = entry.get("visual_priority", "NORMAL")
        sections[priority if priority in sections else "NORMAL"].append((entry, request, target))
    lines = [
        "# Calibration Review Checklist",
        "",
        "Navigation artifact only. Review the listed translated candidate locations in Word; it is not a semantic-review verdict.",
    ]
    for priority in ("HIGH", "NORMAL"):
        lines.extend(["", f"## {priority} PRIORITY"])
        for entry, request, target in sections[priority]:
            reasons = ", ".join(entry.get("priority_reasons", [])) or "normal"
            categories = ", ".join(entry.get("selection_categories", [])) or "general"
            lines.extend([
                "",
                f"### {entry['display_id']}",
                f"- Chinese target (Ctrl+F): {target}",
                f"- Source: {request.source_text}",
                f"- Selection categories: {categories}",
                f"- Visual priority reason: {reasons}",
            ])
    return "\n".join(lines) + "\n"


@app.command("format-audit")
def format_audit(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. This command is read-only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Read-only M4.5 audit of direct formatting noise and BlockIR granularity."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_format_audit_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        report = FormatTranslationUnitAudit().run(source_path)
    except (PackageValidationError, ValueError) as exc:
        console.print(f"[bold red]M4.5_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "format_unit_audit.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    _render_format_audit(report, run_dir)
    if not report.coverage_complete:
        raise typer.Exit(code=4)


@app.command("format-fragment-audit")
def format_fragment_audit(
    input: Path = typer.Option(..., "--input", exists=False, readable=True, help="Immutable DOCX input. This command is read-only."),
    project_root: Path | None = typer.Option(None, "--project-root", help="Project root for runs/ artifacts."),
) -> None:
    """Read-only M4.6 evidence audit; it does not create a candidate DOCX."""
    source_path = input.resolve()
    if not source_path.is_file():
        console.print(f"[red]INPUT_NOT_FOUND:[/red] File does not exist: {source_path}")
        raise typer.Exit(code=2)
    root = project_root.resolve() if project_root else discover_project_root()
    run_dir = ProjectPaths(root).runs_dir / new_format_fragment_audit_run_id(source_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        report = FormatFragmentDecisionAudit().run(source_path)
    except (PackageValidationError, ValueError) as exc:
        console.print(f"[bold red]M4.6_BLOCKER:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
    (run_dir / "format_fragment_decision_audit.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    _render_format_fragment_decision(report, run_dir)
    if not report.coverage_complete:
        raise typer.Exit(code=4)


def cli_main() -> None:
    app()
