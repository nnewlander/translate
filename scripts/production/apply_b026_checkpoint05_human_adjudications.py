"""Apply the owner-approved B026 checkpoint_05 decisions without providers.

Both decisions are bound to the current Candidate Authority head before any
append-only human evidence is written.  RAW and historical QA remain untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair, HumanApprovedPatch,
    apply_human_adjudication_to_production_checkpoint, build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _candidate_binding, _production_review_group,
    _qa_with_d051, _resolve_checkpoint_authoritative_candidate,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, persist_stage_once
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_026"
CHECKPOINT = "checkpoint_05"
CHECKPOINT_DIR = RUN / BATCH.lower() / CHECKPOINT
HEAD_08263 = "2ee03faab24bd083845c3dc769f121d53fb9550d1fa22e80dbe12120c5b27662"
HEAD_08277 = "4d384e1522a83c6a6d8772bbc90eb4ed06b7ae38b9ee86f5f148141f1a66a1e4"
GROUP_08277 = "srg:body.p.08277:fa2b44b1fa40b4e8"
GROUP_08277_HASH = "fa2b44b1fa40b4e87af121429b6c6dc9b837f9991b3aad719b29af07b4349ae6"
TARGET_08277 = "这些经验教训已被纳入其中。"


def _texts(candidate):
    return [segment.text for segment in candidate.target_segments]


def _numeric_equivalence(request, candidate):
    evidence = ExplicitNumericRepresentationEquivalence(
        unit_uid=request.unit_uid,
        exact_source_span="more than 20,000 usages",
        source_numeric_tokens=["20000"],
        approved_target_representation="调用次数超过两万次",
        reviewed_source_semantic_numeric_value="20000",
        reviewed_target_semantic_numeric_value="20000",
        semantic_unit="USAGES",
        source_comparison_operator="MORE_THAN",
        target_comparison_operator="MORE_THAN",
        source_counter_context="USAGES",
        target_counter_context="USAGES",
    )
    if request.source_text.count(evidence.exact_source_span) != 1:
        raise RuntimeError("B026_HUMAN_08263_SOURCE_NUMERIC_BINDING_MISMATCH")
    if evidence.approved_target_representation not in "".join(_texts(candidate)):
        raise RuntimeError("B026_HUMAN_08263_TARGET_NUMERIC_BINDING_MISMATCH")
    return evidence


def _members_for_group(loaded, raws, current_id, overrides):
    requests = {request.display_id: request for request in loaded["requests"]}
    request = requests[current_id]
    group = _production_review_group(
        request, loaded=loaded, run=RUN, checkpoint=CHECKPOINT,
        existing_raw=raws, candidate_overrides=overrides,
    )
    if group is None:
        raise RuntimeError(f"B026_HUMAN_GROUP_MISSING:{current_id}")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    members = []
    for member in group.members:
        member_request = requests.get(member.display_id)
        if member_request is None:
            raise RuntimeError(f"B026_HUMAN_GROUP_MEMBER_MISSING:{member.display_id}")
        head = overrides.get(member.display_id)
        if head is None:
            head = _resolve_checkpoint_authoritative_candidate(CHECKPOINT_DIR, member.display_id, raws[member_request.unit_uid]).candidate
        members.append((enrich_request(member_request, translation_bible=bible), head))
    return requests, group, members, bible


def _apply(*, loaded, raws, display_id, expected_head, changes, numeric=None, dry_run):
    request = {item.display_id: item for item in loaded["requests"]}[display_id]
    head = _resolve_checkpoint_authoritative_candidate(CHECKPOINT_DIR, display_id, raws[request.unit_uid])
    if head.candidate_sha256 != expected_head:
        raise RuntimeError(f"B026_HUMAN_CURRENT_HEAD_MISMATCH:{display_id}")
    overrides = {display_id: head.candidate, request.unit_uid: head.candidate}
    requests, group, members, bible = _members_for_group(loaded, raws, display_id, overrides)
    provider = next(item for item, _ in members if item.request.display_id == display_id)
    if provider.request_binding_sha256 != enrich_request(request, translation_bible=bible).request_binding_sha256:
        raise RuntimeError(f"B026_HUMAN_REQUEST_BINDING_MISMATCH:{display_id}")
    current = _texts(head.candidate)
    if any(index < 0 or index >= len(current) for index in changes):
        raise RuntimeError(f"B026_HUMAN_SCOPE_MISMATCH:{display_id}")
    targets = list(current)
    for index, text in changes.items():
        targets[index] = text
    if display_id == "body.p.08277":
        if group.semantic_review_group_id != GROUP_08277 or group.group_hash != GROUP_08277_HASH:
            raise RuntimeError("B026_HUMAN_08277_GROUP_BINDING_MISMATCH")
        if set(group.member_work_order_ids) != {"body.p.08276", "body.p.08277"}:
            raise RuntimeError("B026_HUMAN_08277_GROUP_MEMBERS_MISMATCH")
    equivalence = None
    qa_callable = harness_qa_record
    if numeric is not None:
        intended = head.candidate.model_copy(update={"target_segments": [
            segment.model_copy(update={"text": targets[index]})
            for index, segment in enumerate(head.candidate.target_segments)
        ]})
        equivalence = numeric(request, intended)
        if not dry_run:
            persist_stage_once(CHECKPOINT_DIR, "d051_reviewed_equivalences", display_id, {
                "artifact_type": "D051_REVIEWED_EQUIVALENCE", "schema_version": 2,
                "decision_authority": "HUMAN_APPROVED_NUMERIC_REPRESENTATION",
                "display_id": display_id, "unit_uid": request.unit_uid,
                "source_sha256": request.source_sha256,
                "request_binding_sha256": provider.request_binding_sha256,
                "candidate_binding_sha256": _candidate_binding(intended),
                "equivalence": equivalence.model_dump(), "translation_api_calls": 0,
            })
        def qa_callable(view, qa_request, candidate, known_unit_ids):
            return _qa_with_d051(harness_qa_record, view, qa_request, candidate, known_unit_ids, equivalence)
    allowed = [HumanAllowedRepair(
        logical_work_order_id=display_id, unit_uid=request.unit_uid,
        allowed_segment_indices=sorted(changes), source_fingerprint=provider.request_binding_sha256,
    )]
    adjudication = build_human_adjudication(
        group, decision="HUMAN_APPROVED",
        reason="Owner-approved B026 checkpoint_05 Human Semantic Adjudication.",
        allowed_repairs=allowed,
        source_hashes={item.request.display_id: item.request_binding_sha256 for item, _ in members},
        original_candidate_hashes={item.request.display_id: candidate_sha(raws[item.request.unit_uid]) for item, _ in members},
        approved_patches=[HumanApprovedPatch(logical_work_order_id=display_id, unit_uid=request.unit_uid, index=index, target_text=targets[index]) for index in sorted(changes)],
    )
    if dry_run:
        return {"work_order": display_id, "binding": "PASS", "parent": head.candidate_sha256, "group": group.semantic_review_group_id, "approval_mode": "PRESERVE_CURRENT" if targets == current else "MUTATION"}
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=CHECKPOINT_DIR, production_run=RUN, requests=loaded["requests"], views=loaded["views"],
        known_unit_ids=loaded["known_unit_ids"], qa_fn=qa_callable, adjudication=adjudication, group=group,
        members=members, translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label=BATCH, checkpoint=CHECKPOINT,
    )
    refreshed = _resolve_checkpoint_authoritative_candidate(CHECKPOINT_DIR, display_id, raws[request.unit_uid])
    if targets == current:
        if refreshed.candidate_sha256 != head.candidate_sha256:
            raise RuntimeError(f"B026_HUMAN_PRESERVE_CHANGED_HEAD:{display_id}")
    elif refreshed.candidate_sha256 == head.candidate_sha256:
        raise RuntimeError(f"B026_HUMAN_MUTATION_NOT_AUTHORITATIVE:{display_id}")
    return {"work_order": display_id, "binding": "PASS", "parent": head.candidate_sha256, "head": refreshed.candidate_sha256, "qa": all(record.get("passed") for record in result["qa"].values()), "terminal": "HUMAN_APPROVED"}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--dry-run", action="store_true"); args = parser.parse_args()
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=CHECKPOINT)
    raws = load_raw_by_uid(CHECKPOINT_DIR / "translation_raw_response.jsonl")
    result = [
        _apply(loaded=loaded, raws=raws, display_id="body.p.08263", expected_head=HEAD_08263, changes={0: _texts(_resolve_checkpoint_authoritative_candidate(CHECKPOINT_DIR, "body.p.08263", raws[{item.display_id: item for item in loaded["requests"]}["body.p.08263"].unit_uid]).candidate)[0]}, numeric=_numeric_equivalence, dry_run=args.dry_run),
        _apply(loaded=loaded, raws=raws, display_id="body.p.08277", expected_head=HEAD_08277, changes={0: TARGET_08277}, dry_run=args.dry_run),
    ]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
