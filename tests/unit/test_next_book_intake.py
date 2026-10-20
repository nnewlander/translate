import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[2] / "scripts" / "validate_next_book_intake.py"
    spec = importlib.util.spec_from_file_location("validate_next_book_intake", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, source: Path) -> Path:
    config = tmp_path / "intake.json"
    config.write_text(json.dumps({
        "book_id": "new-book-001",
        "source_docx": str(source),
        "source_pdf": None,
        "output_root": str(tmp_path),
        "source_language": "en",
        "target_language": "zh-Hans",
    }), encoding="utf-8")
    return config


def test_next_book_intake_validates_generic_docx_and_new_run_path(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "generic_pipeline_smoke.docx"
    result = _module().validate(_config(tmp_path, fixture))
    assert result["status"] == "PASS"
    assert result["proposed_run"].endswith("new-book-001")


def test_next_book_intake_rejects_unsafe_book_id_and_existing_run(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "generic_pipeline_smoke.docx"
    config = _config(tmp_path, fixture)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["book_id"] = "../not-safe"
    config.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="FILESYSTEM_SAFE"):
        _module().validate(config)
    data["book_id"] = "new-book-001"
    (tmp_path / data["book_id"]).mkdir()
    config.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="NEW_RUN_PATH_ALREADY_EXISTS"):
        _module().validate(config)
