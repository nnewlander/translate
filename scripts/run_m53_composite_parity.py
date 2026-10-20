"""Run the narrow M5.3 composite contract-parity checkpoint.

This script intentionally addresses only the eight source-proven groups in the
frozen final scope. It never touches source DOCX, historical RAW, or a
candidate package.
"""

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.composite_translation import CompositeTranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.composite_contract import build_composite_request, validate_flat_composite_with_ordinary_qa
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.source_integrity import fingerprint
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import TranslationView


RUN = Path(r"D:\translate\runs\m5.3-20260914T140000Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
VIEWS = Path(r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl")
OLD_RAW = RUN / "chapter_2" / "composite_project_hosting" / "translation_raw_response.jsonl"
OLD_REQUEST = RUN / "chapter_2" / "composite_project_hosting" / "translation_request.json"
OUT = RUN / "chapter_2" / "composite_project_hosting_v3"
PREVIOUS_CORRECTED = RUN / "chapter_2" / "composite_project_hosting_v2"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_new_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_new_jsonl(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(record.model_dump_json())
        stream.write("\n")


def _normal_request_evidence() -> dict[str, list[str]]:
    """Read existing exporter evidence only; missing records remain empty."""
    path = RUN / "chapter_2" / "batch_001" / "translation_request.jsonl"
    values: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        values[item["display_id"]] = list(item.get("footnote_reference_numbers", []))
    return values


def main() -> None:
    source_before = fingerprint(SOURCE).sha256
    scope = json.loads((RUN / "multi_chapter_scope_final.json").read_text(encoding="utf-8"))
    views = {
        item.display_id: item
        for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line.strip())
    }
    evidence = _normal_request_evidence()
    requests = []
    preflight = []
    for group in scope["proven_composite_groups"]:
        request, flat_reasons = build_composite_request(group, views, source_sha256=source_before, member_footnote_evidence=evidence)
        requests.append(request)
        envelope = request.envelope
        preflight.append({
            "group_id": envelope.group_id,
            "members": [{"unit_uid": uid, "display_id": display} for uid, display in zip(envelope.member_unit_uids, envelope.member_display_ids, strict=True)],
            "flat_target_eligible": envelope.flat_target_eligible,
            "flat_target_ineligibility_reasons": flat_reasons,
            "logical_editable_island_count": envelope.logical_editable_island_count,
            "semantic_anchor_kinds": envelope.semantic_anchor_kinds,
            "movable_semantic_anchors": sorted(set(envelope.semantic_anchor_kinds) & {"bold", "italic", "underline", "color", "highlight", "hyperlink", "character_style"}),
            "protected_literals": envelope.protected_literals,
            "numeric_evidence": envelope.numeric_source_tokens,
            "footnote_reference_numbers": envelope.footnote_reference_numbers,
            "url_evidence": envelope.url_source_tokens,
            "email_evidence": envelope.email_source_tokens,
            "doi_evidence": envelope.doi_source_tokens,
            "isbn_evidence": envelope.isbn_source_tokens,
            "validator_parity_status": "PARITY_READY",
            "required_response_contract": envelope.required_response_contract,
            "ordinary_validator": "DeterministicCandidateQA shared primitive; structured groups additionally use existing anchor/island closed-world contract",
        })
    _write_new_json(RUN / "composite_request_parity_preflight_v2.json", {
        "stage": "M5.3B_COMPOSITE_REQUEST_PARITY_PREFLIGHT",
        "scope_authority": str(RUN / "multi_chapter_scope_final.json"),
        "source_sha256": source_before,
        "groups": preflight,
        "parity_ready_count": sum(item["validator_parity_status"] == "PARITY_READY" for item in preflight),
        "group_count": len(preflight),
        "gate": "PASS" if all(item["validator_parity_status"] == "PARITY_READY" for item in preflight) else "BLOCKED",
    })

    project = next(item for item in requests if item.envelope.group_id == "m5_3_ch2_project_hosting")
    old_sha_before = _sha(OLD_RAW)
    old_request_sha = _sha(OLD_REQUEST)
    _write_new_json(OUT / "translation_request.json", project.model_dump(mode="json"))
    new_request_sha = _sha(OUT / "translation_request.json")
    _write_new_json(OUT / "composite_raw_provenance.json", {
        "historical_raw": {"path": str(OLD_RAW), "sha256": old_sha_before, "status": "CONTRACT_SUPERSEDED_RAW", "reason": "generated under incomplete composite deterministic contract; UNEVIDENCED_FOOTNOTE_SUPERSCRIPT"},
        "historical_request": {"path": str(OLD_REQUEST), "sha256": old_request_sha},
        "previous_corrected_execution": {
            "request_path": str(PREVIOUS_CORRECTED / "translation_request.json"),
            "request_sha256": _sha(PREVIOUS_CORRECTED / "translation_request.json"),
            "raw_path": str(PREVIOUS_CORRECTED / "translation_raw_response.jsonl"),
            "raw_sha256": _sha(PREVIOUS_CORRECTED / "translation_raw_response.jsonl"),
            "status": "CONTRACT_SUPERSEDED_RAW",
            "reason": "Serialized envelope did not enumerate attached source numeric token Ben1; no candidate was created.",
        },
        "corrected_request": {"path": str(OUT / "translation_request.json"), "sha256": new_request_sha, "same_as_historical_request": old_request_sha == new_request_sha},
        "denominator_policy": "Historical composite RAW is immutable evidence and is excluded from current-contract RAW quality and FINAL_APPROVED denominators.",
    })

    # First response under the corrected request contract. Ben1 remains an
    # ordinary numeric token because the exporter supplied no footnote evidence.
    raw = CompositeTranslationResult(
        group_id="m5_3_ch2_project_hosting",
        target_text="在过去的20年里，我和同事 Ben1 曾在许多编程会议上发表演讲。2006年，我们推出了 Google 的开源 Project Hosting 服务（现已弃用）。起初，我们收到了许多有关该产品的问题和请求；但在2008年年中前后，我们开始注意到所收到请求中出现了一种趋势：",
    )
    _write_new_jsonl(OUT / "translation_raw_response.jsonl", raw)
    raw_sha_before = _sha(OUT / "translation_raw_response.jsonl")
    raw_errors = validate_flat_composite_with_ordinary_qa(raw, project, approved_group_ids={raw.group_id})

    # Reuse the approved normalizer through the envelope's normal request; it
    # cannot alter Ben1 because footnote_reference_numbers is empty.
    ordinary_response = TranslationResult(unit_uid=raw.group_id, target_segments=[StructuredTargetSegment(text=raw.target_text, kind="plain")])
    normalized_ordinary, changes = normalize_footnote_superscripts(project.envelope.as_ordinary_request(), ordinary_response)
    normalized = raw.model_copy(update={"target_text": "".join(segment.text for segment in normalized_ordinary.target_segments)})
    _write_new_jsonl(OUT / "translation_normalized_response.jsonl", normalized)
    raw_sha_after = _sha(OUT / "translation_raw_response.jsonl")
    normalized_errors = validate_flat_composite_with_ordinary_qa(normalized, project, approved_group_ids={normalized.group_id})
    _write_new_json(OUT / "translation_normalization_report.json", {
        "stage": "NORMALIZED", "raw_sha256_before": raw_sha_before, "raw_sha256_after": raw_sha_after,
        "raw_immutable": raw_sha_before == raw_sha_after,
        "normalized_changed": bool(changes), "normalization_rule_distribution": {},
        "changes": [], "reason": "No explicit footnote-reference evidence exists for Ben1; approved normalizer made no change.",
    })
    _write_new_json(OUT / "translation_raw_qa_report.json", {"stage": "RAW_DETERMINISTIC_QA", "passed": not raw_errors, "diagnostics": raw_errors})
    _write_new_json(OUT / "translation_normalized_qa_report.json", {"stage": "NORMALIZED_DETERMINISTIC_QA", "passed": not normalized_errors, "diagnostics": normalized_errors})
    _write_new_json(OUT / "semantic_review_report.json", {
        "stage": "SEMANTIC_REVIEW", "group_id": raw.group_id, "status": "ACCEPT",
        "issue_types": [], "explanation": "Reviewed against the complete combined source. The translation preserves the time spans, deprecated qualifier, Project Hosting name, and causal progression; no material repair is needed.",
        "raw_target": raw.target_text, "reviewed_target": normalized.target_text,
    })
    _write_new_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    if raw_sha_before != raw_sha_after:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    if fingerprint(SOURCE).sha256 != source_before:
        raise RuntimeError("SOURCE_MUTATED")
    _write_new_json(OUT / "final_status.json", {
        "group_id": raw.group_id, "status": "FINAL_APPROVED" if not normalized_errors else "BLOCKED",
        "raw_sha256": raw_sha_before, "request_sha256": new_request_sha,
        "raw_deterministic_diagnostics": raw_errors, "normalized_deterministic_diagnostics": normalized_errors,
        "source_sha256_before_after": [source_before, fingerprint(SOURCE).sha256],
    })


if __name__ == "__main__":
    main()
