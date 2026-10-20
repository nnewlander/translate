"""Apply the owner-supplied B025 checkpoint-07 human decisions; no provider calls."""

from __future__ import annotations

import json
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
from booktrans.production_worker.unit_store import load_raw_by_uid
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_025"
CHECKPOINT = "checkpoint_07"
TARGETS = {
    "body.p.08011": {
        "head": "d419c27eb3a1b6a9954d1836cc7cbf5b4d2d75a1f2d3ea99e146d82cbb88015f",
        "request_binding": "829839c221f7fc4d09c2d3f81b67d74b123caea322236b22a767de978fec2039",
        "group": "srg:body.p.08011:fbab5fa0c72d8f5e",
        "group_hash": "fbab5fa0c72d8f5e58ade1eeec7ad196dd474a9952f302469e76a153ec1d3540",
        "context_hash": "7d54ca246bc1f1852c4e1539a4c5b22b4063d5b9521216120c37ad89796236ed",
        "index": 4,
        "text": "。这意味着同时向用户发布产品的两个版本：一个是希望推出的更新版本，另一个作为基线的版本则相当于安慰剂（也就是再次发布旧版本）。当这两个版本同时向足够多且特征相似的用户群体推出时，就可以将一个版本与另一个版本进行比较，以判断软件的最新版本是否确实优于前一版本。如果用户基数足够大，通常可以在几天甚至几小时内获得具有统计显著性的结果。自动化指标流水线可以在有足够数据确认护栏指标不会受到影响后，立即将该版本扩大到更多流量，从而实现尽可能快的发布。",
    },
    "body.p.08019": {
        "head": "6a9f03766dd7b41bcf881f6c4e26f9b56e271b947d0f48fe384cef5a09f2bd55",
        "request_binding": "743ba3f587505ee6b47676ae165d26fe4afa557f3c76ec46bc7dd929da653162",
        "group": "srg:body.p.08019:ac87e80d27b2fcc6",
        "group_hash": "ac87e80d27b2fcc6e5da72519bbe69af258b87c6964da31ec126d391ffa7d030",
        "context_hash": "f808b36dd5e6c8846b0395c82c8ddade0009450f8e85526fa3ccbc1bd4c93f8f",
        "index": 0,
        "text": "2  丹·西罗克和皮特·库门，",
    },
}


def main() -> None:
    checkpoint_dir = RUN / "full_book_batch_025" / CHECKPOINT
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=CHECKPOINT)
    raws = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
    request_by_id = {request.display_id: request for request in loaded["requests"]}
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    report = []
    for display_id, target in TARGETS.items():
        request = request_by_id[display_id]
        raw = raws[request.unit_uid]
        head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raw)
        if head.candidate_sha256 != target["head"] or request.source_sha256 != loaded["source_sha256"]:
            raise RuntimeError(f"HUMAN_BINDING_CURRENT_HEAD_MISMATCH:{display_id}")
        overrides = {display_id: head.candidate, request.unit_uid: head.candidate}
        group = _production_review_group(
            request, loaded=loaded, run=RUN, checkpoint=CHECKPOINT,
            existing_raw=raws, candidate_overrides=overrides,
        )
        if group is None or group.semantic_review_group_id != target["group"] or group.group_hash != target["group_hash"]:
            raise RuntimeError(f"HUMAN_BINDING_GROUP_MISMATCH:{display_id}")
        members = []
        for member in group.members:
            member_request = request_by_id[member.display_id]
            member_head = _resolve_checkpoint_authoritative_candidate(
                checkpoint_dir, member.display_id, raws[member_request.unit_uid]
            )
            members.append((enrich_request(member_request, translation_bible=bible), member_head.candidate))
        provider_request = next(item[0] for item in members if item[0].request.display_id == display_id)
        if provider_request.request_binding_sha256 != target["request_binding"]:
            raise RuntimeError(f"HUMAN_BINDING_REQUEST_MISMATCH:{display_id}")
        rereview = json.loads((checkpoint_dir / "semantic_rescue_rereviews" / f"{display_id}.json").read_text(encoding="utf-8"))
        if rereview.get("context_sha256") != target["context_hash"] or rereview.get("candidate_binding_sha256") != head.candidate_sha256:
            raise RuntimeError(f"HUMAN_BINDING_REREVIEW_MISMATCH:{display_id}")
        allowed = [HumanAllowedRepair(
            logical_work_order_id=display_id,
            unit_uid=request.unit_uid,
            allowed_segment_indices=[target["index"]],
            source_fingerprint=provider_request.request_binding_sha256,
        )]
        adjudication = build_human_adjudication(
            group,
            decision="HUMAN_APPROVED",
            reason="Owner-approved checkpoint-07 semantic correction.",
            allowed_repairs=allowed,
            source_hashes={item[0].request.display_id: item[0].request_binding_sha256 for item in members},
            original_candidate_hashes={item[0].request.display_id: candidate_sha(raws[item[0].request.unit_uid]) for item in members},
            approved_patches=[HumanApprovedPatch(
                logical_work_order_id=display_id,
                unit_uid=request.unit_uid,
                index=target["index"],
                target_text=target["text"],
            )],
        )
        result = apply_human_adjudication_to_production_checkpoint(
            checkpoint_dir=checkpoint_dir, production_run=RUN,
            requests=loaded["requests"], views=loaded["views"],
            known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
            adjudication=adjudication, group=group, members=members,
            translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
            batch_label=BATCH, checkpoint=CHECKPOINT,
        )
        report.append({"work_order": display_id, "result": result})
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
