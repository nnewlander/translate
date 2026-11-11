from __future__ import annotations

import json
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)


REPOSITORY = Path(__file__).resolve().parents[2]
DOCS = REPOSITORY / "docs"
FIXED_BASELINE = "47ca4276075735508c23e7caa3c03965d93cde5b"
CANDIDATE_FRAGMENTS = tuple(
    sorted((REPOSITORY / "architecture-explorer/candidates").glob("*.json"))
)
NORMATIVE_CLAUSES = {
    "AE-NODE-DETAIL": "responsibility+plain-explanation+required-trust-state",
    "AE-EVIDENCE-ENTRY": "context-menu-detail-only",
    "AE-REVIEWED-RELATIONSHIP": "solid+current-reviewed+exact-match",
    "AE-CANDIDATE-COLLABORATION": "dashed+candidate-unreviewed",
    "AE-CHROME": "required-automated",
    "AE-FIREFOX": "owner-manual",
    "AE-EDGE": "supplemental+blocked-never-pass",
    "AE-REVIEWER-INPUTS": "contract+ticket-delta+owner-overrides+fixed-base-head",
    "AE-REVIEWER-CONFLICT": "report-and-block",
    "AE-T8-INHERITANCE": "contract+inherits+delta+owner-overrides",
}


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return metadata
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid contract metadata in {path}: {line}")
        metadata[key.strip()] = value.strip()
    raise AssertionError(f"unterminated contract metadata in {path}")


def _current_contract() -> tuple[Path, dict[str, str]]:
    contracts = [
        (path, metadata)
        for path in DOCS.glob("architecture-explorer*.md")
        if (metadata := _frontmatter(path)).get("contract_family")
        == "architecture-explorer-acceptance"
        and metadata.get("status") == "current"
    ]
    assert len(contracts) == 1, [str(path) for path, _ in contracts]
    return contracts[0]


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


def _normative_clauses(text: str) -> dict[str, str]:
    heading = "## 规范性验收条款"
    assert heading in text, f"missing {heading}"
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    clauses: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("| AE-"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        assert len(parts) == 2, line
        clause_id, value = parts
        assert clause_id not in clauses, clause_id
        clauses[clause_id] = value
    return clauses


def _assert_normative_contract(text: str) -> None:
    assert _normative_clauses(text) == NORMATIVE_CLAUSES


@pytest.fixture(scope="module")
def published_explorer() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        REPOSITORY / "architecture-explorer/modules.json",
        _json("architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _json("architecture-explorer/reviews/supervisor-worker.v1.json"),
        _json("architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=(
            REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
        ),
    )
    with_facts = attach_python_source_facts(
        reviewed,
        scan_python_source_facts(REPOSITORY),
    )
    navigable = apply_navigation(
        with_facts,
        _json("architecture-explorer/navigation.v1.json"),
    )
    return apply_candidate_fragments(
        navigable,
        [json.loads(path.read_text(encoding="utf-8")) for path in CANDIDATE_FRAGMENTS],
    )


def test_exactly_one_current_architecture_explorer_contract_is_versioned() -> None:
    contracts = [
        (path, metadata)
        for path in DOCS.glob("architecture-explorer*.md")
        if (metadata := _frontmatter(path)).get("contract_family")
        == "architecture-explorer-acceptance"
    ]
    current = [item for item in contracts if item[1].get("status") == "current"]

    assert len(current) == 1, [str(path) for path, _ in current]
    _, metadata = current[0]
    assert metadata["contract_version"] == "1"
    assert metadata["contract_ref"] == "architecture-explorer/v1"
    assert metadata["effective_baseline"] == FIXED_BASELINE
    assert metadata["runtime_authority"] == "false"
    assert metadata["reviewer_conflict_policy"] == "report-and-block"


def test_current_contract_inventory_matches_existing_generated_inputs() -> None:
    _, metadata = _current_contract()
    modules = _json("architecture-explorer/modules.json")["modules"]
    review = _json("architecture-explorer/reviews/supervisor-worker.v1.json")
    navigation = _json("architecture-explorer/navigation.v1.json")
    fragments = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((REPOSITORY / "architecture-explorer/candidates").glob("*.json"))
    ]

    relationship_ids = {
        relationship_id
        for drilldown in navigation["drilldowns"]
        for child in drilldown["children"]
        for relationship_id in child["source_assignment"]["relationship_ids"]
    }
    actual = {
        "top_level_modules": len(modules),
        "reviewed_relationships": len(relationship_ids),
        "approved_evidence": len(review["decision"]["approved_evidence_ids"]),
        "candidate_nodes": sum(len(fragment["nodes"]) for fragment in fragments),
        "candidate_collaborations": sum(
            len(fragment["collaborations"]) for fragment in fragments
        ),
    }

    assert set(metadata["top_level_module_ids"].split(",")) == {
        module["id"] for module in modules
    }
    assert relationship_ids == {metadata["reviewed_relationship_id"]}
    assert actual == {
        key: int(metadata[key])
        for key in (
            "top_level_modules",
            "reviewed_relationships",
            "approved_evidence",
            "candidate_nodes",
            "candidate_collaborations",
        )
    }


def test_current_contract_records_final_t7_trust_and_browser_roles() -> None:
    _, metadata = _current_contract()

    assert metadata["inline_evidence"] == "false"
    assert metadata["evidence_entry"] == "context-menu-detail"
    assert metadata["reviewed_edge_style"] == "solid"
    assert metadata["candidate_edge_style"] == "dashed"
    assert metadata["reviewed_relationship_gate"] == "current-reviewed+exact-match"
    assert metadata["desktop_viewports"] == "1440x900,1280x900,960x900"
    assert metadata["continuous_resize"] == (
        "1440x900>1280x900>960x900>1440x900"
    )
    assert metadata["automated_browser"] == "chrome"
    assert metadata["owner_manual_browser"] == "firefox"
    assert metadata["supplemental_browser"] == "edge"
    assert metadata["supplemental_blocked_is_pass"] == "false"


def test_current_contract_exposes_downstream_and_reviewer_inputs() -> None:
    _, metadata = _current_contract()

    assert metadata["downstream_reference_fields"] == (
        "contract,inherits,delta,owner-overrides"
    )
    assert metadata["spec_reviewer_inputs"] == (
        "contract,ticket-delta,owner-overrides,fixed-base-head"
    )
    assert metadata["reviewer_conflict_policy"] == "report-and-block"


def test_current_contract_body_contains_complete_normative_clauses() -> None:
    path, _ = _current_contract()

    _assert_normative_contract(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "| AE-EVIDENCE-ENTRY | context-menu-detail-only |",
            "",
        ),
        (
            "| AE-REVIEWER-CONFLICT | report-and-block |",
            "| AE-REVIEWER-CONFLICT | choose-a-rule |",
        ),
    ],
)
def test_normative_clause_guard_rejects_missing_or_changed_rule(
    old: str,
    new: str,
) -> None:
    path, _ = _current_contract()
    original = path.read_text(encoding="utf-8")
    mutated = original.replace(old, new, 1)
    assert mutated != original

    with pytest.raises(AssertionError):
        _assert_normative_contract(mutated)


def test_contract_trust_semantics_match_the_published_explorer(
    published_explorer: dict,
) -> None:
    review = _json("architecture-explorer/reviews/supervisor-worker.v1.json")
    review_state = _json("output/architecture-explorer/t5-lite/review-state.v1.json")
    assert review["decision"]["result"] == "APPROVED"

    relationships = published_explorer["architecture"]["relationships"]
    assert len(relationships) == 1
    relationship = relationships[0]
    approved_relationship = review["decision"]["relationship"]
    assert (
        relationship["source_module"],
        relationship["target_module"],
        relationship["type"],
    ) == (
        approved_relationship["source_module"],
        approved_relationship["target_module"],
        approved_relationship["type"],
    )
    assert relationship["review_status"] == review_state["status"] == "current-reviewed"
    assert (
        relationship["source_continuity"]["status"]
        == review_state["continuity_status"]
        == "exact-match"
    )

    approved_evidence = review["decision"]["approved_evidence_ids"]
    assert len(approved_evidence) == 13
    assert relationship["evidence_ids"] == approved_evidence
    assert {item["evidence_id"] for item in relationship["evidence"]} == set(
        approved_evidence
    )

    drilldowns = published_explorer["navigation"]["drilldowns"]
    candidate_nodes = [
        child for drilldown in drilldowns for child in drilldown["children"]
    ]
    candidate_collaborations = [
        collaboration
        for drilldown in drilldowns
        for collaboration in drilldown["collaborations"]
    ]
    assert len(candidate_nodes) == 18
    assert len(candidate_collaborations) == 8
    assert all(node["review_status"] == "candidate" for node in candidate_nodes)
    assert all(
        collaboration["review_status"] == "candidate"
        for collaboration in candidate_collaborations
    )

    partial_nodes = [
        node
        for node in candidate_nodes
        if node["source_assignment"]["status"] == "partial"
    ]
    assert partial_nodes
    assert all(node["source_assignment"]["evidence_ids"] for node in partial_nodes)
    assert all(node["review_status"] == "candidate" for node in partial_nodes)
