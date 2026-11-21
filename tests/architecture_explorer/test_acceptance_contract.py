from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)
from booktrans.architecture_explorer.view_definitions import apply_view_definitions
from booktrans.architecture_explorer.view_projection import apply_view_projection


REPOSITORY = Path(__file__).resolve().parents[2]
DOCS = REPOSITORY / "docs"
V1_EFFECTIVE_BASELINE = "47ca4276075735508c23e7caa3c03965d93cde5b"
CANDIDATE_HEAD = "ef2782873d23db77731aabbb3aaef7362f8f75b0"
V1_NORMATIVE_BODY_SHA256 = (
    "f71e35e0865e403d76a5ee8aa4e2e07f0749ffd0ffea10aa0eb3d81626740e2e"
)
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


def _contracts() -> dict[str, tuple[Path, dict[str, str]]]:
    return {
        metadata["contract_ref"]: (path, metadata)
        for path in DOCS.glob("architecture-explorer*.md")
        if (metadata := _frontmatter(path)).get("contract_family")
        == "architecture-explorer-acceptance"
    }


def _effective_value(metadata: dict[str, str], key: str) -> str:
    contracts = _contracts()
    current = metadata
    seen: set[str] = set()
    while True:
        if key in current:
            return current[key]
        parent_ref = current.get("inherits")
        assert parent_ref and parent_ref not in seen, (key, parent_ref)
        seen.add(parent_ref)
        current = contracts[parent_ref][1]


def _contract_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) == 3, path
    return parts[2]


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
    with_candidates = apply_candidate_fragments(
        navigable,
        [
            *[
                json.loads(path.read_text(encoding="utf-8"))
                for path in CANDIDATE_FRAGMENTS
            ],
            _json(
                "architecture-explorer/recursive-candidates/"
                "worker-launch-boundary.v2.json"
            ),
        ],
    )
    v2_views = _json("architecture-explorer/views/recursive-views.v1.json")
    v2_views["entry_actions"] = [
        action
        for action in v2_views["entry_actions"]
        if action["primary_action"]["target_view_id"]
        == "worker-launch-boundary-internal"
    ]
    v2_views["views"] = [
        view
        for view in v2_views["views"]
        if view["id"] == "worker-launch-boundary-internal"
    ]
    with_views = apply_view_definitions(
        with_candidates,
        v2_views,
    )
    return apply_view_projection(with_views)


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
    assert metadata["contract_version"] == "2"
    assert metadata["contract_ref"] == "architecture-explorer/v2"
    assert metadata["effective_baseline"] == CANDIDATE_HEAD
    assert metadata["runtime_authority"] == "false"
    assert _effective_value(metadata, "reviewer_conflict_policy") == "report-and-block"


def test_v2_contract_activation_supersedes_v1_and_binds_candidate_head() -> None:
    contracts = _contracts()

    assert set(contracts) == {
        "architecture-explorer/v1",
        "architecture-explorer/v2",
        "architecture-explorer/v3",
    }
    v1 = contracts["architecture-explorer/v1"][1]
    assert v1["status"] == "superseded-by-v2"
    assert v1["superseded_by"] == "architecture-explorer/v2"
    assert v1["effective_baseline"] == V1_EFFECTIVE_BASELINE
    v2_path, v2 = contracts["architecture-explorer/v2"]
    assert v2["status"] == "current"
    assert v2["effective_baseline"] == CANDIDATE_HEAD
    assert v2["runtime_authority"] == "false"
    assert v2["authority_issue"] == "https://github.com/nnewlander/translate/issues/51"
    assert v2["fixed_starting_baseline"] == (
        "4c7f8be1f0be48189804a51ed42aaf8bdb64ab94"
    )
    assert v2["approved_spec_sha256"] == (
        "b33db66aabf8869028db2c81207a4d65a109c4ea5c72d225dfd4637c11ce842e"
    )
    text = v2_path.read_text(encoding="utf-8")
    assert "Candidate Nodes: 20" in text
    assert "Candidate Collaborations: 9" in text
    assert "Candidate Decomposition: 2" in text
    assert "Views: 9" in text
    v3_path, v3 = contracts["architecture-explorer/v3"]
    assert v3 == {
        "contract_family": "architecture-explorer-acceptance",
        "contract_version": "3",
        "contract_ref": "architecture-explorer/v3",
        "status": "proposed",
        "runtime_authority": "false",
        "authority_issue": "https://github.com/nnewlander/translate/issues/58",
        "implementation_issue": "https://github.com/nnewlander/translate/issues/59",
        "fixed_starting_baseline": "6a239f6919cbb791e69e56383b91dbb634aeaf0f",
        "approved_spec_sha256": (
            "0eb5f5650897fc0ba15b129a2782c09eeceda551e087cb3ff7e51ca5afbf1283"
        ),
        "inherits": "architecture-explorer/v2",
        "activation": "candidate-head-acceptance-then-contract-status-only-switch",
        "content_delta_manifest_schema": (
            "architecture-explorer.content-delta-manifest.v1"
        ),
    }
    v3_text = v3_path.read_text(encoding="utf-8")
    assert v3_text.count("## Contract status") == 1
    assert "- current_contract: architecture-explorer/v2" in v3_text
    assert "- target_status: proposed" in v3_text
    assert "v2 remains\nthe sole current contract" not in v3_text


def test_current_v2_status_explanation_matches_frontmatter() -> None:
    v2_path, v2 = _contracts()["architecture-explorer/v2"]
    body = _contract_body(v2_path)

    assert v2["status"] == "current"
    assert v2["effective_baseline"] == CANDIDATE_HEAD
    assert "# Architecture Explorer v2 current acceptance contract" in body
    assert "v2 is the sole current contract" in body
    assert f"`effective_baseline` is `{CANDIDATE_HEAD}`" in body
    for stale_status_text in (
        "# Architecture Explorer v2 proposed acceptance contract",
        "does not activate v2",
        "v1 remains the sole current contract",
        "This proposed contract intentionally has no `effective_baseline`",
    ):
        assert stale_status_text not in body


def test_current_contract_inventory_matches_generated_v2_projection(
    published_explorer: dict,
) -> None:
    _, metadata = _current_contract()
    modules = _json("architecture-explorer/modules.json")["modules"]
    review = _json("architecture-explorer/reviews/supervisor-worker.v1.json")
    navigation = _json("architecture-explorer/navigation.v1.json")
    relationship_references = published_explorer["view_projection"][
        "relationship_index"
    ].values()

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
        "candidate_nodes": (
            len(published_explorer["view_projection"]["node_index"])
            - len(modules)
        ),
        "candidate_collaborations": sum(
            reference["semantic_family"] == "candidate-collaboration"
            for reference in relationship_references
        ),
        "candidate_decompositions": sum(
            reference["semantic_family"] == "candidate-decomposition"
            for reference in published_explorer["view_projection"][
                "relationship_index"
            ].values()
        ),
        "views": len(published_explorer["view_projection"]["view_index"]),
    }

    assert set(_effective_value(metadata, "top_level_module_ids").split(",")) == {
        module["id"] for module in modules
    }
    assert relationship_ids == {_effective_value(metadata, "reviewed_relationship_id")}
    assert actual == {
        key: int(_effective_value(metadata, key))
        for key in (
            "top_level_modules",
            "reviewed_relationships",
            "approved_evidence",
            "candidate_nodes",
            "candidate_collaborations",
            "candidate_decompositions",
            "views",
        )
    }


def test_current_contract_records_final_t7_trust_and_browser_roles() -> None:
    _, metadata = _current_contract()

    assert _effective_value(metadata, "inline_evidence") == "false"
    assert _effective_value(metadata, "evidence_entry") == "context-menu-detail"
    assert _effective_value(metadata, "reviewed_edge_style") == "solid"
    assert _effective_value(metadata, "candidate_edge_style") == "dashed"
    assert _effective_value(metadata, "reviewed_relationship_gate") == (
        "current-reviewed+exact-match"
    )
    assert _effective_value(metadata, "desktop_viewports") == (
        "1440x900,1280x900,960x900"
    )
    assert _effective_value(metadata, "continuous_resize") == (
        "1440x900>1280x900>960x900>1440x900"
    )
    assert _effective_value(metadata, "automated_browser") == "chrome"
    assert _effective_value(metadata, "owner_manual_browser") == "firefox"
    assert _effective_value(metadata, "supplemental_browser") == "edge"
    assert _effective_value(metadata, "supplemental_blocked_is_pass") == "false"


def test_current_contract_exposes_downstream_and_reviewer_inputs() -> None:
    _, metadata = _current_contract()

    assert _effective_value(metadata, "downstream_reference_fields") == (
        "contract,inherits,delta,owner-overrides"
    )
    assert _effective_value(metadata, "spec_reviewer_inputs") == (
        "contract,ticket-delta,owner-overrides,fixed-base-head"
    )
    assert _effective_value(metadata, "reviewer_conflict_policy") == (
        "report-and-block"
    )


def test_superseded_v1_normative_body_is_preserved() -> None:
    path, _ = _contracts()["architecture-explorer/v1"]
    body = _contract_body(path)

    assert sha256(body.encode("utf-8")).hexdigest() == V1_NORMATIVE_BODY_SHA256
    _assert_normative_contract(body)


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
    path, _ = _contracts()["architecture-explorer/v1"]
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
