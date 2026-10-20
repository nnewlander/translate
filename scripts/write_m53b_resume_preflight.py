"""Persist the narrow M5.3b PDF-registration/resume findings."""

from hashlib import sha256
import json
from pathlib import Path


RUN = Path(r"D:\translate\runs\m5.3-20260914T140000Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    v3 = RUN / "chapter_2" / "composite_project_hosting_v3"
    raw = v3 / "translation_raw_response.jsonl"
    request = v3 / "translation_request.json"
    payload = {
        "stage": "M5.3B_RESUME_PREFLIGHT",
        "source_sha256": sha(SOURCE),
        "body_p_01136": {
            "verdict": "ISOLATED_RAW_TRANSLATION_FAILURE",
            "request_contract": "PASS",
            "required_anchor_ids": [
                "11b908a1eed29c6054aa63883c56cf138942970d215d7719271867fce6767c8f",
                "8f9e7acad66a6e24758d8d1cb221fc271c9aa9174f5cc5c73da47b7304e70eb7",
                "765f61178289225ac3b3eb84b8609a6dfb1cbc7fcf816b079494ef6e518a7f9f",
            ],
            "raw_missing_anchor": "11b908a1eed29c6054aa63883c56cf138942970d215d7719271867fce6767c8f",
            "classification_reason": "Exporter request declared the complete closed-world anchor set; immutable RAW changed the first target segment to plain and omitted its anchor_ref.",
            "batch_002_gate": "PASS; record this real RAW FAIL, do not retry or alter RAW.",
        },
        "project_hosting_v3_provenance": {
            "verdict": "PASS",
            "request_path": str(request), "request_sha256": sha(request),
            "raw_path": str(raw), "raw_sha256": sha(raw),
            "execution_status": "exclusive-created under v3 corrected request; byte-equality with v2 RAW is permitted and does not imply file reuse.",
        },
        "execution": {"batch_002_gate": "PASS", "new_raw_generated": 0, "note": "No batch_002 artifact is claimed by this preflight."},
    }
    with (RUN / "m5_3b_resume_preflight.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
