from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError, write_site
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.review_cli import main as review_main
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)
from t5_lite_fixture import commit, create_source_repository


REPOSITORY = Path(__file__).resolve().parents[2]
CANDIDATE = REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"
REVIEW_SOURCE = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"
REVIEW_STATE = REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
SNAPSHOT = REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP = REPOSITORY / "architecture-explorer/modules.json"
NAVIGATION = REPOSITORY / "architecture-explorer/navigation.v1.json"
DOCUMENT_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/document-understanding.v1.json"
)
SCOPE_FRAGMENT = REPOSITORY / "architecture-explorer/candidates/scope-and-request.v1.json"
PRODUCTION_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/production-control.v1.json"
)
TRANSLATION_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/translation-execution.v1.json"
)
QUALITY_FRAGMENT = REPOSITORY / "architecture-explorer/candidates/quality-and-repair.v1.json"
PRODUCTION_EVIDENCE_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/production-evidence.v1.json"
)
CANDIDATE_RELEASE_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/candidate-and-release.v1.json"
)

EXPECTED_T6_3_EVIDENCE_SEMANTICS = {
    "translation-routing-and-acquisition": {
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.providers.router",
            "route_semantic_unit",
        ),
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.providers.translation_provider",
            "project_plain_candidate_to_raw",
        ),
    },
    "translation-provider-adapters": {
        ("src/booktrans/providers/qwen_mt.py", "translation_provider", "TranslationProviderRequest"),
        ("src/booktrans/providers/qwen_max.py", "translation_provider", "TranslationProviderRequest"),
    },
    "candidate-collaboration:translation-routing-to-provider": {
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.providers.router",
            "route_semantic_unit",
        ),
        ("src/booktrans/providers/router.py", "booktrans.providers.qwen_max", "QWEN_MAX"),
        ("src/booktrans/providers/router.py", "booktrans.providers.qwen_mt", "QWEN_MT_PLUS"),
    },
    "quality-deterministic-qa": {
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.production_worker.qa",
            "qa_codes",
        ),
        (
            "src/booktrans/production_worker/qa.py",
            "booktrans.domain.calibration",
            "TranslationResult",
        ),
        (
            "src/booktrans/production_worker/qa.py",
            "booktrans.harness.tagged_roundtrip",
            "FormatAnchorQA",
        ),
    },
    "quality-semantic-review-and-repair": {
        (
            "src/booktrans/production_worker/semantic_review_run.py",
            "booktrans.production_worker.reviewer_context",
            "ReviewerContextUnit",
        ),
        (
            "src/booktrans/production_worker/semantic_review_run.py",
            "booktrans.production_worker.group_repair",
            "decide_semantic_group_repair_contract",
        ),
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.providers.repair_gate",
            "decide_approved_repair_contract",
        ),
        (
            "src/booktrans/providers/repair_gate.py",
            "booktrans.providers.qwen_max",
            "ApprovedRepairContract",
        ),
    },
    "candidate-collaboration:quality-qa-to-semantic-repair": {
        (
            "src/booktrans/production_worker/semantic_review_run.py",
            "booktrans.production_worker.qa",
            "qa_codes",
        ),
        (
            "src/booktrans/production_worker/semantic_review_run.py",
            "booktrans.production_worker.group_repair",
            "decide_semantic_group_repair_contract",
        ),
        (
            "src/booktrans/production_worker/pipeline.py",
            "booktrans.providers.repair_gate",
            "decide_approved_repair_contract",
        ),
    },
}

EXPECTED_T6_5_EVIDENCE_SEMANTICS = {
    "checkpoint-evidence-persistence": {
        (
            "src/booktrans/production_worker/production_checkpoint.py",
            "booktrans.production_worker.artifacts",
            "write_json_once",
        ),
    },
    "checkpoint-manifest-seal": {
        (
            "src/booktrans/production_worker/production_checkpoint.py",
            "booktrans.harness.batch_manifest",
            "sealed_checkpoint_manifest_projection",
        ),
    },
    "candidate-collaboration:checkpoint-evidence-to-manifest-seal": {
        (
            "src/booktrans/production_worker/production_checkpoint.py",
            "booktrans.production_worker.artifacts",
            "write_json_once",
        ),
        (
            "src/booktrans/production_worker/production_checkpoint.py",
            "booktrans.harness.batch_manifest",
            "sealed_checkpoint_manifest_projection",
        ),
    },
    "candidate-document-projection": {
        (
            "src/booktrans/harness/tagged_roundtrip.py",
            "booktrans.ooxml.tagged_projection",
            "apply_tagged_projections",
        ),
    },
    "package-preservation-verifier": {
        (
            "src/booktrans/harness/tagged_roundtrip.py",
            "booktrans.harness.package_preservation",
            "verify_package_preservation",
        ),
        (
            "src/booktrans/harness/package_preservation.py",
            "booktrans.ooxml.package",
            "open_docx_package",
        ),
    },
    "release-membership-derivation": {
        (
            "scripts/advance_full_book_batch013.py",
            "booktrans.harness.release_membership",
            "derive_release_membership",
        ),
    },
    "candidate-collaboration:projection-to-package-verification": {
        (
            "src/booktrans/harness/tagged_roundtrip.py",
            "booktrans.ooxml.tagged_projection",
            "apply_tagged_projections",
        ),
        (
            "src/booktrans/harness/tagged_roundtrip.py",
            "booktrans.harness.package_preservation",
            "verify_package_preservation",
        ),
    },
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _explorer_with_source_facts() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        SNAPSHOT,
        MODULE_MAP,
        _json(CANDIDATE),
        _json(REVIEW_MAP),
        _json(REVIEW_SOURCE),
        review_state_path=REVIEW_STATE,
    )
    with_facts = attach_python_source_facts(reviewed, scan_python_source_facts(REPOSITORY))
    return apply_navigation(with_facts, _json(NAVIGATION))


def _direct_import_evidence_id(
    explorer: dict,
    *,
    path: str,
    imported_module: str,
) -> str:
    source_module = next(
        item for item in explorer["source_facts"]["modules"] if item["path"] == path
    )
    fact = next(
        item
        for item in source_module["direct_imports"]
        if item["module"] == imported_module
    )
    return fact["evidence"]["evidence_id"]


def _powershell_evidence_id(
    explorer: dict,
    *,
    collection: str,
    field: str,
    value: str,
) -> str:
    supervisor = explorer["source_facts"]["powershell_scripts"][0]
    fact = next(item for item in supervisor[collection] if item[field] == value)
    return fact["evidence"]["evidence_id"]


def _assert_candidate_evidence_semantics(
    explorer: dict,
    owner: dict,
    expected: set[tuple[str, str, str]],
) -> None:
    facts_by_id = {
        fact["evidence"]["evidence_id"]: (module["path"], fact)
        for module in explorer["source_facts"]["modules"]
        for fact in [*module["direct_imports"], *module["entries"]]
    }
    evidence_ids = owner.get(
        "evidence_ids",
        owner.get("source_assignment", {}).get("evidence_ids", []),
    )
    actual = [facts_by_id[evidence_id] for evidence_id in evidence_ids]
    matches = {
        (path, imported_module, symbol)
        for path, imported_module, symbol in expected
        if any(
            source_path == path
            and fact.get("module") == imported_module
            and symbol in fact.get("names", [])
            for source_path, fact in actual
        )
    }
    assert len(actual) == len(expected) and matches == expected, (
        f"semantic evidence mismatch for {owner['id']}: "
        f"expected {sorted(expected)}, got {evidence_ids}"
    )


def test_translation_and_quality_fragments_add_unreviewed_candidate_drilldowns() -> None:
    explorer = _explorer_with_source_facts()

    result = apply_candidate_fragments(
        explorer,
        [_json(TRANSLATION_FRAGMENT), _json(QUALITY_FRAGMENT)],
    )

    drilldowns = {
        item["parent_module_id"]: item
        for item in result["navigation"]["drilldowns"]
    }
    translation = drilldowns["translation-execution"]
    quality = drilldowns["quality-and-repair"]
    assert [child["node_type"] for child in translation["children"]] == [
        "responsibility",
        "component",
    ]
    assert [child["node_type"] for child in quality["children"]] == [
        "responsibility",
        "component",
    ]
    assert len(translation["collaborations"]) == 1
    assert len(quality["collaborations"]) == 1
    assert all(
        child["review_status"] == "candidate"
        and child["source_assignment"]["status"] == "unverified"
        for drilldown in (translation, quality)
        for child in drilldown["children"]
    )
    assert result["architecture"] == explorer["architecture"]


def test_translation_and_quality_candidates_keep_their_expected_source_semantics() -> None:
    explorer = _explorer_with_source_facts()
    result = apply_candidate_fragments(
        explorer,
        [_json(TRANSLATION_FRAGMENT), _json(QUALITY_FRAGMENT)],
    )
    drilldowns = {
        item["parent_module_id"]: item
        for item in result["navigation"]["drilldowns"]
    }
    owners = {
        item["id"]: item
        for parent in ("translation-execution", "quality-and-repair")
        for collection in ("children", "collaborations")
        for item in drilldowns[parent][collection]
    }

    for owner_id, expected in EXPECTED_T6_3_EVIDENCE_SEMANTICS.items():
        _assert_candidate_evidence_semantics(result, owners[owner_id], expected)

    assert (
        drilldowns["translation-execution"]["collaborations"][0]["source_node_id"],
        drilldowns["translation-execution"]["collaborations"][0]["target_node_id"],
    ) == ("translation-routing-and-acquisition", "translation-provider-adapters")
    assert (
        drilldowns["quality-and-repair"]["collaborations"][0]["source_node_id"],
        drilldowns["quality-and-repair"]["collaborations"][0]["target_node_id"],
    ) == ("quality-deterministic-qa", "quality-semantic-review-and-repair")


def test_semantic_evidence_check_rejects_a_real_but_unrelated_evidence_id() -> None:
    explorer = _explorer_with_source_facts()
    fragment = _json(QUALITY_FRAGMENT)
    fragment["nodes"][0]["evidence_ids"][0] = _direct_import_evidence_id(
        explorer,
        path="src/booktrans/providers/qwen_mt.py",
        imported_module="translation_provider",
    )
    result = apply_candidate_fragments(explorer, [fragment])
    owner = next(
        item
        for drilldown in result["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "quality-and-repair"
        for item in drilldown["children"]
        if item["id"] == "quality-deterministic-qa"
    )

    with pytest.raises(AssertionError, match="semantic evidence mismatch"):
        _assert_candidate_evidence_semantics(
            result,
            owner,
            EXPECTED_T6_3_EVIDENCE_SEMANTICS["quality-deterministic-qa"],
        )


def test_translation_candidates_explain_the_worker_provider_boundary() -> None:
    result = apply_candidate_fragments(
        _explorer_with_source_facts(),
        [_json(TRANSLATION_FRAGMENT)],
    )
    drilldown = next(
        item
        for item in result["navigation"]["drilldowns"]
        if item["parent_module_id"] == "translation-execution"
    )
    worker, provider = drilldown["children"]
    collaboration = drilldown["collaborations"][0]

    assert all(
        term in worker["responsibility"] + worker["plain_explanation"]
        for term in ("Worker/pipeline", "编排", "路由", "RAW 投影")
    )
    assert all(
        term in provider["responsibility"] + provider["plain_explanation"]
        for term in ("Provider", "模型适配", "不负责 Worker 的流程编排")
    )
    assert "Worker" in collaboration["professional_description"]
    assert "Provider" in collaboration["professional_description"]


def test_candidate_fragment_adds_unreviewed_document_drilldown_without_changing_trust() -> None:
    explorer = _explorer_with_source_facts()
    before = deepcopy(explorer)
    evidence_id = _direct_import_evidence_id(
        explorer,
        path="src/booktrans/ooxml/extractor.py",
        imported_module="booktrans.domain.ir",
    )
    fragment = {
        "schema": "architecture-explorer.candidate-fragment.v1",
        "parent_module_id": "document-understanding",
        "name": "文档理解与定位",
        "coverage_note": "本层只展示有静态源码事实支持的未审核候选，不表示源码归属已经核验。",
        "nodes": [
            {
                "id": "document-ir-extraction",
                "node_type": "component",
                "name": "可定位文档提取",
                "professional_description": "候选组件：把 DOCX 内容投影为带定位信息的文档中间表示。",
                "plain_explanation": "像给原书制作带坐标的档案卡，方便后续准确找到每段内容。这里只是未审核候选。",
                "review_status": "candidate",
                "evidence_ids": [evidence_id],
            }
        ],
        "collaborations": [],
    }

    result = apply_candidate_fragments(explorer, [fragment])

    assert explorer == before
    assert result["architecture"] == before["architecture"]
    drilldown = next(
        item
        for item in result["navigation"]["drilldowns"]
        if item["parent_module_id"] == "document-understanding"
    )
    assert drilldown["children"] == [
        {
            "id": "document-ir-extraction",
            "node_type": "component",
            "name": "可定位文档提取",
            "responsibility": "候选组件：把 DOCX 内容投影为带定位信息的文档中间表示。",
            "plain_explanation": "像给原书制作带坐标的档案卡，方便后续准确找到每段内容。这里只是未审核候选。",
            "review_status": "candidate",
            "source_assignment": {
                "status": "unverified",
                "note": "静态源码事实只支持这项候选解释，不证明文件已经归属该架构模块。",
                "relationship_ids": [],
                "evidence_ids": [evidence_id],
            },
        }
    ]
    assert drilldown["collaborations"] == []
    assert len(result["candidate_content_sha256"]) == 64


def test_candidate_collaboration_can_share_real_evidence_without_becoming_reviewed() -> None:
    explorer = _explorer_with_source_facts()
    extractor_evidence = _direct_import_evidence_id(
        explorer,
        path="src/booktrans/ooxml/extractor.py",
        imported_module="booktrans.domain.ir",
    )
    view_evidence = _direct_import_evidence_id(
        explorer,
        path="src/booktrans/ooxml/translation_view.py",
        imported_module="booktrans.domain.ir",
    )
    fragment = {
        "schema": "architecture-explorer.candidate-fragment.v1",
        "parent_module_id": "document-understanding",
        "name": "文档理解与定位",
        "coverage_note": "仅展示有源码事实支持的未审核候选。",
        "nodes": [
            {
                "id": "document-package-extraction",
                "node_type": "responsibility",
                "name": "DOCX 内容提取",
                "professional_description": "候选职责：读取 DOCX 包内容并形成可定位文档事实。",
                "plain_explanation": "像先拆开档案盒并给每页贴坐标标签。",
                "review_status": "candidate",
                "evidence_ids": [extractor_evidence],
            },
            {
                "id": "document-translation-view",
                "node_type": "component",
                "name": "翻译视图构造",
                "professional_description": "候选组件：从文档中间表示组织可翻译视图。",
                "plain_explanation": "像从带坐标的档案中整理出真正需要翻译的工作台。",
                "review_status": "candidate",
                "evidence_ids": [extractor_evidence, view_evidence],
            },
        ],
        "collaborations": [
            {
                "id": "candidate-collaboration:document-extraction-to-view",
                "source_node_id": "document-package-extraction",
                "target_node_id": "document-translation-view",
                "name": "提取结果交给翻译视图",
                "professional_description": "候选协作：可定位文档事实作为翻译视图构造的输入边界。",
                "plain_explanation": "像档案整理员把贴好坐标的材料交给翻译工作台；静态引用只支持这项候选解释。",
                "review_status": "candidate",
                "evidence_ids": [view_evidence],
            }
        ],
    }

    result = apply_candidate_fragments(explorer, [fragment])

    drilldown = next(
        item
        for item in result["navigation"]["drilldowns"]
        if item["parent_module_id"] == "document-understanding"
    )
    assert drilldown["children"][1]["source_assignment"]["evidence_ids"] == [
        extractor_evidence,
        view_evidence,
    ]
    assert drilldown["collaborations"] == [
        {
            "id": "candidate-collaboration:document-extraction-to-view",
            "source_node_id": "document-package-extraction",
            "target_node_id": "document-translation-view",
            "name": "提取结果交给翻译视图",
            "professional_description": "候选协作：可定位文档事实作为翻译视图构造的输入边界。",
            "plain_explanation": "像档案整理员把贴好坐标的材料交给翻译工作台；静态引用只支持这项候选解释。",
            "review_status": "candidate",
            "evidence_ids": [view_evidence],
        }
    ]


def test_production_candidate_fragment_enriches_t4_nodes_without_changing_trust() -> None:
    explorer = _explorer_with_source_facts()
    before_architecture = deepcopy(explorer["architecture"])
    before_coverage = [
        deepcopy(module["source_coverage"])
        for module in explorer["architecture"]["modules"]
    ]
    launch = _powershell_evidence_id(
        explorer,
        collection="entries",
        field="symbol",
        value="Start-DeterministicWorkerProcess",
    )
    refresh = _powershell_evidence_id(
        explorer,
        collection="entries",
        field="symbol",
        value="Refresh-State",
    )
    worker_binding = _powershell_evidence_id(
        explorer,
        collection="script_bindings",
        field="variable",
        value="deterministicWorkerScript",
    )
    adapter_binding = _powershell_evidence_id(
        explorer,
        collection="script_bindings",
        field="variable",
        value="controlPlaneAdapterScript",
    )
    dot_source = explorer["source_facts"]["powershell_scripts"][0]["dot_sources"][0][
        "evidence"
    ]["evidence_id"]
    fragment = {
        "schema": "architecture-explorer.candidate-fragment.v1",
        "parent_module_id": "production-control",
        "name": "生产调度与控制",
        "coverage_note": "只展示静态事实支持的未审核候选，不提升源码覆盖。",
        "nodes": [
            {
                "id": "supervisor-orchestration",
                "node_type": "responsibility",
                "name": "Supervisor 编排",
                "professional_description": "候选职责：组织生产控制循环。",
                "plain_explanation": "像值班调度员看住整个循环；源码只证明入口存在。",
                "review_status": "candidate",
                "evidence_ids": [refresh],
            },
            {
                "id": "worker-launch-boundary",
                "node_type": "responsibility",
                "name": "Worker 启动边界",
                "professional_description": "候选职责：建立受约束的 Worker 进程边界。",
                "plain_explanation": "像一道发车闸门；正式关系仍由原有审核证据负责。",
                "review_status": "candidate",
                "evidence_ids": [launch, worker_binding],
            },
            {
                "id": "state-and-control-decisions",
                "node_type": "responsibility",
                "name": "状态与控制决策",
                "professional_description": "候选职责：读取状态并形成控制决定。",
                "plain_explanation": "像根据仪表盘决定继续、停下或移交。",
                "review_status": "candidate",
                "evidence_ids": [refresh, dot_source],
            },
            {
                "id": "production-control-adapters",
                "node_type": "component",
                "name": "控制决策适配器",
                "professional_description": "候选组件：提供控制决策适配边界。",
                "plain_explanation": "像把不同控制判断接到统一插座。",
                "review_status": "candidate",
                "evidence_ids": [adapter_binding, dot_source],
            },
        ],
        "collaborations": [
            {
                "id": "candidate-collaboration:supervisor-to-control-adapters",
                "source_node_id": "supervisor-orchestration",
                "target_node_id": "production-control-adapters",
                "name": "Supervisor 装载控制适配器",
                "professional_description": "候选协作：Supervisor 静态 dot-source 控制适配器。",
                "plain_explanation": "像调度员把控制规则工具箱接入工作台；不证明运行时结果。",
                "review_status": "candidate",
                "evidence_ids": [adapter_binding, dot_source],
            }
        ],
    }

    result = apply_candidate_fragments(explorer, [fragment])

    assert result["architecture"] == before_architecture
    assert [
        module["source_coverage"] for module in result["architecture"]["modules"]
    ] == before_coverage
    relationship = result["architecture"]["relationships"]
    assert len(relationship) == 1
    assert relationship[0]["review_status"] == "current-reviewed"
    assert len(relationship[0]["evidence"]) == 13
    drilldown = next(
        item
        for item in result["navigation"]["drilldowns"]
        if item["parent_module_id"] == "production-control"
    )
    assert [item["id"] for item in drilldown["children"]][:3] == [
        "supervisor-orchestration",
        "worker-launch-boundary",
        "state-and-control-decisions",
    ]
    worker = drilldown["children"][1]
    assert worker["source_assignment"]["status"] == "partial"
    assert worker["source_assignment"]["relationship_ids"] == [
        "relationship:production-control:launches:translation-execution"
    ]
    assert all(item["plain_explanation"] for item in drilldown["children"])
    assert drilldown["collaborations"][0]["review_status"] == "candidate"


def test_shipped_fragments_add_limited_evidence_backed_candidates_for_two_modules() -> None:
    explorer = _explorer_with_source_facts()
    before_architecture = deepcopy(explorer["architecture"])

    result = apply_candidate_fragments(
        explorer,
        [_json(PRODUCTION_FRAGMENT), _json(DOCUMENT_FRAGMENT), _json(SCOPE_FRAGMENT)],
    )

    assert result["architecture"] == before_architecture
    drilldowns = {
        item["parent_module_id"]: item for item in result["navigation"]["drilldowns"]
    }
    assert set(drilldowns) == {
        "production-control",
        "document-understanding",
        "scope-and-request",
    }
    for parent_id in ("document-understanding", "scope-and-request"):
        drilldown = drilldowns[parent_id]
        assert len(drilldown["children"]) == 2
        assert {item["node_type"] for item in drilldown["children"]} == {
            "responsibility",
            "component",
        }
        assert len(drilldown["collaborations"]) == 1
        assert all(item["review_status"] == "candidate" for item in drilldown["children"])
        assert drilldown["collaborations"][0]["review_status"] == "candidate"
        assert all(item["plain_explanation"] for item in drilldown["children"])
        assert drilldown["collaborations"][0]["plain_explanation"]
        assert all(
            item["source_assignment"]["evidence_ids"]
            for item in drilldown["children"]
        )
    production = drilldowns["production-control"]
    assert len(production["children"]) == 5
    assert {item["node_type"] for item in production["children"]} == {
        "responsibility",
        "component",
    }
    assert len(production["collaborations"]) == 2
    assert all(item["review_status"] == "candidate" for item in production["children"])
    assert all(item["plain_explanation"] for item in production["children"])


def test_t6_5_fragments_complete_seven_candidate_drilldowns_without_changing_trust() -> None:
    explorer = _explorer_with_source_facts()
    before_architecture = deepcopy(explorer["architecture"])

    result = apply_candidate_fragments(
        explorer,
        [
            _json(PRODUCTION_FRAGMENT),
            _json(DOCUMENT_FRAGMENT),
            _json(SCOPE_FRAGMENT),
            _json(TRANSLATION_FRAGMENT),
            _json(QUALITY_FRAGMENT),
            _json(PRODUCTION_EVIDENCE_FRAGMENT),
            _json(CANDIDATE_RELEASE_FRAGMENT),
        ],
    )

    assert result["architecture"] == before_architecture
    assert all(
        module["source_coverage"]["status"] == "unverified"
        for module in result["architecture"]["modules"]
    )
    relationship = result["architecture"]["relationships"]
    assert len(relationship) == 1
    assert relationship[0]["review_status"] == "current-reviewed"
    assert len(relationship[0]["evidence"]) == 13

    drilldowns = {
        item["parent_module_id"]: item for item in result["navigation"]["drilldowns"]
    }
    assert set(drilldowns) == {
        "document-understanding",
        "scope-and-request",
        "translation-execution",
        "quality-and-repair",
        "production-evidence",
        "production-control",
        "candidate-and-release",
    }
    expected_shape = {
        "production-evidence": (2, 1),
        "candidate-and-release": (3, 1),
    }
    for parent_id, (node_count, collaboration_count) in expected_shape.items():
        drilldown = drilldowns[parent_id]
        assert len(drilldown["children"]) == node_count
        assert len(drilldown["collaborations"]) == collaboration_count
        assert {item["node_type"] for item in drilldown["children"]} == {
            "responsibility",
            "component",
        }
        assert all(item["review_status"] == "candidate" for item in drilldown["children"])
        assert all(item["plain_explanation"] for item in drilldown["children"])
        assert all(
            item["source_assignment"]["status"] == "unverified"
            for item in drilldown["children"]
        )
        assert all(
            item["source_assignment"]["evidence_ids"]
            for item in drilldown["children"]
        )
        assert drilldown["collaborations"][0]["review_status"] == "candidate"
        assert drilldown["collaborations"][0]["plain_explanation"]

    assert "不表示七模块源码覆盖已完成正式审核" in drilldowns[
        "production-evidence"
    ]["coverage_note"]
    assert "不是每个 Checkpoint" in drilldowns["candidate-and-release"][
        "coverage_note"
    ]


def test_t6_5_candidates_keep_their_expected_source_semantics_and_endpoints() -> None:
    result = apply_candidate_fragments(
        _explorer_with_source_facts(),
        [_json(PRODUCTION_EVIDENCE_FRAGMENT), _json(CANDIDATE_RELEASE_FRAGMENT)],
    )
    drilldowns = {
        item["parent_module_id"]: item for item in result["navigation"]["drilldowns"]
    }
    owners = {
        item["id"]: item
        for parent in ("production-evidence", "candidate-and-release")
        for collection in ("children", "collaborations")
        for item in drilldowns[parent][collection]
    }
    assert set(owners) == set(EXPECTED_T6_5_EVIDENCE_SEMANTICS)

    for owner_id, expected in EXPECTED_T6_5_EVIDENCE_SEMANTICS.items():
        _assert_candidate_evidence_semantics(result, owners[owner_id], expected)

    checkpoint_collaboration = drilldowns["production-evidence"]["collaborations"][0]
    assert (
        checkpoint_collaboration["source_node_id"],
        checkpoint_collaboration["target_node_id"],
    ) == ("checkpoint-evidence-persistence", "checkpoint-manifest-seal")
    release_collaboration = drilldowns["candidate-and-release"]["collaborations"][0]
    assert (
        release_collaboration["source_node_id"],
        release_collaboration["target_node_id"],
    ) == ("candidate-document-projection", "package-preservation-verifier")


def test_t6_5_semantic_evidence_rejects_a_real_but_unrelated_evidence_id() -> None:
    explorer = _explorer_with_source_facts()
    fragment = _json(CANDIDATE_RELEASE_FRAGMENT)
    fragment["nodes"][0]["evidence_ids"][0] = _direct_import_evidence_id(
        explorer,
        path="src/booktrans/providers/qwen_mt.py",
        imported_module="translation_provider",
    )
    result = apply_candidate_fragments(explorer, [fragment])
    owner = next(
        item
        for drilldown in result["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "candidate-and-release"
        for item in drilldown["children"]
        if item["id"] == "candidate-document-projection"
    )

    with pytest.raises(AssertionError, match="semantic evidence mismatch"):
        _assert_candidate_evidence_semantics(
            result,
            owner,
            EXPECTED_T6_5_EVIDENCE_SEMANTICS["candidate-document-projection"],
        )


def test_candidate_fragments_reject_duplicate_parent_definitions() -> None:
    explorer = _explorer_with_source_facts()
    fragment = _json(DOCUMENT_FRAGMENT)

    with pytest.raises(ExplorerInputError, match="parent module must be unique"):
        apply_candidate_fragments(explorer, [fragment, deepcopy(fragment)])


def test_candidate_fragment_rejects_dangling_source_evidence() -> None:
    explorer = _explorer_with_source_facts()
    fragment = _json(DOCUMENT_FRAGMENT)
    fragment["nodes"][0]["evidence_ids"][0] = "evidence:sha256:" + "0" * 64

    with pytest.raises(ExplorerInputError, match="unknown source evidence"):
        apply_candidate_fragments(explorer, [fragment])


def test_candidate_fragment_rejects_unresolved_powershell_evidence() -> None:
    explorer = _explorer_with_source_facts()
    fragment = _json(DOCUMENT_FRAGMENT)
    supervisor = explorer["source_facts"]["powershell_scripts"][0]
    unresolved = next(
        item
        for item in supervisor["script_bindings"]
        if item["variable"] == "stateScript"
    )
    assert unresolved["resolution"] == "unknown"
    fragment["nodes"][0]["evidence_ids"][0] = unresolved["evidence"]["evidence_id"]

    with pytest.raises(ExplorerInputError, match="unknown source evidence"):
        apply_candidate_fragments(explorer, [fragment])


def test_candidate_fragments_reject_global_node_id_collisions() -> None:
    explorer = _explorer_with_source_facts()
    document = _json(DOCUMENT_FRAGMENT)
    scope = _json(SCOPE_FRAGMENT)
    duplicate_id = document["nodes"][0]["id"]
    scope["nodes"][0]["id"] = duplicate_id
    scope["collaborations"][0]["source_node_id"] = duplicate_id

    with pytest.raises(ExplorerInputError, match="node id must be globally unique"):
        apply_candidate_fragments(explorer, [document, scope])


def test_reviewed_build_cli_accepts_independent_candidate_fragments(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    for relative in (
        "scripts/production/production_campaign_supervisor.ps1",
        "src/booktrans/ooxml/extractor.py",
        "src/booktrans/ooxml/translation_view.py",
        "scripts/write_full_book_translation_scope.py",
        "src/booktrans/production_worker/selection.py",
        "src/booktrans/production_worker/pipeline.py",
        "src/booktrans/production_worker/qa.py",
        "src/booktrans/production_worker/semantic_review_run.py",
        "src/booktrans/providers/qwen_max.py",
        "src/booktrans/providers/qwen_mt.py",
        "src/booktrans/providers/repair_gate.py",
        "src/booktrans/providers/router.py",
        "src/booktrans/production_worker/production_checkpoint.py",
        "src/booktrans/harness/tagged_roundtrip.py",
        "src/booktrans/harness/package_preservation.py",
        "src/booktrans/ooxml/tagged_projection.py",
        "scripts/advance_full_book_batch013.py",
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, destination)
    commit(repository, "add T6 source facts")
    assert review_main(
        [
            "update-review-state",
            "--repository", str(repository),
            "--candidate", str(CANDIDATE),
            "--review-mapping", str(REVIEW_MAP),
            "--review-source", str(REVIEW_SOURCE),
            "--review-state", "output/review-state.json",
            "--assessed-at", "2026-11-06T00:00:00Z",
        ]
    ) == 0

    build_args = [
        "build",
        "--repository", str(repository),
        "--snapshot", str(SNAPSHOT),
        "--mapping", str(MODULE_MAP),
        "--candidate", str(CANDIDATE),
        "--review-mapping", str(REVIEW_MAP),
        "--review-source", str(REVIEW_SOURCE),
        "--review-state", "output/review-state.json",
        "--navigation", str(NAVIGATION),
        "--candidate-fragment", str(PRODUCTION_FRAGMENT),
        "--candidate-fragment", str(DOCUMENT_FRAGMENT),
        "--candidate-fragment", str(SCOPE_FRAGMENT),
        "--candidate-fragment", str(TRANSLATION_FRAGMENT),
        "--candidate-fragment", str(QUALITY_FRAGMENT),
        "--candidate-fragment", str(PRODUCTION_EVIDENCE_FRAGMENT),
        "--candidate-fragment", str(CANDIDATE_RELEASE_FRAGMENT),
        "--web-assets", str(REPOSITORY / "architecture-explorer/web"),
        "--output-directory", "output/site",
    ]
    assert review_main(build_args) == 0

    data = json.loads(
        (repository / "output/site/explorer-data.json").read_text(encoding="utf-8")
    )
    assert [
        item["parent_module_id"] for item in data["navigation"]["drilldowns"]
    ] == [
        "production-control",
        "document-understanding",
        "scope-and-request",
        "translation-execution",
        "quality-and-repair",
        "production-evidence",
        "candidate-and-release",
    ]
    assert [
        item["path"] for item in data["generation_inputs"]["candidate_fragments"]
    ] == [
        PRODUCTION_FRAGMENT.name,
        DOCUMENT_FRAGMENT.name,
        SCOPE_FRAGMENT.name,
        TRANSLATION_FRAGMENT.name,
        QUALITY_FRAGMENT.name,
        PRODUCTION_EVIDENCE_FRAGMENT.name,
        CANDIDATE_RELEASE_FRAGMENT.name,
    ]
    assert data["architecture"]["relationships"][0]["review_status"] == "current-reviewed"

    supervisor = repository / "scripts/production/production_campaign_supervisor.ps1"
    supervisor.write_text(
        supervisor.read_text(encoding="utf-8") + "\n# unrelated source comment\n",
        encoding="utf-8",
    )

    assert review_main(build_args) == 0
    rebuilt = json.loads(
        (repository / "output/site/explorer-data.json").read_text(encoding="utf-8")
    )
    relationship = rebuilt["architecture"]["relationships"][0]
    assert relationship["review_status"] == "current-reviewed"
    assert relationship["source_continuity"]["status"] == "exact-match"
    assert len(relationship["evidence_ids"]) == 13


@pytest.mark.parametrize(
    ("viewport_width", "viewport_height"),
    [(1280, 900), (1440, 900), (390, 844)],
)
def test_real_browser_shows_candidate_nodes_collaborations_evidence_and_typography(
    tmp_path: Path,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser_candidates = (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )
    browser = next((path for path in browser_candidates if path.is_file()), None)
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")
    data = apply_candidate_fragments(
        _explorer_with_source_facts(),
        [
            _json(PRODUCTION_FRAGMENT),
            _json(DOCUMENT_FRAGMENT),
            _json(SCOPE_FRAGMENT),
            _json(TRANSLATION_FRAGMENT),
            _json(QUALITY_FRAGMENT),
            _json(PRODUCTION_EVIDENCE_FRAGMENT),
            _json(CANDIDATE_RELEASE_FRAGMENT),
        ],
    )
    site = tmp_path / "site"
    write_site(data, site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_2_candidate_dom.js",
        site / "t6_2_candidate_dom.js",
    )
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_4_production_dom.js",
        site / "t6_4_production_dom.js",
    )
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_3_candidate_dom.js",
        site / "t6_3_candidate_dom.js",
    )
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_5_candidate_dom.js",
        site / "t6_5_candidate_dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="t6_4_production_dom.js"></script>'
            '<script src="t6_2_candidate_dom.js"></script>'
            '<script src="t6_3_candidate_dom.js"></script>'
            '<script src="t6_5_candidate_dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-breakpad",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            f"--window-size={viewport_width},{viewport_height}",
            f"--user-data-dir={tmp_path / 'browser-profile'}",
            "--virtual-time-budget=2000",
            "--dump-dom",
            page.resolve().as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'data-t6-candidate="pass"' in completed.stdout, completed.stdout
    assert 'data-t6-production="pass"' in completed.stdout, completed.stdout
    assert 'data-t6-three-candidate="pass"' in completed.stdout, completed.stdout
    assert 'data-t6-five-candidate="pass"' in completed.stdout, completed.stdout
