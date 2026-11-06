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
TRANSLATION_FRAGMENT = (
    REPOSITORY / "architecture-explorer/candidates/translation-execution.v1.json"
)
QUALITY_FRAGMENT = REPOSITORY / "architecture-explorer/candidates/quality-and-repair.v1.json"


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


def test_shipped_fragments_add_limited_evidence_backed_candidates_for_two_modules() -> None:
    explorer = _explorer_with_source_facts()
    before_architecture = deepcopy(explorer["architecture"])

    result = apply_candidate_fragments(
        explorer,
        [_json(DOCUMENT_FRAGMENT), _json(SCOPE_FRAGMENT)],
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
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, destination)
    commit(repository, "add T6-2 source facts")
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

    assert review_main(
        [
            "build",
            "--repository", str(repository),
            "--snapshot", str(SNAPSHOT),
            "--mapping", str(MODULE_MAP),
            "--candidate", str(CANDIDATE),
            "--review-mapping", str(REVIEW_MAP),
            "--review-source", str(REVIEW_SOURCE),
            "--review-state", "output/review-state.json",
            "--navigation", str(NAVIGATION),
            "--candidate-fragment", str(DOCUMENT_FRAGMENT),
            "--candidate-fragment", str(SCOPE_FRAGMENT),
            "--candidate-fragment", str(TRANSLATION_FRAGMENT),
            "--candidate-fragment", str(QUALITY_FRAGMENT),
            "--web-assets", str(REPOSITORY / "architecture-explorer/web"),
            "--output-directory", "output/site",
        ]
    ) == 0

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
    ]
    assert [
        item["path"] for item in data["generation_inputs"]["candidate_fragments"]
    ] == [
        DOCUMENT_FRAGMENT.name,
        SCOPE_FRAGMENT.name,
        TRANSLATION_FRAGMENT.name,
        QUALITY_FRAGMENT.name,
    ]
    assert data["architecture"]["relationships"][0]["review_status"] == "current-reviewed"


def test_real_browser_shows_candidate_nodes_collaborations_and_evidence(
    tmp_path: Path,
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
            _json(DOCUMENT_FRAGMENT),
            _json(SCOPE_FRAGMENT),
            _json(TRANSLATION_FRAGMENT),
            _json(QUALITY_FRAGMENT),
        ],
    )
    site = tmp_path / "site"
    write_site(data, site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_2_candidate_dom.js",
        site / "t6_2_candidate_dom.js",
    )
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t6_3_candidate_dom.js",
        site / "t6_3_candidate_dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="t6_2_candidate_dom.js"></script>'
            '<script src="t6_3_candidate_dom.js"></script></body>',
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
    assert 'data-t6-three-candidate="pass"' in completed.stdout, completed.stdout
