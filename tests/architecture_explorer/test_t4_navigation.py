from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.navigation import NAVIGATION_SCHEMA, apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
CANDIDATE = REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"
REVIEW_SOURCE = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"
SNAPSHOT = REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP = REPOSITORY / "architecture-explorer/modules.json"
NAVIGATION = REPOSITORY / "architecture-explorer/navigation.v1.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def t3_data() -> dict:
    return publish_reviewed_explorer(
        REPOSITORY,
        SNAPSHOT,
        MODULE_MAP,
        _json(CANDIDATE),
        _json(REVIEW_MAP),
        _json(REVIEW_SOURCE),
    )


def test_navigation_adds_candidate_responsibilities_without_mutating_t3_relationship(t3_data: dict) -> None:
    before = deepcopy(t3_data)
    result = apply_navigation(t3_data, _json(NAVIGATION))

    assert t3_data == before
    assert result["architecture"]["relationships"] == before["architecture"]["relationships"]
    assert result["navigation"]["schema"] == NAVIGATION_SCHEMA
    assert len(result["navigation_content_sha256"]) == 64
    drilldown = result["navigation"]["drilldowns"][0]
    assert drilldown["parent_module_id"] == "production-control"
    assert [item["name"] for item in drilldown["children"]] == [
        "Supervisor 编排",
        "Worker 启动边界",
        "状态与控制决策",
    ]
    assert all(item["review_status"] == "candidate" for item in drilldown["children"])
    assert "全部源码覆盖" in drilldown["coverage_note"]


def test_partial_assignment_references_the_exact_t3_relationship_record(t3_data: dict) -> None:
    result = apply_navigation(t3_data, _json(NAVIGATION))
    relationship = result["architecture"]["relationships"][0]
    children = result["navigation"]["drilldowns"][0]["children"]
    boundary = next(item for item in children if item["id"] == "worker-launch-boundary")

    assert boundary["source_assignment"]["status"] == "partial"
    assert boundary["source_assignment"]["relationship_ids"] == [relationship["id"]]
    assert "evidence" not in boundary
    assert relationship["review_status"] == "current-reviewed"
    assert len(relationship["evidence_ids"]) == 13


def test_history_and_auxiliary_is_default_collapsed_and_unclassified(t3_data: dict) -> None:
    history = apply_navigation(t3_data, _json(NAVIGATION))["navigation"]["history_and_auxiliary"]
    assert history["default_expanded"] is False
    assert history["items"]
    assert {item["classification"] for item in history["items"]} == {"unclassified"}
    assert "文件名" in history["items"][0]["note"]


def test_navigation_rejects_a_second_or_unknown_relationship_fact(t3_data: dict) -> None:
    navigation = _json(NAVIGATION)
    boundary = navigation["drilldowns"][0]["children"][1]
    boundary["source_assignment"]["relationship_ids"] = ["relationship:invented"]

    with pytest.raises(ExplorerInputError, match="unknown relationship"):
        apply_navigation(t3_data, navigation)


def test_navigation_rejects_promoting_candidate_responsibility(t3_data: dict) -> None:
    navigation = _json(NAVIGATION)
    navigation["drilldowns"][0]["children"][0]["review_status"] = "current-reviewed"

    with pytest.raises(ExplorerInputError, match="must remain candidate"):
        apply_navigation(t3_data, navigation)


def test_node_interaction_state_suite_covers_navigation_menu_and_misclick_guard() -> None:
    completed = subprocess.run(
        ["node", str(REPOSITORY / "tests/architecture_explorer/t4_state_interactions.cjs")],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    assert "all assertions passed" in completed.stdout


def test_offline_ui_wires_read_only_breadcrumb_context_menu_and_local_state_asset() -> None:
    page = (REPOSITORY / "architecture-explorer/web/index.html").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")
    state = (REPOSITORY / "architecture-explorer/web/t4-state.js").read_text(encoding="utf-8")

    assert '<div id="breadcrumb"' in page
    assert "breadcrumb" not in page.split('<div id="breadcrumb"', 1)[1].split("</div>", 1)[0].lower() or "<a " not in page
    assert "查看关系与源码证据" in page
    assert 'src="t4-state.js"' in page
    assert "contextmenu" in app
    assert "pointerdown" in app
    assert "stopImmediatePropagation" in app
    assert "suppressPrimaryOnce" in state


def test_return_navigation_is_after_history_and_uses_bottom_alignment() -> None:
    page = (REPOSITORY / "architecture-explorer/web/index.html").read_text(encoding="utf-8")
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    assert page.index('id="history-and-auxiliary"') < page.index('id="bottom-navigation"')
    assert page.index('id="bottom-navigation"') < page.index('id="load-error"')
    bottom_markup = page.split('id="bottom-navigation"', 1)[1].split("</nav>", 1)[0]
    assert bottom_markup.index('id="back-button"') < bottom_markup.index('id="home-button"')
    assert ".bottom-navigation #back-button { justify-self: start; }" in styles
    assert ".bottom-navigation #home-button { justify-self: end; }" in styles
    assert "bottomNavigation.hidden = atTop" in app


def test_workflow_toggle_uses_warm_gradient_highlight_and_restores_by_class_toggle() -> None:
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    highlight = styles.split(
        '.workflow-highlight .module-card[data-module-id="production-control"]', 1
    )[1].split("}", 1)[0]
    assert "border: 6px solid transparent" in highlight
    assert "linear-gradient(135deg" in highlight
    assert "#f5bd46" in highlight
    assert 'classList.toggle("workflow-highlight", active)' in app
    assert 'setAttribute("aria-pressed", String(active))' in app
    assert 'active ? "取消流程高亮" : "高亮最小生产流程"' in app
