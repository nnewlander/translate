"""Exact packet compatibility for the historical worker Decision v1."""

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
from .presentation_zh import build_relationship_presentation_zh
from .source_primitives import build_static_evidence


NODE_REVIEW_PACKET_SCHEMA = "architecture-explorer.node-review-packet.v1"
IMPLEMENTATION_BASELINE = "14073edcea76a2b200bf15aaa4231c13f20e5ec9"
ALLOWED_RESULTS = ("APPROVED", "REJECTED")
EVIDENCE_RECORD_FIELDS = (
    "evidence_id",
    "source_role",
    "evidence_kind",
    "path",
    "symbol",
    "structural_anchor",
    "syntax_position",
    "current_location",
    "snippet",
    "normalized_snippet_sha256",
    "file_sha256",
)
PRESENTATION_FIELDS = ("group", "name", "explanation", "supports")


def _validated_static_evidence_record(
    raw: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != set(EVIDENCE_RECORD_FIELDS):
        raise ExplorerInputError(f"{label} has an invalid field set")
    location = raw.get("current_location")
    string_fields = tuple(
        field
        for field in EVIDENCE_RECORD_FIELDS
        if field != "current_location"
    )
    if (
        not all(isinstance(raw.get(field), str) for field in string_fields)
        or not isinstance(location, dict)
    ):
        raise ExplorerInputError(f"{label} is incomplete")
    line_fields = {"line_start", "line_end"}
    column_fields = {"column_start", "column_end"}
    allowed_fields = [line_fields]
    if raw["evidence_kind"] in {"python_definition", "python_call"}:
        allowed_fields.append(line_fields | column_fields)
    if set(location) not in allowed_fields or any(
        type(value) is not int for value in location.values()
    ):
        raise ExplorerInputError(f"{label} has an invalid location")
    line_start, line_end = location["line_start"], location["line_end"]
    if not 1 <= line_start <= line_end:
        raise ExplorerInputError(f"{label} has an invalid line location")
    rebuilt = build_static_evidence(
        path=raw["path"],
        source_role=raw["source_role"],
        evidence_kind=raw["evidence_kind"],
        symbol=raw["symbol"],
        structural_anchor=raw["structural_anchor"],
        syntax_position=raw["syntax_position"],
        snippet=raw["snippet"],
        line_start=line_start,
        line_end=line_end,
        file_sha256=raw["file_sha256"],
    )
    if column_fields <= location.keys():
        column_start, column_end = location["column_start"], location["column_end"]
        snippet_lines = raw["snippet"].split("\n")
        expected_end = len(snippet_lines[-1].encode("utf-8"))
        if line_start == line_end:
            expected_end += column_start
        if (
            min(column_start, column_end) < 0
            or (line_start, column_start) >= (line_end, column_end)
            or len(snippet_lines) != line_end - line_start + 1
            or column_end != expected_end
        ):
            raise ExplorerInputError(f"{label} has an invalid column location")
        rebuilt["current_location"].update(
            column_start=column_start, column_end=column_end
        )
    if rebuilt != raw:
        raise ExplorerInputError(f"{label} content hash mismatch")
    return rebuilt


def _formal_evidence(
    explorer: dict[str, Any],
    relationship: dict[str, Any],
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    raw_records = relationship.get("evidence")
    if not isinstance(raw_records, list) or len(raw_records) != len(evidence_ids):
        raise ExplorerInputError("Node Review Packet needs 13 formal Evidence records")
    records = [
        _validated_static_evidence_record(
            raw,
            label=f"Node Review Packet formal Evidence {position}",
        )
        for position, raw in enumerate(raw_records, start=1)
    ]
    if [item["evidence_id"] for item in records] != evidence_ids:
        raise ExplorerInputError(
            "Node Review Packet formal Evidence identity or order mismatch"
        )

    supporting = relationship.get("supporting_evidence")
    if not isinstance(supporting, list):
        raise ExplorerInputError("Node Review Packet T3 supporting Evidence is missing")
    expected_presentation = build_relationship_presentation_zh(
        explorer.get("architecture", {}).get("modules", []),
        relationship,
        [*records, *supporting],
    )
    if relationship.get("presentation_zh") != expected_presentation:
        raise ExplorerInputError("Node Review Packet T3 Chinese presentation mismatch")
    presentation_items = expected_presentation["evidence_items"]
    result = []
    for record in records:
        evidence_id = record["evidence_id"]
        presentation = presentation_items.get(evidence_id)
        if (
            not isinstance(presentation, dict)
            or set(presentation) != set(PRESENTATION_FIELDS)
            or not all(
                isinstance(presentation.get(field), str) and presentation[field]
                for field in PRESENTATION_FIELDS
            )
        ):
            raise ExplorerInputError(
                f"Node Review Packet formal Evidence presentation is incomplete: {evidence_id}"
            )
        result.append(
            {
                **deepcopy(record),
                "presentation_zh": {
                    field: presentation[field] for field in PRESENTATION_FIELDS
                },
            }
        )
    return result


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

    formal_evidence_ids = claims[-1]["value"]["formal_evidence_ids"]
    formal_evidence = _formal_evidence(
        explorer,
        relationship,
        formal_evidence_ids,
    )

    evidence_ids = candidate_fact["source_assignment"]["evidence_ids"]
    evidence_by_id = _source_evidence(explorer, evidence_ids)
    candidate_evidence = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise ExplorerInputError(
                f"Node Review Packet Candidate Evidence is missing: {evidence_id}"
            )
        candidate_evidence.append(
            {
                field: deepcopy(evidence[field])
                for field in EVIDENCE_RECORD_FIELDS
            }
        )

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
        "formal_evidence": formal_evidence,
        "inputs": {
            **VERSIONED_INPUTS,
            "candidate_fact_sha256": _canonical_sha256(candidate_fact),
            "claims_sha256": _canonical_sha256(claims),
            "formal_evidence_sha256": _canonical_sha256(formal_evidence),
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
            "- 下列 13 条正式 Evidence 按 T3 权威 Evidence ID 顺序列出。",
        ]
    )
    for position, evidence in enumerate(packet["formal_evidence"], start=1):
        location = evidence["current_location"]
        presentation = evidence["presentation_zh"]
        snippet = evidence["snippet"].replace("\r\n", "\n").replace("\r", "\n")
        lines.extend(
            [
                "",
                f"### {position}. {presentation['name']}（`{evidence['evidence_kind']}`）",
                "",
                f"- Evidence ID：`{evidence['evidence_id']}`",
                f"- 证据组：`{presentation['group']}`",
                f"- 支持判断：{presentation['supports']}",
                f"- 通俗说明：{presentation['explanation']}",
                f"- 源码角色：`{evidence['source_role']}`",
                f"- 位置：`{evidence['path']}:{location['line_start']}-{location['line_end']}`",
                f"- Symbol：`{evidence['symbol']}`",
                f"- 结构锚点：`{evidence['structural_anchor']}`",
                f"- 规范化片段 SHA-256：`{evidence['normalized_snippet_sha256']}`",
                "",
                "```text",
                snippet,
                "```",
            ]
        )
    lines.extend(["", "## 批准后仍保留的未知项", ""])
    lines.extend(f"- {unknown}" for unknown in formal["retained_unknowns"])
    lines.extend(
        [
            "",
            "## 两阶段人工操作",
            "",
            "1. 先审阅本报告；本阶段不创建 GitHub 评论，也不产生 Decision。",
            "2. 先用 `--draft` 生成明确的非权威草稿；仅在 T9-2 获得单独授权后创建该评论，以取得真实 comment ID。",
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


def render_node_review_draft(packet: dict[str, Any]) -> str:
    """Render a comment placeholder that cannot parse as a T8 Decision."""

    object_key = packet["object_key"]
    return "\n".join(
        (
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_DRAFT_V1",
            "Authority: NONE",
            "Status: NON-AUTHORITATIVE",
            (
                "Object-Key: "
                f"{object_key['kind']} + {object_key['parent_module_id']} + "
                f"{object_key['node_id']}"
            ),
            f"Packet-SHA256: {packet['packet_sha256']}",
            (
                "Instruction: Preserve this comment ID; replace this entire draft "
                "only after explicit Owner decision authorization."
            ),
        )
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


def write_node_review_draft(
    packet: dict[str, Any],
    output_directory: Path,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    draft_path = output_directory / "github-comment-draft.txt"
    draft_path.write_text(
        render_node_review_draft(packet),
        encoding="utf-8",
        newline="\n",
    )
    return draft_path


__all__ = [
    "NODE_REVIEW_PACKET_SCHEMA",
    "build_node_review_packet",
    "render_node_review_comment",
    "render_node_review_draft",
    "render_node_review_report",
    "write_node_review_draft",
    "write_node_review_packet",
]
