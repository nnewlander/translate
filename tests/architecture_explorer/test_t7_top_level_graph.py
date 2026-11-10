from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
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
PRODUCTION_FRAGMENT = REPOSITORY / "architecture-explorer/candidates/production-control.v1.json"


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
    return apply_candidate_fragments(navigable, [_json(PRODUCTION_FRAGMENT)])


def _desktop_browser() -> Path | None:
    return next(
        (
            path
            for path in (
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            )
            if path.is_file()
        ),
        None,
    )


def _dump_dom(
    browser: Path,
    page: Path,
    profile: Path,
    *,
    virtual_time_budget: int,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> subprocess.CompletedProcess[str]:
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
            f"--window-size={viewport_width},{viewport_height}",
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


@pytest.mark.parametrize(("viewport_width", "viewport_height"), [(1280, 900), (1440, 900)])
def test_real_browser_supports_compact_cards_and_multiple_inline_expansions(
    tmp_path: Path,
    explorer_data: dict,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser = _desktop_browser()
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")

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

    assert completed.returncode == 0, completed.stderr
    assert 'data-t7-inline-regression="pass"' in completed.stdout, completed.stdout


@pytest.mark.parametrize(("viewport_width", "viewport_height"), [(1024, 900), (1280, 900), (1440, 900)])
def test_real_browser_renders_production_candidates_in_the_shared_graph(
    tmp_path: Path,
    explorer_data: dict,
    viewport_width: int,
    viewport_height: int,
) -> None:
    browser = _desktop_browser()
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")

    site = tmp_path / "site"
    write_site(explorer_data, site, WEB_ASSETS)
    shutil.copyfile(
        REPOSITORY / "tests/architecture_explorer/t7_production_candidate_dom.js",
        site / "t7-production-candidate-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t7-production-candidate-dom.js"></script></body>',
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

    assert completed.returncode == 0, completed.stderr
    assert 'data-t7-production-candidate="pass"' in completed.stdout, completed.stdout


def test_degraded_relationship_never_receives_reviewed_graph_treatment(
    tmp_path: Path,
    explorer_data: dict,
) -> None:
    browser = _desktop_browser()
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")

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

    assert completed.returncode == 0, completed.stderr
    assert 'class="graph-edge reviewed"' not in completed.stdout
    assert 'class="relationship-line review-needs-review"' in completed.stdout
