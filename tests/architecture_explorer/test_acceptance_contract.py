from __future__ import annotations

import json
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
DOCS = REPOSITORY / "docs"
FIXED_BASELINE = "47ca4276075735508c23e7caa3c03965d93cde5b"


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
