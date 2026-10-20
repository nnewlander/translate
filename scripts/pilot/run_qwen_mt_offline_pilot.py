"""Run an isolated Qwen-MT candidate pilot through the existing import Harness.

This never reads or writes a production checkpoint.  Inputs must be request
artifacts from already sealed/release-approved work; output must be a new,
otherwise empty pilot directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.harness.calibration import CalibrationImporter
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import persist_raw_response
from booktrans.providers.provenance import persist_provider_provenance
from booktrans.providers.qwen_mt import QwenMTProvider, relevant_aids
from booktrans.providers.translation_provider import (
    ApprovedTranslationExample,
    ProviderContractGap,
    TranslationProviderRequest,
    project_plain_candidate_to_raw,
)
from booktrans.domain.calibration import CalibrationRequest


def _load_requests(paths: list[Path], limit: int) -> list[CalibrationRequest]:
    result: list[CalibrationRequest] = []
    seen: set[str] = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            request = CalibrationRequest.model_validate_json(line)
            if request.unit_uid in seen:
                raise RuntimeError("PILOT_DUPLICATE_UNIT_UID")
            seen.add(request.unit_uid)
            result.append(request)
            if len(result) == limit:
                return result
    return result


def _load_examples(path: Path | None) -> tuple[ApprovedTranslationExample, ...]:
    if path is None:
        return ()
    values: list[ApprovedTranslationExample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            values.append(ApprovedTranslationExample(**json.loads(line)))
    return tuple(values)


def _projection_gaps(requests: list[CalibrationRequest]) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {}
    for request in requests:
        if len(request.source_segments) != 1:
            gaps.setdefault("PROVIDER_CONTRACT_GAP_MULTI_SEGMENT_PLAIN_TEXT", []).append(request.display_id)
        elif request.source_segments[0].kind == "protected":
            gaps.setdefault("PROVIDER_CONTRACT_GAP_PROTECTED_ONLY_UNIT", []).append(request.display_id)
    return gaps


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--request", type=Path, action="append", required=True)
    parser.add_argument("--translation-bible", type=Path, required=True)
    parser.add_argument("--approved-tm", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 25 <= args.limit <= 50:
        raise RuntimeError("PILOT_SIZE_MUST_BE_25_TO_50")
    requests = _load_requests(args.request, args.limit)
    if len(requests) != args.limit:
        raise RuntimeError("PILOT_INSUFFICIENT_APPROVED_REQUESTS")
    source_sha = fingerprint(args.source).sha256
    if any(request.source_sha256 != source_sha for request in requests):
        raise RuntimeError("PILOT_SOURCE_SHA_MISMATCH")
    gaps = _projection_gaps(requests)
    plan = {
        "artifact_type": "QWEN_MT_OFFLINE_PILOT_PLAN",
        "schema_version": 1,
        "unit_count": len(requests),
        "source_sha256": source_sha,
        "logical_work_order_ids": [request.display_id for request in requests],
        "projection_contract_gaps": gaps,
        "projection_compatible": not gaps,
        "coverage": {
            "multi_segment": sum(len(request.source_segments) > 1 for request in requests),
            "protected_segment": sum(any(segment.kind == "protected" for segment in request.source_segments) for request in requests),
            "footnote_related": sum(bool(request.verified_footnote_numeric_bindings or request.footnote_reference_numbers) for request in requests),
        },
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0 if not gaps else 3
    if gaps:
        raise ProviderContractGap("PILOT_PROJECTION_CONTRACT_GAP")
    if args.out is None or args.out.exists():
        raise RuntimeError("PILOT_OUTPUT_MUST_BE_NEW")
    examples = _load_examples(args.approved_tm)
    provider = QwenMTProvider.from_environment()
    candidates = []
    responses = []
    for request in requests:
        provider_request = relevant_aids(
            TranslationProviderRequest(request=request), translation_bible=args.translation_bible, approved_examples=examples
        )
        candidate = provider.translate(provider_request)
        candidates.append(candidate)
        responses.append(project_plain_candidate_to_raw(provider_request, candidate))
    args.out.mkdir(parents=True)
    request_path = args.out / "translation_request.jsonl"
    with request_path.open("x", encoding="utf-8", newline="\n") as stream:
        for request in requests:
            stream.write(request.model_dump_json())
            stream.write("\n")
    persist_provider_provenance(args.out / "translation_provider_provenance.jsonl", candidates)
    raw_sha = persist_raw_response(args.out / "translation_raw_response.jsonl", responses)
    importer = CalibrationImporter()
    result = importer.run(args.source, request_path, args.out / "translation_raw_response.jsonl", args.out / "candidate.docx")
    _write_json_new(args.out / "pilot_report.json", {
        **plan,
        "raw_sha256": raw_sha,
        "deterministic_anchor_pass_count": sum(status.status == "PASS" for status in result.response_statuses),
        "deterministic_anchor_fail_count": sum(status.status == "FAIL" for status in result.response_statuses),
        "failure_types": dict(Counter(diagnostic.split(":", 1)[0] for status in result.response_statuses for diagnostic in status.diagnostics)),
        "provider_attempt_count": sum(candidate.attempt_count for candidate in candidates),
        "usage": [dict(candidate.usage) for candidate in candidates],
    })
    print(json.dumps({"status": "PASS", "out": str(args.out), "raw_sha256": raw_sha}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
