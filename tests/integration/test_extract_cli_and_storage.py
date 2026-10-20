import json
import sqlite3

from typer.testing import CliRunner

from booktrans.cli import app
from booktrans.domain.ir import BlockIR


def test_extract_writes_ir_ledger_and_is_idempotent_for_units(rich_docx, tmp_path):
    runner = CliRunner()
    before = rich_docx.read_bytes()
    first = runner.invoke(app, ["extract", "--input", str(rich_docx), "--project-root", str(tmp_path)])
    second = runner.invoke(app, ["extract", "--input", str(rich_docx), "--project-root", str(tmp_path)])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert rich_docx.read_bytes() == before
    reports = list((tmp_path / "runs").glob("*/extract_report.json"))
    assert len(reports) == 2
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["coverage"]["coverage_complete"] is True
    assert report["coverage"]["unclassified_visible_text"] == 0
    assert (reports[0].parent / "document.json").is_file()
    assert (reports[0].parent / "blocks.jsonl").is_file()
    blocks = [BlockIR.model_validate_json(line) for line in (reports[0].parent / "blocks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert blocks
    assert all(block.unit_uid for block in blocks)
    assert (reports[0].parent / "coverage.json").is_file()
    with sqlite3.connect(tmp_path / "data" / "translation.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 2
        unit_count = connection.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    assert unit_count > 0


def test_extract_returns_clear_incomplete_status_and_artifacts(m2_docx, tmp_path):
    result = CliRunner().invoke(app, ["extract", "--input", str(m2_docx), "--project-root", str(tmp_path)])
    assert result.exit_code == 4, result.output
    assert "Coverage complete: NO" in result.output
    coverage = json.loads(next((tmp_path / "runs").glob("*/coverage.json")).read_text(encoding="utf-8"))
    assert coverage["coverage_complete"] is False
    assert coverage["unknown_potential_visible_content"] > 0


def test_extract_rejects_invalid_docx(tmp_path):
    invalid = tmp_path / "not-a-docx.docx"
    invalid.write_text("not a zip", encoding="utf-8")
    result = CliRunner().invoke(app, ["extract", "--input", str(invalid), "--project-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "INVALID_ZIP" in result.output


def test_identity_cli_writes_candidate_and_auditable_reports(identity_docx, tmp_path):
    source_before = identity_docx.read_bytes()
    result = CliRunner().invoke(app, ["identity", "--input", str(identity_docx), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "M3 IDENTITY ROUND-TRIP PASS" in result.output
    run_dir = next((tmp_path / "runs").glob("identity-*"))
    patch = json.loads((run_dir / "patch_report.json").read_text(encoding="utf-8"))
    roundtrip = json.loads((run_dir / "roundtrip_report.json").read_text(encoding="utf-8"))
    assert (run_dir / "candidate" / "identity.docx").is_file()
    assert patch["writes_attempted"] == patch["writes_verified"] > 0
    assert patch["resolution_failures"] == []
    assert roundtrip["candidate_coverage_complete"] is True
    assert roundtrip["logical_mismatch_count"] == 0
    assert source_before == identity_docx.read_bytes()


def test_synthetic_cli_writes_only_audited_candidate(identity_docx, tmp_path):
    source_before = identity_docx.read_bytes()
    result = CliRunner().invoke(app, ["synthetic", "--input", str(identity_docx), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "M4 SYNTHETIC ROUND-TRIP PASS" in result.output
    run_dir = next((tmp_path / "runs").glob("synthetic-*"))
    capability = json.loads((run_dir / "capability_report.json").read_text(encoding="utf-8"))
    validation = json.loads((run_dir / "synthetic_roundtrip_report.json").read_text(encoding="utf-8"))
    assert capability["safe_simple_blocks"] > 0
    assert capability["complex_unsupported_blocks"] > 0
    assert (run_dir / "synthetic_candidates.jsonl").is_file()
    assert (run_dir / "candidate" / "synthetic.docx").is_file()
    assert validation["candidate_coverage_complete"] is True
    assert validation["expected_block_mismatch_count"] == 0
    assert source_before == identity_docx.read_bytes()
