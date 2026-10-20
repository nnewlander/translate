"""Apply the owner-approved B025 release decisions without provider calls.

The packet is the immutable binding contract.  Each decision is re-bound to
the live Candidate Authority head before any append-only Human evidence is
persisted; a changed head therefore fails closed.
"""
from __future__ import annotations

import json
import re
import argparse
from pathlib import Path

from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair,
    HumanApprovedPatch,
    apply_human_adjudication_to_production_checkpoint,
    build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _candidate_binding,
    _qa_with_d051,
    _production_review_group,
    _resolve_checkpoint_authoritative_candidate,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, persist_stage_once
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_025"
BATCH_DIR = RUN / BATCH.lower()
PACKET_PATH = BATCH_DIR / "human_release_packet_v1.json"

# ``None`` means PRESERVE_CURRENT.  For that contract the existing human
# mechanism receives byte-identical patches for all segments and consequently
# emits a PRESERVE_CURRENT terminal without creating a candidate fork.
DECISIONS: dict[str, dict[str, object]] = {
    "body.p.07841": {"segments": {0: "新构建的二进制文件，但仍使用相同的线上后端）是一种低成本的故障隔离方法。"}},
    "body.p.07868": {"segments": {
        1: "禁用那些无法立即修复的失败测试，是保持测试套件全绿的务实做法，这能让你确信自己已掌握所有测试失败的情况。此外，自动化维护测试套件（包括管理发布流程以及为已修复的测试更新跟踪缺陷），能保持套件整洁并防止技术债务累积。用 DevOps 术语来说，我们可以把",
        2: "图 23-5 中的指标称为", 3: "MTTCU：平均清理时间。",
    }},
    "body.p.07870": {"segments": {
        1: "我们描述的这些场景远非 Takeout 所面临的全部持续集成（CI）挑战，仍有更多问题需要解决。例如，我们曾提到隔离上游服务故障的难度，见",
        2: "第 490 页的“CI 挑战”一节",
    }},
    "body.p.07903": {"segments": None},
    "body.p.07923": {"segments": {0: "测量崩溃或延迟等关键健康指标，并持续改进这些指标；", 1: "数据驱动的决策制定"}},
    "body.p.07924": {"segments": {0: "对健康指标进行 A/B 测试以确保质量；", 1: "分阶段发布"}},
    "body.p.07943": {"segments": {0: "实现可靠持续发布的一个关键，是确保工程师对", 1: "所有变更实施“功能开关防护”"}},
    "body.p.07988": {"segments": {
        0: "尽管产品更新频率需要权衡，但目标是", 1: "让这些选择都是有意识作出的",
        2: "。有了顺畅、运行良好的持续交付（CD）流程，可发布版本的", 3: "创建频率",
        4: "可以与用户", 5: "接收它的频率",
        6: "分离开来。你或许能够做到每周、每天甚至每小时部署，但并不一定真的按这样的频率部署；你应该结合用户的具体需求和更宏观的组织目标，有意识地选择发布流程，并确定最能支持产品长期可持续性的人员配置和工具模型。",
    }},
    "body.p.07990": {"segments": {0: "设置这些，以及进行识别和移除，都会产生一定的前期成本。"}},
    "body.p.08001": {"segments": {
        0: "如果你面向所有用户开发，你的客户端可能会运行在智能屏幕、智能音箱，以及 Android 和 iOS 手机与平板上；同时，你的软件也可能具备足够的灵活性，允许用户自定义使用体验。即便你只针对 Android 设备进行开发，仅凭超过 20 亿台 Android 设备的多样性，就足以让发布版本的兼容性测试工作变得令人望而生畏。更何况，在技术革新的高速迭代之下，等到有人读到本章时，可能已经涌现出全新的设备类别。",
    }},
    "body.p.08007": {"segments": {
        0: "我们在向 Android 部署时注意到的一个问题是，用户指标可能会出现具有统计显著性的变化，",
        1: "而这仅仅是由推送一次更新造成的",
    }},
    "body.p.08030": {"segments": {1: "开发速率是一项团队运动", 2: "：大型团队协作开发代码的最佳工作流；模块化"}},
}


def _packet_items() -> dict[str, dict[str, object]]:
    packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
    if packet.get("artifact_type") != "BATCH_HUMAN_RELEASE_PACKET" or packet.get("batch") != BATCH:
        raise RuntimeError("B025_HUMAN_PACKET_BINDING_INVALID")
    items = {item.get("work_order_id"): item for item in packet.get("items", []) if isinstance(item, dict)}
    if set(items) != set(DECISIONS):
        raise RuntimeError("B025_HUMAN_PACKET_SCOPE_MISMATCH")
    return items


def _candidate_texts(head) -> list[str]:
    return [segment.text for segment in head.candidate.target_segments]


def _verify_packet_binding(*, item: dict[str, object], checkpoint: str, request, head, provider_request, group) -> None:
    binding = item.get("binding")
    context = item.get("canonical_context")
    if not isinstance(binding, dict) or not isinstance(context, dict):
        raise RuntimeError(f"B025_HUMAN_PACKET_SHAPE_INVALID:{request.display_id}")
    expected = {
        "batch": BATCH, "checkpoint": checkpoint,
        "logical_work_order_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": provider_request.request_binding_sha256,
        "current_authoritative_candidate_sha256": head.candidate_sha256,
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"B025_HUMAN_PACKET_CURRENT_BINDING_MISMATCH:{request.display_id}")
    if (
        context.get("semantic_review_group_id") != group.semantic_review_group_id
        or context.get("group_hash") != group.group_hash
    ):
        raise RuntimeError(f"B025_HUMAN_PACKET_GROUP_BINDING_MISMATCH:{request.display_id}")


def _verify_numeric_terminal(head) -> None:
    rendered = "".join(_candidate_texts(head))
    if "two billion" in rendered or not re.search(r"(?:20\s*亿|二十亿)", rendered):
        raise RuntimeError("B025_HUMAN_08001_NUMERIC_MUTATION_NOT_CURRENT")


def _08001_numeric_equivalence(request, candidate) -> ExplicitNumericRepresentationEquivalence:
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid=request.unit_uid,
        exact_source_span="two billion",
        source_numeric_tokens=[],
        approved_target_representation="20 亿",
        reviewed_source_semantic_numeric_value="2000000000",
        reviewed_target_semantic_numeric_value="2000000000",
        semantic_unit="B025 release Human adjudication body.p.08001",
    )
    if request.source_text.count(equivalence.exact_source_span) != 1 or equivalence.approved_target_representation not in "".join(segment.text for segment in candidate.target_segments):
        raise RuntimeError("B025_HUMAN_08001_NUMERIC_EQUIVALENCE_NOT_EXACT")
    return equivalence


def _already_applied_current(*, checkpoint_dir: Path, display_id: str, packet_binding: dict[str, object], request, head) -> bool:
    """Consume only this packet's exact, already-published Human terminal."""
    evidence_path = checkpoint_dir / "human_adjudications" / f"{display_id}.json"
    if not evidence_path.is_file():
        return False
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not (
        evidence.get("artifact_type") == "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION"
        and evidence.get("batch_label") == BATCH
        and evidence.get("logical_work_order_id") == display_id
        and evidence.get("unit_uid") == request.unit_uid
        and evidence.get("source_sha256") == request.source_sha256
        and evidence.get("request_binding_sha256") == packet_binding.get("request_binding_sha256")
        and evidence.get("decision") == "HUMAN_APPROVED"
    ):
        raise RuntimeError(f"B025_HUMAN_EXISTING_TERMINAL_BINDING_INVALID:{display_id}")
    candidate_path = checkpoint_dir / "human_approved_candidates" / f"{display_id}.json"
    if candidate_path.is_file():
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        if not (
            candidate.get("semantic_terminal") == "HUMAN_APPROVED"
            and candidate.get("parent_candidate_sha256") == packet_binding.get("current_authoritative_candidate_sha256")
            and candidate.get("candidate_binding_sha256") == head.candidate_sha256
            and isinstance(candidate.get("qa"), dict) and candidate["qa"].get("passed") is True
        ):
            raise RuntimeError(f"B025_HUMAN_EXISTING_MUTATION_BINDING_INVALID:{display_id}")
        return True
    if not (
        evidence.get("approval_mode") == "PRESERVE_CURRENT"
        and evidence.get("approved_candidate_sha256") == head.candidate_sha256
        and packet_binding.get("current_authoritative_candidate_sha256") == head.candidate_sha256
    ):
        raise RuntimeError(f"B025_HUMAN_EXISTING_PRESERVE_BINDING_INVALID:{display_id}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    items = _packet_items()
    report: list[dict[str, object]] = []
    for display_id, decision in DECISIONS.items():
        item = items[display_id]
        binding = item["binding"]
        assert isinstance(binding, dict)
        checkpoint = binding["checkpoint"]
        if not isinstance(checkpoint, str):
            raise RuntimeError(f"B025_HUMAN_PACKET_CHECKPOINT_INVALID:{display_id}")
        checkpoint_dir = BATCH_DIR / checkpoint
        loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
        raws = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
        requests = {request.display_id: request for request in loaded["requests"]}
        request = requests.get(display_id)
        if request is None or request.unit_uid not in raws:
            raise RuntimeError(f"B025_HUMAN_CURRENT_REQUEST_OR_RAW_MISSING:{display_id}")
        head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raws[request.unit_uid])
        if _already_applied_current(
            checkpoint_dir=checkpoint_dir, display_id=display_id,
            packet_binding=binding, request=request, head=head,
        ):
            report.append({
                "work_order": display_id, "approval_mode": "ALREADY_APPLIED",
                "authoritative_candidate_sha256": head.candidate_sha256,
                "binding": "PASS", "human_terminal": "HUMAN_APPROVED",
            })
            continue
        overrides = {display_id: head.candidate, request.unit_uid: head.candidate}
        group = _production_review_group(
            request, loaded=loaded, run=RUN, checkpoint=checkpoint,
            existing_raw=raws, candidate_overrides=overrides,
        )
        if group is None:
            raise RuntimeError(f"B025_HUMAN_GROUP_MISSING:{display_id}")
        bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
        members = []
        for member in group.members:
            member_request = requests.get(member.display_id)
            if member_request is None or member_request.unit_uid not in raws:
                raise RuntimeError(f"B025_HUMAN_GROUP_MEMBER_MISSING:{display_id}:{member.display_id}")
            member_head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, member.display_id, raws[member_request.unit_uid])
            members.append((enrich_request(member_request, translation_bible=bible), member_head.candidate))
        provider_request = next(provider for provider, _ in members if provider.request.display_id == display_id)
        _verify_packet_binding(item=item, checkpoint=checkpoint, request=request, head=head, provider_request=provider_request, group=group)
        changes = decision["segments"]
        current = _candidate_texts(head)
        if changes is None:
            target_texts = list(current)
            allowed_indices = list(range(len(current)))
            approval_mode = "PRESERVE_CURRENT"
        else:
            if not isinstance(changes, dict) or not all(isinstance(index, int) and isinstance(text, str) for index, text in changes.items()):
                raise RuntimeError(f"B025_HUMAN_DECISION_INVALID:{display_id}")
            if any(index < 0 or index >= len(current) for index in changes):
                raise RuntimeError(f"B025_HUMAN_SEGMENT_SCOPE_INVALID:{display_id}")
            target_texts = list(current)
            for index, text in changes.items():
                target_texts[index] = text
            allowed_indices = sorted(changes)
            approval_mode = "MUTATION"

        numeric_equivalence = None
        qa_callable = harness_qa_record
        if display_id == "body.p.08001":
            expected_candidate = head.candidate.model_copy(update={
                "target_segments": [
                    segment.model_copy(update={"text": target_texts[index]})
                    for index, segment in enumerate(head.candidate.target_segments)
                ],
            })
            numeric_equivalence = _08001_numeric_equivalence(request, expected_candidate)
            if not args.dry_run:
                # This is existing D051 evidence, exact-bound to the intended
                # Human candidate.  If interrupted before the candidate is
                # published, the resolver ignores it because its candidate
                # binding is not current.
                persist_stage_once(checkpoint_dir, "d051_reviewed_equivalences", display_id, {
                    "artifact_type": "D051_REVIEWED_EQUIVALENCE",
                    "schema_version": 1,
                    "decision_authority": "HUMAN_APPROVED_NUMERIC_REPRESENTATION",
                    "display_id": display_id,
                    "unit_uid": request.unit_uid,
                    "source_sha256": request.source_sha256,
                    "request_binding_sha256": provider_request.request_binding_sha256,
                    "candidate_binding_sha256": _candidate_binding(expected_candidate),
                    "equivalence": numeric_equivalence.model_dump(),
                    "translation_api_calls": 0,
                })

            def qa_callable(view, qa_request, working, known_unit_ids):
                if qa_request.display_id == display_id:
                    return _qa_with_d051(
                        harness_qa_record, view, qa_request, working,
                        known_unit_ids, numeric_equivalence,
                    )
                return harness_qa_record(view, qa_request, working, known_unit_ids)

        allowed = [HumanAllowedRepair(
            logical_work_order_id=display_id, unit_uid=request.unit_uid,
            allowed_segment_indices=allowed_indices,
            source_fingerprint=provider_request.request_binding_sha256,
        )]
        adjudication = build_human_adjudication(
            group, decision="HUMAN_APPROVED",
            reason="Owner-approved B025 release-boundary semantic adjudication.",
            allowed_repairs=allowed,
            source_hashes={provider.request.display_id: provider.request_binding_sha256 for provider, _ in members},
            original_candidate_hashes={provider.request.display_id: candidate_sha(raws[provider.request.unit_uid]) for provider, _ in members},
            approved_patches=[HumanApprovedPatch(
                logical_work_order_id=display_id, unit_uid=request.unit_uid,
                index=index, target_text=text,
            ) for index, text in (enumerate(target_texts) if changes is None else sorted(changes.items()))],
        )
        if args.dry_run:
            report.append({
                "work_order": display_id, "approval_mode": approval_mode,
                "parent_candidate_sha256": head.candidate_sha256,
                "binding": "PASS", "numeric_terminal": "PASS" if display_id == "body.p.08001" else None,
            })
            continue
        result = apply_human_adjudication_to_production_checkpoint(
            checkpoint_dir=checkpoint_dir, production_run=RUN,
            requests=loaded["requests"], views=loaded["views"], known_unit_ids=loaded["known_unit_ids"],
            qa_fn=qa_callable, adjudication=adjudication, group=group, members=members,
            translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
            batch_label=BATCH, checkpoint=checkpoint,
        )
        refreshed = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raws[request.unit_uid])
        if approval_mode == "PRESERVE_CURRENT":
            if refreshed.candidate_sha256 != head.candidate_sha256:
                raise RuntimeError(f"B025_HUMAN_PRESERVE_HEAD_CHANGED:{display_id}")
        elif refreshed.candidate_sha256 == head.candidate_sha256:
            raise RuntimeError(f"B025_HUMAN_MUTATION_NOT_AUTHORITATIVE:{display_id}")
        if display_id == "body.p.08001":
            _verify_numeric_terminal(refreshed)
        report.append({
            "work_order": display_id, "approval_mode": approval_mode,
            "parent_candidate_sha256": head.candidate_sha256,
            "authoritative_candidate_sha256": refreshed.candidate_sha256,
            "core_qa_pass": all(record.get("passed") for record in result["qa"].values()),
            "human_terminal": "HUMAN_APPROVED",
        })
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
