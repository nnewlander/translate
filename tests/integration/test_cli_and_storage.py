import json
import sqlite3

from typer.testing import CliRunner

from booktrans.cli import app


def test_inspect_emits_valid_json_report_and_does_not_mutate_source(rich_docx, tmp_path):
    source_before = rich_docx.read_bytes()
    result = CliRunner().invoke(app, ["inspect", "--input", str(rich_docx), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    reports = list((tmp_path / "runs").glob("*/inspect.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["source"]["source_mutated"] is False
    assert report["content"]["images"] == 1
    assert {"run_id", "source", "package", "content", "special_constructs", "warnings", "blockers"} <= report.keys()
    assert rich_docx.read_bytes() == source_before


def test_same_sha_is_one_document_with_multiple_inspection_runs(rich_docx, tmp_path):
    runner = CliRunner()
    first = runner.invoke(app, ["inspect", "--input", str(rich_docx), "--project-root", str(tmp_path)])
    second = runner.invoke(app, ["inspect", "--input", str(rich_docx), "--project-root", str(tmp_path)])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    with sqlite3.connect(tmp_path / "data" / "translation.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM inspection_runs").fetchone()[0] == 2


def test_database_is_initialized_by_inspect(minimal_docx, tmp_path):
    result = CliRunner().invoke(app, ["inspect", "--input", str(minimal_docx), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    database = tmp_path / "data" / "translation.db"
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"schema_version", "documents", "inspection_runs", "parts"} <= tables


def test_cli_reports_missing_input_actionably(tmp_path):
    result = CliRunner().invoke(
        app,
        ["inspect", "--input", str(tmp_path / "missing.docx"), "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "INPUT_NOT_FOUND" in result.output
