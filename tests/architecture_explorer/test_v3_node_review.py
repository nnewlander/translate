from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.node_review_definitions import (
    build_node_review_packets,
    project_node_reviews,
    render_node_review_decision_body,
    validate_node_review_definitions,
    verify_node_review_v2_materials,
)
from booktrans.architecture_explorer.node_review import (
    _NodeReviewApprovalAnchor as NodeReviewApprovalAnchor,
    _NodeReviewTrustPolicy as NodeReviewTrustPolicy,
)
from booktrans.architecture_explorer.node_review_continuity import (
    _evaluate_node_review_state,
    _update_node_review_state,
)
from booktrans.architecture_explorer.review_cli import main as review_main
from booktrans.architecture_explorer.review_source import review_source_sha256
from booktrans.architecture_explorer.node_review_v2_continuity import (
    evaluate_node_review_v2_state,
    update_node_review_v2_state,
)
from test_t8_node_review import _fixture_materials, _fixture_policy
from test_t9_review_packet import _published_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
DEFINITIONS = REPOSITORY / "architecture-explorer/review-definitions/node-reviews.v1.json"


def _definitions() -> dict:
    return json.loads(DEFINITIONS.read_text(encoding="utf-8"))


def test_public_packet_resolves_recursive_candidate_node_with_a_unique_identity() -> None:
    path = REPOSITORY / "architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json"
    fragment = json.loads(path.read_text(encoding="utf-8"))
    explorer = apply_candidate_fragments(_published_explorer(), [fragment])
    target = fragment["nodes"][0]
    node_id = "supervisor-process-launch-construction"
    definitions = _definitions()
    definition = definitions["definitions"][0]
    definition["object_key"]["node_id"] = node_id
    definition["candidate_evidence_ids"] = target["evidence_ids"]
    definition["legacy_compatibility"] = None
    definition["dependencies_and_limits"].update(
        dependencies=[], zero_dependency_reason="This review is limited to process construction."
    )
    definitions["definitions"] = [definition]
    validated = validate_node_review_definitions(definitions)

    packet = build_node_review_packets(explorer, validated)[f"candidate-node::{node_id}"]

    assert packet["candidate_fact"]["name"] == "Supervisor 进程启动构造"
    assert packet["candidate_evidence"][0]["path"].endswith(".ps1")
    explorer["navigation"]["drilldowns"][0]["children"].append(
        deepcopy(explorer["recursive_candidate_fragments"][0]["nodes"][0])
    )
    with pytest.raises(ExplorerInputError, match=f"resolve exactly once: {node_id}"):
        build_node_review_packets(explorer, validated)


def _projected_reviews(explorer: dict) -> dict:
    projection = explorer["node_review_projection"]
    assert projection["schema"] == "architecture-explorer.node-review-projection.v1"
    return projection["objects"]


def test_two_real_review_definitions_build_order_independent_neutral_packets() -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())

    first = build_node_review_packets(explorer, definitions)
    reversed_definitions = deepcopy(definitions)
    reversed_definitions.reverse()
    second = build_node_review_packets(explorer, reversed_definitions)

    assert list(first) == [
        "candidate-node::translation-routing-and-acquisition",
        "candidate-node::worker-launch-boundary",
    ]
    assert first == second
    assert {
        key: packet["object_key"] for key, packet in first.items()
    } == {
        "candidate-node::translation-routing-and-acquisition": {
            "kind": "candidate-node",
            "node_id": "translation-routing-and-acquisition",
        },
        "candidate-node::worker-launch-boundary": {
            "kind": "candidate-node",
            "node_id": "worker-launch-boundary",
        },
    }
    assert all(packet["allowed_results"] == ["APPROVED", "REJECTED"] for packet in first.values())
    assert all("result" not in packet and "decision" not in packet for packet in first.values())
    assert len({packet["packet_sha256"] for packet in first.values()}) == 2
    translation = first["candidate-node::translation-routing-and-acquisition"]
    assert translation["inputs"]["candidate_fact_sha256"] == (
        "4df3ffb72e132ec009dda9ef8f788e3ce7a6d6c4f26eed4a316dcb762339cf9a"
    )
    worker_compatibility = first["candidate-node::worker-launch-boundary"][
        "legacy_v1_compatibility"
    ]
    assert worker_compatibility == {
        "schema": "architecture-explorer.node-review-packet.v1",
        "object_key": {
            "kind": "candidate-node",
            "parent_module_id": "production-control",
            "node_id": "worker-launch-boundary",
        },
        "candidate_fact_sha256": "0e12aa409d1c94d6f1959c59fcf3c1b6331ef18b055792db0dca460ab1ac0a2f",
        "claims_sha256": "f3252dd5a349b0c2b5902503c22e4cb8be5f568e5045cc63f96b9be6c5208783",
        "candidate_evidence_fingerprint_sha256": "1fb5703461195ea8a3f314a01ddfce1c2b1d0ffa493f63f566d7f3fcce6954c7",
        "formal_evidence_sha256": "be75396ea97e1cc19f6ef65a6f55576cc983d66bf84a2d6f46ac7dc3c0566566",
        "packet_sha256": "2d542b551fd8568b362a286755c288141bf6a9f51cc001141928efafe7f778cd",
    }


def test_one_prepare_run_writes_two_isolated_neutral_packet_directories(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_path = repository / "explorer-data.json"
    definitions_path = repository / "node-reviews.v1.json"
    explorer_path.write_text(
        json.dumps(_published_explorer(), ensure_ascii=False), encoding="utf-8"
    )
    definitions_path.write_text(
        json.dumps(_definitions(), ensure_ascii=False), encoding="utf-8"
    )

    assert review_main(
        [
            "prepare-node-reviews",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definitions",
            str(definitions_path),
            "--output-directory",
            "output/reviews",
        ]
    ) == 0

    output = repository / "output/reviews"
    object_directories = sorted(path.name for path in output.iterdir())
    assert object_directories == [
        "candidate-node__translation-routing-and-acquisition",
        "candidate-node__worker-launch-boundary",
    ]
    for directory in output.iterdir():
        assert (directory / "review-packet.json").is_file()
        assert (directory / "review-packet.md").is_file()
        assert not (directory / "github-comment.txt").exists()
        assert not (directory / "github-comment-draft.txt").exists()

    with pytest.raises(ExplorerInputError, match="output collision"):
        review_main(
            [
                "prepare-node-reviews",
                "--repository",
                str(repository),
                "--explorer-data",
                str(explorer_path),
                "--review-definitions",
                str(definitions_path),
                "--output-directory",
                "output/reviews",
            ]
        )


def test_worker_v1_is_current_while_translation_remains_independently_pending(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "worker-state.json"
    _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T12:55:00Z",
        trust_policy=_fixture_policy(),
    )
    worker_state = _evaluate_node_review_state(decision, state_path)
    worker_state["object_key"] = deepcopy(decision["object_key"])

    projected = project_node_reviews(
        explorer,
        definitions,
        {
            "candidate-node::worker-launch-boundary": {
                "decision": decision,
                "source": source,
                "state": worker_state,
            }
        },
        trust_policy=_fixture_policy(),
    )

    nodes = {
        node["id"]: node
        for drilldown in projected["navigation"]["drilldowns"]
        for node in drilldown["children"]
    }
    assert all("node_review" not in node for node in nodes.values())
    reviews = _projected_reviews(projected)
    assert reviews["candidate-node::worker-launch-boundary"]["projection"] == "Current · Approved"
    assert reviews["candidate-node::translation-routing-and-acquisition"] == {
        "object_key": {
            "kind": "candidate-node",
            "node_id": "translation-routing-and-acquisition",
        },
        "decision": None,
        "state": {
            "status": "not-reviewed",
            "continuity": "not-assessed",
            "reasons": ["trusted-node-review-decision:missing"],
        },
        "projection": "Pending Review",
    }
    assert "candidate-node::translation-provider-adapters" not in reviews


@pytest.mark.parametrize("legacy_profile", [None, "mismatched-object"])
def test_decision_v1_requires_an_exact_declared_legacy_compatibility_profile(
    legacy_profile: str | None,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    worker = next(
        item
        for item in definitions
        if item["object_key"]["node_id"] == "worker-launch-boundary"
    )
    if legacy_profile is None:
        worker["legacy_compatibility"] = None
    else:
        worker["legacy_compatibility"]["object_key"]["parent_module_id"] = (
            "translation-execution"
        )
    source, decision = _fixture_materials(explorer, result="APPROVED")

    with pytest.raises(ExplorerInputError, match="legacy compatibility"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::worker-launch-boundary": {
                    "decision": decision,
                    "source": source,
                    "state": {
                        "object_key": decision["object_key"],
                        "status": "current",
                        "continuity": "exact-match",
                        "decision_id": decision["decision_id"],
                        "reasons": [],
                    },
                }
            },
            trust_policy=_fixture_policy(),
        )


def test_decision_v1_definition_requires_the_legacy_compatibility_field() -> None:
    raw = _definitions()
    worker = next(
        item
        for item in raw["definitions"]
        if item["object_key"]["node_id"] == "worker-launch-boundary"
    )
    del worker["legacy_compatibility"]

    with pytest.raises(ExplorerInputError, match="closed schema"):
        validate_node_review_definitions(raw)


def test_projection_preserves_trusted_continuity_display_evidence(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "worker-state.json"
    persisted = _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T12:56:00Z",
        trust_policy=_fixture_policy(),
    )
    state = _evaluate_node_review_state(decision, state_path)
    state["object_key"] = deepcopy(decision["object_key"])

    projected = project_node_reviews(
        explorer,
        definitions,
        {
            "candidate-node::worker-launch-boundary": {
                "decision": decision,
                "source": source,
                    "state": state,
            }
        },
        trust_policy=_fixture_policy(),
    )

    state = _projected_reviews(projected)[
        "candidate-node::worker-launch-boundary"
    ]["state"]
    assert state["evaluated_source_snapshot"] == persisted[
        "evaluated_source_snapshot"
    ]
    assert state["current_evidence_locations"] == persisted[
        "current_evidence_locations"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("evaluated_source_snapshot", {"revision": "incomplete"}),
        (
            "current_evidence_locations",
            [{"evidence_id": "evidence:forged", "path": "forged.py"}],
        ),
    ),
)
def test_projection_rejects_malformed_continuity_display_evidence(
    field: str,
    value: object,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state = {
        "object_key": decision["object_key"],
        "status": "current",
        "continuity": "exact-match",
        "decision_id": decision["decision_id"],
        "reasons": [],
        field: value,
    }

    with pytest.raises(ExplorerInputError, match="Continuity display evidence"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::worker-launch-boundary": {
                    "decision": decision,
                    "source": source,
                    "state": state,
                }
            },
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize(
    "missing_fields",
    (
        ("evaluated_source_snapshot",),
        ("current_evidence_locations",),
        ("evaluated_source_snapshot", "current_evidence_locations"),
    ),
    ids=("snapshot", "locations", "snapshot-and-locations"),
)
def test_decision_v1_current_requires_complete_bound_continuity_evidence(
    tmp_path: Path,
    missing_fields: tuple[str, ...],
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "node-review-state.json"
    _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T13:00:00Z",
        trust_policy=_fixture_policy(),
    )
    state = _evaluate_node_review_state(decision, state_path)
    state["object_key"] = deepcopy(decision["object_key"])
    for field in missing_fields:
        del state[field]

    with pytest.raises(ExplorerInputError, match="current Continuity evidence"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::worker-launch-boundary": {
                    "decision": decision,
                    "source": source,
                    "state": state,
                }
            },
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize("mismatch", ("snapshot", "partial-locations"))
def test_decision_v1_current_rejects_mismatched_continuity_evidence(
    tmp_path: Path,
    mismatch: str,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "node-review-state.json"
    _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T13:00:30Z",
        trust_policy=_fixture_policy(),
    )
    state = _evaluate_node_review_state(decision, state_path)
    state["object_key"] = deepcopy(decision["object_key"])
    if mismatch == "snapshot":
        state["evaluated_source_snapshot"]["source_facts_sha256"] = "0" * 64
    else:
        state["current_evidence_locations"] = state[
            "current_evidence_locations"
        ][:-1]

    with pytest.raises(ExplorerInputError, match="current Continuity evidence"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::worker-launch-boundary": {
                    "decision": decision,
                    "source": source,
                    "state": state,
                }
            },
            trust_policy=_fixture_policy(),
        )


def _translation_v2_materials(
    explorer: dict,
    definitions: list[dict],
) -> tuple[dict, dict, dict, NodeReviewTrustPolicy]:
    definition = next(
        item
        for item in definitions
        if item["object_key"]["node_id"] == "translation-routing-and-acquisition"
    )
    packet = build_node_review_packets(explorer, [definition])[
        "candidate-node::translation-routing-and-acquisition"
    ]
    worker_source, _ = _fixture_materials(explorer, result="APPROVED")
    source = deepcopy(worker_source)
    source["comment"].update(
        {
            "id": 999999004,
            "node_id": "T8_FIXTURE_COMMENT_999999004",
            "html_url": "https://github.com/nnewlander/translate/issues/999999#issuecomment-999999004",
        }
    )
    decision = {
        "schema": "architecture-explorer.node-review-decision.v2",
        "decision_id": "github-comment:999999004",
        "object_key": packet["object_key"],
        "result": "APPROVED",
        "reviewed_source_snapshot": packet["reviewed_source_snapshot"],
        "review_notes": "Imported from a pinned GitHub Owner/admin review source.",
        "source_receipt_sha256": "pending",
        "inputs": packet["inputs"],
        "claims": packet["claims"],
    }
    source["comment"]["body"] = render_node_review_decision_body(decision)
    decision["source_receipt_sha256"] = review_source_sha256(source)
    policy = NodeReviewTrustPolicy(
        (
            NodeReviewApprovalAnchor(
                issue_id=999999001,
                issue_number=999999,
                comment_id=999999004,
                comment_created_at="2026-11-12T00:00:00Z",
                comment_updated_at="2026-11-12T00:00:00Z",
                source_receipt_sha256=(
                    "da89e5a874f356933f78e652992fe1df5bed1805a2098702865f0933a5b333da"
                ),
            ),
        )
    )
    return definition, source, decision, policy


@pytest.mark.parametrize(
    "missing_fields",
    (
        ("evaluated_source_snapshot",),
        ("current_evidence_locations",),
        ("evaluated_source_snapshot", "current_evidence_locations"),
    ),
    ids=("snapshot", "locations", "snapshot-and-locations"),
)
def test_decision_v2_current_requires_complete_bound_continuity_evidence(
    tmp_path: Path,
    missing_fields: tuple[str, ...],
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    definition, source, decision, policy = _translation_v2_materials(
        explorer, definitions
    )
    state_path = tmp_path / "node-review-state-v2.json"
    update_node_review_v2_state(
        explorer,
        definition,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T13:01:00Z",
        trust_policy=policy,
    )
    state = evaluate_node_review_v2_state(decision, state_path)
    for field in missing_fields:
        del state[field]

    with pytest.raises(ExplorerInputError, match="current Continuity evidence"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::translation-routing-and-acquisition": {
                    "decision": decision,
                    "source": source,
                    "state": state,
                }
            },
            trust_policy=policy,
        )


@pytest.mark.parametrize("mismatch", ("snapshot", "partial-locations"))
def test_decision_v2_current_rejects_mismatched_continuity_evidence(
    tmp_path: Path,
    mismatch: str,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    definition, source, decision, policy = _translation_v2_materials(
        explorer, definitions
    )
    state_path = tmp_path / "node-review-state-v2.json"
    update_node_review_v2_state(
        explorer,
        definition,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T13:01:30Z",
        trust_policy=policy,
    )
    state = evaluate_node_review_v2_state(decision, state_path)
    if mismatch == "snapshot":
        state["evaluated_source_snapshot"]["source_facts_sha256"] = "0" * 64
    else:
        state["current_evidence_locations"] = state[
            "current_evidence_locations"
        ][:-1]

    with pytest.raises(ExplorerInputError, match="current Continuity evidence"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::translation-routing-and-acquisition": {
                    "decision": decision,
                    "source": source,
                    "state": state,
                }
            },
            trust_policy=policy,
        )


def test_decision_v2_reads_exact_identity_zero_dependencies_and_retained_unknowns(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    definition, source, decision, policy = _translation_v2_materials(
        explorer, definitions
    )
    assert decision["source_receipt_sha256"] == (
        "da89e5a874f356933f78e652992fe1df5bed1805a2098702865f0933a5b333da"
    )

    verified_decision, verified_source = verify_node_review_v2_materials(
        explorer,
        definition,
        decision,
        source,
        trust_policy=policy,
    )

    assert verified_decision == decision
    assert verified_source == source
    dependency_claim = verified_decision["claims"][-1]
    assert dependency_claim["kind"] == "DEPENDENCIES_AND_LIMITS"
    assert dependency_claim["value"]["dependencies"] == []
    assert dependency_claim["value"]["zero_dependency_reason"]
    assert dependency_claim["value"]["retained_unknowns"]
    state_path = tmp_path / "translation-node-review-state.json"
    persisted = update_node_review_v2_state(
        explorer,
        definition,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T12:00:00Z",
        trust_policy=policy,
    )
    assert persisted["schema"] == "architecture-explorer.node-review-state.v2"
    assert persisted["object_key"] == decision["object_key"]
    assert persisted["state"] == "current"
    assert persisted["continuity"] == "exact-match"
    evaluated = evaluate_node_review_v2_state(decision, state_path)
    assert evaluated["status"] == "current"
    projected_with_continuity = project_node_reviews(
        explorer,
        definitions,
        {
            "candidate-node::translation-routing-and-acquisition": {
                "decision": decision,
                "source": source,
                "state": evaluated,
            }
        },
        trust_policy=policy,
    )
    projected_state = _projected_reviews(projected_with_continuity)[
        "candidate-node::translation-routing-and-acquisition"
    ]["state"]
    projected_reviews = _projected_reviews(projected_with_continuity)
    assert projected_reviews["candidate-node::translation-routing-and-acquisition"]["projection"] == "Current · Approved"
    assert projected_reviews["candidate-node::worker-launch-boundary"]["projection"] == "Pending Review"
    assert projected_state["evaluated_source_snapshot"] == persisted[
        "evaluated_source_snapshot"
    ]
    assert projected_state["current_evidence_locations"] == persisted[
        "current_evidence_locations"
    ]
    scan_failed = deepcopy(explorer)
    scan_failed["_node_review_scan_error"] = "parse-failure"
    failed_state = update_node_review_v2_state(
        scan_failed,
        definition,
        decision,
        source,
        tmp_path / "translation-node-review-scan-failed.json",
        assessed_at="2026-11-20T12:01:00Z",
        trust_policy=policy,
    )
    assert failed_state["state"] == "unknown"
    assert failed_state["continuity"] == "parse-failure"
    assert failed_state["reasons"] == ["source-scan:parse-failure"]

    changed = deepcopy(explorer)
    changed_target = next(
        node
        for drilldown in changed["navigation"]["drilldowns"]
        for node in drilldown["children"]
        if node["id"] == "translation-routing-and-acquisition"
    )
    changed_target["name"] = "materially changed fixture"
    invalidated = update_node_review_v2_state(
        changed,
        definition,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T12:02:00Z",
        trust_policy=policy,
    )
    assert invalidated["state"] == "needs-review"
    still_invalidated = update_node_review_v2_state(
        explorer,
        definition,
        decision,
        source,
        state_path,
        assessed_at="2026-11-20T12:03:00Z",
        trust_policy=policy,
    )
    assert still_invalidated["state"] == "needs-review"
    assert still_invalidated["continuity"] == "material-change"
    historical = project_node_reviews(
        changed,
        definitions,
        {
            "candidate-node::translation-routing-and-acquisition": {
                "decision": decision,
                "source": source,
                "state": {
                    "object_key": decision["object_key"],
                    "status": "needs-review",
                    "continuity": "material-change",
                    "decision_id": decision["decision_id"],
                    "reasons": ["node-review-material:material-change"],
                },
            }
        },
        trust_policy=policy,
    )
    historical_reviews = _projected_reviews(historical)
    assert historical_reviews["candidate-node::translation-routing-and-acquisition"]["projection"] == "Historical · Approved"
    tampered_decision = deepcopy(decision)
    tampered_decision["claims"][0]["value"]["node_id"] = "tampered-node"
    with pytest.raises(ExplorerInputError, match="Claims hash mismatch"):
        project_node_reviews(
            changed,
            definitions,
            {
                "candidate-node::translation-routing-and-acquisition": {
                    "decision": tampered_decision,
                    "source": source,
                    "state": {
                        "object_key": decision["object_key"],
                        "status": "needs-review",
                        "continuity": "material-change",
                        "decision_id": decision["decision_id"],
                        "reasons": ["node-review-material:material-change"],
                    },
                }
            },
            trust_policy=policy,
        )


def test_decision_v2_packet_supports_multiple_exact_reviewed_dependencies() -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    worker = deepcopy(
        next(
            item
            for item in definitions
            if item["object_key"]["node_id"] == "worker-launch-boundary"
        )
    )
    original = next(
        item
        for item in explorer["architecture"]["relationships"]
        if item["id"] == "relationship:production-control:launches:translation-execution"
    )
    second = deepcopy(original)
    second["id"] = "relationship:fixture:second-reviewed-dependency"
    explorer["architecture"]["relationships"].append(second)
    second_binding = deepcopy(worker["dependencies_and_limits"]["dependencies"][0])
    second_binding["object_id"] = second["id"]
    worker["dependencies_and_limits"]["dependencies"].append(second_binding)

    packet = build_node_review_packets(explorer, [worker])[
        "candidate-node::worker-launch-boundary"
    ]

    dependency_claim = packet["claims"][-1]
    assert dependency_claim["kind"] == "DEPENDENCIES_AND_LIMITS"
    assert [
        item["object_id"] for item in dependency_claim["value"]["dependencies"]
    ] == [
        "relationship:production-control:launches:translation-execution",
        "relationship:fixture:second-reviewed-dependency",
    ]
    assert len(packet["dependency_evidence"]) == 26


def test_dependency_claims_binding_rejects_tampered_reviewed_claims() -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    worker = next(
        item
        for item in definitions
        if item["object_key"]["node_id"] == "worker-launch-boundary"
    )
    relationship = next(
        item
        for item in explorer["architecture"]["relationships"]
        if item["id"]
        == "relationship:production-control:launches:translation-execution"
    )
    relationship["review_decision"]["retained_unknowns"].append(
        "tampered dependency claim"
    )

    with pytest.raises(ExplorerInputError, match="dependency binding mismatch"):
        build_node_review_packets(explorer, [worker])


@pytest.mark.parametrize("mutation", ["duplicate", "zero-reason", "evidence-conflict"])
def test_review_definitions_fail_closed_on_identity_or_claim_binding_conflicts(
    mutation: str,
) -> None:
    raw = _definitions()
    if mutation == "duplicate":
        raw["definitions"].append(deepcopy(raw["definitions"][0]))
        with pytest.raises(ExplorerInputError, match="Duplicate Node Review Definition"):
            validate_node_review_definitions(raw)
        return
    translation = next(
        item
        for item in raw["definitions"]
        if item["object_key"]["node_id"] == "translation-routing-and-acquisition"
    )
    if mutation == "zero-reason":
        translation["dependencies_and_limits"]["zero_dependency_reason"] = ""
        with pytest.raises(ExplorerInputError, match="Zero dependencies require"):
            validate_node_review_definitions(raw)
        return
    translation["candidate_evidence_ids"].reverse()
    definitions = validate_node_review_definitions(raw)
    with pytest.raises(ExplorerInputError, match="Definition Evidence mismatch"):
        build_node_review_packets(_published_explorer(), definitions)


def test_cross_object_decision_and_continuity_substitution_fail_closed() -> None:
    explorer = _published_explorer()
    definitions = validate_node_review_definitions(_definitions())
    source, decision = _fixture_materials(explorer, result="APPROVED")
    translation_key = {
        "kind": "candidate-node",
        "node_id": "translation-routing-and-acquisition",
    }
    swapped_decision = deepcopy(decision)
    swapped_decision["object_key"] = translation_key
    with pytest.raises(ExplorerInputError, match="Decision object"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::translation-routing-and-acquisition": {
                    "decision": swapped_decision,
                    "source": source,
                    "state": {
                        "object_key": translation_key,
                        "status": "current",
                        "continuity": "exact-match",
                        "decision_id": decision["decision_id"],
                        "reasons": [],
                    },
                }
            },
            trust_policy=_fixture_policy(),
        )

    with pytest.raises(ExplorerInputError, match="Continuity object mismatch"):
        project_node_reviews(
            explorer,
            definitions,
            {
                "candidate-node::worker-launch-boundary": {
                    "decision": decision,
                    "source": source,
                    "state": {
                        "object_key": translation_key,
                        "status": "current",
                        "continuity": "exact-match",
                        "decision_id": decision["decision_id"],
                        "reasons": [],
                    },
                }
            },
            trust_policy=_fixture_policy(),
        )


def test_legacy_v1_compatibility_profile_cannot_be_attached_to_another_object() -> None:
    raw = _definitions()
    worker = next(
        item
        for item in raw["definitions"]
        if item["object_key"]["node_id"] == "worker-launch-boundary"
    )
    translation = next(
        item
        for item in raw["definitions"]
        if item["object_key"]["node_id"] == "translation-routing-and-acquisition"
    )
    translation["legacy_compatibility"] = deepcopy(worker["legacy_compatibility"])

    with pytest.raises(ExplorerInputError, match="Legacy compatibility identity mismatch"):
        validate_node_review_definitions(raw)
