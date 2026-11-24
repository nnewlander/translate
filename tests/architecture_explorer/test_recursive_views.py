from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.view_definitions import apply_view_definitions
from booktrans.architecture_explorer.view_projection import apply_view_projection
from booktrans.architecture_explorer.generator import write_site
from booktrans.architecture_explorer.review_cli import main as review_main
from tests.architecture_explorer.test_view_projection import _current_explorer
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


REPOSITORY = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


def _recursive_fragment() -> dict:
    return _json("architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json")


def _semantic_routing_fragment() -> dict:
    return _json(
        "architecture-explorer/recursive-candidates/"
        "translation-routing-and-acquisition.v2.json"
    )


def _real_recursive_fragments() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (REPOSITORY / "architecture-explorer/recursive-candidates").glob("*.json")
        )
    ]


def _recursive_explorer(fragment: dict | None = None) -> dict:
    return apply_candidate_fragments(
        _current_explorer(), [fragment] if fragment is not None else _real_recursive_fragments()
    )


def _real_call_fact() -> dict:
    for source_file in _current_explorer()["source_facts"]["python_call_facts"]["files"]:
        if source_file["calls"]:
            return source_file["calls"][0]
    raise AssertionError("fixed Source Facts contain no Call Fact")


def test_real_recursive_candidate_input_projects_the_approved_internal_view() -> None:
    explorer = _recursive_explorer()
    explorer = apply_view_definitions(
        explorer,
        _json("architecture-explorer/views/recursive-views.v1.json"),
    )

    projection = apply_view_projection(explorer)["view_projection"]

    assert len(projection["view_index"]) == 12
    assert len(projection["node_index"]) == 34  # seven modules plus twenty-seven candidates
    semantic_families = [
        reference["semantic_family"]
        for reference in projection["relationship_index"].values()
    ]
    assert semantic_families.count("formal-relationship") == 1
    assert semantic_families.count("candidate-collaboration") == 12
    assert semantic_families.count("candidate-decomposition") == 9

    internal = projection["view_index"]["worker-launch-boundary-internal"]
    assert internal["scope_node_id"] == "worker-launch-boundary"
    assert [appearance["node_id"] for appearance in internal["appearances"]] == [
        "worker-launch-boundary",
        "supervisor-process-launch-construction",
        "deterministic-worker-wrapper",
        "worker-production-entry-guard",
    ]
    assert internal["relationship_ids"] == [
        "candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction",
        "candidate-decomposition:worker-launch-boundary-to-worker-production-entry-guard",
        "candidate-collaboration:supervisor-to-worker-wrapper",
        "candidate-collaboration:worker-wrapper-to-production-entry-guard",
    ]

    production_boundary = next(
        appearance
        for appearance in projection["view_index"]["production-control"]["appearances"]
        if appearance["node_id"] == "worker-launch-boundary"
    )
    assert production_boundary["primary_action"] == {
        "kind": "open-view",
        "target_view_id": "worker-launch-boundary-internal",
        "transition_kind": "decomposition",
        "destination_label": "查看 Worker 启动边界内部职责",
    }
    assert "primary_action" not in internal["appearances"][0]

    fragment = next(
        item
        for item in explorer["recursive_candidate_fragments"]
        if item["scope_node_id"] == "worker-launch-boundary"
    )
    nodes = {node["id"]: node for node in fragment["nodes"]}
    assert nodes["supervisor-process-launch-construction"]["source_assignment"][
        "evidence_ids"
    ] == ["evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a"]
    assert nodes["worker-production-entry-guard"]["source_assignment"][
        "evidence_ids"
    ] == [
        "evidence:sha256:6e36e3c009c8408b2fddd0188c1597ee8fa83511b38d60f09f734a1a4c722aad",
        "evidence:sha256:73ff577c855008cf0261991574392080b5d717caadbea0d82cbd75612fc7d43d",
    ]
    relationships = {item["id"]: item for item in fragment["relationships"]}
    assert relationships[
        "candidate-collaboration:worker-wrapper-to-production-entry-guard"
    ]["evidence_ids"] == [
        "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8",
        "evidence:sha256:73ff577c855008cf0261991574392080b5d717caadbea0d82cbd75612fc7d43d",
    ]
    assert all(node["review_status"] == "candidate" for node in nodes.values())
    assert all(
        relationship["review_status"] == "candidate"
        for relationship in relationships.values()
    )


def test_recursive_candidate_input_rejects_unknown_evidence() -> None:
    fragment = _recursive_fragment()
    fragment["nodes"][0]["evidence_ids"] = ["evidence:sha256:" + "0" * 64]

    with pytest.raises(ExplorerInputError, match="unknown source evidence"):
        _recursive_explorer(fragment)


def test_recursive_candidate_input_accepts_definition_and_call_fact_evidence() -> None:
    fragment = _recursive_fragment()
    definition_id = fragment["nodes"][1]["evidence_ids"][0]
    call_id = _real_call_fact()["evidence"]["evidence_id"]
    fragment["nodes"][0]["evidence_ids"] = [definition_id, call_id]

    explorer = _recursive_explorer(fragment)

    assert explorer["recursive_candidate_fragments"][0]["nodes"][0][
        "source_assignment"
    ]["evidence_ids"] == [definition_id, call_id]


def test_recursive_candidate_input_rejects_a_decomposition_cycle() -> None:
    fragment = _recursive_fragment()
    fragment["relationships"].append(
        {
            "id": "candidate-decomposition:entry-guard-to-worker-launch-boundary",
            "semantic_family": "candidate-decomposition",
            "source_node_id": "worker-production-entry-guard",
            "target_node_id": "worker-launch-boundary",
            "name": "非法循环分解",
            "professional_description": "测试专用非法循环分解。",
            "plain_explanation": "测试专用非法循环分解。",
            "review_status": "candidate",
            "evidence_ids": fragment["nodes"][1]["evidence_ids"],
        }
    )

    with pytest.raises(
        ExplorerInputError,
        match="worker-launch-boundary -> worker-production-entry-guard -> worker-launch-boundary",
    ):
        _recursive_explorer(fragment)


def test_recursive_candidate_input_rejects_a_decomposition_self_loop() -> None:
    fragment = _recursive_fragment()
    decomposition = next(
        relationship
        for relationship in fragment["relationships"]
        if relationship["semantic_family"] == "candidate-decomposition"
    )
    decomposition["target_node_id"] = decomposition["source_node_id"]

    with pytest.raises(
        ExplorerInputError,
        match=r"worker-launch-boundary -> worker-launch-boundary",
    ):
        _recursive_explorer(fragment)


def test_recursive_candidate_input_rejects_a_duplicate_relationship_id() -> None:
    fragment = _recursive_fragment()
    duplicate = deepcopy(fragment["relationships"][0])
    fragment["relationships"].append(duplicate)

    with pytest.raises(ExplorerInputError, match="Relationship id must be globally unique"):
        _recursive_explorer(fragment)


def test_recursive_candidate_input_rejects_an_unknown_relationship_endpoint() -> None:
    fragment = _recursive_fragment()
    fragment["relationships"][0]["target_node_id"] = "missing-candidate-node"

    with pytest.raises(ExplorerInputError, match="references unknown Node"):
        _recursive_explorer(fragment)


def test_recursive_candidate_input_allows_collaboration_cycles_and_acyclic_shared_children() -> None:
    fragment = _recursive_fragment()
    evidence_ids = fragment["nodes"][1]["evidence_ids"]
    fragment["relationships"].extend(
        [
            {
                "id": "candidate-collaboration:entry-guard-to-worker-wrapper-test-only",
                "semantic_family": "candidate-collaboration",
                "source_node_id": "worker-production-entry-guard",
                "target_node_id": "deterministic-worker-wrapper",
                "name": "测试专用往返协作",
                "professional_description": "受控测试数据验证协作循环合法。",
                "plain_explanation": "这不是生产架构数据。",
                "review_status": "candidate",
                "evidence_ids": evidence_ids,
            },
            {
                "id": "candidate-decomposition:launch-construction-to-entry-guard-test-only",
                "semantic_family": "candidate-decomposition",
                "source_node_id": "supervisor-process-launch-construction",
                "target_node_id": "worker-production-entry-guard",
                "name": "测试专用共享子节点",
                "professional_description": "受控测试数据验证无环共享子节点合法。",
                "plain_explanation": "这不是生产架构数据。",
                "review_status": "candidate",
                "evidence_ids": evidence_ids,
            },
        ]
    )

    explorer = _recursive_explorer(fragment)

    assert len(explorer["recursive_candidate_fragments"][0]["relationships"]) == 5


def test_view_projection_rejects_invalid_references_and_decomposition_scope() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["entry_actions"][0]["primary_action"]["target_view_id"] = "missing-view"
    with pytest.raises(ExplorerInputError, match="unknown target View"):
        apply_view_projection(apply_view_definitions(_recursive_explorer(), definitions))

    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["entry_actions"][0]["primary_action"]["transition_kind"] = "unknown"
    with pytest.raises(ExplorerInputError, match="Transition kind is invalid"):
        apply_view_projection(apply_view_definitions(_recursive_explorer(), definitions))

    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["views"][0]["scope_node_id"] = "deterministic-worker-wrapper"
    with pytest.raises(ExplorerInputError, match="target scope must match"):
        apply_view_projection(apply_view_definitions(_recursive_explorer(), definitions))


def test_view_projection_allows_an_explicit_perspective_return_transition() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["views"][0]["appearances"][0]["primary_action"] = {
        "kind": "open-view",
        "target_view_id": "production-control",
        "transition_kind": "perspective",
        "destination_label": "返回生产调度视角",
    }

    projection = apply_view_projection(
        apply_view_definitions(_recursive_explorer(), definitions)
    )["view_projection"]

    assert projection["view_index"]["worker-launch-boundary-internal"][
        "appearances"
    ][0]["primary_action"]["target_view_id"] == "production-control"


def test_view_projection_rejects_a_missing_destination_label() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    del definitions["entry_actions"][0]["primary_action"]["destination_label"]

    with pytest.raises(ExplorerInputError, match="destination label"):
        apply_view_projection(apply_view_definitions(_recursive_explorer(), definitions))


def test_view_projection_rejects_an_invalid_action_kind() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["entry_actions"][0]["primary_action"]["kind"] = "teleport"

    with pytest.raises(ExplorerInputError, match="Action kind is invalid"):
        apply_view_projection(apply_view_definitions(_recursive_explorer(), definitions))


def test_view_projection_allows_a_valid_cross_node_transition() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["views"][0]["appearances"][1]["primary_action"] = {
        "kind": "open-view",
        "target_view_id": "translation-execution",
        "transition_kind": "cross-node",
        "destination_label": "转到翻译执行视角",
    }

    projection = apply_view_projection(
        apply_view_definitions(_recursive_explorer(), definitions)
    )["view_projection"]

    action = projection["view_index"]["worker-launch-boundary-internal"][
        "appearances"
    ][1]["primary_action"]
    assert action["transition_kind"] == "cross-node"
    assert action["target_view_id"] == "translation-execution"


def test_open_source_trail_rejects_an_evidence_id_substring() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    real_definition_id = _recursive_fragment()["nodes"][1]["evidence_ids"][0]
    definitions["views"][0]["appearances"][1]["primary_action"] = {
        "kind": "open-source-trail",
        "target_evidence_id": real_definition_id[:-1],
        "destination_label": "查看源码",
    }

    with pytest.raises(ExplorerInputError, match="unknown Source Trail Evidence"):
        apply_view_projection(
            apply_view_definitions(_recursive_explorer(), definitions)
        )


@pytest.mark.parametrize("evidence_kind", ["definition", "call"])
def test_open_source_trail_accepts_exact_navigable_evidence(
    evidence_kind: str,
) -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    evidence_id = (
        _recursive_fragment()["nodes"][1]["evidence_ids"][0]
        if evidence_kind == "definition"
        else _real_call_fact()["evidence"]["evidence_id"]
    )
    definitions["views"][0]["appearances"][1]["primary_action"] = {
        "kind": "open-source-trail",
        "target_evidence_id": evidence_id,
        "destination_label": "查看源码",
    }

    projected = apply_view_projection(
        apply_view_definitions(_recursive_explorer(), definitions)
    )

    assert projected["source_evidence_index"][evidence_id]["source_trail_kind"] == (
        evidence_kind
    )


def test_open_source_trail_rejects_unknown_and_display_only_evidence() -> None:
    for evidence_id, diagnostic in (
        ("evidence:sha256:" + "0" * 64, "unknown Source Trail Evidence"),
        (
            _recursive_fragment()["nodes"][0]["evidence_ids"][0],
            "Source Trail target is not navigable",
        ),
    ):
        definitions = _json("architecture-explorer/views/recursive-views.v1.json")
        definitions["views"][0]["appearances"][1]["primary_action"] = {
            "kind": "open-source-trail",
            "target_evidence_id": evidence_id,
            "destination_label": "查看源码",
        }

        with pytest.raises(ExplorerInputError, match=diagnostic):
            apply_view_projection(
                apply_view_definitions(_recursive_explorer(), definitions)
            )


def test_open_source_trail_rejects_a_non_string_evidence_id() -> None:
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["views"][0]["appearances"][1]["primary_action"] = {
        "kind": "open-source-trail",
        "target_evidence_id": 53,
        "destination_label": "查看源码",
    }

    with pytest.raises(ExplorerInputError, match="Source Trail Evidence id"):
        apply_view_projection(
            apply_view_definitions(_recursive_explorer(), definitions)
        )


def test_renderer_has_no_recursive_depth_or_new_view_special_case() -> None:
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(
        encoding="utf-8"
    )

    assert app.count("const drawRelationshipGraph =") == 1
    assert "worker-launch-boundary-internal" not in app
    assert "worker-launch-boundary-internal" not in styles
    assert "third-level" not in app
    assert "fourth-level" not in app


@pytest.fixture(scope="module")
def recursive_explorer_data() -> dict:
    explorer = _recursive_explorer()
    explorer = apply_view_definitions(
        explorer,
        _json("architecture-explorer/views/recursive-views.v1.json"),
    )
    return apply_view_projection(explorer)


@pytest.mark.parametrize("viewport_width", [1440, 1280, 960])
def test_real_offline_chrome_explores_the_internal_candidate_view(
    tmp_path: Path,
    viewport_width: int,
) -> None:
    browser_name, browser = _desktop_browser()
    site = REPOSITORY / "output/architecture-explorer/ticket-53-tests" / tmp_path.name
    build_args = [
        "build",
        "--repository", str(REPOSITORY),
        "--snapshot", str(REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"),
        "--mapping", str(REPOSITORY / "architecture-explorer/modules.json"),
        "--candidate", str(REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        "--review-mapping", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"),
        "--review-source", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        "--review-state", str(REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"),
        "--navigation", str(REPOSITORY / "architecture-explorer/navigation.v1.json"),
    ]
    for candidate in sorted((REPOSITORY / "architecture-explorer/candidates").glob("*.json")):
        build_args.extend(("--candidate-fragment", str(candidate)))
    for candidate in sorted(
        (REPOSITORY / "architecture-explorer/recursive-candidates").glob("*.json")
    ):
        build_args.extend(("--candidate-fragment", str(candidate)))
    build_args.extend(
        (
            "--view-definition",
            str(REPOSITORY / "architecture-explorer/views/recursive-views.v1.json"),
            "--legacy-node-review-v1",
            "--web-assets",
            str(REPOSITORY / "architecture-explorer/web"),
            "--output-directory",
            str(site.relative_to(REPOSITORY)),
        )
    )
    assert review_main(build_args) == 0
    generated = _json(str(site.relative_to(REPOSITORY) / "explorer-data.json"))
    assert len(generated["view_projection"]["view_index"]) == 12
    assert generated["view_projection"]["view_index"]["worker-launch-boundary-internal"]
    legacy = _current_explorer()
    assert generated["architecture"]["relationships"] == legacy["architecture"]["relationships"]
    assert len(generated["architecture"]["relationships"][0]["evidence"]) == 13
    assert sum(
        len(view["children"]) for view in generated["navigation"]["drilldowns"]
    ) == 18
    assert sum(
        len(view["collaborations"]) for view in generated["navigation"]["drilldowns"]
    ) == 8
    legacy_review = generated["node_review_projection"]["objects"][
        "candidate-node::worker-launch-boundary"
    ]
    assert legacy_review["projection"] == "Pending Review"
    recursive_nodes = generated["recursive_candidate_fragments"][0]["nodes"]
    assert all("node_review" not in node for node in recursive_nodes)
    assert all(node["source_assignment"]["status"] == "unverified" for node in recursive_nodes)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t12_recursive_view_dom.js",
        site / "t12-recursive-view-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t12-recursive-view-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=3500,
        viewport_width=viewport_width,
        viewport_height=900,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t12-recursive-view="pass"',
    )


def test_controlled_data_recurses_beyond_three_views_through_the_same_renderer(
    tmp_path: Path,
) -> None:
    production_fragment = _recursive_fragment()
    test_fragment = {
        "schema": "architecture-explorer.candidate-fragment.v2",
        "scope_node_id": "supervisor-process-launch-construction",
        "nodes": [
            {
                "id": "test-only-depth-four",
                "node_type": "responsibility",
                "name": "测试专用第四层职责",
                "professional_description": "只用于证明递归能力，不属于生产架构。",
                "plain_explanation": "受控测试数据，不会进入真实产品输入。",
                "review_status": "candidate",
                "evidence_ids": production_fragment["nodes"][0]["evidence_ids"],
            }
        ],
        "relationships": [
            {
                "id": "candidate-decomposition:test-only-depth-four",
                "semantic_family": "candidate-decomposition",
                "source_node_id": "supervisor-process-launch-construction",
                "target_node_id": "test-only-depth-four",
                "name": "测试专用递归分解",
                "professional_description": "只验证超过三层的通用分解投影。",
                "plain_explanation": "受控测试数据，不是生产架构主张。",
                "review_status": "candidate",
                "evidence_ids": production_fragment["nodes"][0]["evidence_ids"],
            }
        ],
    }
    explorer = apply_candidate_fragments(
        _current_explorer(),
        [*_real_recursive_fragments(), test_fragment],
    )
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["entry_actions"].append(
        {
            "view_id": "worker-launch-boundary-internal",
            "node_id": "supervisor-process-launch-construction",
            "primary_action": {
                "kind": "open-view",
                "target_view_id": "test-only-depth-four-view",
                "transition_kind": "decomposition",
                "destination_label": "查看测试专用下一层",
            },
        }
    )
    definitions["views"].append(
        {
            "id": "test-only-depth-four-view",
            "display_name": "测试专用第四层 View",
            "perspective": "responsibility",
            "scope_node_id": "supervisor-process-launch-construction",
            "coverage_note": "受控测试数据；不属于生产架构。",
            "appearances": [
                {"node_id": "supervisor-process-launch-construction"},
                {"node_id": "test-only-depth-four"},
            ],
            "relationship_ids": ["candidate-decomposition:test-only-depth-four"],
        }
    )
    explorer = apply_view_definitions(explorer, definitions)
    projection = apply_view_projection(explorer)["view_projection"]
    assert len(projection["view_index"]) == 13

    browser_name, browser = _desktop_browser()
    site = tmp_path / "site"
    write_site(explorer, site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t12_deep_recursive_dom.js",
        site / "t12-deep-recursive-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t12-deep-recursive-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=3000,
        viewport_width=960,
        viewport_height=900,
    )
    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t12-deep-recursive="pass"',
    )


def test_real_chrome_reconnects_recursive_view_edges_during_same_page_resize(
    tmp_path: Path,
    recursive_explorer_data: dict,
) -> None:
    browser_name, browser = _desktop_browser()
    if browser_name != "chrome":
        pytest.skip("same-page recursive resize is a required Chrome acceptance test")
    site = tmp_path / "site"
    write_site(
        recursive_explorer_data,
        site,
        REPOSITORY / "architecture-explorer/web",
    )
    evidence_path = tmp_path / "recursive-viewport-evidence.json"
    completed = subprocess.run(
        [
            "node",
            str(REPOSITORY / "tests/architecture_explorer/t7_continuous_resize.cjs"),
            str(browser),
            (site / "index.html").resolve().as_uri(),
            str(tmp_path / "recursive-resize-profile"),
            str(evidence_path),
            "recursive-resize",
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "recursive 1440x900 -> 1280x900 -> 960x900 -> 480x900 -> 1440x900" in completed.stdout
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert [graph["graph_id"] for graph in evidence["graphs"]] == [
        "worker-launch-boundary-internal"
    ]
    assert [item["requested"] for item in evidence["graphs"][0]["observations"]] == [
        [1440, 900],
        [1280, 900],
        [960, 900],
        [480, 900],
        [1440, 900],
    ]


def test_real_browser_displays_call_evidence_and_opens_its_source_trail(
    tmp_path: Path,
) -> None:
    call = _real_call_fact()
    call_id = call["evidence"]["evidence_id"]
    fragment = _recursive_fragment()
    fragment["nodes"][0]["evidence_ids"].append(call_id)
    explorer = apply_candidate_fragments(
        _current_explorer(),
        [
            fragment,
            _semantic_routing_fragment(),
            _json("architecture-explorer/recursive-candidates/qwen-provider-adapters.v2.json"),
            _json(
                "architecture-explorer/recursive-candidates/"
                "quality-semantic-review-and-repair.v2.json"
            ),
        ],
    )
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    definitions["views"][0]["appearances"][3]["primary_action"] = {
        "kind": "open-source-trail",
        "target_evidence_id": call_id,
        "destination_label": "查看入口调用源码",
    }
    explorer = apply_view_definitions(explorer, definitions)

    browser_name, browser = _desktop_browser()
    site = tmp_path / "site"
    write_site(explorer, site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t12_call_evidence_dom.js",
        site / "t12-call-evidence-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t12-call-evidence-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=3000,
        viewport_width=1280,
        viewport_height=900,
    )
    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t12-call-evidence="pass"',
    )
