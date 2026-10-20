"""Apply the owner-approved B026 checkpoint-04 group adjudication without providers."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair,
    HumanApprovedPatch,
    apply_human_adjudication_to_production_checkpoint,
    build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _production_review_group,
    _resolve_checkpoint_authoritative_candidate,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, persist_stage_once
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
BATCH = "FULL_BOOK_BATCH_026"
CHECKPOINT = "checkpoint_04"
CURRENT = "body.p.08236"
EXPECTED_GROUP = "srg:body.p.08236:d28d7403a55ae260"
EXPECTED_GROUP_HASH = "d28d7403a55ae2601c17994cd4c602ca03251abd224f5ae56489aec6498f3966"
EXPECTED_HEADS = {
    "body.p.08235": "d73ae25ba25e1a0957d8fcd2a60f417f42400a172d52b38fbf0f872cd2c3541e",
    "body.p.08236": "e433fb48690dc9a71cd37ec9d43c7a48d6d89985f058192d08241337e8587c28",
}
PATCHES = {
    "body.p.08235": "但如果他们希望自动化地对过去一年该服务产生的 1 TB 日志进行筛选（出于类似目的），那么等待大约一天才能得到结果显然是不可接受的。一种计算服务能够让工程师在分布式环境中仅用几分钟就完成分析（利用数百个核心），这就意味着，要么立即获得分析结果，要么要等到明天。对于需要反复迭代的任务——例如，在查看结果后还需要进一步优化查询——这种差异甚至可能意味着：要么在一天内完成，",
    "body.p.08236": "要么根本无法完成。",
}
MISSING_SOURCE = "and not having it done at all."


def main() -> None:
    checkpoint_dir = RUN / "full_book_batch_026" / CHECKPOINT
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=CHECKPOINT)
    requests = loaded["requests"]
    request_by_id = {request.display_id: request for request in requests}
    raws = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None

    heads = {
        display_id: _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, display_id, raws[request_by_id[display_id].unit_uid]
        )
        for display_id in EXPECTED_HEADS
    }
    if {display_id: head.candidate_sha256 for display_id, head in heads.items()} != EXPECTED_HEADS:
        raise RuntimeError("HUMAN_GROUP_CURRENT_HEAD_MISMATCH")
    group = _production_review_group(
        request_by_id[CURRENT], loaded=loaded, run=RUN, checkpoint=CHECKPOINT,
        existing_raw=raws,
        candidate_overrides={
            key: head.candidate for key, head in heads.items()
        } | {
            request_by_id[key].unit_uid: head.candidate for key, head in heads.items()
        },
    )
    if (
        group is None
        or group.semantic_review_group_id != EXPECTED_GROUP
        or group.group_hash != EXPECTED_GROUP_HASH
        or list(group.member_work_order_ids) != list(EXPECTED_HEADS)
        or set(group.repairable_work_order_ids) != set(EXPECTED_HEADS)
    ):
        raise RuntimeError("HUMAN_GROUP_BINDING_MISMATCH")
    members = [
        (enrich_request(request_by_id[member.display_id], translation_bible=bible), heads[member.display_id].candidate)
        for member in group.members
    ]
    evidence = {
        "artifact_type": "SOURCE_EXTRACTION_EXACT_EVIDENCE",
        "schema_version": 1,
        "display_id": CURRENT,
        "semantic_review_group_id": group.semantic_review_group_id,
        "semantic_group_hash": group.group_hash,
        "source_sha256": sha256(SOURCE.read_bytes()).hexdigest(),
        "ooxml_location": {
            "part": "word/document.xml",
            "paragraph_index": 8746,
            "run_text": "having",
            "vml_textbox_text": "and not it done at all.",
            "visual_reading_order": "having and not it done at all.",
        },
        "pdf_proof": {"path": str(SOURCE.with_suffix('.pdf')), "page": 529, "exact_sentence_suffix": "having it done in a day and not having it done at all."},
        "extracted_work_order_gap": {"body.p.08235": "ends at '...in a day'", "body.p.08236": "having"},
        "missing_source_span": MISSING_SOURCE,
        "verification": "DOCX_OOXML_AND_PDF_NATIVE_EXACT",
        "translation_api_calls": 0,
    }
    persist_stage_once(checkpoint_dir, "source_extraction_exact_evidence", CURRENT, evidence)

    allowed = [
        HumanAllowedRepair(
            logical_work_order_id=display_id,
            unit_uid=request_by_id[display_id].unit_uid,
            allowed_segment_indices=[0],
            source_fingerprint=next(item[0].request_binding_sha256 for item in members if item[0].request.display_id == display_id),
        )
        for display_id in EXPECTED_HEADS
    ]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason=("Owner-approved exact group correction. Local immutable DOCX OOXML and PDF page 529 "
                f"uniquely verify the omitted source continuation: {MISSING_SOURCE}"),
        allowed_repairs=allowed,
        source_hashes={item[0].request.display_id: item[0].request_binding_sha256 for item in members},
        original_candidate_hashes={display_id: candidate_sha(raws[request_by_id[display_id].unit_uid]) for display_id in EXPECTED_HEADS},
        approved_patches=[
            HumanApprovedPatch(logical_work_order_id=display_id, unit_uid=request_by_id[display_id].unit_uid, index=0, target_text=text)
            for display_id, text in PATCHES.items()
        ],
        approved_group_text=" ".join(PATCHES[member.display_id] for member in group.members),
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=checkpoint_dir, production_run=RUN, requests=requests,
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
        adjudication=adjudication, group=group, members=members, translation_bible=bible,
        triage_classification="TRUE_SEMANTIC_HUMAN", batch_label=BATCH, checkpoint=CHECKPOINT,
    )
    print(json.dumps({"evidence": evidence, "result": result}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
