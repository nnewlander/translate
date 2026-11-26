from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import pytest

from booktrans.architecture_explorer.candidate_fragments import (
    authoritative_candidate_fragment_paths,
)
from booktrans.architecture_explorer.review_cli import main
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


REPOSITORY = Path(__file__).resolve().parents[2]
EXPLORER = REPOSITORY / "architecture-explorer"


@pytest.fixture(scope="module")
def generated_site(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    suffix = tmp_path_factory.mktemp("checkpoint-release").parent.name
    site = Path("output/architecture-explorer/v4-ticket-08-tests") / suffix
    args = [
        "build",
        "--repository", str(REPOSITORY),
        "--snapshot", str(REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"),
        "--mapping", str(EXPLORER / "modules.json"),
        "--candidate", str(EXPLORER / "review-inputs/supervisor-worker-857a4b8.json"),
        "--review-mapping", str(EXPLORER / "reviews/supervisor-worker.v1.json"),
        "--review-source", str(EXPLORER / "reviews/supervisor-worker.source.v1.json"),
        "--review-state", str(REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"),
        "--navigation", str(EXPLORER / "navigation.v1.json"),
        "--legacy-node-review-v1",
        "--web-assets", str(EXPLORER / "web"),
        "--output-directory", str(site),
    ]
    fragments = authoritative_candidate_fragment_paths(tuple(
        path.relative_to(REPOSITORY).as_posix()
        for directory in ("candidates", "recursive-candidates")
        for path in (EXPLORER / directory).glob("*.json")
    ))
    for fragment in fragments:
        args.extend(("--candidate-fragment", str(REPOSITORY / fragment)))

    assert main(args) == 0
    site = REPOSITORY / site
    return site, json.loads((site / "explorer-data.json").read_text(encoding="utf-8"))


def test_seal_route_is_discovered_and_projected_as_pending(generated_site) -> None:
    site, data = generated_site
    projection = data["view_projection"]
    assert projection["schema"] == "architecture-explorer.view-projection.v2"
    views = projection["view_index"]
    entry = next(
        appearance for appearance in views["production-evidence"]["appearances"]
        if appearance["node_id"] == "checkpoint-manifest-seal"
    )
    assert entry["primary_action"]["target_view_id"] == "checkpoint-manifest-seal-internal"
    assert entry["primary_action"]["transition_kind"] == "decomposition"
    relationship_id = "candidate-decomposition:checkpoint-manifest-seal-to-prepared-seal-generation"
    assert relationship_id in views["checkpoint-manifest-seal-internal"]["relationship_ids"]
    edge = projection["relationship_index"][relationship_id]
    assert edge["semantic"] == "decomposition"
    assert edge["review"]["result"] == "Pending"
    assert edge["review"]["approved_scope"] == []
    assert (site / "app.js").read_bytes() == (EXPLORER / "web/app.js").read_bytes()


def test_final_qa_to_release_membership_projects_as_a_cross_module_collaboration(
    generated_site,
) -> None:
    _, data = generated_site
    projection = data["view_projection"]
    views = projection["view_index"]
    route = views["checkpoint-evidence-persistence-internal"]
    final_qa = next(
        item for item in route["appearances"] if item["node_id"] == "checkpoint-final-qa-evidence"
    )
    assert final_qa["primary_action"]["transition_kind"] == "cross-node"
    release_view = views[final_qa["primary_action"]["target_view_id"]]
    edge_id = "candidate-collaboration:checkpoint-final-qa-evidence-to-release-membership-derivation"
    assert edge_id in release_view["relationship_ids"]
    edge = projection["relationship_index"][edge_id]
    assert (edge["source_node_id"], edge["target_node_id"], edge["semantic"]) == (
        "checkpoint-final-qa-evidence", "release-membership-derivation", "collaboration",
    )
    assert edge["review"]["state"] == "not-reviewed"
    sources = [projection["evidence_index"][key] for key in edge["evidence_ids"]]
    assert {
        (entry["definition"]["qualified_symbol"], entry["source_trail_kind"])
        for entry in sources
    } >= {
        ("booktrans.production_worker.production_checkpoint.run_production_checkpoint", "definition"),
        ("booktrans.harness.release_membership.derive_release_membership", "definition"),
    }


def test_document_validation_projects_a_separate_harness_workflow(generated_site) -> None:
    _, data = generated_site
    projection = data["view_projection"]
    views = projection["view_index"]
    package = views["package-preservation-verifier-internal"]
    edge_id = "candidate-collaboration:tagged-candidate-output-to-protected-package-payload-check"
    assert edge_id in package["relationship_ids"]
    edge = projection["relationship_index"][edge_id]
    assert edge["semantic"] == "collaboration"
    assert edge["review"]["result"] == "Pending"
    assert any(
        projection["evidence_index"][key]["definition"]["qualified_symbol"]
        == "booktrans.harness.calibration.CalibrationImporter.run"
        for key in edge["evidence_ids"]
    )
    assert {entry["node_id"] for entry in package["appearances"]}.isdisjoint(
        {"release-membership-derivation", "batch-release-debt-reconciliation"},
    )
    assert "视觉" in package["coverage_note"]


@pytest.mark.parametrize("viewport_width", [1440, 500])
def test_real_chrome_browses_checkpoint_release_views(
    generated_site: tuple[Path, dict],
    tmp_path: Path,
    viewport_width: int,
) -> None:
    site, _ = generated_site
    browser_name, browser = _desktop_browser()
    script = "v4-ticket-08-dom.js"
    shutil.copyfile(Path(__file__).with_name(script), site / script)
    index = (site / "index.html").read_text(encoding="utf-8")
    page = site / f"checkpoint-release-{viewport_width}.html"
    page.write_text(
        index.replace("</body>", f'<script src="{script}"></script></body>'),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser, page, tmp_path / "profile",
        virtual_time_budget=5000, viewport_width=viewport_width,
    )
    error = re.search(r'data-ticket08-error="([^"]+)"', completed.stdout)
    assert error is None, error.group(1) if error else ""
    _assert_browser_dom(
        completed, browser_name=browser_name, pass_marker='data-ticket-08="pass"',
    )
    assert f'data-ticket-08-width="{viewport_width}"' in completed.stdout
