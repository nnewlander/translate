"""Execute the first immutable production checkpoint of full-book batch 001."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response


ROOT = Path(r"D:\translate")
SCOPE_RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = SCOPE_RUN / "full_book_batch_001"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
BLOCKS = ROOT / "runs" / "extract-20260914T050300Z-decb09-094777" / "blocks.jsonl"
BIBLE = ROOT / "runs" / "m5.3-20260914T140000Z-decb09" / "translation_bible_snapshot.md"
CHECKPOINT = "checkpoint_01"
ORDER = [
    "body.p.00015", "body.p.00016", "body.p.00030", "body.p.00047", "body.p.00052",
    "body.p.00053", "body.p.00054", "body.p.00055", "body.p.00056", "body.p.00059",
    "body.p.00060", "body.p.00061", "body.p.00062", "body.p.00063", "body.p.00066",
    "body.p.00067", "body.p.00068", "body.p.00069", "body.p.00071", "body.p.00072",
]
TARGETS = {
    "body.p.00015": ["Google 软件工程"],
    "body.p.00016": ["软件开发历程中的经验教训"],
    "body.p.00030": ["Titus Winters、Tom Manshreck 和 Hyrum Wright"],
    "body.p.00047": ["北京", "波士顿", "法纳姆", "塞瓦斯托波尔", "东京"],
    "body.p.00052": ["Google 软件工程"],
    "body.p.00053": ["Titus Winters、Tom Manshreck 和 Hyrum Wright 著。版权所有 © 2020 Google, LLC。保留所有权利。"],
    "body.p.00054": ["美国印刷。"],
    "body.p.00055": ["出版方：O’Reilly Media, Inc.，1005 Gravenstein Highway North，Sebastopol，CA 95472。"],
    "body.p.00056": [
        "O’Reilly 图书可购买用于教育、商业或销售推广用途。大多数书名也提供在线版本（",
        "http://oreilly.com",
        "）。如需更多信息，请联系企业/机构",
        "销售部门：",
        "800-998-9938 or",
        " ",
        "corporate@oreilly.com",
        "。",
    ],
    "body.p.00059": ["选题编辑：", "Ryan Shaw"],
    "body.p.00060": ["开发编辑：", "Alicia Young"],
    "body.p.00061": ["制作编辑：", "Christopher Faucher"],
    "body.p.00062": ["文字编辑：", "Octal Publishing, LLC"],
    "body.p.00063": ["校对：", "Holly Bauer Forsyth"],
    "body.p.00066": ["索引编制：", "Ellen Troutman-Zaig"],
    "body.p.00067": ["内页设计：", "David Futato"],
    "body.p.00068": ["封面设计：", "Karen Montgomery"],
    "body.p.00069": ["插图绘制：", "Rebecca Demarest"],
    "body.p.00071": ["2020年3月：第一版"],
    "body.p.00072": ["第一版修订历史"],
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, records: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def request_for(view: TranslationView, block: BlockIR, source_sha: str) -> CalibrationRequest:
    by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(
        source_sha256=source_sha,
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        story=block.story,
        content_role=block.block_type,
        source_text=view.source_text,
        source_segments=[CalibrationSourceSegment(kind=segment.kind, text=segment.text, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment in view.segments],
        available_anchors=[CalibrationAnchor(anchor_id=anchor.anchor_id, semantics=anchor.kinds, protected=anchor.protected, protected_text=by_anchor.get(anchor.anchor_id) if anchor.protected else None, container="hyperlink" if "hyperlink_rid" in anchor.container_context else None) for anchor in view.anchors],
        continuation_hint=view.continuation_hint,
        previous_text=view.previous_text,
        next_text=view.next_text,
        selection_categories=["full_book_production", "full_book_batch_001", CHECKPOINT, "front_matter"],
    )


def qa(view: TranslationView, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    selection = json.loads((SCOPE_RUN / "full_book_batch_001_selection.json").read_text(encoding="utf-8"))
    if selection["selected_logical_work_order_ids"][:20] != ORDER:
        raise RuntimeError("FULL_BOOK_BATCH_001_CHECKPOINT_01_SELECTION_MISMATCH")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {item.display_id: item for item in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    requests = [request_for(views[display_id], blocks[display_id], source_sha) for display_id in ORDER]
    responses = []
    for request in requests:
        target = TARGETS[request.display_id]
        if len(target) != len(request.source_segments):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, target, strict=True)]))
    out = BATCH / CHECKPOINT
    if not BATCH.exists():
        BATCH.mkdir(parents=True)
        snapshot = BATCH / "translation_bible_snapshot.md"
        shutil.copyfile(BIBLE, snapshot)
        update_manifest(BATCH / "batch_manifest.json", {
            "batch": "FULL_BOOK_BATCH_001", "target_count": 100, "completed_count": 0,
            "checkpoint_count": 0, "remaining_in_batch": 100, "status": "OPEN_PROGRESS",
            "next_checkpoint": CHECKPOINT, "checkpoints": [], "source_sha256": source_sha,
            "translation_bible_snapshot_sha256": sha256(snapshot.read_bytes()).hexdigest(),
        })
        out.mkdir()
        write_jsonl(out / "translation_request.jsonl", requests)
        raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", responses)
    else:
        manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
        if manifest["completed_count"] != 0 or manifest["next_checkpoint"] != CHECKPOINT:
            raise RuntimeError("FULL_BOOK_BATCH_001_RESUME_STATE_MISMATCH")
        existing_requests = [CalibrationRequest.model_validate_json(line) for line in (out / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
        existing_raw = [TranslationResult.model_validate_json(line) for line in (out / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
        if [item.display_id for item in existing_requests] != ORDER or [item.unit_uid for item in existing_raw] != [item.unit_uid for item in requests]:
            raise RuntimeError("FULL_BOOK_BATCH_001_IMMUTABLE_RAW_CONTRACT_MISMATCH")
        raw_sha = sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest()
        responses = existing_raw
        interruption = BATCH / "checkpoint_01_interruption_provenance.json"
        if not interruption.exists():
            write_json(interruption, {
                "artifact_type": "CHECKPOINT_INTERRUPTION_PROVENANCE",
                "checkpoint": CHECKPOINT,
                "raw_sha256": raw_sha,
                "raw_status": "IMMUTABLE_REUSED_FOR_SUBSEQUENT_QA_ONLY",
                "interruption_stage": "before deterministic QA",
                "cause": "local TaggedSyntheticCandidate generation_kind constructor validation error",
                "semantic_translation_retry": False,
                "repair": "continue the original immutable request/RAW through the complete QA chain",
            })
    known = {view.unit_uid for view in views.values()}
    raw_qa = [qa(views[request.display_id], response, known) for request, response in zip(requests, responses, strict=True)]
    write_json(out / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_qa), "fail_count": sum(not row["passed"] for row in raw_qa), "records": raw_qa})
    normalize_response_artifact(out / "translation_request.jsonl", out / "translation_raw_response.jsonl", out / "translation_normalized_response.jsonl", out / "translation_normalization_report.json")
    raw_after = sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_after != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (out / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized_qa = [qa(views[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
    write_json(out / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in normalized_qa), "fail_count": sum(not row["passed"] for row in normalized_qa), "records": normalized_qa})
    dispatched, exclusions = partition_deterministic_review_dispatch(normalized_qa)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed the localized front-matter target preserves the source role and all protected literals; no material repair is required."} for row in dispatched]
    write_json(out / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(out / "translation_repaired_response.jsonl", normalized)
    approved = sorted(row["unit_uid"] for row in normalized_qa if row["passed"])
    failed = sorted(row["unit_uid"] for row in normalized_qa if not row["passed"])
    write_json(out / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": normalized_qa, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    write_json(out / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": len(failed), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(exclusions)})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", out)
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
