"""Offline first confirmation for a mixed Candidate review batch.

Packets remain the authority for review material.  This module prepares an
Owner facing summary and freezes explicit decisions; it never imports reviews.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError
from .node_review_definitions import (
    build_node_review_packets,
    validate_node_review_definitions,
)
from .relationship_review import (
    build_relationship_review_packets,
    validate_relationship_review_definitions,
)


BATCH_SCHEMA = "architecture-explorer.batch-review.v1"
CONFIRMATION_SCHEMA = "architecture-explorer.batch-first-confirmation.v1"
_SUGGESTIONS = {"APPROVED", "REJECTED", "DEFERRED"}
_RESULTS = _SUGGESTIONS | {"PENDING"}
_KIND_ORDER = {"candidate-node": 0, "candidate-collaboration": 1, "candidate-decomposition": 2}


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _packet_key(key: str, packet: dict[str, Any]) -> None:
    if not isinstance(packet, dict):
        raise ExplorerInputError(f"Invalid Batch Packet: {key}")
    object_key = packet.get("object_key", {})
    if not isinstance(object_key, dict):
        raise ExplorerInputError(f"Invalid Batch Packet object key: {key}")
    object_id = object_key.get("object_id", object_key.get("node_id"))
    if key != f"{object_key.get('kind')}::{object_id}":
        raise ExplorerInputError(f"Batch Packet object identity mismatch: {key}")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or digest != _sha({k: v for k, v in packet.items() if k != "packet_sha256"}):
        raise ExplorerInputError(f"Batch Packet integrity mismatch: {key}")


def _check_batch(batch: dict[str, Any]) -> None:
    if not isinstance(batch, dict) or set(batch) != {"schema", "objects", "batch_sha256"} or batch.get("schema") != BATCH_SCHEMA:
        raise ExplorerInputError("Invalid batch review schema")
    objects = batch.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise ExplorerInputError("Batch review needs objects")
    for key, item in objects.items():
        if not isinstance(key, str) or not isinstance(item, dict) or set(item) != {"packet", "analysis"}:
            raise ExplorerInputError("Invalid batch review object")
        _packet_key(key, item["packet"])
        analysis = item["analysis"]
        if not isinstance(analysis, dict) or set(analysis) != {"topic", "display_name", "source_check", "assessment", "concerns", "risk", "suggestion", "suggestion_reason"}:
            raise ExplorerInputError(f"Invalid AI analysis: {key}")
        if not all(isinstance(analysis[field], str) and analysis[field].strip() for field in ("topic", "display_name", "source_check", "assessment", "risk", "suggestion_reason")):
            raise ExplorerInputError(f"Incomplete AI analysis: {key}")
        if not isinstance(analysis["concerns"], list) or not all(isinstance(x, str) and x.strip() for x in analysis["concerns"]):
            raise ExplorerInputError(f"Invalid concerns: {key}")
        if analysis["suggestion"] not in _SUGGESTIONS:
            raise ExplorerInputError(f"Invalid AI suggestion: {key}")
    if batch["batch_sha256"] != _sha({"schema": batch["schema"], "objects": objects}):
        raise ExplorerInputError("Batch review content changed")


def prepare_batch_review(
    explorer: dict[str, Any],
    node_definitions: dict[str, Any] | None,
    relationship_definitions: dict[str, Any] | None,
    analysis_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build and validate all material through the existing public Packet seam."""

    packets: dict[str, dict[str, Any]] = {}
    if node_definitions is not None:
        packets.update(build_node_review_packets(explorer, validate_node_review_definitions(node_definitions)))
    if relationship_definitions is not None:
        packets.update(build_relationship_review_packets(explorer, validate_relationship_review_definitions(relationship_definitions)))
    if not packets or set(analysis_by_key) != set(packets):
        raise ExplorerInputError("Batch AI analysis must cover exactly the Packet objects")
    source = explorer.get("source_facts", {}).get("source_snapshot", {})
    current = {"revision": source.get("revision"), "worktree_state": source.get("worktree", {}).get("state")}
    for key, packet in packets.items():
        if packet["reviewed_source_snapshot"] != current:
            raise ExplorerInputError(f"Batch source snapshot mismatch: {key}")
    objects = {key: {"packet": deepcopy(packet), "analysis": deepcopy(analysis_by_key[key])} for key, packet in sorted(packets.items())}
    batch = {"schema": BATCH_SCHEMA, "objects": objects}
    batch["batch_sha256"] = _sha(batch)
    _check_batch(batch)
    return batch


def _validate_decisions(batch: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> None:
    _check_batch(batch)
    if not isinstance(decisions, dict) or set(decisions) != set(batch["objects"]):
        raise ExplorerInputError("First confirmation must list every batch object")
    for key, decision in decisions.items():
        if not isinstance(decision, dict) or not {"result", "reason"} <= set(decision) or set(decision) - {"result", "reason", "rejection_basis", "scope"}:
            raise ExplorerInputError(f"Invalid per-object decision: {key}")
        if decision["result"] not in _RESULTS or not isinstance(decision["reason"], str) or not decision["reason"].strip():
            raise ExplorerInputError(f"Invalid per-object result or reason: {key}")
        if "rejection_basis" in decision and decision["result"] != "REJECTED":
            raise ExplorerInputError(f"Rejection basis only applies to REJECTED: {key}")
        if decision["result"] == "APPROVED":
            if decision.get("scope") != "FULL_PACKET":
                raise ExplorerInputError(f"Approval must explicitly cover the full Packet: {key}")
        elif "scope" in decision:
            raise ExplorerInputError(f"Only APPROVED may declare a Packet scope: {key}")


def _ordered_objects(batch: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(batch["objects"].items(), key=lambda entry: (_KIND_ORDER[entry[1]["packet"]["object_key"]["kind"]], entry[0]))


def render_final_decision_list(batch: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> str:
    """Deterministic content for Codex to show before the Owner's one confirmation."""

    _validate_decisions(batch, decisions)
    lines = [
        "# 首次确认：逐对象最终决定清单", "",
        "> 请核对下列每项决定及完整批准范围。此清单只是离线内容绑定；真实 Owner 身份须由后续 GitHub Trust Policy 独立验证。", "",
        f"- 审核材料：`batch-review.json`，规范清单：`checklist.md`", "",
    ]
    for number, (key, item) in enumerate(_ordered_objects(batch), start=1):
        packet = item["packet"]
        decision = decisions[key]
        result = decision["result"]
        claim_kinds = "、".join(claim["kind"] for claim in packet["claims"])
        candidate_evidence = packet["candidate_evidence"]
        dependency_evidence = packet.get("dependency_evidence", [])
        limits = next(claim["value"] for claim in packet["claims"] if claim["kind"] == "DEPENDENCIES_AND_LIMITS")
        name = item["analysis"]["display_name"]
        lines.extend([
            f"## 对象 {number}：{name}", "",
            f"- 稳定身份：`{key}`",
            f"- 最终决定：**{result}**",
            f"- 理由：{decision['reason']}",
        ])
        if result == "APPROVED":
            lines.extend([
                f"- 批准范围：此对象完整 Packet 的全部 Claims（{claim_kinds}）；包含 Packet 中全部候选 Evidence（{len(candidate_evidence)} 条）与依赖 Evidence（{len(dependency_evidence)} 条），受下述源码快照及保留未知项约束。不覆盖其他对象或运行时结论。不能只批准其中一部分；若需缩小范围，应暂缓并重新准备 Packet。",
                "- Evidence 精确身份与定位：见同一份 `checklist.md` 的本对象审核附录；Packet 摘要见下。",
                "- 保留未知项（不批准）：" + "；".join(limits["retained_unknowns"]),
            ])
        else:
            lines.append("- 批准范围：不批准任何 Claims；拒绝、暂缓或未决定均不构成批准。")
        lines.extend([
            f"- 源码快照：`{packet['reviewed_source_snapshot']['revision']}`（{packet['reviewed_source_snapshot']['worktree_state']}）",
            f"- Packet：`{packet['packet_sha256']}`；Claims：`{packet['inputs']['claims_sha256']}`", "",
        ])
    lines.extend(["请仅在以上对象、决定、理由及范围均准确时，一次明确确认这份最终清单。", ""])
    return "\n".join(lines)


def confirm_batch_review(
    batch: dict[str, Any], decisions: dict[str, dict[str, Any]], *, final_decision_list: str
) -> dict[str, Any]:
    """Freeze reviewed content; caller must obtain the Owner's confirmation separately."""

    expected_list = render_final_decision_list(batch, decisions)
    if final_decision_list != expected_list:
        raise ExplorerInputError("Final decision list differs from the batch and per-object decisions")
    record = {
        "schema": CONFIRMATION_SCHEMA,
        "batch_sha256": batch["batch_sha256"],
        "checklist_sha256": hashlib.sha256((render_batch_checklist(batch) + "\n").encode("utf-8")).hexdigest(),
        "final_decision_list_sha256": hashlib.sha256(expected_list.encode("utf-8")).hexdigest(),
        "decisions": deepcopy(decisions),
        "bindings": {
            key: {
                "object_key": deepcopy(item["packet"]["object_key"]),
                "packet_sha256": item["packet"]["packet_sha256"],
                "claims_sha256": item["packet"]["inputs"]["claims_sha256"],
                "candidate_evidence_sha256": item["packet"]["inputs"]["candidate_evidence_sha256"],
                "reviewed_source_snapshot": deepcopy(item["packet"]["reviewed_source_snapshot"]),
                "decision_sha256": _sha(decisions[key]),
            }
            for key, item in batch["objects"].items()
        },
    }
    record["confirmation_sha256"] = _sha(record)
    return record


def validate_batch_confirmation(record: dict[str, Any], batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return immutable per-object decisions to #79/#80 only after exact recheck."""

    _check_batch(batch)
    if not isinstance(record, dict) or set(record) != {"schema", "batch_sha256", "checklist_sha256", "final_decision_list_sha256", "decisions", "bindings", "confirmation_sha256"} or record.get("schema") != CONFIRMATION_SCHEMA:
        raise ExplorerInputError("Invalid first confirmation schema")
    if record["batch_sha256"] != batch["batch_sha256"] or record["confirmation_sha256"] != _sha({k: v for k, v in record.items() if k != "confirmation_sha256"}):
        raise ExplorerInputError("First confirmation content changed")
    expected = confirm_batch_review(batch, record["decisions"], final_decision_list=render_final_decision_list(batch, record["decisions"]))
    if record != expected:
        raise ExplorerInputError("First confirmation bindings changed")
    return deepcopy(record["decisions"])


def decided_objects(record: dict[str, Any], batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Handoff: only APPROVED/REJECTED may proceed to comment preparation."""

    return {key: value for key, value in validate_batch_confirmation(record, batch).items() if value["result"] in {"APPROVED", "REJECTED"}}


def render_batch_checklist(batch: dict[str, Any]) -> str:
    """Readable Chinese checklist; suggestions are visibly separate from decisions."""

    _check_batch(batch)
    lines = [
        "# Architecture Explorer 集中审核清单", "",
        "> 这是候选材料。AI 建议不等于 Owner 决定；每项默认 Pending。技术原文见同目录 `batch-review.json`。", "",
        f"本批 {len(batch['objects'])} 项。您可在聊天中按下方序号说明各项意图和理由；Codex 先展示逐对象最终决定清单，您核对后一次集中确认。未明确决定的对象保持 Pending。", "",
    ]
    grouped: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
    for number, (key, item) in enumerate(_ordered_objects(batch), start=1):
        fact, analysis = item["packet"]["candidate_fact"], item["analysis"]
        topic = analysis.get("topic") or fact.get("name") or "候选架构对象"
        grouped.setdefault(topic, []).append((number, key, item))
    technical_index = []
    for topic, entries in grouped.items():
        lines.extend([f"## 审核主题：{topic}", ""])
        for number, key, item in entries:
            packet, analysis = item["packet"], item["analysis"]
            fact = packet["candidate_fact"]
            kind = {"candidate-node": "候选职责", "candidate-collaboration": "候选协作", "candidate-decomposition": "候选分解"}[packet["object_key"]["kind"]]
            claims = {claim["kind"]: claim["value"] for claim in packet["claims"]}
            unknowns = claims["DEPENDENCIES_AND_LIMITS"]["retained_unknowns"]
            name = analysis.get("display_name") or fact.get("name") or key
            responsibility = fact.get("responsibility") or fact.get("professional_description") or fact.get("plain_explanation")
            lines.extend([f"### {number}. {name}（{kind}）", "", f"**职责 / 关系**：{responsibility}", "", "**需要判断的 Claims**", ""])
            if kind == "候选职责":
                lines.extend([
                    f"- 职责：{claims['RESPONSIBILITY']}",
                    f"- 分类依据：{claims['CLASSIFICATION']['rationale']}",
                    f"- 通俗解释：{claims['PLAIN_EXPLANATION']}",
                ])
            else:
                endpoints = claims["ENDPOINT_RESPONSIBILITIES"]
                semantics = claims["SEMANTICS_AND_DIRECTION"]
                lines.extend([
                    f"- 起点职责：{endpoints['source']['responsibility']}",
                    f"- 终点职责：{endpoints['target']['responsibility']}",
                    f"- 关系与方向：{'协作' if semantics['family'] == 'collaboration' else '分解'}；从上述起点指向上述终点。",
                    f"- 关系解释：{claims['RELATIONSHIP_DESCRIPTION']['plain_explanation']}",
                ])
            lines.extend(["", "**Evidence 与源码支持**", ""])
            for evidence_number, evidence in enumerate(packet["candidate_evidence"], start=1):
                location = evidence["current_location"]
                lines.append(f"- 证据 {evidence_number}：`{evidence['path']}:{location['line_start']}-{location['line_end']}`，`{evidence['symbol']}`。")
            lines.extend([
                f"- AI 源码分析：{analysis['source_check']}", "",
                f"**不能证明**：{analysis['assessment']}", "",
                "**保留未知项**：" + "；".join(unknowns), "",
                "**疑点**：" + ("；".join(analysis["concerns"]) or "暂无列明疑点"), "",
                f"**风险**：{analysis['risk']}", "",
                f"**AI 建议**：{analysis['suggestion']}。{analysis.get('suggestion_reason', '')}（仅供参考）", "",
                "**Owner 决定：Pending**。可选择批准、拒绝或暂缓，并说明理由。", "",
            ])
            technical_index.append(f"- 对象 {number}：`batch-review.json` → `objects` → `{key}`；Packet `{packet['packet_sha256']}`。")
    lines.extend(["## 一次集中确认", "", "您可以直接回复，例如：\"对象 1 可批准完整 Packet，理由……；对象 2 暂缓，理由……；对象 3 拒绝，理由……。请整理最终决定清单。\" 未提及的对象保持 Pending。Codex 展示包含中文名称、稳定身份、决定、理由和完整范围的最终清单后，您只需对该明确清单确认一次。", "", "## 审核附录", "", "以下是同一份清单的完整审核范围。批准只覆盖对应对象的整个 Packet 和全部 Claims；若只认可其中一部分，请暂缓并重新准备材料。", ""])
    for number, (key, item) in enumerate(_ordered_objects(batch), start=1):
        packet = item["packet"]
        limits = next(claim["value"] for claim in packet["claims"] if claim["kind"] == "DEPENDENCIES_AND_LIMITS")
        lines.extend([
            f"### 对象 {number}：{item['analysis']['display_name']} 的完整范围", "",
            f"- 源码快照：`{packet['reviewed_source_snapshot']['revision']}`；工作树状态：`{packet['reviewed_source_snapshot']['worktree_state']}`。",
            "- 如批准：批准此对象完整 Packet 的全部 Claims、下列 Evidence 所界定的静态解释；不批准保留未知项、其他对象或未经证实的运行时结论。",
            "- 完整 Claims：", "",
        ])
        for claim in packet["claims"]:
            lines.append(f"  - **{claim['kind']}**（来源：{claim['source']}）：{json.dumps(claim['value'], ensure_ascii=False, sort_keys=True)}")
        lines.extend(["", "- Evidence 定位：", ""])
        for evidence in [*packet["candidate_evidence"], *packet.get("dependency_evidence", [])]:
            location = evidence["current_location"]
            lines.append(f"  - `{evidence['evidence_id']}`：`{evidence['path']}:{location['line_start']}-{location['line_end']}`；symbol `{evidence['symbol']}`；文件 SHA-256 `{evidence['file_sha256']}`。")
        if limits["dependencies"]:
            lines.extend(["", "- 依赖："])
            lines.extend(f"  - {json.dumps(dependency, ensure_ascii=False, sort_keys=True)}" for dependency in limits["dependencies"])
        else:
            lines.append(f"- 零正式架构依赖：{limits['zero_dependency_reason']}")
        lines.extend(["- 保留未知项：" + "；".join(limits["retained_unknowns"]), ""])
    lines.extend(["## 技术定位", "", "以下索引仅供追溯；完整 Packet 原文也保存在 `batch-review.json`。", "", *technical_index, ""])
    return "\n".join(lines)
