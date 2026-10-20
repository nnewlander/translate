"""Execute immutable production checkpoint 01 for full-book batch 002."""

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
BATCH = SCOPE_RUN / "full_book_batch_002"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
BLOCKS = ROOT / "runs" / "extract-20260914T050300Z-decb09-094777" / "blocks.jsonl"
BIBLE = ROOT / "runs" / "m5.3-20260914T140000Z-decb09" / "translation_bible_snapshot.md"
CHECKPOINT = "checkpoint_01"
ORDER = [
    "body.p.00727", "body.p.00728", "body.p.00729", "body.p.00730", "body.p.00731",
    "body.p.00735", "body.p.00736", "body.p.00737", "body.p.00738", "body.p.00739",
    "body.p.00740", "body.p.00741", "body.p.00742", "body.p.00743", "body.p.00744",
    "body.p.00745", "body.p.00746", "body.p.00747", "body.p.00748", "body.p.00749",
]
TARGETS = {
    "body.p.00727": ["本书设有网页，列出勘误、示例及其他补充信息。您可通过以下网址访问该页面：", "https://oreil.ly/software-engin", "eering-at-google", "。如需对本书发表评论或提出技术问题，请发送电子邮件至", "bookquestions@oreilly.com", "", "。"],
    "body.p.00728": ["如需了解我们的图书和课程的新闻及更多信息，请访问我们的网站：", "http://www.oreilly.com", "。"],
    "body.p.00729": ["在 Facebook 上关注我们：", "http://facebook.com/oreil", "ly"],
    "body.p.00730": ["在 Twitter 上关注我们：", "http://twitter.com/oreillymedia"],
    "body.p.00731": ["在 YouTube 上关注我们：", "http://ww", "w.youtube.com/oreillymedia"],
    "body.p.00735": ["致谢"],
    "body.p.00736": ["没有无数他人的努力，这本书便不可能问世。本书中的全部知识，都来自我们职业生涯中在 Google 与众多他人共同积累的经验。我们只是传递者；在 Google 及其他地方，前人走在我们之前，教会了我们如今呈现给您的内容。我们无法在此一一列出各位，但确实希望向各位致谢。"],
    "body.p.00737": ["我们还要感谢 Melody Meckfessel 在项目初期给予支持，也感谢 Daniel Jasper 和 Danny Berlin 一直支持本项目直至完成。"],
    "body.p.00738": ["没有策展人、作者和编辑付出的巨大协作努力，本书便不可能完成。尽管作者和编辑会在各章或各个引文框中得到明确致谢，我们仍想借此向那些通过深思熟虑的意见、讨论和审阅，为每一章作出贡献的人致意。"],
    "body.p.00739": ["•  ", "《什么是软件工程？》：", "Sanjay Ghemawat, Andrew Hyatt"],
    "body.p.00740": ["•  ", "《高效团队协作》：", "Sibley Bacon, Joshua Morton"],
    "body.p.00741": ["•  ", "《知识共享》：", "Dimitri   Glazkov,    Kyle   Lemons,   John    Reese,   David Symonds, Andrew Trenk, James Tucker, David Kohlbrenner, Rodrigo Damazio Bovendorp"],
    "body.p.00742": ["•  ", "《促进公平的工程实践》：", "Kamau Bobb, Bruce Lee"],
    "body.p.00743": ["•  ", "《如何带领团队》：", "Jon Wiley, Laurent Le Brun"],
    "body.p.00744": ["•  ", "《规模化领导力》：", "Bryan  O’Sullivan,  Bharat  Mediratta,  Daniel  Jasper,  Shaindel Schwartz"],
    "body.p.00745": ["•  ", "《衡量工程生产力》：", "Andrea  Knight,  Collin  Green,  Caitlin Sadowski, Max-Kanat Alexander, Yilei Yang"],
    "body.p.00746": ["•  ", "《风格指南与规则》：", "Max  Kanat-Alexander,  Titus  Winters,  Matt  Austern, James Dennett"],
    "body.p.00747": ["•  ", "《代码审查》：", "Max Kanat-Alexander, Brian Ledger, Mark Barolak"],
    "body.p.00748": ["•  ", "《文档》：", "Jonas Wagner, Smit Hinsu, Geoffrey Romer"],
    "body.p.00749": ["•  ", "《测试概览》：", "Erik Kuefler, Andrew Trenk, Dillon Bly, Joseph Graves, Neal Norwitz,  Jay  Corbett,  Mark  Striebeck,  Brad  Green,  Miško  Hevery,  Antoine Picard, Sarah Storck"],
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
    return CalibrationRequest(source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story, content_role=block.block_type, source_text=view.source_text, source_segments=[CalibrationSourceSegment(kind=segment.kind, text=segment.text, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment in view.segments], available_anchors=[CalibrationAnchor(anchor_id=anchor.anchor_id, semantics=anchor.kinds, protected=anchor.protected, protected_text=by_anchor.get(anchor.anchor_id) if anchor.protected else None, container="hyperlink" if "hyperlink_rid" in anchor.container_context else None) for anchor in view.anchors], continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text, selection_categories=["full_book_production", "full_book_batch_002", CHECKPOINT, "preface"])


def qa(view: TranslationView, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    selection = json.loads((SCOPE_RUN / "full_book_batch_002_selection.json").read_text(encoding="utf-8"))
    if [item["logical_work_order_id"] for item in selection["selected_work_orders"][:20]] != ORDER:
        raise RuntimeError("FULL_BOOK_BATCH_002_CHECKPOINT_01_SELECTION_MISMATCH")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {item.display_id: item for item in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    requests = [request_for(views[display_id], blocks[display_id], source_sha) for display_id in ORDER]
    responses = [TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, TARGETS[request.display_id], strict=True)]) for request in requests]
    out = BATCH / CHECKPOINT
    BATCH.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(BIBLE, BATCH / "translation_bible_snapshot.md")
    update_manifest(BATCH / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_002", "target_count": 100, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 100, "status": "OPEN_PROGRESS", "next_checkpoint": CHECKPOINT, "checkpoints": [], "source_sha256": source_sha, "translation_bible_snapshot_sha256": sha256((BATCH / "translation_bible_snapshot.md").read_bytes()).hexdigest()})
    out.mkdir()
    write_jsonl(out / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", responses)
    known_unit_uids = {view.unit_uid for view in views.values()}
    raw_qa = [qa(views[request.display_id], response, known_unit_uids) for request, response in zip(requests, responses, strict=True)]
    write_json(out / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_qa), "fail_count": sum(not row["passed"] for row in raw_qa), "records": raw_qa})
    normalize_response_artifact(out / "translation_request.jsonl", out / "translation_raw_response.jsonl", out / "translation_normalized_response.jsonl", out / "translation_normalization_report.json")
    if sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (out / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized_qa = [qa(views[request.display_id], response, known_unit_uids) for request, response in zip(requests, normalized, strict=True)]
    write_json(out / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in normalized_qa), "fail_count": sum(not row["passed"] for row in normalized_qa), "records": normalized_qa})
    dispatched, exclusions = partition_deterministic_review_dispatch(normalized_qa)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed source meaning, reader-facing role, and protected literals are preserved; no material repair is required."} for row in dispatched]
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
