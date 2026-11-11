from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import write_site
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)


REPOSITORY = Path(__file__).resolve().parents[2]
WEB_ASSETS = REPOSITORY / "architecture-explorer/web"
CANDIDATE_FRAGMENTS = tuple(
    REPOSITORY / "architecture-explorer/candidates" / filename
    for filename in (
        "document-understanding.v1.json",
        "scope-and-request.v1.json",
        "production-control.v1.json",
        "translation-execution.v1.json",
        "quality-and-repair.v1.json",
        "production-evidence.v1.json",
        "candidate-and-release.v1.json",
    )
)

REMAINING_CANDIDATE_MODULES = (
    "document-understanding",
    "scope-and-request",
    "translation-execution",
    "quality-and-repair",
    "production-evidence",
    "candidate-and-release",
)

EXPECTED_CANDIDATE_GRAPH_IDS = {
    "document-understanding": (
        ("document-package-extraction", "document-translation-view"),
        ("candidate-collaboration:document-extraction-to-view",),
    ),
    "scope-and-request": (
        ("scope-logical-work-order-selection", "scope-request-context-assembly"),
        ("candidate-collaboration:scope-selection-to-request",),
    ),
    "production-control": (
        (
            "supervisor-orchestration",
            "worker-launch-boundary",
            "state-and-control-decisions",
            "production-control-adapters",
            "deterministic-worker-wrapper",
        ),
        (
            "candidate-collaboration:supervisor-to-control-adapters",
            "candidate-collaboration:supervisor-to-worker-wrapper",
        ),
    ),
    "translation-execution": (
        ("translation-routing-and-acquisition", "translation-provider-adapters"),
        ("candidate-collaboration:translation-routing-to-provider",),
    ),
    "quality-and-repair": (
        ("quality-deterministic-qa", "quality-semantic-review-and-repair"),
        ("candidate-collaboration:quality-qa-to-semantic-repair",),
    ),
    "production-evidence": (
        ("checkpoint-evidence-persistence", "checkpoint-manifest-seal"),
        ("candidate-collaboration:checkpoint-evidence-to-manifest-seal",),
    ),
    "candidate-and-release": (
        (
            "candidate-document-projection",
            "package-preservation-verifier",
            "release-membership-derivation",
        ),
        ("candidate-collaboration:projection-to-package-verification",),
    ),
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def explorer_data() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        REPOSITORY / "architecture-explorer/modules.json",
        _json(REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _json(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"),
        _json(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json",
    )
    with_facts = attach_python_source_facts(reviewed, scan_python_source_facts(REPOSITORY))
    navigable = apply_navigation(with_facts, _json(REPOSITORY / "architecture-explorer/navigation.v1.json"))
    return apply_candidate_fragments(navigable, [_json(path) for path in CANDIDATE_FRAGMENTS])


DESKTOP_BROWSERS = {
    "chrome": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    "edge": Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
}


def _desktop_browser() -> tuple[str, Path]:
    browser_name = os.environ.get("ARCHITECTURE_EXPLORER_BROWSER", "").strip().lower()
    if browser_name not in DESKTOP_BROWSERS:
        raise RuntimeError(
            "set ARCHITECTURE_EXPLORER_BROWSER explicitly to chrome or edge"
        )
    browser = DESKTOP_BROWSERS[browser_name]
    if not browser.is_file():
        raise RuntimeError(
            f"selected {browser_name} browser is unavailable at {browser}"
        )
    return browser_name, browser


def test_desktop_browser_requires_an_explicit_supported_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARCHITECTURE_EXPLORER_BROWSER", raising=False)

    with pytest.raises(RuntimeError, match="ARCHITECTURE_EXPLORER_BROWSER"):
        _desktop_browser()

    monkeypatch.setenv("ARCHITECTURE_EXPLORER_BROWSER", "safari")
    with pytest.raises(RuntimeError, match="chrome or edge"):
        _desktop_browser()


def test_desktop_browser_honours_an_explicit_edge_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    edge = tmp_path / "msedge.exe"
    edge.touch()
    monkeypatch.setitem(DESKTOP_BROWSERS, "edge", edge)
    monkeypatch.setenv("ARCHITECTURE_EXPLORER_BROWSER", "edge")

    browser_name, browser = _desktop_browser()

    assert browser_name == "edge"
    assert browser.name == "msedge.exe"


def test_empty_browser_dom_cannot_be_reported_as_success() -> None:
    completed = subprocess.CompletedProcess(
        args=["msedge.exe", "--dump-dom"],
        returncode=0,
        stdout="",
        stderr="",
    )

    with pytest.raises(AssertionError, match="edge returned empty DOM"):
        _assert_browser_dom(
            completed,
            browser_name="edge",
            pass_marker='data-t7-example="pass"',
        )


_VIEWPORT_FRAME_INSETS: dict[Path, tuple[int, int]] = {}


def _viewport_frame_insets(browser: Path, scratch: Path) -> tuple[int, int]:
    cached = _VIEWPORT_FRAME_INSETS.get(browser)
    if cached is not None:
        return cached

    probe_width, probe_height = 1280, 900
    probe = scratch / "viewport-probe.html"
    probe.write_text(
        '<script>document.documentElement.dataset.viewport = `${window.innerWidth}x${window.innerHeight}`;</script>',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--no-first-run",
            "--no-default-browser-check",
            "--force-device-scale-factor=1",
            f"--window-size={probe_width},{probe_height}",
            f"--user-data-dir={scratch / 'viewport-probe-profile'}",
            "--dump-dom",
            probe.resolve().as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"browser viewport probe failed: {completed.stderr}")
    match = re.search(r'data-viewport="(\d+)x(\d+)"', completed.stdout)
    if match is None:
        raise RuntimeError("browser viewport probe did not report innerWidth/innerHeight")
    inner_width, inner_height = (int(value) for value in match.groups())
    insets = (probe_width - inner_width, probe_height - inner_height)
    if insets[0] < 0 or insets[1] < 0:
        raise RuntimeError(f"browser viewport probe returned invalid frame insets: {insets}")
    _VIEWPORT_FRAME_INSETS[browser] = insets
    return insets


def _dump_dom(
    browser: Path,
    page: Path,
    profile: Path,
    *,
    virtual_time_budget: int,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> subprocess.CompletedProcess[str]:
    frame_width, frame_height = _viewport_frame_insets(browser, profile.parent)
    return subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--force-device-scale-factor=1",
            f"--window-size={viewport_width + frame_width},{viewport_height + frame_height}",
            f"--user-data-dir={profile}",
            f"--virtual-time-budget={virtual_time_budget}",
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


def _assert_browser_dom(
    completed: subprocess.CompletedProcess[str],
    *,
    browser_name: str,
    pass_marker: str | None,
) -> str:
    assert completed.returncode == 0, (
        f"{browser_name} exited with {completed.returncode}: {completed.stderr}"
    )
    assert completed.stdout.strip(), (
        f"{browser_name} returned empty DOM; stderr: {completed.stderr or '<empty>'}"
    )
    if pass_marker is not None:
        assert pass_marker in completed.stdout, completed.stdout
    return completed.stdout


def test_all_candidate_graphs_preserve_the_published_node_and_collaboration_ids(
    explorer_data: dict,
) -> None:
    drilldowns = {
        item["parent_module_id"]: item for item in explorer_data["navigation"]["drilldowns"]
    }

    assert set(drilldowns) == set(EXPECTED_CANDIDATE_GRAPH_IDS)
    for parent_module_id, (
        node_ids,
        collaboration_ids,
    ) in EXPECTED_CANDIDATE_GRAPH_IDS.items():
        drilldown = drilldowns[parent_module_id]
        assert tuple(node["id"] for node in drilldown["children"]) == node_ids
        assert tuple(item["id"] for item in drilldown["collaborations"]) == collaboration_ids
        assert all(node["review_status"] == "candidate" for node in drilldown["children"])
        assert all(
            item["review_status"] == "candidate" for item in drilldown["collaborations"]
        )

    assert sum(len(item["children"]) for item in drilldowns.values()) == 18
    assert sum(len(item["collaborations"]) for item in drilldowns.values()) == 8


def test_inline_expansion_state_supports_multiple_cards_and_clears_on_navigation() -> None:
    completed = subprocess.run(
        ["node", str(REPOSITORY / "tests/architecture_explorer/t7_top_level_state.cjs")],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "all assertions passed" in completed.stdout


def test_real_browser_opens_candidate_evidence_from_every_node_context_menu(
    tmp_path: Path,
    explorer_data: dict,
) -> None:
    browser_name, browser = _desktop_browser()

    site = tmp_path / "site"
    write_site(explorer_data, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_candidate_context_evidence_dom.js",
        site / "t7-candidate-context-evidence-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t7-candidate-context-evidence-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=9000,
        viewport_width=1280,
        viewport_height=900,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t7-candidate-context-evidence="pass"',
    )


@pytest.mark.parametrize(("viewport_width", "viewport_height"), [(1280, 900), (1440, 900)])
def test_real_browser_supports_compact_cards_and_multiple_inline_expansions(
    tmp_path: Path,
    explorer_data: dict,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser_name, browser = _desktop_browser()

    site = tmp_path / "site"
    write_site(explorer_data, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_top_level_dom.js",
        site / "t7-top-level-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t7-top-level-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=2500,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t7-inline-regression="pass"',
    )


@pytest.mark.parametrize(("viewport_width", "viewport_height"), [(960, 900), (1280, 900), (1440, 900)])
def test_real_browser_renders_production_candidates_in_the_shared_graph(
    tmp_path: Path,
    explorer_data: dict,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser_name, browser = _desktop_browser()

    site = tmp_path / "site"
    write_site(explorer_data, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_production_candidate_dom.js",
        site / "t7-production-candidate-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            (
                f'<script>window.T7_EXPECTED_VIEWPORT = [{viewport_width}, {viewport_height}];</script>'
                '<script src="t7-production-candidate-dom.js"></script></body>'
            ),
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=2500,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t7-production-candidate="pass"',
    )


@pytest.mark.parametrize("parent_module_id", REMAINING_CANDIDATE_MODULES)
@pytest.mark.parametrize(("viewport_width", "viewport_height"), [(960, 900), (1280, 900), (1440, 900)])
def test_real_browser_renders_each_remaining_candidate_module_in_the_shared_graph(
    tmp_path: Path,
    explorer_data: dict,
    parent_module_id: str,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser_name, browser = _desktop_browser()

    site = tmp_path / "site"
    write_site(explorer_data, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_remaining_candidate_dom.js",
        site / "t7-remaining-candidate-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            (
                f'<script>window.T7_TEST_PARENT = "{parent_module_id}";</script>'
                f'<script>window.T7_EXPECTED_VIEWPORT = [{viewport_width}, {viewport_height}];</script>'
                '<script src="t7-remaining-candidate-dom.js"></script></body>'
            ),
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=3500,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t7-remaining-candidate="pass"',
    )


def test_real_browser_draws_no_inferred_edge_without_a_candidate_collaboration(
    tmp_path: Path,
    explorer_data: dict,
) -> None:
    browser_name, browser = _desktop_browser()

    without_collaboration = deepcopy(explorer_data)
    document_drilldown = next(
        item
        for item in without_collaboration["navigation"]["drilldowns"]
        if item["parent_module_id"] == "document-understanding"
    )
    document_drilldown["collaborations"] = []
    site = tmp_path / "site"
    write_site(without_collaboration, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_no_collaboration_dom.js",
        site / "t7-no-collaboration-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            '<script src="t7-no-collaboration-dom.js"></script></body>',
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=1500,
    )

    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t7-no-collaboration="pass"',
    )


def test_degraded_relationship_never_receives_reviewed_graph_treatment(
    tmp_path: Path,
    explorer_data: dict,
) -> None:
    browser_name, browser = _desktop_browser()

    degraded = deepcopy(explorer_data)
    relationship = degraded["architecture"]["relationships"][0]
    relationship["review_status"] = "needs-review"
    relationship["source_continuity"]["status"] = "material-change"
    site = tmp_path / "site"
    write_site(degraded, site, WEB_ASSETS)

    completed = _dump_dom(
        browser,
        site / "index.html",
        tmp_path / "browser-profile",
        virtual_time_budget=500,
    )

    dom = _assert_browser_dom(completed, browser_name=browser_name, pass_marker=None)
    assert 'class="graph-edge reviewed"' not in dom
    assert 'class="relationship-line review-needs-review"' in dom
