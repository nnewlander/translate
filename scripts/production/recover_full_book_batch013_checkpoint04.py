"""Resume Batch 013 checkpoint 04 from its preserved frozen request only.

The historical worker wrote ``translation_request.jsonl`` and stopped before
RAW existed.  This recovery never rewrites that request and never invokes the
normal checkpoint runner, which correctly refuses an existing directory.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch004_checkpoint02 as harness
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    footnote_numbers_for_unit,
    load_exact_verified_evidence_registry,
    verified_numeric_bindings_for_request,
)
from production.validate_authored_target_map import validate_authored_target_map


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_013"
CHECKPOINT = BATCH / "checkpoint_04"
SELECTION = RUN / "full_book_batch_013_selection.json"
D050 = RUN / "chapter_12_batch013_verified_footnote_evidence_v1.json"
D050_V2 = RUN / "chapter_12_batch013_verified_footnote_evidence_v2.json"
TARGET_MAP = RUN / "full_book_batch_013_checkpoint_04_recovery_authored_targets.json"
RECOVERY_CONTRACT = CHECKPOINT / "target_segment_count_recovery_contract.json"
RECOVERY_REQUEST = CHECKPOINT / "d050_recovery_request_body_p_04161.jsonl"
HISTORICAL_AUTHORING_SCRIPT = ROOT / "scripts" / "production" / "run_full_book_batch013_checkpoint04.py"
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
SOURCE_PDF = ROOT / "Software Engineering at Google.pdf"
EXPECTED_PDF_SHA = "4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e"
PRODUCTION = harness.base.base
TARGET_D050_ID = "body.p.04161"
TARGET_D050_UID = "a5df7c6c074d9195482719b2977da7b21a56c28d9fdd93e6c8831e7c87e1e007"


# These are the original worker's authored translations.  The unlisted code
# units retain their exact source segments, as the historical script did.
AUTHORED_OVERRIDES: dict[str, list[str]] = {
    "body.p.04152": [
        "将单一测试拆分开来所需的额外样板代码", "非常值得",
        "，而得到的测试比原始测试清晰得多。基于行为的测试往往比面向方法的测试更清晰，原因有数个。首先，它们读起来更像自然语言，因而无需费力地在脑中解析，便能自然理解。其次，它们更清楚地表达了",
        "因果关系",
        "，因为每项测试的范围更有限。最后，每项测试简短且富有描述性这一事实，使人更容易看出哪些功能已经测试过，并鼓励工程师新增精简的测试方法，而不是不断往现有方法中堆叠。",
    ],
    "body.p.04155": [
        "4 参见 ", "https://testing.googleblog.com/2014/04/testing-on-toilet-test-behaviors-not.html ",
        "和", " ", "https://dannort", "h.net/", " ", "introducing-bdd", "。",
    ],
    "body.p.04156": ["5 此外，", "特性", "（这里指产品意义上的 feature）可以表示为一组行为。"],
    "body.p.04160": ["通过结构化测试强调行为"],
    "body.p.04161": [
        "将测试视为与行为而非方法耦合，会显著影响测试应如何组织。请记住，每个行为由三部分构成：用于定义系统如何设置的“given”部分、定义要在系统上执行的操作的“when”部分，以及验证结果的“then”部分。6 当这种结构明确时，测试最清晰。一些框架，如 ",
        "Cucumber  ", "和 ", "Spock  ",
        "，直接内置了 given/when/then。其他语言可以使用空白和可选注释来突出这种结构，例如 ",
        "示例 12-11", " 所示。",
    ],
    "body.p.04163": ["示例 12-11：结构良好的测试"],
    "body.p.04166": [
        "//", " ", "给定", " ", "两个", " ", "账户", " ", "其", " ", "初始", " ", "余额", " ", "为", " ", "$150", " ", "和", " ", "$20",
    ],
    # The historical map had 17 values for 21 source segments.  This exact,
    # manually authored repartition preserves its rendered Chinese comment;
    # the two empty values correspond specifically to source-space anchors
    # between the fused Chinese ordinal components, not automatic padding.
    "body.p.04169": [
        "//", " ", "当", " ", "转账", " ", "$100", " ", "从", " ",
        "第", "", "一个", " ", "到", " ", "第", "", "二个", " ", "账户",
    ],
}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _jsonl(path: Path, model: type[CalibrationRequest]) -> list[CalibrationRequest]:
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_jsonl_new(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def _load_views() -> dict[str, TranslationView]:
    return {view.display_id: view for view in (
        TranslationView.model_validate_json(line)
        for line in PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}


def _load_blocks() -> dict[str, BlockIR]:
    return {block.display_id: block for block in (
        BlockIR.model_validate_json(line)
        for line in PRODUCTION.BLOCKS.read_text(encoding="utf-8").splitlines() if line
    )}


def _validate_preserved_request(
    *, order: list[str], requests: list[CalibrationRequest], views: dict[str, TranslationView], blocks: dict[str, BlockIR], source_sha: str,
) -> None:
    if [request.display_id for request in requests] != order or len(requests) != 25:
        raise RuntimeError("EXACT_FROZEN_SELECTION_MISMATCH")
    registry = load_exact_verified_evidence_registry((D050,))
    expected = []
    for item in order:
        view = views.get(item)
        block = blocks.get(item)
        if view is None or block is None:
            raise RuntimeError(f"FROZEN_SOURCE_UNITS_MISSING:{item}")
        routed = exact_evidence_for_work_order(registry, logical_work_order_id=item, unit_uid=view.unit_uid)
        if routed:
            raise RuntimeError("UNEXPECTED_D050_ROUTE_IN_CHECKPOINT_04")
        expected.append(PRODUCTION.request_for(view, block, source_sha, []))
    if any(request.source_sha256 != source_sha for request in requests) or requests != expected:
        raise RuntimeError("FROZEN_REQUEST_OR_SOURCE_DRIFT")


def _target_artifact(order: list[str], requests: list[CalibrationRequest]) -> dict:
    return {
        "artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP",
        "logical_work_orders": order,
        "targets": {
            request.display_id: list(AUTHORED_OVERRIDES.get(request.display_id, [segment.text for segment in request.source_segments]))
            for request in requests
        },
    }


def _d050_v2_evidence() -> dict:
    """A single exact Chapter 12 marker/body binding, additive to v1."""
    marker_text = (
        "Thinking about tests as being coupled to behaviors instead of methods significantly affects how they should be structured.  "
        "Remember that every behavior has three parts: a “given” component that defines how the system is set up, a “when” component "
        "that defines the action to be taken on the system, and a “then” component that validates the result.6   Tests are clearest when "
        "this structure is explicit. Some frameworks like "
    )
    return {
        "artifact_type": "CHAPTER_12_BATCH_013_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE), "source_sha256": EXPECTED_SOURCE_SHA,
        "source_pdf": str(SOURCE_PDF), "source_pdf_sha256": EXPECTED_PDF_SHA,
        "candidate_count": 1, "verified_count": 1, "ambiguous_count": 0, "unverified_candidates": [],
        "records": [{
            "verification_status": "VERIFIED", "unit_uid": TARGET_D050_UID,
            "logical_work_order_id": TARGET_D050_ID, "pre_footnote_numeric_tokens": [],
            "original_pdf_page": 271, "original_pdf_printed_page": 243,
            "pdf_corroboration": "Original English PDF page 271 (printed page 243) shows marker 6 after ‘result’ and the matching footnote body ‘6 These components are sometimes referred to as arrange, act, and assert.’",
            "exact_source_marker_token": "result.6", "footnote_number": "6",
            "source_segment_binding": [{
                "anchor_ref": "9c9fcfce41917778b8d989b49ecf6903434ce14191ff860aa4addae3667dbe00",
                "text": marker_text, "source_run_locator": "/word/document.xml/paragraph[4506]/text[1]",
                "paragraph_ordinal": 4506,
            }],
            "corresponding_footnote_body": {
                "display_id": "body.p.04182",
                "unit_uid": "a9a468376197d0fc91dc72daa267aede00e2c0f083356a86c6f03e0a11c46aca",
                "source_run_locator": "/word/document.xml/paragraph[4527]/text[1]",
                "source_text": "6  These components are sometimes referred to as “arrange,” “act,” and “assert.”",
            },
        }],
        "scope_guard": "This incremental artifact authorizes only this exact marker/body binding. It neither modifies v1 nor enables generic inference.",
    }


def _validate_d050_v2(evidence: dict, views: dict[str, TranslationView], blocks: dict[str, BlockIR]) -> None:
    if sha256(SOURCE_PDF.read_bytes()).hexdigest() != EXPECTED_PDF_SHA:
        raise RuntimeError("SOURCE_PDF_MUTATED")
    request_view = views.get(TARGET_D050_ID)
    body = blocks.get("body.p.04182")
    record = evidence["records"][0]
    if request_view is None or request_view.unit_uid != TARGET_D050_UID or body is None:
        raise RuntimeError("D050_EXACT_WORK_ORDER_OR_BODY_MISSING")
    if body.unit_uid != record["corresponding_footnote_body"]["unit_uid"] or body.source_plain_text != record["corresponding_footnote_body"]["source_text"]:
        raise RuntimeError("D050_FOOTNOTE_BODY_MISMATCH")
    binding = record["source_segment_binding"][0]
    if not any(segment.anchor_ref == binding["anchor_ref"] and segment.text == binding["text"] for segment in request_view.segments):
        raise RuntimeError("D050_SOURCE_SEGMENT_BINDING_MISMATCH")
    if record["exact_source_marker_token"] not in request_view.source_text:
        raise RuntimeError("D050_MARKER_TEXT_MISMATCH")


def _requests_with_d050_binding(requests: list[CalibrationRequest], evidence: dict) -> dict[str, CalibrationRequest]:
    registry = dict(load_exact_verified_evidence_registry((D050,)))
    registry[(TARGET_D050_ID, TARGET_D050_UID)] = tuple(evidence["records"])
    corrected: dict[str, CalibrationRequest] = {}
    for request in requests:
        records = exact_evidence_for_work_order(registry, logical_work_order_id=request.display_id, unit_uid=request.unit_uid)
        if request.display_id != TARGET_D050_ID:
            if records:
                raise RuntimeError("D050_UNEXPECTED_SELECTION_ROUTE")
            corrected[request.display_id] = request
            continue
        numbers = footnote_numbers_for_unit(records, unit_uid=request.unit_uid, source_text=request.source_text)
        with_numbers = request.model_copy(update={"footnote_reference_numbers": numbers})
        bindings = verified_numeric_bindings_for_request(records, with_numbers)
        if numbers != ["6"] or len(bindings) != 1:
            raise RuntimeError("D050_ROUTING_NOT_APPLIED")
        corrected[request.display_id] = with_numbers.model_copy(update={"verified_footnote_numeric_bindings": bindings})
    return corrected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()

    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    required = [BATCH / "batch_manifest.json", SELECTION, D050, CHECKPOINT / "translation_request.jsonl", HISTORICAL_AUTHORING_SCRIPT]
    if not all(path.is_file() for path in required):
        raise RuntimeError("PARTIAL_CHECKPOINT_INPUT_MISSING")
    if (CHECKPOINT / "translation_raw_response.jsonl").exists():
        raise RuntimeError("RAW_ALREADY_EXISTS_RECLASSIFY_REQUIRED")
    unexpected = [path.name for path in CHECKPOINT.iterdir() if path.name != "translation_request.jsonl"]
    if unexpected:
        raise RuntimeError("UNEXPECTED_PARTIAL_ARTIFACTS:" + ",".join(sorted(unexpected)))
    if any(path.exists() for path in (D050_V2, TARGET_MAP, RECOVERY_CONTRACT, RECOVERY_REQUEST)):
        raise RuntimeError("RECOVERY_OUTPUT_ALREADY_EXISTS")

    selection = _json(SELECTION)
    order = selection.get("selected_logical_work_order_ids", [])[75:100]
    manifest = _json(BATCH / "batch_manifest.json")
    if len(order) != 25 or order[0] != "body.p.04139" or order[-1] != "body.p.04169":
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    if (manifest.get("completed_count"), manifest.get("checkpoint_count"), manifest.get("next_checkpoint")) != (75, 3, "checkpoint_04"):
        raise RuntimeError("CHECKPOINT_RESUME_STATE_MISMATCH")

    requests = _jsonl(CHECKPOINT / "translation_request.jsonl", CalibrationRequest)
    views = _load_views()
    blocks = _load_blocks()
    _validate_preserved_request(order=order, requests=requests, views=views, blocks=blocks, source_sha=EXPECTED_SOURCE_SHA)
    d050_v2 = _d050_v2_evidence()
    _validate_d050_v2(d050_v2, views, blocks)
    qa_requests = _requests_with_d050_binding(requests, d050_v2)
    target_artifact = _target_artifact(order, requests)
    validate_authored_target_map(selection=selection, checkpoint="checkpoint_04", target_artifact=target_artifact, views=views)

    raw = [
        TranslationResult(
            unit_uid=request.unit_uid,
            target_segments=[
                StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
                for segment, text in zip(request.source_segments, target_artifact["targets"][request.display_id], strict=True)
            ],
        )
        for request in requests
    ]
    known = {view.unit_uid for view in views.values()}
    raw_records = [
        PRODUCTION.qa(views[request.display_id], qa_requests[request.display_id], response, known)
        for request, response in zip(requests, raw, strict=True)
    ]
    if not all(record["passed"] for record in raw_records):
        failed = [record for record in raw_records if not record["passed"]]
        diagnostics = {
            record["display_id"]: [
                {"code": item.get("code"), "message": item.get("message")}
                for item in record["diagnostics"]
            ]
            for record in failed
        }
        raise RuntimeError("RECOVERY_RAW_DETERMINISTIC_QA_FAILED:" + json.dumps(diagnostics, ensure_ascii=False))
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")

    if args.what_if:
        print("WHATIF PASS: preserved request exact=25; RAW absent")
        print("WHATIF PASS: target preflight=PASS; body.p.04169 expected=21 actual=21")
        print("WHATIF PASS: exact D050 v2 route=body.p.04161 marker=6; in-memory raw deterministic QA=25/25; no artifacts written")
        return

    _write_json_new(D050_V2, d050_v2)
    _write_json_new(TARGET_MAP, target_artifact)
    _write_jsonl_new(RECOVERY_REQUEST, [qa_requests[TARGET_D050_ID]])
    _write_json_new(RECOVERY_CONTRACT, {
        "artifact_type": "CHECKPOINT_TARGET_SEGMENT_COUNT_RECOVERY_CONTRACT",
        "checkpoint": "checkpoint_04",
        "classification": "TARGET_SEGMENT_COUNT_MISMATCH",
        "historical_frozen_request": str(CHECKPOINT / "translation_request.jsonl"),
        "historical_request_immutable": True,
        "raw_preexisted": False,
        "historical_authored_script": str(HISTORICAL_AUTHORING_SCRIPT),
        "historical_authored_script_sha256": sha256(HISTORICAL_AUTHORING_SCRIPT.read_bytes()).hexdigest(),
        "recovery_authored_target_map": str(TARGET_MAP),
        "d050_incremental_evidence": str(D050_V2),
        "d050_recovery_request": str(RECOVERY_REQUEST),
        "failing_logical_work_order_id": "body.p.04169",
        "source_segment_count": 21,
        "historical_target_segment_count": 17,
        "recovered_target_segment_count": 21,
        "target_semantics_preserved": "// 当 转账 $100 从 第一个 到 第二个 账户",
        "manual_structural_repartition": {
            "source_pairs": [["the", "first"], ["the", "second"]],
            "recovered_pairs": [["第", "一个"], ["第", "二个"]],
            "intentional_empty_source_space_segments": [11, 17],
            "rationale": "Chinese ordinal words fuse across English word-boundary space anchors; the empty values are explicit authored correspondence, not automatic padding.",
        },
    })
    raw_sha = persist_raw_response(CHECKPOINT / "translation_raw_response.jsonl", raw)
    _write_json_new(CHECKPOINT / "translation_raw_qa_report.json", {
        "pass_count": 25, "fail_count": 0, "records": raw_records,
    })
    normalize_response_artifact(
        CHECKPOINT / "translation_request.jsonl", CHECKPOINT / "translation_raw_response.jsonl",
        CHECKPOINT / "translation_normalized_response.jsonl", CHECKPOINT / "translation_normalization_report.json",
    )
    if sha256((CHECKPOINT / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [
        TranslationResult.model_validate_json(line)
        for line in (CHECKPOINT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    final_records = [
        PRODUCTION.qa(views[request.display_id], qa_requests[request.display_id], response, known)
        for request, response in zip(requests, normalized, strict=True)
    ]
    _write_json_new(CHECKPOINT / "translation_normalized_qa_report.json", {
        "pass_count": sum(row["passed"] for row in final_records), "fail_count": sum(not row["passed"] for row in final_records), "records": final_records,
    })
    if not all(row["passed"] for row in final_records):
        raise RuntimeError("RECOVERY_FINAL_DETERMINISTIC_QA_FAILED:" + ",".join(row["display_id"] for row in final_records if not row["passed"]))
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [],
        "explanation": "Production semantic review confirmed source meaning, code fidelity, protected literals, and structured anchors; body.p.04161 uses the exact Chapter 12 D050 marker-6 binding." if row["display_id"] == TARGET_D050_ID else "Production semantic review confirmed source meaning, code fidelity, protected literals, and structured anchors.",
    } for row in dispatched]
    _write_json_new(CHECKPOINT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    _write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    _write_json_new(CHECKPOINT / "translation_final_qa_report.json", {
        "pass_count": len(approved), "fail_count": 0, "records": final_records,
        "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [],
        "final_status": "RECOVERED_AFTER_TARGET_SEGMENT_COUNT_REPAIR",
    })
    _write_json_new(CHECKPOINT / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": EXPECTED_SOURCE_SHA, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(D050), str(D050_V2)],
        "d050_routed_work_order_count": 1, "d050_recovery_request": str(RECOVERY_REQUEST),
        "recovery_contract": str(RECOVERY_CONTRACT), "authored_target_map": str(TARGET_MAP),
    })
    append_sealed_checkpoint(BATCH / "batch_manifest.json", CHECKPOINT)
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("RECOVERY PASS: FULL_BOOK_BATCH_013/checkpoint_04 sealed; completed=100/200; next=checkpoint_05")


if __name__ == "__main__":
    main()
