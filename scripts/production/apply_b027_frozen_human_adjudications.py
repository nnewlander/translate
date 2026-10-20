"""Materialize and apply the owner-frozen B027 Human decisions; no model calls."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.effective_source import resolve_effective_source
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair, HumanApprovedPatch, apply_human_adjudication_to_production_checkpoint,
    build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _rescue_working_overrides, _resolve_checkpoint_authoritative_candidate, build_current_review_group,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_027"
BATCH_DIR = RUN / BATCH.lower()
DECISION_PATH = BATCH_DIR / "b027_human_adjudication_decisions_v1.json"

DECISIONS = {
    "artifact_type": "B027_HUMAN_ADJUDICATION_DECISIONS", "schema_version": 2,
    "batch": BATCH, "decision_count": 7,
    "binding_rule": "Human decision is bound only to expected_current_head(s). Apply derives effective source, current group, request binding, QA and Candidate Authority from immutable current evidence.",
    "decisions": [
        {"work_order_id": "body.p.08406", "action": "CORRECT_TARGET", "expected_current_head": "3f10d2f4e4c2f6fb743289e35802703cc581a4ab147deaac6199e20f01240675", "target_segments": ["然而，无服务器架构的主要吸引力并不在大型组织作出选择时，而在较小的组织或团队作出选择时；在这种情况下，这种比较本身就不公平。无服务器模型虽然限制更多，但允许基础设施供应商承担总体管理开销中更大的一部分，从而", "降低", "用户的管理开销。在共享的无服务器架构（如 AWS Lambda 或 Google Cloud Run）上运行一个团队的代码，要比在托管容器服务（如 GKE 或 AKS）上搭建集群来运行代码简单得多（也便宜得多），前提是该集群并未由多个团队共享。如果你的团队希望享受托管计算服务带来的好处，但你所在的更大组织不愿或无法迁移到"]},
        {"work_order_ids": ["body.p.08422", "body.p.08423"], "action": "CORRECT_GROUP", "expected_current_heads": {"body.p.08422": "6b442c1aa73611d8eb76d4209e2e4ff00751fd8ba7f0710c06308c8c84cc9edc", "body.p.08423": "6030c8557ab9b89b77439608bf22913a91efff560dac65f395ce5c90dc1ca686"}, "target_segments_by_work_order": {"body.p.08422": ["对此，一种部分缓解办法是使用基于开源架构（如 Kubernetes）运行的公有云解决方案。"], "body.p.08423": ["这样做是为了确保即使某个基础设施提供商因某种原因变得不可接受，仍然存在一条迁移路径。虽然这能降低很大一部分风险，但并非万无一失。"]}},
        {"work_order_id": "body.p.08516", "action": "CORRECT_TARGET", "expected_current_head": "7c138e351726d77304b237eccac0f9df64ca7380f72a02f1057908f4095f265a", "target_segments": ["种子数据，其中的偏差，", "282"]},
        {"work_order_id": "body.p.08610", "action": "CORRECT_TARGET", "expected_current_head": "9b25e37acc98a1483cf562e8294389e6b8d0767a6d3717eeb4b12e6e0b969eb4", "target_segments": ["针对未预见行为的测试，", "284"]},
        {"work_order_id": "body.p.08611", "action": "CORRECT_TARGET", "expected_current_head": "4ce2f523f3f9514d95677784f07bbe031bba749e7f57900e696da2aaf2648916", "target_segments": ["行为发生变化时对测试的更新，", "234"]},
        {"work_order_id": "body.p.08614", "action": "CORRECT_TARGET", "expected_current_head": "0987994628c42e35acb1bfe90df709e9e21acb6ae47d2f5522e5bbeb0abf837b", "target_segments": ["互动中偏见的细微表现，", "48 ", "偏见普遍存在，", "70"]},
        {"work_order_id": "body.p.08620", "action": "CORRECT_TARGET", "expected_current_head": "c7a9c15c1b5af27f9289b6256a5eb549a498ae1d4e978d21b0713af41e58a66f", "target_segments": ["Boost C++ 库，兼容性承诺，", "435 ", "分支管理，", "336", "-", "339"]},
    ],
}


def _write_decisions() -> dict:
    if DECISION_PATH.is_file():
        existing = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
        if existing != DECISIONS:
            raise RuntimeError("B027_HUMAN_DECISION_ARTIFACT_CONFLICT")
    else:
        write_json_once(DECISION_PATH, DECISIONS)
    reread = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
    heads = sum(len(item.get("expected_current_heads", {})) or (1 if "expected_current_head" in item else 0) for item in reread["decisions"])
    if reread != DECISIONS or reread.get("decision_count") != 7 or heads != 8:
        raise RuntimeError("B027_HUMAN_DECISION_ARTIFACT_INCOMPLETE")
    return reread


def _apply_item(item: dict) -> dict:
    ids = list(item.get("work_order_ids") or [item["work_order_id"]])
    current_id = ids[-1]
    checkpoint = "checkpoint_01" if current_id.startswith("body.p.084") else ("checkpoint_03" if current_id == "body.p.08516" else ("checkpoint_06" if current_id.startswith("body.p.0861") else "checkpoint_07"))
    directory = BATCH_DIR / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    requests = {request.display_id: request for request in loaded["requests"]}
    raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
    effective = {display_id: resolve_effective_source(checkpoint_dir=directory, request=requests[display_id]).request for display_id in ids}
    heads = {display_id: _resolve_checkpoint_authoritative_candidate(directory, display_id, raws[requests[display_id].unit_uid]) for display_id in ids}
    expected = dict(item.get("expected_current_heads") or {current_id: item["expected_current_head"]})
    # A process may stop after append-only evidence has been committed.  Reuse
    # only the exact same frozen decision; a changed decision is a conflict,
    # never a second mutation.
    adjudication_path = directory / "human_adjudications" / f"{current_id}.json"
    if adjudication_path.is_file():
        existing = json.loads(adjudication_path.read_text(encoding="utf-8"))
        expected_patches = item.get("target_segments_by_work_order") or {current_id: item["target_segments"]}
        actual_patches = {(patch["logical_work_order_id"], patch["index"]): patch["target_text"] for patch in existing.get("approved_patches", [])}
        wanted_patches = {(display_id, index): text for display_id, targets in expected_patches.items() for index, text in enumerate(targets)}
        if existing.get("expected_current_heads") != expected or actual_patches != wanted_patches:
            raise RuntimeError(f"B027_HUMAN_DECISION_EXISTING_CONFLICT:{current_id}")
        return {"work_order_ids": ids, "checkpoint": checkpoint, "patched": [], "qa_pass": True, "idempotent_reuse": True}
    if {display_id: heads[display_id].candidate_sha256 for display_id in ids} != expected:
        raise RuntimeError(f"B027_HUMAN_DECISION_CAS_STALE:{current_id}")
    overrides = _rescue_working_overrides(directory, loaded, raws)
    group = build_current_review_group(effective[current_id], loaded=loaded, run=RUN, checkpoint=checkpoint, existing_raw=raws, candidate_overrides=overrides)
    if group is None or list(group.member_work_order_ids) != ids:
        raise RuntimeError(f"B027_HUMAN_CURRENT_GROUP_UNSAFE:{current_id}")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    members = []
    for member in group.members:
        request = effective.get(member.display_id) or requests[member.display_id]
        resolved = _resolve_checkpoint_authoritative_candidate(directory, member.display_id, raws[requests[member.display_id].unit_uid])
        members.append((enrich_request(request, translation_bible=bible), resolved.candidate))
    target_map = item.get("target_segments_by_work_order") or {current_id: item["target_segments"]}
    patches = []
    allowed = []
    for display_id in ids:
        target = target_map[display_id]
        request = effective[display_id]
        if len(target) != len(request.source_segments):
            raise RuntimeError(f"B027_HUMAN_TARGET_SEGMENT_COUNT_INVALID:{display_id}")
        provider = next(provider for provider, _ in members if provider.request.display_id == display_id)
        allowed.append(HumanAllowedRepair(logical_work_order_id=display_id, unit_uid=request.unit_uid, allowed_segment_indices=list(range(len(target))), source_fingerprint=provider.request_binding_sha256))
        patches.extend(HumanApprovedPatch(logical_work_order_id=display_id, unit_uid=request.unit_uid, index=index, target_text=text) for index, text in enumerate(target))
    adjudication = build_human_adjudication(group, decision="HUMAN_APPROVED", reason="Owner-frozen B027 release adjudication.", allowed_repairs=allowed, source_hashes={provider.request.display_id: provider.request_binding_sha256 for provider, _ in members}, original_candidate_hashes={display_id: candidate_sha(raws[requests[display_id].unit_uid]) for display_id in ids}, expected_current_heads=expected, approved_patches=patches)
    result = apply_human_adjudication_to_production_checkpoint(checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"], views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record, adjudication=adjudication, group=group, members=members, translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN", batch_label=BATCH, checkpoint=checkpoint, human_group_transaction_id="b027-08422-08423" if len(ids) > 1 else None)
    return {"work_order_ids": ids, "checkpoint": checkpoint, "patched": result["patched"], "qa_pass": all(row.get("passed") for row in result["qa"].values())}


def main() -> None:
    artifact = _write_decisions()
    report = {"artifact_path": str(DECISION_PATH), "artifact_sha256": sha256(DECISION_PATH.read_bytes()).hexdigest(), "results": [_apply_item(item) for item in artifact["decisions"]], "translation_api_calls": 0, "reviewer_api_calls": 0}
    write_json_once(BATCH_DIR / "b027_human_adjudication_apply_report_v1.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
