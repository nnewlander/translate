from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import re
import shutil

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.content_delta import canonical_json_bytes
from booktrans.architecture_explorer.generator import write_site
from booktrans.architecture_explorer.view_definitions import apply_view_definitions
from booktrans.architecture_explorer.view_projection import apply_view_projection
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)
from tests.architecture_explorer.test_view_projection import _current_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
FRAGMENT = REPOSITORY / "architecture-explorer/recursive-candidates/qwen-provider-adapters.v2.json"
WORKER_FRAGMENT = REPOSITORY / "architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json"
SEMANTIC_ROUTING_FRAGMENT = REPOSITORY / "architecture-explorer/recursive-candidates/translation-routing-and-acquisition.v2.json"
QUALITY_REVIEW_FRAGMENT = REPOSITORY / "architecture-explorer/recursive-candidates/quality-semantic-review-and-repair.v2.json"
VIEW_DEFINITIONS = REPOSITORY / "architecture-explorer/views/recursive-views.v1.json"
DELTA_CONTRIBUTION = REPOSITORY / "docs/architecture-explorer-v3-ticket-04-content-delta.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _combined_view_definitions() -> dict:
    return _json(VIEW_DEFINITIONS)


def _combined_fragments(fragment: dict) -> list[dict]:
    return [
        _json(WORKER_FRAGMENT),
        _json(SEMANTIC_ROUTING_FRAGMENT),
        fragment,
        _json(QUALITY_REVIEW_FRAGMENT),
    ]


def test_qwen_provider_fragment_projects_two_evidence_backed_adapters_without_collaboration() -> None:
    fragment = _json(FRAGMENT)
    explorer = apply_candidate_fragments(
        _current_explorer(), _combined_fragments(fragment)
    )
    explorer = apply_view_definitions(explorer, _combined_view_definitions())
    projection = apply_view_projection(explorer)["view_projection"]

    assert fragment["scope_node_id"] == "translation-provider-adapters"
    assert [node["id"] for node in fragment["nodes"]] == [
        "qwen-mt-single-text-adapter",
        "qwen-max-structured-translation-adapter",
    ]
    assert [relationship["id"] for relationship in fragment["relationships"]] == [
        "candidate-decomposition:translation-provider-adapters-to-qwen-mt-single-text-adapter",
        "candidate-decomposition:translation-provider-adapters-to-qwen-max-structured-translation-adapter",
    ]
    assert {
        relationship["semantic_family"] for relationship in fragment["relationships"]
    } == {"candidate-decomposition"}

    nodes = {node["id"]: node for node in fragment["nodes"]}
    assert nodes["qwen-mt-single-text-adapter"]["evidence_ids"] == [
        "evidence:sha256:68e3b87c5840c3d9a195191a26596f0d3ef1d4ed55df9ef983d5f703deef5504",
        "evidence:sha256:f9e82e1319536376ff94ba3e751355519df8038bf8f5704116a272817b4ca73d",
        "evidence:sha256:601034257756a5dacad2d2ef240ec8f81ea69df19e6b5aa48fe351a6cdab90c5",
        "evidence:sha256:ea3534de04d762c72d980c779591be38d208763150bf79424fd0b2d9bc1f2e30",
    ]
    assert nodes["qwen-max-structured-translation-adapter"]["evidence_ids"] == [
        "evidence:sha256:eea9ce0866ba8e783deb872e9c62e9139dc9b41ec215d6515efc9db04c2f2bea",
        "evidence:sha256:f19cd98a7bc6dcd892d7880bac15f7fbf0cbf3db70c5dfee64f9162a4e09cac7",
        "evidence:sha256:984489f7ed1dc3bf6ffc258e92c39a1d3554ea499a9dfb6e6e75fc35305fbdc3",
        "evidence:sha256:39cbf8872a181ebded36df6a3bbb5071fd5da00515163e941391d6d9fb57dcff",
        "evidence:sha256:78430c7182e8c7c65313dfe899941dff1832a149402635845cfa8c4a9ed6db5d",
    ]
    assert all(node["review_status"] == "candidate" for node in fragment["nodes"])
    assert all(
        relationship["review_status"] == "candidate"
        for relationship in fragment["relationships"]
    )
    assert "运行时 Provider 选择未知" in fragment["coverage_note"]
    assert "receiver dispatch 未知" in fragment["coverage_note"]
    assert "单段文本请求" in nodes["qwen-mt-single-text-adapter"]["professional_description"]
    assert "TranslationCandidate" in nodes["qwen-mt-single-text-adapter"]["professional_description"]
    assert "provenance" in nodes["qwen-mt-single-text-adapter"]["professional_description"]
    assert "schema" in nodes["qwen-max-structured-translation-adapter"]["professional_description"]
    assert "RAW/TranslationResult" in nodes["qwen-max-structured-translation-adapter"]["professional_description"]
    assert "不包含 reviewer/repair" in nodes["qwen-max-structured-translation-adapter"]["plain_explanation"]

    internal = projection["view_index"]["translation-provider-adapters-internal"]
    assert internal["display_name"] == "Qwen 提供方适配"
    assert internal["scope_node_id"] == "translation-provider-adapters"
    assert [appearance["node_id"] for appearance in internal["appearances"]] == [
        "translation-provider-adapters",
        "qwen-mt-single-text-adapter",
        "qwen-max-structured-translation-adapter",
    ]
    assert internal["relationship_ids"] == [
        "candidate-decomposition:translation-provider-adapters-to-qwen-mt-single-text-adapter",
        "candidate-decomposition:translation-provider-adapters-to-qwen-max-structured-translation-adapter",
    ]
    parent = next(
        appearance
        for appearance in projection["view_index"]["translation-execution"]["appearances"]
        if appearance["node_id"] == "translation-provider-adapters"
    )
    assert parent["primary_action"] == {
        "kind": "open-view",
        "target_view_id": "translation-provider-adapters-internal",
        "transition_kind": "decomposition",
        "destination_label": "查看 Qwen Provider 适配内部组件",
    }
    assert "primary_action" not in internal["appearances"][0]


@pytest.mark.parametrize("viewport_width", [1440, 1280, 960, 480])
def test_real_chrome_navigates_qwen_provider_internal_view_without_geometry_collisions(
    tmp_path: Path, viewport_width: int,
) -> None:
    fragment = _json(FRAGMENT)
    explorer = apply_candidate_fragments(
        _current_explorer(), _combined_fragments(fragment)
    )
    explorer = apply_view_definitions(explorer, _combined_view_definitions())
    explorer = apply_view_projection(explorer)

    site = tmp_path / "site"
    write_site(explorer, site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t13_qwen_provider_internal_dom.js",
        site / "t13-qwen-provider-internal-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="t13-qwen-provider-internal-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    browser_name, browser = _desktop_browser()
    completed = _dump_dom(
        browser,
        page,
        tmp_path / f"browser-profile-{viewport_width}",
        virtual_time_budget=3500,
        viewport_width=viewport_width,
        viewport_height=900,
    )
    browser_error = re.search(r'data-t13-error="([^"]+)"', completed.stdout)
    assert browser_error is None, browser_error.group(1) if browser_error else ""
    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t13-qwen-provider-internal="pass"',
    )


def test_ticket_content_delta_contribution_records_exact_canonical_additions() -> None:
    contribution = _json(DELTA_CONTRIBUTION)
    fragment = _json(FRAGMENT)
    definitions = _combined_view_definitions()
    explorer = apply_candidate_fragments(
        _current_explorer(), _combined_fragments(fragment)
    )
    explorer = apply_view_definitions(explorer, definitions)
    projection = apply_view_projection(explorer)["view_projection"]
    view = projection["view_index"]["translation-provider-adapters-internal"]
    action = next(
        item
        for item in definitions["entry_actions"]
        if item["view_id"] == "translation-execution"
        and item["node_id"] == "translation-provider-adapters"
    )
    objects = [
        (
            "candidate-fragment",
            fragment["scope_node_id"],
            {
                key: value
                for key, value in fragment.items()
                if key not in {"nodes", "relationships", "collaborations"}
            },
        ),
        *(("candidate-node", node["id"], node) for node in fragment["nodes"]),
        *(
            (relationship["semantic_family"], relationship["id"], relationship)
            for relationship in fragment["relationships"]
        ),
        ("view", view["id"], view),
        (
            "view-entry-action",
            "translation-execution::translation-provider-adapters",
            action,
        ),
    ]
    expected = []
    for object_type, object_id, payload in objects:
        envelope = {
            "object_type": object_type,
            "object_id": object_id,
            "payload": payload,
        }
        expected.append(
            {
                "operation": "add",
                "object_type": object_type,
                "object_id": object_id,
                "before_sha256": None,
                "after_sha256": sha256(canonical_json_bytes(envelope)).hexdigest(),
            }
        )

    assert contribution["schema"] == (
        "architecture-explorer.ticket-content-delta-contribution.v1"
    )
    assert contribution["base_revision"] == "66585816244dbd58570d1b9363ab8585abb89b09"
    assert contribution["final_manifest"] is False
    assert contribution["changes"] == sorted(
        expected, key=lambda item: (item["object_type"], item["object_id"])
    )
    assert not any(
        change["object_type"] == "candidate-collaboration"
        for change in contribution["changes"]
    )
