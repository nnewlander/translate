from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError
from .review_source import review_source_sha256, verify_owner_admin_review_source


NODE_REVIEW_OBJECT_KEY = {
    "kind": "candidate-node",
    "parent_module_id": "production-control",
    "node_id": "worker-launch-boundary",
}
NODE_REVIEW_DECISION_SCHEMA = "architecture-explorer.node-review-decision.v1"
REVIEWED_REVISION = "95e219a926023aff0022dbe0174e9a361e20c8b1"
CLAIM_KINDS = (
    "IDENTITY",
    "NAME",
    "CLASSIFICATION",
    "RESPONSIBILITY",
    "PLAIN_EXPLANATION",
    "SOURCE_ASSIGNMENT",
    "CANDIDATE_EVIDENCE",
    "FORMAL_DEPENDENCY_AND_LIMITS",
)
CLASSIFICATION_RATIONALE = (
    "worker-launch-boundary 描述的是生产控制把受参数和守卫约束的启动请求跨越进程边界"
    "交给翻译执行这一稳定架构职责，而不是某一个文件、函数、脚本包装器或可部署组件。"
    "三条 Candidate Evidence 跨越控制侧启动准备与 Worker 入口定位，只支持该职责边界值得"
    "作为候选节点表达；它们不足以证明一个物理组件、完整源码边界或排他所有权。"
)
RETAINED_UNKNOWNS = (
    "是否在某次实际运行中到达或成功跨越该边界",
    "运行时前置条件、凭据和外部依赖是否可用",
    "该职责的完整源码覆盖",
    "任何文件、symbol 或代码区域是否排他归属该节点",
    "观察到的名称、字面值和直接绑定以外的参数业务含义",
    "相邻组件是否获批",
    "任何 Candidate Collaboration 是否获批",
    "顶层模块源码覆盖是否完成",
)
VERSIONED_INPUTS = {
    "candidate_fragment_sha256": (
        "201c4283ccbf2850395c5a326dee0a5a8462fb8194e05df755e743bee731e669"
    ),
    "navigation_sha256": (
        "e4589d88bf091a318561bf525a9e24657bb5f55ab21fa9e8e50eae243fd14f70"
    ),
    "t3_review_map_sha256": (
        "03ad7e0b78c454326dbeab311d6133ed141c4bc1b54de0c6bf537ae9e4c707f1"
    ),
    "t3_review_source_sha256": (
        "62c296838c8d76c6e621ebb473e38c3b45b67d970c88a41b59cf73ed2de6ad2b"
    ),
    "candidate_scanner_version": "architecture-explorer-t6-4/1.5",
    "relationship_candidate_scanner_version": "architecture-explorer-t2/1.3",
}
EXPECTED_CANDIDATE_FACT = {
    "id": "worker-launch-boundary",
    "node_type": "responsibility",
    "name": "Worker 启动边界",
    "responsibility": (
        "候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。"
    ),
    "plain_explanation": (
        "像一道受控发车闸门，Supervisor 准备好明确的 Worker 脚本后才跨进程启动。"
        "这里新增的是未审核源码说明；唯一正式 Supervisor→Worker 关系仍以原有 13 条批准证据为准。"
    ),
    "review_status": "candidate",
    "source_assignment": {
        "status": "partial",
        "note": (
            "T3 已审核关系只支持启动边界及其证据链；单条调用关系不足以证明完整源码归属。"
        ),
        "relationship_ids": [
            "relationship:production-control:launches:translation-execution"
        ],
        "evidence_ids": [
            "evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a",
            "evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429",
            "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8",
        ],
    },
}


def _target_node(explorer: dict[str, Any]) -> dict[str, Any]:
    matches = [
        node
        for drilldown in explorer.get("navigation", {}).get("drilldowns", [])
        if drilldown.get("parent_module_id") == NODE_REVIEW_OBJECT_KEY["parent_module_id"]
        for node in drilldown.get("children", [])
        if node.get("id") == NODE_REVIEW_OBJECT_KEY["node_id"]
    ]
    if len(matches) != 1:
        raise ExplorerInputError("Node review target must resolve exactly once")
    return matches[0]


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _expected_source_body(decision: dict[str, Any]) -> str:
    inputs = decision.get("inputs", {})
    object_key = decision.get("object_key", {})
    return "\n".join(
        (
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_V1",
            f"Decision-ID: {decision.get('decision_id')}",
            f"Result: {decision.get('result')}",
            (
                "Object-Key: "
                f"{object_key.get('kind')} + {object_key.get('parent_module_id')} + "
                f"{object_key.get('node_id')}"
            ),
            f"Claims-SHA256: {inputs.get('claims_sha256')}",
            f"Candidate-Fact-SHA256: {inputs.get('candidate_fact_sha256')}",
            f"Reviewed-Revision: {decision.get('reviewed_source_snapshot', {}).get('revision')}",
        )
    )


def _relationship(explorer: dict[str, Any]) -> dict[str, Any]:
    relationship_id = "relationship:production-control:launches:translation-execution"
    matches = [
        item
        for item in explorer.get("architecture", {}).get("relationships", [])
        if item.get("id") == relationship_id
    ]
    if len(matches) != 1:
        raise ExplorerInputError("Node review formal relationship must resolve exactly once")
    return matches[0]


def _source_evidence(explorer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            evidence = value.get("evidence")
            if isinstance(evidence, dict) and isinstance(evidence.get("evidence_id"), str):
                found[evidence["evidence_id"]] = evidence
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(explorer.get("source_facts", {}))
    return found


def _candidate_fact(target: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "node_type",
        "name",
        "responsibility",
        "plain_explanation",
        "review_status",
        "source_assignment",
    )
    return {key: target.get(key) for key in keys}


def _current_versioned_inputs(
    explorer: dict[str, Any],
    relationship: dict[str, Any],
) -> dict[str, Any]:
    generation = explorer.get("generation_inputs", {})
    fragments = generation.get("candidate_fragments", [])
    production_fragment = next(
        (
            item
            for item in fragments
            if isinstance(item, dict) and item.get("path") == "production-control.v1.json"
        ),
        {},
    )
    formal_decision = relationship.get("review_decision", {})
    return {
        "candidate_fragment_sha256": production_fragment.get("sha256"),
        "navigation_sha256": generation.get("navigation", {}).get("sha256"),
        "t3_review_map_sha256": generation.get("review_mapping", {}).get("sha256"),
        "t3_review_source_sha256": generation.get("review_source", {}).get("sha256"),
        "candidate_scanner_version": explorer.get("source_facts", {}).get(
            "scanner_version"
        ),
        "relationship_candidate_scanner_version": formal_decision.get(
            "reviewed_source_snapshot", {}
        ).get("scanner_version"),
    }


def _expected_claims(
    explorer: dict[str, Any],
    target: dict[str, Any],
    relationship: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = _source_evidence(explorer)
    candidate_ids = target.get("source_assignment", {}).get("evidence_ids", [])
    candidate_evidence = []
    for evidence_id in candidate_ids:
        item = evidence.get(evidence_id)
        if item is None:
            raise ExplorerInputError(f"Node review Candidate Evidence is missing: {evidence_id}")
        candidate_evidence.append(
            {"evidence_id": evidence_id, "role": item.get("evidence_kind")}
        )
    formal_decision = relationship.get("review_decision")
    if not isinstance(formal_decision, dict):
        raise ExplorerInputError("Node review needs the existing T3 Decision")
    return [
        {
            "kind": "IDENTITY",
            "value": dict(NODE_REVIEW_OBJECT_KEY),
            "source": "candidate-fact.object-key",
        },
        {
            "kind": "NAME",
            "value": target.get("name"),
            "source": "candidate-fact.name",
        },
        {
            "kind": "CLASSIFICATION",
            "value": {
                "node_type": target.get("node_type"),
                "rationale": CLASSIFICATION_RATIONALE,
            },
            "source": "candidate-fact.node-type",
        },
        {
            "kind": "RESPONSIBILITY",
            "value": target.get("responsibility"),
            "source": "candidate-fact.responsibility",
        },
        {
            "kind": "PLAIN_EXPLANATION",
            "value": target.get("plain_explanation"),
            "source": "candidate-fact.plain-explanation",
        },
        {
            "kind": "SOURCE_ASSIGNMENT",
            "value": target.get("source_assignment"),
            "source": "candidate-fact.source-assignment",
        },
        {
            "kind": "CANDIDATE_EVIDENCE",
            "value": candidate_evidence,
            "source": "candidate-fact.source-assignment.evidence-ids",
        },
        {
            "kind": "FORMAL_DEPENDENCY_AND_LIMITS",
            "value": {
                "relationship_id": relationship.get("id"),
                "t3_decision_id": formal_decision.get("decision_id"),
                "t3_result": formal_decision.get("result"),
                "t3_reviewed_snapshot": formal_decision.get(
                    "reviewed_source_snapshot", {}
                ).get("revision"),
                "formal_evidence_ids": relationship.get("evidence_ids"),
                "retained_unknowns": list(RETAINED_UNKNOWNS),
            },
            "source": "formal-relationship.review-contract",
        },
    ]


def _validate_decision(
    explorer: dict[str, Any],
    target: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any] | None,
    trusted_source_sha256: str | None,
) -> None:
    expected_fields = {
        "schema",
        "decision_id",
        "object_key",
        "result",
        "reviewed_source_snapshot",
        "review_notes",
        "source_receipt_sha256",
        "inputs",
        "claims",
    }
    if set(decision) != expected_fields:
        raise ExplorerInputError("Node Review Decision must use the closed schema")
    if _candidate_fact(target) != EXPECTED_CANDIDATE_FACT:
        raise ExplorerInputError("Node Review Candidate Fact does not match the fixed input")
    if trusted_source_sha256 is None:
        raise ExplorerInputError("Node review has no pinned trusted source digest")
    source_errors = verify_owner_admin_review_source(
        review_source,
        expected_sha256=trusted_source_sha256,
    )
    if source_errors:
        raise ExplorerInputError(f"Node review source is untrusted: {source_errors}")
    if decision.get("schema") != NODE_REVIEW_DECISION_SCHEMA:
        raise ExplorerInputError("Unsupported Node Review Decision schema")
    if decision.get("object_key") != NODE_REVIEW_OBJECT_KEY:
        raise ExplorerInputError("Node Review Decision object key mismatch")
    if decision.get("result") not in {"APPROVED", "REJECTED"}:
        raise ExplorerInputError("Unsupported Node Review Decision result")
    source = review_source or {}
    comment = source.get("comment") if isinstance(source.get("comment"), dict) else {}
    if decision.get("decision_id") != f"github-comment:{comment.get('id')}":
        raise ExplorerInputError("Node Review Decision source comment mismatch")
    if decision.get("source_receipt_sha256") != review_source_sha256(source):
        raise ExplorerInputError("Node Review Decision source receipt mismatch")
    if comment.get("body") != _expected_source_body(decision):
        raise ExplorerInputError("Node Review Decision does not match the source comment body")
    if decision.get("reviewed_source_snapshot") != {
        "revision": REVIEWED_REVISION,
        "worktree_state": "clean",
    }:
        raise ExplorerInputError("Node Review Decision reviewed snapshot mismatch")

    relationship = _relationship(explorer)
    if relationship.get("review_status") != "current-reviewed" or relationship.get(
        "source_continuity", {}
    ).get("status") != "exact-match":
        raise ExplorerInputError("Node review formal dependency is not current and exact")
    if _current_versioned_inputs(explorer, relationship) != VERSIONED_INPUTS:
        raise ExplorerInputError("Node review current versioned input binding mismatch")
    claims = decision.get("claims")
    if not isinstance(claims, list):
        raise ExplorerInputError("Node Review Decision Claims must be a list")
    kinds = [claim.get("kind") for claim in claims if isinstance(claim, dict)]
    if tuple(kinds) != CLAIM_KINDS or len(kinds) != len(claims):
        raise ExplorerInputError("Node Review Decision Claims are not the closed ordered set")
    expected_claims = _expected_claims(explorer, target, relationship)
    if claims != expected_claims:
        raise ExplorerInputError("Node Review Decision Claim binding mismatch")

    inputs = decision.get("inputs")
    if not isinstance(inputs, dict):
        raise ExplorerInputError("Node Review Decision inputs must be an object")
    expected_inputs = {
        **VERSIONED_INPUTS,
        "candidate_fact_sha256": _canonical_sha256(_candidate_fact(target)),
        "claims_sha256": _canonical_sha256(expected_claims),
    }
    if inputs != expected_inputs:
        raise ExplorerInputError("Node Review Decision input binding mismatch")


def apply_node_review(
    explorer: dict[str, Any],
    *,
    decision: dict[str, Any] | None = None,
    review_source: dict[str, Any] | None = None,
    trusted_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Project the fixed T8 node review without mutating candidate facts."""

    result = deepcopy(explorer)
    target = _target_node(result)
    if decision is None:
        target["node_review"] = {
            "object_key": dict(NODE_REVIEW_OBJECT_KEY),
            "decision": None,
            "state": {
                "status": "not-reviewed",
                "continuity": "not-assessed",
                "reasons": ["trusted-node-review-decision:missing"],
            },
            "projection": "Pending Review",
        }
        return result

    _validate_decision(
        result,
        target,
        decision,
        review_source,
        trusted_source_sha256,
    )
    decision_result = decision["result"]
    source = review_source or {}
    repository = source.get("repository", {})
    comment = source.get("comment", {})
    permission = source.get("permission", {})
    target["node_review"] = {
        "object_key": dict(NODE_REVIEW_OBJECT_KEY),
        "decision": deepcopy(decision),
        "source": {
            "status": "trusted-owner-admin",
            "sha256": review_source_sha256(source),
            "repository": repository.get("full_name"),
            "comment_url": comment.get("html_url"),
            "author": comment.get("user", {}).get("login"),
            "author_association": comment.get("author_association"),
            "permission": permission.get("permission"),
        },
        "state": {
            "status": "current",
            "continuity": "exact-match",
            "decision_id": decision["decision_id"],
            "reasons": [],
        },
        "projection": (
            "Current · Approved" if decision_result == "APPROVED" else "Current · Rejected"
        ),
    }
    return result


__all__ = [
    "CLAIM_KINDS",
    "NODE_REVIEW_DECISION_SCHEMA",
    "NODE_REVIEW_OBJECT_KEY",
    "apply_node_review",
]
