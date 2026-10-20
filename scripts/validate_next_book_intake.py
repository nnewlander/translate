"""Read-only preflight for a new book intake JSON; it creates no run artifacts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any

from booktrans.ooxml.inspector import OOXMLInspector
from booktrans.ooxml.package import PackageValidationError


SAFE_BOOK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
REQUIRED_FIELDS = ("book_id", "source_docx", "output_root", "source_language", "target_language")


def validate(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError("INTAKE_CONFIG_OBJECT_REQUIRED")
    missing = [field for field in REQUIRED_FIELDS if not isinstance(config.get(field), str) or not config[field].strip()]
    if missing:
        raise ValueError(f"INTAKE_REQUIRED_FIELDS_MISSING: {','.join(missing)}")
    book_id = config["book_id"]
    if not SAFE_BOOK_ID.fullmatch(book_id):
        raise ValueError("INTAKE_BOOK_ID_NOT_FILESYSTEM_SAFE")

    source_docx = Path(config["source_docx"])
    _require_readable_file(source_docx, "SOURCE_DOCX")
    try:
        OOXMLInspector().inspect(source_docx)
    except PackageValidationError as error:
        raise ValueError(f"SOURCE_DOCX_INVALID: {error}") from error

    source_pdf_value = config.get("source_pdf")
    if source_pdf_value is not None:
        if not isinstance(source_pdf_value, str) or not source_pdf_value.strip():
            raise ValueError("SOURCE_PDF_MUST_BE_A_NONEMPTY_STRING_OR_NULL")
        _require_readable_file(Path(source_pdf_value), "SOURCE_PDF")

    output_root = Path(config["output_root"])
    if not output_root.is_dir() or not os.access(output_root, os.W_OK):
        raise ValueError("OUTPUT_ROOT_NOT_AVAILABLE")
    proposed_run = output_root / book_id
    if proposed_run.exists():
        raise ValueError(f"NEW_RUN_PATH_ALREADY_EXISTS: {proposed_run}")
    return {
        "status": "PASS",
        "book_id": book_id,
        "source_docx": str(source_docx.resolve()),
        "source_pdf": str(Path(source_pdf_value).resolve()) if source_pdf_value else None,
        "output_root": str(output_root.resolve()),
        "proposed_run": str(proposed_run.resolve()),
        "source_language": config["source_language"],
        "target_language": config["target_language"],
    }


def _require_readable_file(path: Path, label: str) -> None:
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError(f"{label}_NOT_READABLE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.config), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
