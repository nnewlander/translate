from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from booktrans.architecture_explorer.review_cli import main as review_main
from tests.architecture_explorer.test_t7_top_level_graph import (
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


REPOSITORY = Path(__file__).resolve().parents[2]
EXPLORER = REPOSITORY / "architecture-explorer"


@pytest.fixture(scope="module")
def quality_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build_run = tmp_path_factory.mktemp("t9-site")
    output = Path("output/architecture-explorer/ticket-09-tests") / build_run.parent.name
    arguments = [
        "build", "--repository", str(REPOSITORY),
        "--snapshot", "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        "--mapping", str(EXPLORER / "modules.json"),
        "--candidate", str(EXPLORER / "review-inputs/supervisor-worker-857a4b8.json"),
        "--review-mapping", str(EXPLORER / "reviews/supervisor-worker.v1.json"),
        "--review-source", str(EXPLORER / "reviews/supervisor-worker.source.v1.json"),
        "--review-state", "output/architecture-explorer/t5-lite/review-state.v1.json",
        "--navigation", str(EXPLORER / "navigation.v1.json"),
        "--web-assets", str(EXPLORER / "web"),
        "--output-directory", str(output),
    ]
    for directory in ("candidates", "recursive-candidates"):
        for fragment in sorted((EXPLORER / directory).glob("*.json")):
            arguments.extend(("--candidate-fragment", str(fragment)))

    assert review_main(arguments) == 0
    site = REPOSITORY / output
    assert (site / "index.html").is_file()
    return site


def test_build_exposes_quality_responsibilities_and_candidate_disposition(quality_site: Path) -> None:
    data = json.loads((quality_site / "explorer-data.json").read_text(encoding="utf-8"))
    projection = data["view_projection"]
    assert projection["schema"] == "architecture-explorer.view-projection.v2"
    views = projection["view_index"]
    responsibility = views["quality-deterministic-qa-internal"]
    disposition = views["quality-candidate-disposition"]
    assert responsibility["perspective"] == "responsibility"
    assert disposition["perspective"] == "candidate-disposition"
    assert disposition["scope_node_id"] == responsibility["scope_node_id"]
    assert {appearance["node_id"] for appearance in disposition["appearances"]} >= {
        "translation-routing-and-acquisition", "quality-qa-record",
        "semantic-review-contract-boundary", "repair-authorization-gate",
    }
    relationships = projection["relationship_index"]
    assert {
        (edge["source_node_id"], edge["target_node_id"])
        for key in disposition["relationship_ids"]
        if (edge := relationships[key])["semantic"] == "collaboration"
    } >= {
        ("quality-qa-record", "repair-authorization-gate"),
        ("quality-qa-record", "semantic-review-contract-boundary"),
        ("semantic-review-contract-boundary", "repair-authorization-gate"),
    }
    failure = relationships["candidate-collaboration:quality-qa-record-to-repair-authorization-gate"]
    assert failure["review"]["result"] == "Pending"
    assert failure["review"]["approved_scope"] == []
    evidence = projection["evidence_index"]
    assert any(
        evidence[key]["source_trail_kind"] == "call"
        for key in failure["evidence_ids"]
    )


def test_checkpoint_recovery_uses_canonical_qa_facts_as_policy_inputs(quality_site: Path) -> None:
    data = json.loads((quality_site / "explorer-data.json").read_text(encoding="utf-8"))
    projection = data["view_projection"]
    recovery = projection["view_index"]["quality-checkpoint-recovery-inputs"]
    assert recovery["perspective"] == "candidate-disposition"
    relationships = [projection["relationship_index"][key] for key in recovery["relationship_ids"]]
    assert {
        (edge["source_node_id"], edge["target_node_id"], edge["semantic"]) for edge in relationships
    } == {
        ("quality-qa-record", "quality-canonical-qa-failure", "collaboration"),
        ("quality-canonical-qa-failure", "quality-numeric-recovery-eligibility", "collaboration"),
    }
    for edge in relationships:
        assert edge["review"]["result"] == "Pending"
        assert edge["review"]["approved_scope"] == []
    evidence = projection["evidence_index"]
    edges_by_source = {edge["source_node_id"]: edge for edge in relationships}
    report_edge = edges_by_source["quality-qa-record"]
    policy_edge = edges_by_source["quality-canonical-qa-failure"]
    report_facts = [evidence[key] for key in report_edge["evidence_ids"]]
    assert {
        fact.get("definition", {}).get("qualified_symbol") for fact in report_facts
    } >= {
        "scripts.production.build_recovery_evidence.collect_immutable_recovery_facts",
        "booktrans.production_control.canonical_qa_failure.canonicalize_qa_failure",
    }
    eligibility = projection["node_index"]["quality-numeric-recovery-eligibility"]
    production_facts = [
        evidence[key]
        for key in set(policy_edge["evidence_ids"]) | set(eligibility["source_assignment"]["evidence_ids"])
    ]
    assert {fact.get("definition", {}).get("qualified_symbol") for fact in production_facts} >= {
        "booktrans.production_control.recovery_evidence_builder.build_recovery_evidence",
        "scripts.production.decide_recovery_policy.main",
        "booktrans.production_control.recovery_policy_registry.decide_recovery_policy",
    }
    assert {fact.get("fact", {}).get("target_path") for fact in production_facts} >= {
        "scripts/production/build_recovery_evidence.py",
        "scripts/production/decide_recovery_policy.py",
    }
    assert any(
        fact.get("fact", {}).get("symbol") == "Get-AutoRecoveryPolicy"
        for fact in production_facts
    )
    assert "src/booktrans/production_worker/numeric_recovery.py" not in {
        fact["file_path"] for fact in report_facts + production_facts
    }


@pytest.mark.parametrize("width", [1440, 500])
def test_chrome_navigates_real_qa_decomposition_and_disposition(
    quality_site: Path, tmp_path: Path, width: int,
) -> None:
    browser_name, browser = _desktop_browser()
    site = tmp_path / "site"
    shutil.copytree(quality_site, site)
    shutil.copyfile(Path(__file__).with_name("v4-ticket-09-quality-dom.js"), site / "qa-check.js")
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="qa-check.js"></script></body>',
        ),
        encoding="utf-8",
    )
    completed = _dump_dom(
        browser, page, tmp_path / "profile",
        virtual_time_budget=4500, viewport_width=width, viewport_height=900,
    )
    _assert_browser_dom(completed, browser_name=browser_name, pass_marker=None)
    assert 'data-t9-quality="pass"' in completed.stdout, next(
        (line for line in completed.stdout.splitlines() if "data-t9-quality-error" in line),
        "QA browser checks did not produce a result",
    )
    assert f'data-t9-quality-width="{width}"' in completed.stdout
