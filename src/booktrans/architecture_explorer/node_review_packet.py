from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .generator import ExplorerInputError
from .node_review import (
    EXPECTED_CANDIDATE_FACT,
    NODE_REVIEW_OBJECT_KEY,
    REVIEWED_REVISION,
    VERSIONED_INPUTS,
    _canonical_sha256,
    _candidate_fact,
    _expected_claims,
    _expected_source_body,
    _relationship,
    _reviewed_claims,
    _source_evidence,
    _target_node,
)


NODE_REVIEW_PACKET_SCHEMA = "architecture-explorer.node-review-packet.v1"
IMPLEMENTATION_BASELINE = "14073edcea76a2b200bf15aaa4231c13f20e5ec9"
ALLOWED_RESULTS = ("APPROVED", "REJECTED")


def build_node_review_packet(explorer: dict[str, Any]) -> dict[str, Any]:
    """Build the one closed T9 review packet from validated T8 material."""

    target = _target_node(explorer)
    candidate_fact = _candidate_fact(target)
    if candidate_fact != EXPECTED_CANDIDATE_FACT:
        raise ExplorerInputError("Node Review Packet Candidate Fact mismatch")

    relationship = _relationship(explorer)
    claims = _expected_claims(explorer, target, relationship)
    if claims != _reviewed_claims():
        raise ExplorerInputError("Node Review Packet Claim binding mismatch")

    evidence_ids = candidate_fact["source_assignment"]["evidence_ids"]
    evidence_by_id = _source_evidence(explorer, evidence_ids)
    candidate_evidence = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise ExplorerInputError(
                f"Node Review Packet Candidate Evidence is missing: {evidence_id}"
            )
        candidate_evidence.append(deepcopy(evidence))

    packet = {
        "schema": NODE_REVIEW_PACKET_SCHEMA,
        "object_key": dict(NODE_REVIEW_OBJECT_KEY),
        "implementation_baseline": IMPLEMENTATION_BASELINE,
        "reviewed_source_snapshot": {
            "revision": REVIEWED_REVISION,
            "worktree_state": "clean",
        },
        "allowed_results": list(ALLOWED_RESULTS),
        "candidate_fact": deepcopy(candidate_fact),
        "claims": deepcopy(claims),
        "candidate_evidence": candidate_evidence,
        "inputs": {
            **VERSIONED_INPUTS,
            "candidate_fact_sha256": _canonical_sha256(candidate_fact),
            "claims_sha256": _canonical_sha256(claims),
        },
    }
    packet["packet_sha256"] = _canonical_sha256(packet)
    return packet


def _claim(packet: dict[str, Any], kind: str) -> dict[str, Any]:
    matches = [claim for claim in packet["claims"] if claim.get("kind") == kind]
    if len(matches) != 1:
        raise ExplorerInputError(f"Node Review Packet needs one {kind} Claim")
    return matches[0]


def render_node_review_report(packet: dict[str, Any]) -> str:
    candidate = packet["candidate_fact"]
    classification = _claim(packet, "CLASSIFICATION")["value"]
    source_assignment = _claim(packet, "SOURCE_ASSIGNMENT")["value"]
    formal = _claim(packet, "FORMAL_DEPENDENCY_AND_LIMITS")["value"]
    lines = [
        "# Architecture Explorer T9：首次 Owner 单节点审核材料",
        "",
        "> 本材料不是审核决定，也不包含默认批准或拒绝。它只帮助 Owner 审阅固定候选节点。",
        "",
        "## 审核范围",
        "",
        f"- 对象：`{packet['object_key']['kind']} + {packet['object_key']['parent_module_id']} + {packet['object_key']['node_id']}`",
        f"- 工程集成基线：`{packet['implementation_baseline']}`",
        f"- Decision 受审核源码快照：`{packet['reviewed_source_snapshot']['revision']}`",
        f"- Review Packet SHA-256：`{packet['packet_sha256']}`",
        "- 可选审核结果：`APPROVED` 或 `REJECTED`（本材料未选择结果）",
        "",
        "## 候选节点说明",
        "",
        f"- 名称：{candidate['name']}",
        f"- 类型：`{classification['node_type']}`（候选职责，不是物理组件）",
        f"- 专业职责：{candidate['responsibility']}",
        f"- 通俗解释：{candidate['plain_explanation']}",
        f"- 源码归属：`{source_assignment['status']}`",
        f"- 归属限制：{source_assignment['note']}",
        "",
        "### 分类理由",
        "",
        classification["rationale"],
        "",
    ]
    claim_labels = {
        "IDENTITY": "对象身份",
        "NAME": "名称",
        "CLASSIFICATION": "分类",
        "RESPONSIBILITY": "专业职责",
        "PLAIN_EXPLANATION": "通俗解释",
        "SOURCE_ASSIGNMENT": "源码归属",
        "CANDIDATE_EVIDENCE": "候选 Evidence",
        "FORMAL_DEPENDENCY_AND_LIMITS": "正式依赖与限制",
    }
    lines.extend(["", "## 八类审核声明", ""])
    for position, claim in enumerate(packet["claims"], start=1):
        lines.append(
            f"{position}. **{claim_labels[claim['kind']]}**（`{claim['kind']}`）— 来源：`{claim['source']}`"
        )

    lines.extend(["", "## 三条候选源码证据", ""])
    for position, evidence in enumerate(packet["candidate_evidence"], start=1):
        location = evidence["current_location"]
        snippet = evidence["snippet"].replace("\r\n", "\n").replace("\r", "\n")
        lines.extend(
            [
                f"### {position}. `{evidence['evidence_kind']}`",
                "",
                f"- Evidence ID：`{evidence['evidence_id']}`",
                f"- 源码角色：`{evidence['source_role']}`",
                f"- 位置：`{evidence['path']}:{location['line_start']}-{location['line_end']}`",
                f"- Symbol：`{evidence['symbol']}`",
                f"- 结构锚点：`{evidence['structural_anchor']}`",
                f"- 规范化片段 SHA-256：`{evidence['normalized_snippet_sha256']}`",
                "",
                "```text",
                snippet,
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## T3 正式依赖与十三条正式 Evidence",
            "",
            f"- Relationship：`{formal['relationship_id']}`",
            f"- T3 Decision：`{formal['t3_decision_id']}` / `{formal['t3_result']}`",
            f"- T3 reviewed snapshot：`{formal['t3_reviewed_snapshot']}`",
            "- 13 条正式 Evidence：",
        ]
    )
    lines.extend(f"  - `{evidence_id}`" for evidence_id in formal["formal_evidence_ids"])
    lines.extend(["", "## 批准后仍保留的未知项", ""])
    lines.extend(f"- {unknown}" for unknown in formal["retained_unknowns"])
    lines.extend(
        [
            "",
            "## 两阶段人工操作",
            "",
            "1. 先审阅本报告；本阶段不创建 GitHub 评论，也不产生 Decision。",
            "2. 在 T9-2 获得单独授权后创建明确的非权威草稿评论，以取得真实 comment ID。",
            "3. Owner 明确选择结果后，用 `--result APPROVED|REJECTED --comment-id <真实正整数 ID>` 再次运行本地入口，生成精确最终正文。",
            "4. Owner 只把同一条草稿评论编辑为该最终正文；草稿本身不是可信决定，也不能成为 Production Anchor。",
            "5. 最终编辑后不要再次修改评论；T9-2 必须重新读取该版本，并把真实 comment ID、最终正文和最终 `updated_at` 一起绑定进可信来源收据。",
            "",
        ]
    )
    return "\n".join(lines)


def render_node_review_comment(
    packet: dict[str, Any],
    *,
    result: str,
    comment_id: int,
) -> str:
    """Render the exact T8 protocol body for one explicit Owner result and comment."""

    if result not in ALLOWED_RESULTS:
        raise ExplorerInputError("Node Review result must be APPROVED or REJECTED")
    if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id <= 0:
        raise ExplorerInputError("Node Review comment ID must be a positive integer")
    return _expected_source_body(
        {
            "decision_id": f"github-comment:{comment_id}",
            "result": result,
            "object_key": packet["object_key"],
            "inputs": packet["inputs"],
            "reviewed_source_snapshot": packet["reviewed_source_snapshot"],
        }
    )


def write_node_review_packet(
    packet: dict[str, Any],
    output_directory: Path,
    *,
    result: str | None = None,
    comment_id: int | None = None,
) -> tuple[Path, Path, Path | None]:
    if (result is None) != (comment_id is None):
        raise ExplorerInputError(
            "Node Review result and comment ID must be supplied together"
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    packet_path = output_directory / "review-packet.json"
    report_path = output_directory / "review-packet.md"
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report_path.write_text(
        render_node_review_report(packet),
        encoding="utf-8",
        newline="\n",
    )
    comment_path = None
    if result is not None and comment_id is not None:
        comment_path = output_directory / "github-comment.txt"
        comment_path.write_text(
            render_node_review_comment(
                packet,
                result=result,
                comment_id=comment_id,
            ),
            encoding="utf-8",
            newline="\n",
        )
    return packet_path, report_path, comment_path


__all__ = [
    "NODE_REVIEW_PACKET_SCHEMA",
    "build_node_review_packet",
    "render_node_review_comment",
    "render_node_review_report",
    "write_node_review_packet",
]
