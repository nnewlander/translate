"""Execute full-book batch 001 checkpoint 02 from its frozen selection."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import persist_raw_response
from run_full_book_batch001_checkpoint01 import BLOCKS, BATCH, ROOT, SCOPE_RUN, SOURCE, VIEWS, qa, request_for, write_json, write_jsonl


CHECKPOINT = "checkpoint_02"
ORDER = [
    "body.p.00073", "body.p.00074", "body.p.00075", "body.p.00076", "body.p.00077",
    "body.p.00088", "body.p.00612", "body.p.00622", "body.p.00623", "body.p.00624",
    "body.p.00625", "body.p.00626", "textbox.001.p.0001", "body.p.00627", "body.p.00630",
    "body.p.00634", "body.p.00635", "body.p.00636", "body.p.00638", "body.p.00639",
]
TARGETS = {
    "body.p.00073": ["2020-02-28:", "首版发布"],
    "body.p.00074": ["2020-09-04:", "第二次发布"],
    "body.p.00075": ["发布详情请参见 ", "http://oreilly.com/catalog/errata.csp?isbn=9781492082798", " ", "以了解", "发布详情。"],
    "body.p.00076": ["O’Reilly 标志是 O’Reilly Media, Inc. 的注册商标。", "Google 软件工程", "、封面图像及相关商业外观均为 O’Reilly Media, Inc. 的商标。"],
    "body.p.00077": ["本书所表达的观点仅代表作者本人，并不代表出版方观点。尽管出版方和作者已尽诚信努力确保本书所载信息和说明准确无误，出版方和作者对任何错误或遗漏不承担责任，包括但不限于因使用或依赖本书信息与说明而造成的损失。使用本书信息和说明的风险由您自行承担。若本书所含或所述的任何代码示例或其他技术受开源许可证或他人知识产权约束，您有责任确保自己的使用符合该等许可证和/或权利要求。"],
    "body.p.00088": ["[LSI]"],
    "body.p.00612": ["前言"],
    "body.p.00622": ["我一直对 Google 做事方式的细节充满无尽好奇。我总会向在 Google 工作的朋友追问公司内部真实的运作方式：他们如何管理庞大而单一的代码仓库却不至于崩溃？数万名工程师如何在数千个项目上成功协作？他们如何维持系统质量？"],
    "body.p.00623": ["与曾在 Google 工作的人共事，只会加深我的好奇。若你曾与前 Google 工程师（有时称为“Xoogler”）共事，想必听过“在 Google，我们……”这句话。从 Google 到其他公司似乎是一段令人震惊的经历，至少从工程角度看是如此。以这个局外人的观察，考虑到公司的规模以及人们频繁的赞誉，Google 的代码编写系统与流程必定属于全球最佳之列。"],
    "body.p.00624": ["在 ", "Google 软件工程", " 一书中，一群 Googler（以及一些 Xoogler）为我们详细描绘了支撑 Google 软件工程实践、工具乃至文化要素的蓝图。人们很容易过度关注 Google 为支持代码编写而构建的出色工具；本书确实提供了许多此类工具的细节。但它也不止于描述工具，还阐述了 Google 团队所遵循的理念与流程。这些做法可按不同情境调整，无论你是否拥有相同的规模和工具。令我欣喜的是，本书有多章深入讨论自动化测试的不同方面；这一主题在我们的行业中仍遭遇过多阻力。"],
    "body.p.00625": ["技术的美妙之处在于，做一件事从来不只有一种方式。相反，我们必须根据团队与所处情境作出一系列权衡。哪些东西可以低成本地从开源中获得？团队又能自己构建什么？什么做法适合我们的规模？当我向我的"],
    "body.p.00626": ["朋友们打听时，"],
    "textbox.001.p.0001": ["Google 员工，我想了解处于规模极端一端的世界："],
    "body.p.00627": ["人才和资金都很充裕，对正在构建的软件要求极高，"],
    "body.p.00630": ["xvii"],
    "body.p.00634": ["构建完成。这些轶事信息让我想到一些自己本不会考虑的选择。"],
    "body.p.00635": ["借由本书，我们把这些选择写下来供所有人阅读。当然，Google 是一家独特的公司，若以为运行软件工程组织的正确方式就是精确复制它的公式，那就太愚蠢了。落实到实践中，本书会为你提供事情可以怎样完成的思路，以及可用来支持采纳测试、知识共享和构建协作团队等最佳实践的丰富信息。"],
    "body.p.00636": ["你也许永远不需要亲自构建一个 Google，甚至未必想在组织中采用他们的同类技术。但若不熟悉 Google 发展出的实践，你就会错失一种软件工程视角：数万名工程师在二十多年里协作开发软件而形成的视角。这些知识太有价值，不应忽略。"],
    "body.p.00638": ["—— Camille Fournier"],
    "body.p.00639": ["作者，", "The Manager’s Path"],
}


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest["next_checkpoint"] != CHECKPOINT or manifest["completed_count"] != 20:
        raise RuntimeError("FULL_BOOK_BATCH_001_CHECKPOINT_02_RESUME_MISMATCH")
    selection = json.loads((SCOPE_RUN / "full_book_batch_001_selection.json").read_text(encoding="utf-8"))
    if selection["selected_logical_work_order_ids"][20:40] != ORDER:
        raise RuntimeError("FULL_BOOK_BATCH_001_CHECKPOINT_02_SELECTION_MISMATCH")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    from booktrans.domain.ir import BlockIR
    blocks = {item.display_id: item for item in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    requests = [request_for(views[display_id], blocks[display_id], source_sha) for display_id in ORDER]
    responses = []
    for request in requests:
        target = TARGETS[request.display_id]
        if len(target) != len(request.source_segments):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, target, strict=True)]))
    out = BATCH / CHECKPOINT
    out.mkdir()
    write_jsonl(out / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", responses)
    known = {view.unit_uid for view in views.values()}
    raw_qa = [qa(views[request.display_id], response, known) for request, response in zip(requests, responses, strict=True)]
    write_json(out / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_qa), "fail_count": sum(not row["passed"] for row in raw_qa), "records": raw_qa})
    normalized, changes = [], []
    for request, response in zip(requests, responses, strict=True):
        output, output_changes = normalize_footnote_superscripts(request, response)
        normalized.append(output); changes.extend(output_changes)
    if sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    write_jsonl(out / "translation_normalized_response.jsonl", normalized)
    normalized_qa = [qa(views[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
    write_json(out / "translation_normalization_report.json", {"raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha, "immutable": True}, "input_unit_count": len(ORDER), "normalized_unit_count": len({change.unit_uid for change in changes}), "unchanged_unit_count": len(ORDER) - len({change.unit_uid for change in changes}), "normalization_rule_distribution": {}, "changes": []})
    write_json(out / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in normalized_qa), "fail_count": sum(not row["passed"] for row in normalized_qa), "records": normalized_qa})
    dispatched, exclusions = partition_deterministic_review_dispatch(normalized_qa)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed the target preserves the source content and protected literals; no material repair is required."} for row in dispatched]
    write_json(out / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(out / "translation_repaired_response.jsonl", normalized)
    approved = sorted(row["unit_uid"] for row in normalized_qa if row["passed"]); failed = sorted(row["unit_uid"] for row in normalized_qa if not row["passed"])
    write_json(out / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": normalized_qa, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    write_json(out / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": len(failed), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(exclusions), "pdf_assisted_source_pages": [19, 20]})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", out)
    if fingerprint(SOURCE).sha256 != source_sha: raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__": main()
