from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.navigation import NAVIGATION_SCHEMA, apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
CANDIDATE = REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"
REVIEW_SOURCE = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"
REVIEW_STATE = REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
SNAPSHOT = REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP = REPOSITORY / "architecture-explorer/modules.json"
NAVIGATION = REPOSITORY / "architecture-explorer/navigation.v1.json"
WEB_ASSETS = REPOSITORY / "architecture-explorer/web"


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
        review_state_path=REVIEW_STATE,
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


@pytest.mark.parametrize(
    ("review_status", "continuity_status"),
    [
        ("unknown", "exact-match"),
        ("needs-review", "exact-match"),
        ("current-reviewed", "missing"),
    ],
)
def test_partial_assignment_rejects_relationship_that_fails_t3_trust_gate(
    t3_data: dict,
    review_status: str,
    continuity_status: str,
) -> None:
    invalid = deepcopy(t3_data)
    relationship = invalid["architecture"]["relationships"][0]
    relationship["review_status"] = review_status
    relationship["source_continuity"]["status"] = continuity_status

    with pytest.raises(
        ExplorerInputError,
        match="partial source assignment requires a current-reviewed relationship with exact-match continuity",
    ):
        apply_navigation(invalid, _json(NAVIGATION))


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


def test_real_browser_dom_order_closes_menu_without_suppressing_next_click(
    tmp_path: Path,
    t3_data: dict,
) -> None:
    browser_candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    browser = next((path for path in browser_candidates if path.is_file()), None)
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")

    from booktrans.architecture_explorer.generator import write_site

    site = tmp_path / "site"
    write_site(apply_navigation(t3_data, _json(NAVIGATION)), site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t4_dom_interactions.js",
        site / "t4-dom-interactions.js",
    )
    page = site / "index.html"
    html = page.read_text(encoding="utf-8").replace(
        "</body>", '<script src="t4-dom-interactions.js"></script></body>'
    )
    page.write_text(html, encoding="utf-8")
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
    assert 'data-dom-regression="pass"' in completed.stdout, completed.stdout


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
    assert "suppressPrimaryOnce" not in state
    assert 'navigationPath: ["top-level"]' in state


def test_return_navigation_uses_fixed_edge_icons_and_top_level_visibility() -> None:
    page = (REPOSITORY / "architecture-explorer/web/index.html").read_text(encoding="utf-8")
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    assert page.index('id="history-and-auxiliary"') < page.index('id="bottom-navigation"')
    assert page.index('id="bottom-navigation"') < page.index('id="load-error"')
    navigation_markup = page.split('id="bottom-navigation"', 1)[1].split("</nav>", 1)[0]
    assert navigation_markup.index('id="back-button"') < navigation_markup.index('id="home-button"')
    assert 'aria-label="返回上一级"' in navigation_markup
    assert 'aria-label="返回最高层"' in navigation_markup
    assert '<span class="floating-nav-icon" aria-hidden="true">←</span>' in navigation_markup
    assert '<span class="floating-nav-icon" aria-hidden="true">⌂</span>' in navigation_markup
    assert 'role="tooltip">返回上一级</span>' in navigation_markup
    assert 'role="tooltip">返回最高层</span>' in navigation_markup
    assert "position: fixed" in styles
    assert "top: 50%" in styles
    assert ".floating-navigation #back-button { left: 12px; }" in styles
    assert ".floating-navigation #home-button { right: 12px; }" in styles
    assert ".floating-navigation[hidden] { display: none; }" in styles
    assert "bottomNavigation.hidden = atTop" in app


def test_workflow_toggle_uses_warm_led_glow_and_restores_by_class_toggle() -> None:
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    highlight = styles.split(
        '.workflow-highlight .module-card[data-module-id="production-control"]', 1
    )[1].split("}", 1)[0]
    assert "border-color: #ffd633" in highlight
    assert "outline: 2px solid #ffd633" in highlight
    assert "0 0 6px 3px rgba(255, 221, 82, 0.42)" in highlight
    assert "0 0 14px 7px rgba(255, 239, 158, 0.25)" in highlight
    assert "linear-gradient" not in highlight
    assert 'classList.toggle("workflow-highlight", active)' in app
    assert 'setAttribute("aria-pressed", String(active))' in app
    assert 'active ? "取消流程高亮" : "高亮最小生产流程"' in app


def test_source_snapshot_is_default_collapsed_and_keeps_native_keyboard_control() -> None:
    page = (REPOSITORY / "architecture-explorer/web/index.html").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    snapshot_markup = page.split('<details id="source-snapshot"', 1)[1].split("</details>", 1)[0]
    snapshot_start_tag = snapshot_markup.split(">", 1)[0]
    assert " open" not in snapshot_start_tag
    assert "<summary>" in snapshot_markup
    assert "点击展开" not in snapshot_markup
    assert "点击收起" not in snapshot_markup
    assert 'id="snapshot-metadata"' in snapshot_markup
    for label in ("Revision", "工作区", "扫描器", "扫描时间", "确定性摘要"):
        assert label in app
    assert 'snapshotDetails.addEventListener("click"' in app
    assert 'event.target.closest?.("summary")' in app
    assert "event.preventDefault()" in app
    assert "stateApi.nextSnapshotOpen(snapshotDetails.open" in app


def test_snapshot_card_click_avoids_double_toggle_and_text_selection_collapse() -> None:
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")

    assert "hasSelectedSnapshotText" in app
    assert "selection.isCollapsed" in app
    assert "snapshotDetails.contains(selection.getRangeAt(0).commonAncestorContainer)" in app
    assert "isMouseActivation: event.detail > 0" in app
    assert "hasSelectedText: hasSelectedSnapshotText()" in app
    assert ".snapshot:hover" in styles
    assert "cursor: pointer" in styles
    assert ".snapshot dd" in styles and "cursor: text" in styles


def test_floating_navigation_keeps_palette_focus_and_hover_tooltips() -> None:
    styles = (REPOSITORY / "architecture-explorer/web/styles.css").read_text(encoding="utf-8")

    assert "border: 1px solid #d6a1b4" in styles
    assert "background: #fff4f7" in styles
    assert "color: #99546d" in styles
    assert ".floating-navigation button:hover" in styles
    assert ".floating-navigation button:focus-visible" in styles
    assert "outline: 3px solid rgba(214, 161, 180, 0.42)" in styles
    assert ".floating-navigation button:hover .floating-nav-tooltip" in styles
    assert ".floating-navigation button:focus-visible .floating-nav-tooltip" in styles
    assert "visibility: visible" in styles
