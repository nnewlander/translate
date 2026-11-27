from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

from booktrans.architecture_explorer.candidate_fragments import (
    authoritative_candidate_fragment_paths,
)
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


REPOSITORY = Path(__file__).resolve().parents[2]
PUBLISH = "scripts/architecture_explorer/generate_reviewed_relationship.py"
EXTRACTION = "document-package-extraction"
EXTRACTION_VIEW = "v4-t07-docx-extraction"
STORY_PARTS = "v4-t07-docx-story-parts"


@pytest.fixture(scope="module")
def built_site():
    candidates = authoritative_candidate_fragment_paths(
        tuple(
            path.relative_to(REPOSITORY).as_posix()
            for directory in ("candidates", "recursive-candidates")
            for path in (REPOSITORY / "architecture-explorer" / directory).glob("*.json")
        )
    )
    with TemporaryDirectory(prefix="t07-", dir=REPOSITORY / "output") as output:
        site = Path(output)
        command = [
            sys.executable,
            PUBLISH,
            "build",
            "--repository", ".",
            "--snapshot", "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
            "--mapping", "architecture-explorer/modules.json",
            "--candidate", "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json",
            "--review-mapping", "architecture-explorer/reviews/supervisor-worker.v1.json",
            "--review-source", "architecture-explorer/reviews/supervisor-worker.source.v1.json",
            "--review-state", "output/architecture-explorer/t5-lite/review-state.v1.json",
            "--navigation", "architecture-explorer/navigation.v1.json",
            "--web-assets", "architecture-explorer/web",
            "--output-directory", site.relative_to(REPOSITORY).as_posix(),
        ]
        for fragment in candidates:
            command.extend(("--candidate-fragment", fragment))
        completed = subprocess.run(
            command, cwd=REPOSITORY, capture_output=True, encoding="utf-8", timeout=120
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        yield site


@pytest.fixture(scope="module")
def projection(built_site):
    return json.loads((built_site / "explorer-data.json").read_text(encoding="utf-8"))[
        "view_projection"
    ]


def test_public_build_opens_docx_extraction_with_source_evidence(built_site, projection):
    assert projection["schema"] == "architecture-explorer.view-projection.v2"
    document_view = projection["view_index"]["document-understanding"]
    parent = next(item for item in document_view["appearances"] if item["node_id"] == EXTRACTION)
    assert parent.get("primary_action", {}).get("target_view_id") == EXTRACTION_VIEW
    internal = projection["view_index"][EXTRACTION_VIEW]
    assert internal["scope_node_id"] == EXTRACTION
    assert STORY_PARTS in {item["node_id"] for item in internal["appearances"]}
    node = projection["node_index"][STORY_PARTS]
    assert node["review"]["result"] == "Pending"
    evidence = projection["evidence_index"]
    assert {
        evidence[item]["definition"]["qualified_symbol"]
        for item in node["source_assignment"]["evidence_ids"]
    } == {
        "booktrans.ooxml.extractor.DocumentExtractor.extract",
        "booktrans.ooxml.extractor.DocumentExtractor._parse_word_parts",
        "booktrans.ooxml.extractor.DocumentExtractor._story_part_names",
    }
    assert (built_site / "app.js").read_bytes() == (
        REPOSITORY / "architecture-explorer/web/app.js"
    ).read_bytes()


def test_document_view_reaches_scope_and_request_with_distinct_data_handoffs(projection):
    start = projection["view_index"]["document-understanding"]
    view_builder = next(
        item for item in start["appearances"] if item["node_id"] == "document-translation-view"
    )
    assert view_builder.get("primary_action", {}).get("target_view_id") == "v4-t07-view-construction"
    bridge = projection["view_index"]["v4-t07-document-to-scope"]
    relationships = projection["relationship_index"]
    assert {
        (relationships[item]["source_node_id"], relationships[item]["target_node_id"])
        for item in bridge["relationship_ids"]
    } == {
        ("v4-t07-translation-segments", "v4-t07-logical-work-order-projection"),
        ("v4-t07-translation-segments", "v4-t07-calibration-request"),
    }
    assert all(relationships[item]["semantic"] == "collaboration" for item in bridge["relationship_ids"])
    evidence = projection["evidence_index"]
    for relationship_id in bridge["relationship_ids"]:
        relationship = relationships[relationship_id]
        assert relationship["review"]["result"] == "Pending"
        assert relationship["review"]["approved_scope"] == []
        assert all(evidence[item]["source_trail_kind"] == "definition" for item in relationship["evidence_ids"])
    assert {
        evidence[item]["definition"]["qualified_symbol"]
        for item in relationships["candidate-collaboration:v4-t07-d050-exact-binding-to-request"]["evidence_ids"]
    } == {
        "booktrans.production_worker.selection.load_checkpoint_requests",
        "booktrans.ooxml.verified_footnotes.exact_evidence_for_work_order",
        "booktrans.production_worker.selection.build_calibration_request",
        "booktrans.ooxml.verified_footnotes.verified_numeric_bindings_for_request",
    }


@pytest.mark.parametrize("width", [1440, 500])
def test_real_chrome_ticket_07_navigation(built_site, tmp_path, width):
    name, browser = _desktop_browser()
    assert name == "chrome"
    site = tmp_path / "site"
    shutil.copytree(built_site, site)
    script = "v4_ticket_07_dom.js"
    shutil.copyfile(Path(__file__).with_name(script), site / script)
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", f'<script src="{script}"></script></body>'
        ),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser, page, tmp_path / "profile", virtual_time_budget=7000, viewport_width=width
    )
    error = re.search(r'data-t07-error="([^"]+)"', completed.stdout)
    assert error is None, error.group(1) if error else ""
    _assert_browser_dom(completed, browser_name=name, pass_marker='data-t07="pass"')
    assert f'data-t07-width="{width}"' in completed.stdout


@pytest.mark.parametrize("width", [1440, 500])
def test_real_chrome_names_the_destination_before_cross_node_navigation(built_site, tmp_path, width):
    name, browser = _desktop_browser()
    assert name == "chrome"
    site = tmp_path / "site"
    shutil.copytree(built_site, site)
    script = "t11_navigation_titles_dom.js"
    shutil.copyfile(Path(__file__).with_name(script), site / script)
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", f'<script src="{script}"></script></body>'
        ),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser, page, tmp_path / "profile", virtual_time_budget=7000, viewport_width=width
    )
    error = re.search(r'data-t11-navigation-titles-error="([^"]+)"', completed.stdout)
    assert error is None, error.group(1) if error else ""
    _assert_browser_dom(completed, browser_name=name, pass_marker='data-t11-navigation-titles="pass"')
    views = json.loads((built_site / "explorer-data.json").read_text(encoding="utf-8"))[
        "view_projection"
    ]["view_index"]
    appearances = [appearance for view in views.values() for appearance in view["appearances"]]
    entries = [item for item in appearances if item.get("primary_action", {}).get("kind") == "open-view"]
    cross_node_count = sum(
        item["node_id"] != views[item["primary_action"]["target_view_id"]].get("scope_node_id")
        for item in entries
    )
    coverage = f"{len(views)}/{len(appearances)}/{len(entries)}/{cross_node_count}"
    assert f'data-t11-nav-coverage="{coverage}"' in completed.stdout
