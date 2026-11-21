from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import re
import shutil

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.content_delta import canonical_json_bytes
from booktrans.architecture_explorer.generator import ExplorerInputError, write_site
from booktrans.architecture_explorer.view_definitions import apply_view_definitions
from booktrans.architecture_explorer.view_projection import apply_view_projection
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)
from tests.architecture_explorer.test_view_projection import _current_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
FRAGMENT_PATH = (
    REPOSITORY
    / "architecture-explorer/recursive-candidates/quality-semantic-review-and-repair.v2.json"
)
BASE_VIEW_DEFINITIONS_PATH = (
    REPOSITORY / "architecture-explorer/views/recursive-views.v1.json"
)
TICKET_VIEW_DEFINITIONS_PATH = (
    REPOSITORY
    / "architecture-explorer/views/quality-semantic-review-and-repair.ticket-63.v1.json"
)
CONTENT_DELTA_PATH = (
    REPOSITORY / "docs/architecture-explorer-v3-ticket-05-content-delta.json"
)
WORKER_FRAGMENT_PATH = (
    REPOSITORY / "architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json"
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ticket_view_definitions() -> dict:
    baseline = _json(BASE_VIEW_DEFINITIONS_PATH)
    contribution = _json(TICKET_VIEW_DEFINITIONS_PATH)
    return {
        "schema": "architecture-explorer.view-definitions.v1",
        "entry_actions": [
            *baseline["entry_actions"],
            *contribution["entry_actions"],
        ],
        "views": [*baseline["views"], *contribution["views"]],
    }


def test_ticket_63_projects_three_distinct_candidate_responsibilities_with_evidence() -> None:
    explorer = apply_candidate_fragments(_current_explorer(), [_json(FRAGMENT_PATH)])
    fragment = explorer["recursive_candidate_fragments"][0]
    nodes = {node["id"]: node for node in fragment["nodes"]}
    relationships = {item["id"]: item for item in fragment["relationships"]}

    assert set(nodes) == {
        "semantic-review-contract-boundary",
        "repair-authorization-gate",
        "constrained-repair-call-boundary",
    }
    assert set(relationships) == {
        "candidate-decomposition:quality-semantic-review-and-repair-to-semantic-review-contract-boundary",
        "candidate-decomposition:quality-semantic-review-and-repair-to-repair-authorization-gate",
        "candidate-decomposition:quality-semantic-review-and-repair-to-constrained-repair-call-boundary",
        "candidate-collaboration:semantic-review-contract-boundary-to-repair-authorization-gate",
        "candidate-collaboration:repair-authorization-gate-to-constrained-repair-call-boundary",
    }

    assert all(node["review_status"] == "candidate" for node in nodes.values())
    assert all(
        relationship["review_status"] == "candidate"
        for relationship in relationships.values()
    )
    assert all(
        node["source_assignment"]["status"] == "unverified"
        for node in nodes.values()
    )

    review = nodes["semantic-review-contract-boundary"]
    assert review["source_assignment"]["evidence_ids"] == [
        "evidence:sha256:2ad2ef43dd06310aac8f94beb75ced83a771a10e09787afbffa312c8df52b9ab",
        "evidence:sha256:21bdd7d118512a9003b58a70c49bed4c10f42a27df0ef5a88614b4f5e363b8e5",
        "evidence:sha256:b28fe7c13cfa10b3c65bd84e681bb5628f1589e15bd46b84923923632059c139",
        "evidence:sha256:6b3eae8fe5fc3dfa81f7762da21b98e8579ca042c4a87f2fbc739983433790d1",
    ]
    assert "具体 reviewer receiver dispatch 仍未知" in review["responsibility"]

    gate = nodes["repair-authorization-gate"]
    assert gate["source_assignment"]["evidence_ids"] == [
        "evidence:sha256:cb24e0b86ebcd884747d463bafdd332faf46a4ec9bc6c8614cfdbac3278d9579",
        "evidence:sha256:c4b4ffa893088a9463f196276d23df2e240683e0f2899783b2f248c5e6552dfd",
    ]
    assert "批准合同范围" in gate["responsibility"]
    assert "不作自动批准" in gate["responsibility"]

    repair = nodes["constrained-repair-call-boundary"]
    assert repair["source_assignment"]["evidence_ids"] == [
        "evidence:sha256:e1a90c2d85a20605e101b13d2b236cbc0bcbd76522a1c402fdc0c76846aded77",
        "evidence:sha256:60c20454d9012e1006dc2a6f10060f74e4a8113a506b0426d5f6bafedf2bfceb",
    ]
    assert "批准范围" in repair["responsibility"]
    assert "具体 repair receiver dispatch 仍未知" in repair["responsibility"]

    for relationship in relationships.values():
        assert relationship["evidence_ids"]
        assert "不证明实际运行顺序或运行可达" in relationship[
            "professional_description"
        ]


def test_ticket_63_projects_internal_view_entry_without_trust_propagation() -> None:
    explorer = apply_candidate_fragments(
        _current_explorer(), [_json(WORKER_FRAGMENT_PATH), _json(FRAGMENT_PATH)]
    )
    quality_drilldown = next(
        item
        for item in explorer["navigation"]["drilldowns"]
        if item["parent_module_id"] == "quality-and-repair"
    )
    parent_node = next(
        item
        for item in quality_drilldown["children"]
        if item["id"] == "quality-semantic-review-and-repair"
    )
    parent_node["node_review"] = {"projection": "Current / Approved"}
    explorer = apply_view_definitions(explorer, _ticket_view_definitions())

    projected = apply_view_projection(explorer)
    projection = projected["view_projection"]
    internal = projection["view_index"]["quality-semantic-review-and-repair-internal"]

    assert internal["scope_node_id"] == "quality-semantic-review-and-repair"
    assert [item["node_id"] for item in internal["appearances"]] == [
        "quality-semantic-review-and-repair",
        "semantic-review-contract-boundary",
        "repair-authorization-gate",
        "constrained-repair-call-boundary",
    ]
    assert internal["relationship_ids"] == [
        "candidate-decomposition:quality-semantic-review-and-repair-to-semantic-review-contract-boundary",
        "candidate-decomposition:quality-semantic-review-and-repair-to-repair-authorization-gate",
        "candidate-decomposition:quality-semantic-review-and-repair-to-constrained-repair-call-boundary",
        "candidate-collaboration:semantic-review-contract-boundary-to-repair-authorization-gate",
        "candidate-collaboration:repair-authorization-gate-to-constrained-repair-call-boundary",
    ]

    quality_view = projection["view_index"]["quality-and-repair"]
    parent_appearance = next(
        item
        for item in quality_view["appearances"]
        if item["node_id"] == "quality-semantic-review-and-repair"
    )
    assert parent_appearance["primary_action"] == {
        "kind": "open-view",
        "target_view_id": "quality-semantic-review-and-repair-internal",
        "transition_kind": "decomposition",
        "destination_label": "查看语义审核与受限修复内部职责",
    }

    fragment = next(
        item
        for item in projected["recursive_candidate_fragments"]
        if {node["id"] for node in item["nodes"]}
        == {
            "semantic-review-contract-boundary",
            "repair-authorization-gate",
            "constrained-repair-call-boundary",
        }
    )
    assert all(node["review_status"] == "candidate" for node in fragment["nodes"])
    assert all("node_review" not in node for node in fragment["nodes"])
    assert parent_node["node_review"]["projection"] == "Current / Approved"
    assert all(
        relationship["review_status"] == "candidate"
        for relationship in fragment["relationships"]
    )


def test_ticket_63_candidate_decomposition_remains_acyclic() -> None:
    fragment = _json(FRAGMENT_PATH)
    fragment["relationships"].append(
        {
            "id": "candidate-decomposition:ticket-63-test-only-cycle",
            "semantic_family": "candidate-decomposition",
            "source_node_id": "semantic-review-contract-boundary",
            "target_node_id": "quality-semantic-review-and-repair",
            "name": "测试专用非法反向分解",
            "professional_description": "只用于验证 Ticket 63 的分解 DAG 失败关闭。",
            "plain_explanation": "这不是生产候选关系。",
            "review_status": "candidate",
            "evidence_ids": fragment["nodes"][0]["evidence_ids"],
        }
    )

    with pytest.raises(
        ExplorerInputError,
        match=(
            "quality-semantic-review-and-repair -> semantic-review-contract-boundary "
            "-> quality-semantic-review-and-repair"
        ),
    ):
        apply_candidate_fragments(_current_explorer(), [fragment])


def test_ticket_63_canonical_hash_contribution_matches_authoritative_objects() -> None:
    fragment = _json(FRAGMENT_PATH)
    definitions = _ticket_view_definitions()
    explorer = apply_candidate_fragments(
        _current_explorer(), [_json(WORKER_FRAGMENT_PATH), fragment]
    )
    projection = apply_view_projection(
        apply_view_definitions(explorer, definitions)
    )["view_projection"]
    view = projection["view_index"]["quality-semantic-review-and-repair-internal"]
    action = next(
        item
        for item in definitions["entry_actions"]
        if item["view_id"] == "quality-and-repair"
        and item["node_id"] == "quality-semantic-review-and-repair"
    )
    metadata = {
        key: value
        for key, value in fragment.items()
        if key not in {"nodes", "relationships", "collaborations"}
    }
    objects = [
        ("candidate-fragment", fragment["scope_node_id"], metadata),
        *(("candidate-node", item["id"], item) for item in fragment["nodes"]),
        *(
            (item["semantic_family"], item["id"], item)
            for item in fragment["relationships"]
        ),
        ("view", view["id"], view),
        (
            "view-entry-action",
            f'{action["view_id"]}::{action["node_id"]}',
            action,
        ),
    ]
    actual = []
    for object_type, object_id, payload in objects:
        envelope = {
            "object_type": object_type,
            "object_id": object_id,
            "payload": payload,
        }
        actual.append(
            {
                "operation": "add",
                "object_type": object_type,
                "object_id": object_id,
                "before_sha256": None,
                "after_sha256": sha256(canonical_json_bytes(envelope)).hexdigest(),
            }
        )
    actual.sort(key=lambda item: (item["object_type"], item["object_id"]))

    contribution = _json(CONTENT_DELTA_PATH)
    assert contribution["ticket_base_revision"] == (
        "66585816244dbd58570d1b9363ab8585abb89b09"
    )
    assert contribution["final_batch_manifest_produced"] is False
    assert contribution["changes"] == actual


def test_ticket_63_reuses_renderer_without_deterministic_qa_scope() -> None:
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(
        encoding="utf-8"
    )
    fragment_text = FRAGMENT_PATH.read_text(encoding="utf-8")
    view = next(
        item
        for item in _json(TICKET_VIEW_DEFINITIONS_PATH)["views"]
        if item["id"] == "quality-semantic-review-and-repair-internal"
    )

    assert "quality-semantic-review-and-repair-internal" not in app
    assert "quality-semantic-review-and-repair-internal" not in styles
    assert "qa_fn" not in fragment_text
    assert all(
        item["node_id"] != "quality-deterministic-qa"
        for item in view["appearances"]
    )


@pytest.mark.parametrize("viewport_width", [1440, 1280, 960, 480])
def test_ticket_63_real_chrome_enters_inspects_and_returns_from_internal_view(
    tmp_path: Path,
    viewport_width: int,
) -> None:
    browser_name, browser = _desktop_browser()
    assert browser_name == "chrome", "Ticket 63 acceptance requires real Chrome"
    explorer = apply_candidate_fragments(
        _current_explorer(), [_json(WORKER_FRAGMENT_PATH), _json(FRAGMENT_PATH)]
    )
    explorer = apply_view_definitions(explorer, _ticket_view_definitions())
    site = tmp_path / "site"
    write_site(apply_view_projection(explorer), site, REPOSITORY / "architecture-explorer/web")
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/ticket_63_semantic_review_dom.js",
        site / "ticket-63-semantic-review-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="ticket-63-semantic-review-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=7000,
        viewport_width=viewport_width,
        viewport_height=900,
    )

    browser_error = re.search(r'data-ticket63-error="([^"]+)"', completed.stdout)
    assert browser_error is None, browser_error.group(1) if browser_error else ""

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-ticket63-semantic-review="pass"',
    )
