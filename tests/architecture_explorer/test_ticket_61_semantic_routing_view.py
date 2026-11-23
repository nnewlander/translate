from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

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


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


def _ticket_61_projection() -> dict:
    explorer = apply_candidate_fragments(
        _current_explorer(),
        [
            _json("architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json"),
            _json(
                "architecture-explorer/recursive-candidates/"
                "translation-routing-and-acquisition.v2.json"
            ),
            _json("architecture-explorer/recursive-candidates/qwen-provider-adapters.v2.json"),
            _json(
                "architecture-explorer/recursive-candidates/"
                "quality-semantic-review-and-repair.v2.json"
            ),
        ],
    )
    explorer = apply_view_definitions(
        explorer,
        _json("architecture-explorer/views/recursive-views.v1.json"),
    )
    return apply_view_projection(explorer)


def test_ticket_61_real_data_projects_only_the_approved_internal_objects() -> None:
    projected = _ticket_61_projection()
    projection = projected["view_projection"]

    assert len(projection["node_index"]) == 34
    assert len(projection["view_index"]) == 12
    assert sum(
        reference["semantic_family"] == "candidate-collaboration"
        for reference in projection["relationship_index"].values()
    ) == 12
    assert sum(
        reference["semantic_family"] == "candidate-decomposition"
        for reference in projection["relationship_index"].values()
    ) == 9

    internal = projection["view_index"]["translation-routing-and-acquisition-internal"]
    assert internal["scope_node_id"] == "translation-routing-and-acquisition"
    assert [appearance["node_id"] for appearance in internal["appearances"]] == [
        "translation-routing-and-acquisition",
        "semantic-unit-route-decision",
        "translation-provider-dispatch-and-raw-projection",
    ]
    assert internal["relationship_ids"] == [
        "candidate-decomposition:translation-routing-and-acquisition-to-semantic-unit-route-decision",
        "candidate-decomposition:translation-routing-and-acquisition-to-translation-provider-dispatch-and-raw-projection",
        "candidate-collaboration:semantic-unit-route-decision-to-translation-provider-dispatch-and-raw-projection",
    ]

    translation_view = projection["view_index"]["translation-execution"]
    scope = next(
        appearance
        for appearance in translation_view["appearances"]
        if appearance["node_id"] == "translation-routing-and-acquisition"
    )
    assert scope["primary_action"] == {
        "kind": "open-view",
        "target_view_id": "translation-routing-and-acquisition-internal",
        "transition_kind": "decomposition",
        "destination_label": "查看语义路由与候选获取内部职责",
    }
    assert "primary_action" not in internal["appearances"][0]

    fragment = next(
        item
        for item in projected["recursive_candidate_fragments"]
        if item["scope_node_id"] == "translation-routing-and-acquisition"
    )
    nodes = {node["id"]: node for node in fragment["nodes"]}
    relationships = {item["id"]: item for item in fragment["relationships"]}
    assert set(nodes) == {
        "semantic-unit-route-decision",
        "translation-provider-dispatch-and-raw-projection",
    }
    assert set(relationships) == set(internal["relationship_ids"])
    assert all(node["review_status"] == "candidate" for node in nodes.values())
    assert all(
        relationship["review_status"] == "candidate"
        for relationship in relationships.values()
    )
    assert all("node_review" not in node for node in nodes.values())

    route_evidence = nodes["semantic-unit-route-decision"]["source_assignment"][
        "evidence_ids"
    ]
    assert route_evidence == [
        "evidence:sha256:e8aa77742b6c89e17eb224d888ab7c8feb2b8f69500edf9ee21d10c823bc01fd",
        "evidence:sha256:4e2f5b8f4df9cd957c300be39038a518ec9320963488d80642d55076656d2bfa",
    ]
    dispatch_evidence = nodes[
        "translation-provider-dispatch-and-raw-projection"
    ]["source_assignment"]["evidence_ids"]
    assert dispatch_evidence == [
        "evidence:sha256:326d7f9ec550c147e347710d216ccb36599f224427358602bc16b554b6b9117c",
        "evidence:sha256:2bbed5580700f742f3334e8ad69970f5a74f0f568580ba2a046706ef96facc8c",
        "evidence:sha256:991cf2dc83a87d39bd8950c9e6b62cb695517ce6aef5abd60ab0189436075c42",
        "evidence:sha256:844102e6051ddf2ec6ff4dce2f53437030fc0e6532843ac114e61a9522983d96",
        "evidence:sha256:f60811cca5914c260a41cc65dbb13ed8f69e3d992364f091bfe826d8afdd0680",
        "evidence:sha256:e3bbe044e1ce1ca9a85b6454073be0d43369e2adf9eb33329dc2fef0efa0a74d",
    ]
    source_index = projected["source_evidence_index"]
    assert source_index[dispatch_evidence[2]]["evidence_kind"] == "python_call"
    assert source_index[dispatch_evidence[3]]["evidence_kind"] == "python_call"
    assert "runtime receiver dispatch" in nodes[
        "translation-provider-dispatch-and-raw-projection"
    ]["plain_explanation"]


def test_ticket_61_contribution_has_the_exact_canonical_add_set() -> None:
    published_baseline = "371b9dd010be7f10b5efe4143f80d689cd653c52"
    historical_base_revision = "66585816244dbd58570d1b9363ab8585abb89b09"
    fragment_path = (
        "architecture-explorer/recursive-candidates/"
        "translation-routing-and-acquisition.v2.json"
    )
    subprocess.run(
        ["git", "cat-file", "-e", f"{published_baseline}^{{commit}}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", published_baseline, "HEAD"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )
    baseline_fragment_entries = subprocess.run(
        [
            "git",
            "ls-tree",
            "--name-only",
            published_baseline,
            "--",
            fragment_path,
        ],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert baseline_fragment_entries.stdout == ""
    baseline_views = json.loads(
        subprocess.run(
            [
                "git",
                "show",
                f"{published_baseline}:architecture-explorer/views/recursive-views.v1.json",
            ],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    )
    assert all(
        view["id"] != "translation-routing-and-acquisition-internal"
        for view in baseline_views["views"]
    )
    assert all(
        action["node_id"] != "translation-routing-and-acquisition"
        for action in baseline_views["entry_actions"]
    )

    fragment = _json(
        "architecture-explorer/recursive-candidates/"
        "translation-routing-and-acquisition.v2.json"
    )
    projected = _ticket_61_projection()["view_projection"]
    definitions = _json("architecture-explorer/views/recursive-views.v1.json")
    entry_action = next(
        action
        for action in definitions["entry_actions"]
        if action["view_id"] == "translation-execution"
        and action["node_id"] == "translation-routing-and-acquisition"
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
        *[("candidate-node", node["id"], node) for node in fragment["nodes"]],
        *[
            (relationship["semantic_family"], relationship["id"], relationship)
            for relationship in fragment["relationships"]
        ],
        (
            "view",
            "translation-routing-and-acquisition-internal",
            projected["view_index"]["translation-routing-and-acquisition-internal"],
        ),
        (
            "view-entry-action",
            "translation-execution::translation-routing-and-acquisition",
            entry_action,
        ),
    ]
    actual = {
        (object_type, object_id): hashlib.sha256(
            canonical_json_bytes(
                {
                    "object_type": object_type,
                    "object_id": object_id,
                    "payload": payload,
                }
            )
        ).hexdigest()
        for object_type, object_id, payload in objects
    }

    contribution = _json("docs/architecture-explorer-v3-ticket-03-content-delta.json")
    assert contribution["base_revision"] == historical_base_revision
    assert contribution["final_manifest"] is False
    assert all(
        change["operation"] == "add" and change["before_sha256"] is None
        for change in contribution["changes"]
    )
    assert {
        (change["object_type"], change["object_id"]): change["after_sha256"]
        for change in contribution["changes"]
    } == actual

    assert actual == {
        ("candidate-fragment", "translation-routing-and-acquisition"): "22dd28ec824f6b7fda1eb75a973f6141d90673294f03fede33022a074051768f",
        ("candidate-node", "semantic-unit-route-decision"): "c402e319e30b6504df66a3130ef4f575e61a7ef000207805bc7aca677a9dc1c1",
        ("candidate-node", "translation-provider-dispatch-and-raw-projection"): "4195e6a59cb0dfcc5e69e0c09c3c84b1097f605856c9071d4f21f21e2d153a19",
        (
            "candidate-decomposition",
            "candidate-decomposition:translation-routing-and-acquisition-to-semantic-unit-route-decision",
        ): "0a6088da50765c8f3d5224aef0cfacf5380785208b2864bb5e558990925a5b95",
        (
            "candidate-decomposition",
            "candidate-decomposition:translation-routing-and-acquisition-to-translation-provider-dispatch-and-raw-projection",
        ): "b2f1cd3668d2494cf846a10dd647bee76542cf041ee0ba79c4e9395a6fd3df0b",
        (
            "candidate-collaboration",
            "candidate-collaboration:semantic-unit-route-decision-to-translation-provider-dispatch-and-raw-projection",
        ): "ab3246275267fb2924c7af68e3ce0c9b30300e8935f54f0d2101c09102477f13",
        ("view", "translation-routing-and-acquisition-internal"): "91dfd4d5efac912985ecf0bff18a1c7d06cd241d9f39d9f54495ff1cadf887f5",
        (
            "view-entry-action",
            "translation-execution::translation-routing-and-acquisition",
        ): "33ddf626040ca0e1f82cab53e6d91f6dbc1560a588f062285a9e1a5a6dd4fc6f",
    }


@pytest.mark.parametrize("viewport_width", [1440, 1280, 960, 480])
def test_real_chrome_explores_ticket_61_and_preserves_recursive_navigation(
    tmp_path: Path,
    viewport_width: int,
) -> None:
    browser_name, browser = _desktop_browser()
    site = tmp_path / "site"
    write_site(
        _ticket_61_projection(),
        site,
        REPOSITORY / "architecture-explorer/web",
    )
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/ticket_61_semantic_routing_dom.js",
        site / "ticket-61-semantic-routing-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="ticket-61-semantic-routing-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=4000,
        viewport_width=viewport_width,
        viewport_height=900,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-ticket-61-semantic-routing="pass"',
    )


def test_real_chrome_preserves_every_projected_relationship_at_mobile_width(
    tmp_path: Path,
) -> None:
    browser_name, browser = _desktop_browser()
    if browser_name != "chrome":
        pytest.skip("the all-View relationship matrix is a required Chrome test")
    projected = _ticket_61_projection()
    site = tmp_path / "site"
    write_site(
        projected,
        site,
        REPOSITORY / "architecture-explorer/web",
    )
    evidence_path = tmp_path / "all-views-viewport-evidence.json"
    completed = subprocess.run(
        [
            "node",
            str(REPOSITORY / "tests/architecture_explorer/t7_continuous_resize.cjs"),
            str(browser),
            (site / "index.html").resolve().as_uri(),
            str(tmp_path / "all-views-profile"),
            str(evidence_path),
            "all-views-matrix",
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert [graph["graph_id"] for graph in evidence["graphs"]] == list(
        projected["view_projection"]["view_index"]
    )
    assert all(
        [observation["actual"] for observation in graph["observations"]]
        == [[480, 900], [960, 900], [1280, 900], [1440, 900]]
        for graph in evidence["graphs"]
    )
    published_evidence = os.environ.get(
        "ARCHITECTURE_EXPLORER_JOINT_VIEW_EVIDENCE"
    )
    if published_evidence:
        shutil.copyfile(evidence_path, published_evidence)


def test_real_chrome_exposes_an_unrenderable_relationship_as_a_layout_failure(
    tmp_path: Path,
) -> None:
    browser_name, browser = _desktop_browser()
    if browser_name != "chrome":
        pytest.skip("relationship layout failure disclosure is a required Chrome test")
    site = tmp_path / "site"
    write_site(
        _ticket_61_projection(),
        site,
        REPOSITORY / "architecture-explorer/web",
    )
    completed = subprocess.run(
        [
            "node",
            str(REPOSITORY / "tests/architecture_explorer/t7_continuous_resize.cjs"),
            str(browser),
            (site / "index.html").resolve().as_uri(),
            str(tmp_path / "layout-failure-profile"),
            str(tmp_path / "layout-failure-evidence.json"),
            "layout-failure-disclosure",
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
