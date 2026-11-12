from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.generator import write_site
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.node_review import (
    NODE_REVIEW_IMPORT_NOTE,
    _NodeReviewApprovalAnchor as NodeReviewApprovalAnchor,
    _NodeReviewTrustPolicy as NodeReviewTrustPolicy,
    _project_node_review as _apply_node_review,
    _verify_node_review_materials as verify_node_review_materials,
)
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)
from test_t7_top_level_graph import (
    WEB_ASSETS,
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


REPOSITORY = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _published_explorer_cached() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        REPOSITORY / "architecture-explorer/modules.json",
        _json("architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _json("architecture-explorer/reviews/supervisor-worker.v1.json"),
        _json("architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=(
            REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
        ),
    )
    with_facts = attach_python_source_facts(reviewed, scan_python_source_facts(REPOSITORY))
    navigable = apply_navigation(
        with_facts,
        _json("architecture-explorer/navigation.v1.json"),
    )
    fragments = [
        _json(f"architecture-explorer/candidates/{name}.v1.json")
        for name in (
            "document-understanding",
            "scope-and-request",
            "translation-execution",
            "quality-and-repair",
            "production-evidence",
            "production-control",
            "candidate-and-release",
        )
    ]
    explorer = apply_candidate_fragments(navigable, fragments)
    explorer["generation_inputs"].update(
        {
            "navigation": {
                "path": "navigation.v1.json",
                "sha256": _file_sha256("architecture-explorer/navigation.v1.json"),
            },
            "review_mapping": {
                "path": "supervisor-worker.v1.json",
                "sha256": _file_sha256(
                    "architecture-explorer/reviews/supervisor-worker.v1.json"
                ),
            },
            "review_source": {
                "path": "supervisor-worker.source.v1.json",
                "sha256": _file_sha256(
                    "architecture-explorer/reviews/supervisor-worker.source.v1.json"
                ),
            },
            "candidate_fragments": [
                {
                    "path": f"{name}.v1.json",
                    "sha256": _file_sha256(
                        f"architecture-explorer/candidates/{name}.v1.json"
                    ),
                }
                for name in (
                    "document-understanding",
                    "scope-and-request",
                    "translation-execution",
                    "quality-and-repair",
                    "production-evidence",
                    "production-control",
                    "candidate-and-release",
                )
            ],
        }
    )
    return explorer


def _published_explorer() -> dict:
    return deepcopy(_published_explorer_cached())


def _candidate_nodes(explorer: dict) -> list[dict]:
    return [
        node
        for drilldown in explorer["navigation"]["drilldowns"]
        for node in drilldown["children"]
    ]


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(relative_path: str) -> str:
    return hashlib.sha256((REPOSITORY / relative_path).read_bytes()).hexdigest()


def _target(explorer: dict) -> dict:
    return next(
        node
        for drilldown in explorer["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "production-control"
        for node in drilldown["children"]
        if node["id"] == "worker-launch-boundary"
    )


CLASSIFICATION_RATIONALE = (
    "worker-launch-boundary 描述的是生产控制把受参数和守卫约束的启动请求跨越进程边界"
    "交给翻译执行这一稳定架构职责，而不是某一个文件、函数、脚本包装器或可部署组件。"
    "三条 Candidate Evidence 跨越控制侧启动准备与 Worker 入口定位，只支持该职责边界值得"
    "作为候选节点表达；它们不足以证明一个物理组件、完整源码边界或排他所有权。"
)

RETAINED_UNKNOWNS = [
    "是否在某次实际运行中到达或成功跨越该边界",
    "运行时前置条件、凭据和外部依赖是否可用",
    "该职责的完整源码覆盖",
    "任何文件、symbol 或代码区域是否排他归属该节点",
    "观察到的名称、字面值和直接绑定以外的参数业务含义",
    "相邻组件是否获批",
    "任何 Candidate Collaboration 是否获批",
    "顶层模块源码覆盖是否完成",
]

FORMAL_EVIDENCE_IDS = [
    "evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13",
    "evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711",
    "evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3",
    "evidence:sha256:3929e17745639510a1a3c6a39b5be55b5a6afc2070c3f18cb300fa0107983308",
    "evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a",
    "evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc",
    "evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2",
    "evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa",
    "evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2",
    "evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d",
    "evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5",
    "evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914",
    "evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d",
]


def _fixture_source_body(decision: dict) -> str:
    object_key = decision["object_key"]
    inputs = decision["inputs"]
    return "\n".join(
        (
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_V1",
            f"Decision-ID: {decision['decision_id']}",
            f"Result: {decision['result']}",
            (
                "Object-Key: "
                f"{object_key['kind']} + {object_key['parent_module_id']} + "
                f"{object_key['node_id']}"
            ),
            f"Claims-SHA256: {inputs['claims_sha256']}",
            f"Candidate-Fact-SHA256: {inputs['candidate_fact_sha256']}",
            f"Reviewed-Revision: {decision['reviewed_source_snapshot']['revision']}",
        )
    )


def _fixture_source(decision: dict) -> dict:
    comment_id = int(decision["decision_id"].removeprefix("github-comment:"))
    return {
        "schema": "architecture-explorer.github-review-source.v1",
        "repository": {
            "id": 1425303246,
            "node_id": "R_kgDOVU8Qjg",
            "full_name": "nnewlander/translate",
            "html_url": "https://github.com/nnewlander/translate",
            "private": False,
            "owner": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "MDQ6VXNlcjI1NjA0OTc3Mg==",
                "type": "User",
            },
        },
        "issue": {
            "id": 999999001,
            "node_id": "T8_FIXTURE_ISSUE",
            "number": 999999,
            "html_url": "https://github.com/nnewlander/translate/issues/999999",
            "repository_url": "https://api.github.com/repos/nnewlander/translate",
            "state": "open",
        },
        "comment": {
            "id": comment_id,
            "node_id": f"T8_FIXTURE_COMMENT_{comment_id}",
            "html_url": (
                "https://github.com/nnewlander/translate/issues/999999"
                f"#issuecomment-{comment_id}"
            ),
            "issue_url": (
                "https://api.github.com/repos/nnewlander/translate/issues/999999"
            ),
            "author_association": "OWNER",
            "created_at": "2026-11-12T00:00:00Z",
            "updated_at": "2026-11-12T00:00:00Z",
            "body": _fixture_source_body(decision),
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "MDQ6VXNlcjI1NjA0OTc3Mg==",
                "type": "User",
            },
        },
        "permission": {
            "permission": "admin",
            "role_name": "admin",
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "MDQ6VXNlcjI1NjA0OTc3Mg==",
                "type": "User",
            },
        },
    }


def _claims(explorer: dict) -> list[dict]:
    target = _target(explorer)
    return [
        {
            "kind": "IDENTITY",
            "value": {
                "kind": "candidate-node",
                "parent_module_id": "production-control",
                "node_id": "worker-launch-boundary",
            },
            "source": "candidate-fact.object-key",
        },
        {"kind": "NAME", "value": target["name"], "source": "candidate-fact.name"},
        {
            "kind": "CLASSIFICATION",
            "value": {
                "node_type": "responsibility",
                "rationale": CLASSIFICATION_RATIONALE,
            },
            "source": "candidate-fact.node-type",
        },
        {
            "kind": "RESPONSIBILITY",
            "value": target["responsibility"],
            "source": "candidate-fact.responsibility",
        },
        {
            "kind": "PLAIN_EXPLANATION",
            "value": target["plain_explanation"],
            "source": "candidate-fact.plain-explanation",
        },
        {
            "kind": "SOURCE_ASSIGNMENT",
            "value": target["source_assignment"],
            "source": "candidate-fact.source-assignment",
        },
        {
            "kind": "CANDIDATE_EVIDENCE",
            "value": [
                {
                    "evidence_id": "evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a",
                    "role": "powershell_function_entry",
                },
                {
                    "evidence_id": "evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429",
                    "role": "powershell_script_binding",
                },
                {
                    "evidence_id": "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8",
                    "role": "deterministic_worker_wrapper",
                },
            ],
            "source": "candidate-fact.source-assignment.evidence-ids",
        },
        {
            "kind": "FORMAL_DEPENDENCY_AND_LIMITS",
            "value": {
                "relationship_id": (
                    "relationship:production-control:launches:translation-execution"
                ),
                "t3_decision_id": "github-comment:6516894339",
                "t3_result": "APPROVED",
                "t3_reviewed_snapshot": "857a4b85cf4763b74ecff0d02e660b005a0c8bae",
                "formal_evidence_ids": list(FORMAL_EVIDENCE_IDS),
                "retained_unknowns": list(RETAINED_UNKNOWNS),
            },
            "source": "formal-relationship.review-contract",
        },
    ]


def _fixture_decision(explorer: dict, result: str = "APPROVED") -> dict:
    target = _target(explorer)
    claims = _claims(explorer)
    candidate_fact = {
        key: target[key]
        for key in (
            "id",
            "node_type",
            "name",
            "responsibility",
            "plain_explanation",
            "review_status",
            "source_assignment",
        )
    }
    decision = {
        "schema": "architecture-explorer.node-review-decision.v1",
        "decision_id": (
            "github-comment:999999002"
            if result == "APPROVED"
            else "github-comment:999999003"
        ),
        "object_key": {
            "kind": "candidate-node",
            "parent_module_id": "production-control",
            "node_id": "worker-launch-boundary",
        },
        "result": result,
        "reviewed_source_snapshot": {
            "revision": "95e219a926023aff0022dbe0174e9a361e20c8b1",
            "worktree_state": "clean",
        },
        "review_notes": NODE_REVIEW_IMPORT_NOTE,
        "source_receipt_sha256": "pending-fixture-receipt",
        "inputs": {
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
            "candidate_fact_sha256": _canonical_sha256(candidate_fact),
            "claims_sha256": _canonical_sha256(claims),
        },
        "claims": claims,
    }
    decision["source_receipt_sha256"] = _canonical_sha256(_fixture_source(decision))
    return decision


def _fixture_materials(
    explorer: dict,
    result: str = "APPROVED",
) -> tuple[dict, dict]:
    decision = _fixture_decision(explorer, result=result)
    return _fixture_source(decision), decision


_FIXED_TEST_NODE_REVIEW_POLICY = NodeReviewTrustPolicy(
    (
        NodeReviewApprovalAnchor(
            issue_id=999999001,
            issue_number=999999,
            comment_id=999999002,
            comment_created_at="2026-11-12T00:00:00Z",
            comment_updated_at="2026-11-12T00:00:00Z",
            source_receipt_sha256=(
                "237a8f73b9deb17a1cc0698288ac64d13127a4b9e72074f55abb42ed5e88a2d0"
            ),
        ),
        NodeReviewApprovalAnchor(
            issue_id=999999001,
            issue_number=999999,
            comment_id=999999003,
            comment_created_at="2026-11-12T00:00:00Z",
            comment_updated_at="2026-11-12T00:00:00Z",
            source_receipt_sha256=(
                "8ba030883fbbd8241ca84ab61bb175a1d2725a4032c7972b00fcfb7bfcb89916"
            ),
        ),
    )
)


def _fixture_policy() -> NodeReviewTrustPolicy:
    """Return the literal test-only trust root; never derive it from fixture data."""

    return _FIXED_TEST_NODE_REVIEW_POLICY


def apply_node_review(
    explorer: dict,
    *,
    decision: dict | None = None,
    review_source: dict | None = None,
    trust_policy: NodeReviewTrustPolicy | None = None,
) -> dict:
    """Test adapter: fixture policy stays outside the production projector API."""

    if decision is None:
        return _apply_node_review(explorer)
    assert review_source is not None
    assert trust_policy is not None
    verified_decision, verified_source = verify_node_review_materials(
        explorer,
        decision,
        review_source,
        trust_policy=trust_policy,
    )
    return _apply_node_review(
        explorer,
        decision=verified_decision,
        review_source=verified_source,
    )


def test_no_node_decision_projects_only_the_exact_target_as_pending() -> None:
    explorer = _published_explorer()
    before = deepcopy(explorer)

    result = apply_node_review(explorer)

    assert explorer == before
    targets = [node for node in _candidate_nodes(result) if "node_review" in node]
    assert len(_candidate_nodes(result)) == 18
    assert len(targets) == 1
    assert targets[0]["id"] == "worker-launch-boundary"
    assert targets[0]["review_status"] == "candidate"
    assert targets[0]["node_review"] == {
        "object_key": {
            "kind": "candidate-node",
            "parent_module_id": "production-control",
            "node_id": "worker-launch-boundary",
        },
        "decision": None,
        "state": {
            "status": "not-reviewed",
            "continuity": "not-assessed",
            "reasons": ["trusted-node-review-decision:missing"],
        },
        "projection": "Pending Review",
    }
    assert all(
        "node_review" not in collaboration
        for drilldown in result["navigation"]["drilldowns"]
        for collaboration in drilldown["collaborations"]
    )


def test_trusted_approved_decision_requires_current_exact_state_before_projection() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)

    result = apply_node_review(
        explorer,
        decision=decision,
        review_source=source,
        trust_policy=_fixture_policy(),
    )

    review = _target(result)["node_review"]
    assert review["decision"]["result"] == "APPROVED"
    assert review["state"] == {
        "status": "current",
        "continuity": "exact-match",
        "decision_id": "github-comment:999999002",
        "reasons": [],
    }
    assert review["projection"] == "Current · Approved"
    assert [claim["kind"] for claim in review["decision"]["claims"]] == [
        "IDENTITY",
        "NAME",
        "CLASSIFICATION",
        "RESPONSIBILITY",
        "PLAIN_EXPLANATION",
        "SOURCE_ASSIGNMENT",
        "CANDIDATE_EVIDENCE",
        "FORMAL_DEPENDENCY_AND_LIMITS",
    ]


def test_node_review_decision_rejects_unknown_top_level_fields() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    decision["approval_shortcut"] = True

    with pytest.raises(ExplorerInputError, match="closed schema"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_node_review_source_rejects_non_user_owner_identity() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    source["comment"]["user"]["type"] = "Bot"
    decision["source_receipt_sha256"] = _canonical_sha256(source)

    with pytest.raises(ExplorerInputError, match="untrusted|independently trusted"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_decision_cannot_redefine_the_fixed_candidate_fact_and_resign_it() -> None:
    explorer = _published_explorer()
    _target(explorer)["name"] = "近似 Worker 启动边界"
    source, decision = _fixture_materials(explorer)

    with pytest.raises(ExplorerInputError, match="Candidate Fact"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize(
    ("decision_result", "expected_projection"),
    (
        (None, "Pending Review"),
        ("APPROVED", "Current · Approved"),
        ("REJECTED", "Current · Rejected"),
    ),
)
def test_real_browser_reuses_candidate_card_and_context_evidence_detail(
    tmp_path: Path,
    decision_result: str | None,
    expected_projection: str,
) -> None:
    browser_name, browser = _desktop_browser()
    site = tmp_path / "site"
    explorer = _published_explorer()
    reviewed = apply_node_review(explorer)
    if decision_result is not None:
        source, decision = _fixture_materials(explorer, result=decision_result)
        reviewed = apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )
    write_site(reviewed, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t8_node_review_dom.js",
        site / "t8-node-review-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            f"<script>window.T8_EXPECTED_PROJECTION = {json.dumps(expected_projection)};"
            "</script><script src=\"t8-node-review-dom.js\"></script></body>",
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=4000,
        viewport_width=1280,
        viewport_height=900,
    )
    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t8-node-review="pass"',
    )


def test_trusted_rejected_decision_is_current_but_never_pending_or_approved() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result="REJECTED")

    result = apply_node_review(
        explorer,
        decision=decision,
        review_source=source,
        trust_policy=_fixture_policy(),
    )

    review = _target(result)["node_review"]
    assert review["decision"]["result"] == "REJECTED"
    assert review["state"]["status"] == "current"
    assert review["state"]["continuity"] == "exact-match"
    assert review["projection"] == "Current · Rejected"


@pytest.mark.parametrize(
    "claim_kind",
    (
        "IDENTITY",
        "NAME",
        "CLASSIFICATION",
        "RESPONSIBILITY",
        "PLAIN_EXPLANATION",
        "SOURCE_ASSIGNMENT",
        "CANDIDATE_EVIDENCE",
        "FORMAL_DEPENDENCY_AND_LIMITS",
    ),
)
def test_each_review_claim_requires_its_exact_source_binding(claim_kind: str) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    claim = next(item for item in decision["claims"] if item["kind"] == claim_kind)
    claim["source"] = "nearby-but-untrusted-source"
    decision["inputs"]["claims_sha256"] = _canonical_sha256(decision["claims"])

    with pytest.raises(ExplorerInputError, match="Claim binding mismatch"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "unknown", "only-seven"))
def test_review_claims_are_a_closed_complete_set(mutation: str) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    if mutation in {"missing", "only-seven"}:
        decision["claims"].pop()
    elif mutation == "duplicate":
        decision["claims"].append(deepcopy(decision["claims"][0]))
    else:
        decision["claims"][-1]["kind"] = "IMPLIED_APPROVAL"
    decision["inputs"]["claims_sha256"] = _canonical_sha256(decision["claims"])

    with pytest.raises(ExplorerInputError, match="closed ordered set"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_candidate_and_formal_evidence_stay_exact_and_separate_without_mutation() -> None:
    explorer = _published_explorer()
    before = deepcopy(explorer)
    source, decision = _fixture_materials(explorer)

    result = apply_node_review(
        explorer,
        decision=decision,
        review_source=source,
        trust_policy=_fixture_policy(),
    )

    assert explorer == before
    target_review = _target(result)["node_review"]
    candidate_claim = next(
        claim
        for claim in target_review["decision"]["claims"]
        if claim["kind"] == "CANDIDATE_EVIDENCE"
    )
    formal_claim = next(
        claim
        for claim in target_review["decision"]["claims"]
        if claim["kind"] == "FORMAL_DEPENDENCY_AND_LIMITS"
    )
    candidate_ids = [item["evidence_id"] for item in candidate_claim["value"]]
    formal_ids = formal_claim["value"]["formal_evidence_ids"]
    assert len(candidate_ids) == 3
    assert len(formal_ids) == 13
    assert set(candidate_ids).isdisjoint(formal_ids)
    assert result["architecture"] == before["architecture"]
    assert sum(
        "node_review" in node for node in _candidate_nodes(result)
    ) == 1
    assert all(
        "node_review" not in collaboration
        for drilldown in result["navigation"]["drilldowns"]
        for collaboration in drilldown["collaborations"]
    )


@pytest.mark.parametrize(
    "source_mutation",
    (
        "repository",
        "issue",
        "author",
        "association",
        "permission",
        "empty-body",
        "timestamp-order",
    ),
)
def test_node_review_requires_exact_owner_admin_source_identity(
    source_mutation: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    if source_mutation == "repository":
        source["repository"]["full_name"] = "nnewlander/nearby"
    elif source_mutation == "issue":
        source["issue"]["html_url"] = "https://github.com/nnewlander/translate/issues/1"
    elif source_mutation == "author":
        source["comment"]["user"]["login"] = "claimed-owner"
    elif source_mutation == "association":
        source["comment"]["author_association"] = "MEMBER"
    elif source_mutation == "permission":
        source["permission"]["permission"] = "write"
    elif source_mutation == "empty-body":
        source["comment"]["body"] = ""
    else:
        source["comment"]["updated_at"] = "2026-11-11T23:59:59Z"
    decision["source_receipt_sha256"] = _canonical_sha256(source)

    with pytest.raises(ExplorerInputError, match="untrusted|independently trusted"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_tampered_source_receipt_cannot_match_the_pinned_digest() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    trusted_policy = _fixture_policy()
    source["comment"]["body"] += " tampered"
    decision["source_receipt_sha256"] = _canonical_sha256(source)

    with pytest.raises(ExplorerInputError, match="digest-mismatch"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=trusted_policy,
        )


def test_decision_result_cannot_change_independently_of_the_source_comment() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result="APPROVED")
    decision["result"] = "REJECTED"

    with pytest.raises(ExplorerInputError, match="comment body"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize(
    "binding",
    (
        "candidate_fragment_sha256",
        "navigation_sha256",
        "t3_review_map_sha256",
        "t3_review_source_sha256",
        "candidate_scanner_version",
        "relationship_candidate_scanner_version",
        "candidate_fact_sha256",
        "claims_sha256",
    ),
)
def test_each_versioned_input_binding_is_material(binding: str) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    decision["inputs"][binding] = "wrong-versioned-input"

    with pytest.raises(ExplorerInputError, match="input binding mismatch"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_current_versioned_inputs_cannot_drift_while_decision_hashes_stay_pinned() -> None:
    explorer = _published_explorer()
    explorer["generation_inputs"]["navigation"]["sha256"] = "0" * 64
    source, decision = _fixture_materials(explorer)

    with pytest.raises(ExplorerInputError, match="current versioned input"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize(
    "claim_mutation",
    (
        "classification-component",
        "missing-rationale",
        "candidate-evidence-subset",
        "candidate-evidence-role-swap",
        "formal-evidence-subset",
        "candidate-formal-mix",
        "missing-unknown",
    ),
)
def test_claim_values_cannot_partially_match_or_cross_evidence_boundaries(
    claim_mutation: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    claims = {item["kind"]: item for item in decision["claims"]}
    if claim_mutation == "classification-component":
        claims["CLASSIFICATION"]["value"]["node_type"] = "component"
    elif claim_mutation == "missing-rationale":
        del claims["CLASSIFICATION"]["value"]["rationale"]
    elif claim_mutation == "candidate-evidence-subset":
        claims["CANDIDATE_EVIDENCE"]["value"].pop()
    elif claim_mutation == "candidate-evidence-role-swap":
        values = claims["CANDIDATE_EVIDENCE"]["value"]
        values[0]["role"], values[1]["role"] = values[1]["role"], values[0]["role"]
    elif claim_mutation == "formal-evidence-subset":
        claims["FORMAL_DEPENDENCY_AND_LIMITS"]["value"]["formal_evidence_ids"].pop()
    elif claim_mutation == "candidate-formal-mix":
        claims["FORMAL_DEPENDENCY_AND_LIMITS"]["value"]["formal_evidence_ids"][0] = (
            claims["CANDIDATE_EVIDENCE"]["value"][0]["evidence_id"]
        )
    else:
        claims["FORMAL_DEPENDENCY_AND_LIMITS"]["value"]["retained_unknowns"].pop()
    decision["inputs"]["claims_sha256"] = _canonical_sha256(decision["claims"])

    with pytest.raises(ExplorerInputError, match="Claim binding mismatch"):
        apply_node_review(
            explorer,
            decision=decision,
            review_source=source,
            trust_policy=_fixture_policy(),
        )


def test_repository_contains_no_production_node_review_decision() -> None:
    review_files = list((REPOSITORY / "architecture-explorer/reviews").glob("*.json"))
    assert review_files
    assert all(
        "architecture-explorer.node-review-decision.v1"
        not in path.read_text(encoding="utf-8")
        for path in review_files
    )
