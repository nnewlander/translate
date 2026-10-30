from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_candidate_report(candidate: dict[str, Any]) -> str:
    relation = candidate["candidate_relation"]
    snapshot = candidate["source_snapshot"]
    lines = [
        "# Architecture Explorer T2：Supervisor → Worker 候选证据报告",
        "",
        "> 本报告由确定性扫描器从机器候选记录生成。它是待人工审核的候选材料，不是架构批准。",
        "",
        "## 扫描元数据",
        "",
        f"- 源码 Commit：`{snapshot['revision']}`",
        f"- Worktree 状态：`{snapshot['worktree']['state']}`",
        f"- 扫描器：`{candidate['scanner_version']}`",
        f"- 扫描时间：`{candidate['scanned_at']}`",
        f"- 候选报告摘要 SHA-256：`{candidate['candidate_report_sha256']}`",
        f"- 扫描结果：`{relation['scan_outcome']}`",
        "",
        "## 封闭扫描范围",
        "",
    ]
    for path in candidate["scan_scope"]["production_files"]:
        lines.append(f"- 生产源码：`{path}`")
    for path in candidate["scan_scope"]["auxiliary_test_files"]:
        lines.append(f"- 辅助测试：`{path}`（只作辅助证据）")
    lines.extend(
        [
            "",
            "## 候选关系（未审核）",
            "",
            f"- 状态：`{relation['status']}` / `{relation['review_status']}`",
            f"- 建议方向：`{relation['direction']}`",
            f"- 建议关系类型：`{relation['proposed_relationship_type']}`",
            f"- 静态可达状态：`{relation['reachability']['status']}`",
            f"- 真实运行状态：`{relation['runtime_state']}`",
            "",
            "### 静态扫描能够陈述的条件",
            "",
        ]
    )
    proven = relation["reachability"]["proven_conditions"]
    lines.extend(f"- {item}" for item in proven)
    if not proven:
        lines.append("- 无；必需证据不完整或存在歧义。")
    lines.extend(["", "### 保留未知事项", ""])
    lines.extend(f"- {item}" for item in relation["reachability"]["retained_unknowns"])
    lines.extend(
        [
            "",
            "## 必需证据判定",
            "",
            "| 证据种类 | 扫描状态 | 证据 ID |",
            "|---|---|---|",
        ]
    )
    for kind, finding in candidate["required_findings"].items():
        ids = "<br>".join(f"`{item}`" for item in finding["evidence_ids"]) or "—"
        lines.append(f"| `{kind}` | `{finding['status']}` | {ids} |")
    lines.extend(["", "## 源码证据", ""])
    for item in candidate["evidence"]:
        role = "生产事实" if item["source_role"] == "production" else "辅助测试"
        location = item["current_location"]
        report_snippet = item["snippet"].replace("\r\n", "\n").replace("\r", "\n")
        lines.extend(
            [
                f"### `{item['evidence_kind']}` — {role}",
                "",
                f"- Evidence ID：`{item['evidence_id']}`",
                f"- 位置：`{item['path']}:{location['line_start']}`",
                f"- Symbol：`{item['symbol']}`",
                f"- 结构锚点：`{item['structural_anchor']}`",
                f"- 语法位置：`{item['syntax_position']}`",
                f"- 片段 SHA-256：`{item['normalized_snippet_sha256']}`",
                f"- 文件 SHA-256：`{item['file_sha256']}`",
                "",
                "```text",
                report_snippet,
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 人工审核门禁",
            "",
            "以下模板只能由授权审核者在 GitHub Issue #3 中提交。实施 Agent 不得填写批准结论。",
            "",
            "```text",
            "ARCHITECTURE_REVIEW_DECISION",
            "decision: APPROVED | REJECTED",
            f"reviewed_source_commit: {snapshot['revision']}",
            f"reviewed_worktree_state: {snapshot['worktree']['state']}",
            f"candidate_report_sha256: {candidate['candidate_report_sha256']}",
            f"scanner_version: {candidate['scanner_version']}",
            "approved_evidence_ids:",
            "  - <authorized reviewer selects exact IDs>",
            "source_module: production-control",
            "target_module: translation-execution",
            "direction: production-control -> translation-execution",
            "relationship_type: process-launch",
            "approved_reachability: <condition or UNKNOWN>",
            "runtime_state: NOT_COLLECTED",
            "retained_unknowns: <explicit list>",
            "review_notes: <notes>",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_candidate_outputs(candidate: dict[str, Any], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    machine_path = output_directory / "candidate.json"
    report_path = output_directory / "candidate-review.md"
    machine_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_candidate_report(candidate), encoding="utf-8", newline="\n")
    return machine_path, report_path
