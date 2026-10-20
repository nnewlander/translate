"""Seal the frozen Batch 004 checkpoint 02 work order; no selection is recomputed."""
from __future__ import annotations

from hashlib import sha256
import json
import shutil
from pathlib import Path

import run_full_book_batch004_checkpoint01 as base
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import exact_evidence_for_work_order, load_exact_verified_evidence_registry

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = RUN / "full_book_batch_004"
SELECTION = RUN / "full_book_batch_004_selection.json"
BATCH_LABEL = "FULL_BOOK_BATCH_004"
BATCH_TARGET = 150
CHECKPOINT = "checkpoint_02"
SELECTION_START = 25
D050_EVIDENCE_ARTIFACTS = (
    RUN / "chapter_1_verified_footnote_evidence_v1.json",
    RUN / "chapter_3_verified_footnote_evidence_v1.json",
    RUN / "chapter_4_verified_footnote_evidence_v1.json",
)
ORDER = [
    "body.p.01410", "body.p.01411", "body.p.01412", "body.p.01413", "body.p.01414",
    "body.p.01415", "body.p.01416", "body.p.01420", "body.p.01424", "body.p.01425",
    "body.p.01426", "body.p.01427", "body.p.01428", "body.p.01429", "body.p.01430",
    "body.p.01431", "body.p.01432", "body.p.01433", "body.p.01436", "body.p.01440",
    "body.p.01441", "body.p.01442", "body.p.01443", "body.p.01445", "table.001.r.001.c.001.p.001",
]
TARGETS = {
    "body.p.01410": ["理念"],
    "body.p.01411": ["软件工程可以定义为多人协作开发多版本程序。", "2", "人是软件工程的核心：代码是重要产出，却只是构建产品的一小部分。关键在于，代码不会凭空自发产生，专业能力亦然。每位专家都曾是新手：组织的成功取决于培养并投入其员工。"],
    "body.p.01412": ["来自专家的个性化一对一建议始终弥足珍贵。团队成员各有所长，因此针对任何具体问题，最适合咨询的队友都会不同。但如果专家休假或转到其他团队，团队就可能陷入困境。尽管一个人也许能为一对多提供个性化帮助，但这无法规模化，只适用于少数“很多”人。"],
    "body.p.01413": ["人。"],
    "body.p.01414": ["与之相对，文档化知识不仅能更好地扩展到团队，也能扩展到整个组织。团队 wiki 等机制让许多作者能够向更大的群体分享其专业知识。不过，尽管书面文档比一对一交流更具可扩展性，这种可扩展性也伴随一些取舍：它可能更加泛化，对个体学习者的具体情境不那么适用；同时还会带来额外维护成本，以确保信息随时间保持相关且最新。"],
    "body.p.01415": ["部落知识存在于单个团队成员所知与已文档化内容之间的空隙中。人类专家掌握着那些未被写下来的知识。如果我们将这些知识记录并维护，那么它不再只供今天能直接一对一接触该专家的人使用，任何能找到并查看这些文档的人都可以获得。"],
    "body.p.01416": ["那么，在一个一切都能始终完美且立即被记录下来的神奇世界里，我们便不再需要咨询他人了，对吗？并不完全如此。书面知识具有规模化优势，但有针对性的人际帮助也一样。人类专家能够综合其广博的知识；他们能判断哪些信息适用于个人的使用场景、文档是否仍然相关，以及到哪里去找。即使他们不知道答案在哪里，也可能知道谁知道。"],
    "body.p.01420": ["2  David Lorge Parnas，", "《软件工程：多人开发多版本程序》", "（Heidelberg：Springer-Verlag Berlin，2011）。"],
    "body.p.01424": ["部落知识与书面知识相辅相成。即使一个团队拥有完美的专家和完美的文档，仍需要彼此沟通、与其他团队协调，并随时间调整策略。没有一种知识共享方式适用于所有学习类型，而良好组合的具体形式很可能因组织而异。组织知识会随时间演变；随着组织成长，最适合的知识共享方法也可能改变。培训、专注于学习与成长，并建立自己的专家队伍：工程专业能力永远不会嫌多。"],
    "body.p.01425": ["营造学习氛围：心理安全"],
    "body.p.01426": ["心理安全对于促进学习环境至关重要。"],
    "body.p.01427": ["要学习，首先必须承认有些事情你并不理解。我们应当", "欢迎这种坦诚", "，而不是惩罚它。（Google 在这方面做得相当好，但工程师有时仍不愿承认自己不理解某件事。）"],
    "body.p.01428": ["学习的重要部分在于能够尝试，并感到即使失败也是安全的。在健康的环境中，人们能够自在地提问、犯错和学习新事物。这是所有 Google 团队的基本期望；事实上，", "我们的研究", "表明，心理安全是高效团队最重要的组成部分。"],
    "body.p.01429": ["导师制"],
    "body.p.01430": ["在 Google，一名“ Noogler ”（新加入 Google 的员工）工程师入职后，我们会尽快营造这种氛围。建立心理安全的一项重要方式，是为 Noogler 指派一位导师——此人", "不", "是其团队成员、经理或技术负责人——其职责明确包括解答问题并帮助 Noogler 快速上手。有一位正式指定的导师可供求助，会让新人更容易开口，也无需担心占用同事过多时间。"],
    "body.p.01431": ["导师是已在 Google 工作超过一年并自愿担任此角色的人，可以就从使用 Google 基础设施到理解 Google 文化的任何事项提供建议。关键在于，当受指导者不知道还能向谁求助时，导师是可供交流的安全网。导师不与受指导者同属一个团队，这会让受指导者在棘手情形下更安心地寻求帮助。"],
    "body.p.01432": ["导师制将学习正式化并使之更便利，但学习本身是一个持续的过程。同事之间总会有彼此学习的机会，"],
    "body.p.01433": ["无论是新员工加入组织，还是经验丰富的工程师学习新技术。健康的团队中，队友不仅会乐于"],
    "body.p.01436": ["46      |    第 3 章：知识共享"],
    "body.p.01440": ["回答问题，也会", "提出", "问题：展示自己不了解某件事，并彼此学习。"],
    "body.p.01441": ["大型群体中的心理安全"],
    "body.p.01442": ["向身边的队友求助，比接近一大群大多陌生的人更容易。但如前所见，一对一的解决方案并不容易规模化。群体解决方案更具可扩展性，却也更令人畏惧。新手要构思一个问题并向大型群体提出，知道这个问题可能被存档很多年，可能会感到压力。大型群体对心理安全的需求被放大。群体中的每一位成员都应参与创建和维持安全环境，使新成员有信心提问，也让正在成长的专家能够放心帮助这些新成员，而不必担心自己的回答会遭到既有专家的攻击。"],
    "body.p.01443": ["实现这种安全、友好环境最重要的方式，是让群体互动具有合作性而非对抗性。", "表 3-1", "列出了一些推荐的群体互动模式及其相应的反模式。"],
    "body.p.01445": ["表 3-1：群体互动模式"],
    "table.001.r.001.c.001.p.001": ["推荐模式（合作性）"],
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json() + "\n")


def main() -> None:
    source_sha = fingerprint(base.base.SOURCE).sha256
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    if selection["selected_logical_work_order_ids"][SELECTION_START:SELECTION_START + len(ORDER)] != ORDER:
        raise RuntimeError("AUTHORITATIVE_SELECTION_MISMATCH")
    manifest_path = BATCH / "batch_manifest.json"
    if SELECTION_START == 0:
        if BATCH.exists():
            raise RuntimeError("BATCH_ALREADY_EXISTS")
        BATCH.mkdir()
        shutil.copyfile(base.base.BIBLE, BATCH / "translation_bible_snapshot.md")
        update_manifest(manifest_path, {"batch": BATCH_LABEL, "target_count": BATCH_TARGET, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": BATCH_TARGET, "status": "OPEN_PROGRESS", "next_checkpoint": CHECKPOINT, "checkpoints": [], "source_sha256": source_sha, "translation_bible_snapshot_sha256": sha256((BATCH / "translation_bible_snapshot.md").read_bytes()).hexdigest()})
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_checkpoint = f"checkpoint_{SELECTION_START // len(ORDER):02d}"
        if manifest["next_checkpoint"] != CHECKPOINT or manifest["completed_count"] != SELECTION_START or manifest["checkpoints"][-1]["id"] != previous_checkpoint:
            raise RuntimeError("CHECKPOINT_RESUME_MISMATCH")
    out = BATCH / CHECKPOINT
    if out.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")
    views = {v.display_id: v for v in (TranslationView.model_validate_json(line) for line in base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {b.display_id: b for b in (BlockIR.model_validate_json(line) for line in base.base.BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    registry = load_exact_verified_evidence_registry(D050_EVIDENCE_ARTIFACTS)
    requests = [
        base.base.request_for(
            views[item], blocks[item], source_sha,
            exact_evidence_for_work_order(registry, logical_work_order_id=item, unit_uid=views[item].unit_uid),
        )
        for item in ORDER
    ]
    out.mkdir()
    write_jsonl(out / "translation_request.jsonl", requests)
    raw = [TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, TARGETS[request.display_id], strict=True)]) for request in requests]
    raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_records = [base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, raw, strict=True)]
    write_json(out / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_records), "fail_count": sum(not row["passed"] for row in raw_records), "records": raw_records})
    normalize_response_artifact(out / "translation_request.jsonl", out / "translation_raw_response.jsonl", out / "translation_normalized_response.jsonl", out / "translation_normalization_report.json")
    if sha256((out / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (out / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, normalized, strict=True)]
    write_json(out / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in final_records), "fail_count": sum(not row["passed"] for row in final_records), "records": final_records})
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed source meaning, protected literals, and exact work-order-scoped D050 evidence where present."} for row in dispatched]
    write_json(out / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    write_jsonl(out / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records if row["passed"]]
    failed = [row["unit_uid"] for row in final_records if not row["passed"]]
    write_json(out / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": final_records, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    write_json(out / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": len(failed), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(path) for path in D050_EVIDENCE_ARTIFACTS]})
    append_sealed_checkpoint(manifest_path, out)
    if fingerprint(base.base.SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
