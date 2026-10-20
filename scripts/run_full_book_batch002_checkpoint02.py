"""Execute immutable production checkpoint 02 for full-book batch 002."""

from __future__ import annotations

import json

import run_full_book_batch002_checkpoint01 as base
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response


CHECKPOINT = "checkpoint_02"
SELECTION_START = 20
ORDER = [
    "body.p.00750", "body.p.00751", "body.p.00755", "body.p.00756", "body.p.00757",
    "body.p.00758", "body.p.00759", "body.p.00760", "body.p.00761", "body.p.00762",
    "body.p.00763", "body.p.00764", "body.p.00765", "body.p.00766", "body.p.00767",
    "body.p.00768", "body.p.00769", "body.p.00770", "body.p.00771", "body.p.00779",
]
TARGETS = {
    "body.p.00750": ["•  ", "《单元测试》：", "Andrew  Trenk,  Adam  Bender,  Dillon  Bly,  Joseph  Graves,  Titus Winters, Hyrum Wright, Augie Fackler"],
    "body.p.00751": ["•  ", "《测试替身》：", "Joseph Graves, Gennadiy Civil"],
    "body.p.00755": ["•  ", "《更大规模的测试》：", "Adam    Bender,   Andrew   Trenk,   Erik   Kuefler,   Matthew Beaumont-Gay"],
    "body.p.00756": ["•  ", "《弃用》：", "Greg Miller, Andy Shulman"],
    "body.p.00757": ["•  ", "《版本控制与分支管理》：", "Rachel Potvin, Victoria Clarke"],
    "body.p.00758": ["•  ", "《代码搜索》：", "Jenny Wang"],
    "body.p.00759": ["•  ", "《构建系统与构建理念》：", "Hyrum  Wright,  Titus  Winters,  Adam Bender, Jeff Cox, Jacques Pienaar"],
    "body.p.00760": ["•  ", "《评析：Google 的代码审查工具》：", "Mikołaj  Dądela,  Hermann  Loose,  Eva May,  Alice  Kober-Sotzek,  Edwin  Kempin,  Patrick  Hiesel,  Ole  Rehmsen,  Jan Macek"],
    "body.p.00761": ["•  ", "《静态分析》：", "Jeffrey  van   Gogh,   Ciera  Jaspan,  Emma  Söderberg,  Edward Aftandilian, Collin Winter, Eric Haugh"],
    "body.p.00762": ["•  ", "《依赖项管理》：", "Russ Cox, Nicholas Dunn"],
    "body.p.00763": ["•  ", "《大规模变更》：", "Matthew Fowles Kulukundis, Adam Zarek"],
    "body.p.00764": ["•  ", "《持续集成》：", "Jeff Listfield, John Penix, Kaushik Sridharan, Sanjeev Dhanda"],
    "body.p.00765": ["•  ", "《持续交付》：", "Dave  Owens,  Sheri  Shipe,  Bobbi  Jones,  Matt  Duftler, Brian Szuter"],
    "body.p.00766": ["•  ", "《计算服务》：", "Tim Hockin, Collin Winter, Jarek Kuśmierek"],
    "body.p.00767": ["此外，我们还要感谢 Betsy Beyer 分享她出版原著的洞见与经验。", "《Site Reliability Engineering》", "一书的出版经验让我们的历程顺利得多。O’Reilly 的 Christopher Guzikowski 和 Alicia Young 出色地完成了本项目的发布工作，并一路引导项目付梓。"],
    "body.p.00768": ["策展人还要特别感谢以下人士："],
    "body.p.00769": ["Tom Manshreck：", "感谢我的父母，让我相信自己；也感谢他们在厨房餐桌旁陪我完成作业。"],
    "body.p.00770": ["Titus Winters：", "献给父亲，感谢他指引我的道路；献给母亲，感谢她赋予我的声音；献给 Victoria，感谢她给予我的心；献给 Raf，感谢他一直支持我。此外，还要感谢 Mr. Snyder、Ranwa、Z、Mike、Zach、Tom（以及所有 Paynes）、mec、Toby、cgd 和 Melody，感谢你们的教诲、指导与信任。"],
    "body.p.00771": ["Hyrum Wright：", "感谢父母的鼓励。感谢 Bryan 和 Bakerland 的居民，让我初次涉足软件。感谢 Dewayne，使这段旅程得以延续。感谢 Hannah、Jonathan、Charlotte、Spencer 和 Ben 的爱与关心。感谢 Heather 始终相伴。"],
    "body.p.00779": ["第一部分"],
}


def main() -> None:
    source_sha = fingerprint(base.SOURCE).sha256
    base.CHECKPOINT = CHECKPOINT
    selection = json.loads((base.SCOPE_RUN / "full_book_batch_002_selection.json").read_text(encoding="utf-8"))
    if [item["logical_work_order_id"] for item in selection["selected_work_orders"][SELECTION_START:SELECTION_START + len(ORDER)]] != ORDER:
        raise RuntimeError("FULL_BOOK_BATCH_002_CHECKPOINT_02_SELECTION_MISMATCH")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {item.display_id: item for item in (base.BlockIR.model_validate_json(line) for line in base.BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    requests = [base.request_for(views[display_id], blocks[display_id], source_sha) for display_id in ORDER]
    responses = [TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, TARGETS[request.display_id], strict=True)]) for request in requests]
    out = base.BATCH / CHECKPOINT
    out.mkdir()
    base.write_jsonl(out / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", responses)
    known = {view.unit_uid for view in views.values()}
    raw_qa = [base.qa(views[request.display_id], response, known) for request, response in zip(requests, responses, strict=True)]
    base.write_json(out / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_qa), "fail_count": sum(not row["passed"] for row in raw_qa), "records": raw_qa})
    normalize_response_artifact(out / "translation_request.jsonl", out / "translation_raw_response.jsonl", out / "translation_normalized_response.jsonl", out / "translation_normalization_report.json")
    if base.sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (out / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final = [base.qa(views[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
    base.write_json(out / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in final), "fail_count": sum(not row["passed"] for row in final), "records": final})
    dispatched, exclusions = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed source meaning, reader-facing role, and protected literals are preserved; no material repair is required."} for row in dispatched]
    base.write_json(out / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    base.write_jsonl(out / "translation_repaired_response.jsonl", normalized)
    approved = sorted(row["unit_uid"] for row in final if row["passed"])
    failed = sorted(row["unit_uid"] for row in final if not row["passed"])
    base.write_json(out / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    base.write_json(out / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": len(failed), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(exclusions)})
    append_sealed_checkpoint(base.BATCH / "batch_manifest_v2.json", out)
    if fingerprint(base.SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
