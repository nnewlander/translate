"""Build a new, isolated, diverse Qwen structured-provider pilot plan.

Only requests from sealed checkpoints with passing final QA are eligible.  This
planner is read-only unless ``--out`` is supplied; it never creates a
production checkpoint or copies/mutates production artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.domain.calibration import CalibrationRequest
from booktrans.harness.source_integrity import fingerprint
from booktrans.providers.qwen_mt import _relevant_terms


URL = re.compile(r"https?://|\bwww\.", re.IGNORECASE)
DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?")
CODE_ADJACENT = re.compile(r"`|\b(?:API|HTTP|JSON|XML|Linux|Python|Java|C\+\+|function|class|method|codebase|repository)\b", re.IGNORECASE)

REQUIRED_CATEGORIES = (
    "simple_single_segment",
    "multi_segment",
    "code_adjacent",
    "protected_segment",
    "anchor_or_island",
    "terminology_heavy",
    "number",
    "url",
    "doi",
    "footnote_related",
)


def categories(request: CalibrationRequest, *, translation_bible: Path) -> set[str]:
    text = request.source_text
    result: set[str] = set()
    if len(request.source_segments) == 1 and request.source_segments[0].kind != "protected":
        result.add("simple_single_segment")
    if len(request.source_segments) > 1:
        result.add("multi_segment")
    if any(segment.kind == "protected" for segment in request.source_segments):
        result.add("protected_segment")
    if any(segment.kind == "anchored" or segment.anchor_ref or segment.island_ref for segment in request.source_segments):
        result.add("anchor_or_island")
    if CODE_ADJACENT.search(text):
        result.add("code_adjacent")
    if len(_relevant_terms(text, translation_bible, max_terms=2)) >= 2:
        result.add("terminology_heavy")
    if NUMBER.search(text):
        result.add("number")
    if URL.search(text):
        result.add("url")
    if DOI.search(text):
        result.add("doi")
    if request.verified_footnote_numeric_bindings or request.footnote_reference_numbers:
        result.add("footnote_related")
    return result


def _sealed_request_paths(run_root: Path) -> Iterable[Path]:
    for evidence in sorted(run_root.glob("full_book_batch_*/checkpoint_*/checkpoint_evidence.json")):
        final_qa = evidence.parent / "translation_final_qa_report.json"
        request = evidence.parent / "translation_request.jsonl"
        if not final_qa.is_file() or not request.is_file():
            continue
        try:
            evidence_value = json.loads(evidence.read_text(encoding="utf-8"))
            final_value = json.loads(final_qa.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if evidence_value.get("deterministic_fail_count") == 0 and final_value.get("fail_count") == 0:
            yield request


def eligible_requests(run_root: Path, *, source_sha256: str, translation_bible: Path) -> list[tuple[Path, int, CalibrationRequest, set[str]]]:
    result: list[tuple[Path, int, CalibrationRequest, set[str]]] = []
    seen: set[str] = set()
    for path in _sealed_request_paths(run_root):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            request = CalibrationRequest.model_validate_json(line)
            if request.source_sha256 != source_sha256 or request.unit_uid in seen:
                continue
            seen.add(request.unit_uid)
            result.append((path, line_number, request, categories(request, translation_bible=translation_bible)))
    return result


def choose_plan(candidates: list[tuple[Path, int, CalibrationRequest, set[str]]], *, count: int) -> tuple[list[tuple[Path, int, CalibrationRequest, set[str]]], list[str]]:
    selected: list[tuple[Path, int, CalibrationRequest, set[str]]] = []
    remaining = list(candidates)
    missing: list[str] = []
    for category in REQUIRED_CATEGORIES:
        match = next((entry for entry in remaining if category in entry[3]), None)
        if match is None:
            missing.append(category)
            continue
        selected.append(match)
        remaining.remove(match)
    # Multi-segment behavior is the material Phase 2 question: reserve at
    # least 40% of a 40--60 unit pilot for it, then fill deterministically.
    multi_target = max(1, (count * 2 + 4) // 5)
    for entry in list(remaining):
        if len(selected) >= count or sum("multi_segment" in value[3] for value in selected) >= multi_target:
            break
        if "multi_segment" in entry[3]:
            selected.append(entry)
            remaining.remove(entry)
    selected.extend(remaining[: max(0, count - len(selected))])
    return selected, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--translation-bible", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not 40 <= args.count <= 60:
        raise RuntimeError("QWEN_STRUCTURED_PILOT_SIZE_MUST_BE_40_TO_60")
    if not args.translation_bible.is_file():
        raise RuntimeError("QWEN_STRUCTURED_PILOT_TRANSLATION_BIBLE_MISSING")
    source_sha = fingerprint(args.source).sha256
    selected, missing = choose_plan(
        eligible_requests(args.run_root, source_sha256=source_sha, translation_bible=args.translation_bible), count=args.count
    )
    coverage = Counter(category for _, _, _, values in selected for category in values)
    plan = {
        "artifact_type": "QWEN_STRUCTURED_PROVIDER_PILOT_PLAN",
        "schema_version": 1,
        "source_sha256": source_sha,
        "unit_count": len(selected),
        "required_categories": list(REQUIRED_CATEGORIES),
        "coverage": dict(sorted(coverage.items())),
        "coverage_gaps": missing,
        "multi_segment_target": max(1, (args.count * 2 + 4) // 5),
        "requests": [
            {
                "request_path": str(path.resolve()),
                "line_number": line_number,
                "unit_uid": request.unit_uid,
                "logical_work_order_id": request.display_id,
                "categories": sorted(values),
            }
            for path, line_number, request, values in selected
        ],
    }
    if args.out is not None:
        if args.out.exists():
            raise RuntimeError("QWEN_STRUCTURED_PILOT_PLAN_OUTPUT_MUST_BE_NEW")
        if missing or len(selected) != args.count:
            raise RuntimeError("QWEN_STRUCTURED_PILOT_REQUIRED_COVERAGE_UNAVAILABLE")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return 0 if not missing and len(selected) == args.count else 3


if __name__ == "__main__":
    raise SystemExit(main())
