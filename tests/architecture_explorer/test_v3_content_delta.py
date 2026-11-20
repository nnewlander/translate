from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy
import subprocess

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "architecture-explorer")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD")


def _manifest_repository(tmp_path: Path) -> tuple[Path, str, str, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    object_directory = Path(_git(REPOSITORY, "rev-parse", "--git-path", "objects")).resolve()
    alternates = repository / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_bytes((object_directory.as_posix() + "\n").encode("utf-8"))
    base = "6a239f6919cbb791e69e56383b91dbb634aeaf0f"
    _git(repository, "checkout", "--detach", base)
    fragment_path = repository / "architecture-explorer/candidates/scope-and-request.v1.json"
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    evidence_id = fragment["nodes"][0]["evidence_ids"][0]
    fragment["nodes"].append(
        {
            "id": "ticket-59-candidate-b",
            "node_type": "component",
            "name": "Candidate B",
            "professional_description": "Candidate B component.",
            "plain_explanation": "Candidate B in plain language.",
            "review_status": "candidate",
            "evidence_ids": [evidence_id],
        }
    )
    _write_json(fragment_path, fragment)
    head = _commit(repository, "add candidate B")
    return repository, base, head, fragment_path


def _candidate_acceptance_repository(tmp_path: Path) -> tuple[Path, str, Path]:
    repository = tmp_path / "candidate-acceptance"
    repository.mkdir()
    _git(repository, "init", "-q")
    object_directory = Path(_git(REPOSITORY, "rev-parse", "--git-path", "objects")).resolve()
    alternates = repository / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_bytes((object_directory.as_posix() + "\n").encode("utf-8"))
    _git(repository, "checkout", "--detach", "6a239f6919cbb791e69e56383b91dbb634aeaf0f")
    v3_path = repository / "docs/architecture-explorer-acceptance-v3.md"
    v3_path.write_text(
        (REPOSITORY / "docs/architecture-explorer-acceptance-v3.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest_path = "architecture-explorer/manifests/v3-ticket-01.v1.json"
    destination = repository / manifest_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        (REPOSITORY / manifest_path).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return repository, manifest_path, v3_path


def test_explicit_proposed_v3_target_preserves_v2_as_the_only_current_contract() -> None:
    from booktrans.architecture_explorer.acceptance import select_acceptance_contract

    selection = select_acceptance_contract(
        REPOSITORY,
        target_contract="architecture-explorer/v3",
    )

    assert selection.current_contract == "architecture-explorer/v2"
    assert selection.target_contract == "architecture-explorer/v3"
    assert selection.target_status == "proposed"
    assert "effective_baseline" not in selection.target_metadata
    assert not {
        "candidate_nodes",
        "candidate_collaborations",
        "candidate_decompositions",
        "views",
    } & selection.target_metadata.keys()


def test_manifest_uses_real_commits_and_ignores_dirty_worktree(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )

    repository, base, head, fragment_path = _manifest_repository(tmp_path)
    delta = inspect_content_delta(repository, base_revision=base, head_revision=head)
    manifest = delta.to_manifest(
        delta_id="ticket-59-real-commit-example",
        target_contract="architecture-explorer/v3",
    )

    dirty_fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    dirty_fragment["nodes"].append({"id": "dirty-uncommitted-node"})
    _write_json(fragment_path, dirty_fragment)
    result = validate_content_delta_manifest(
        repository,
        head_revision=head,
        manifest=manifest,
        required_base_revision=base,
    )

    assert result.head_revision == head
    assert ("add", "candidate-node", "ticket-59-candidate-b") in [
        (change.operation, change.object_type, change.object_id) for change in delta.changes
    ]


def test_git_replace_cannot_change_commit_identity_or_content(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta

    repository, base, head, _ = _manifest_repository(tmp_path)
    _git(repository, "replace", head, base)

    delta = inspect_content_delta(repository, base_revision=base, head_revision=head)

    assert ("add", "candidate-node", "ticket-59-candidate-b") in [
        (change.operation, change.object_type, change.object_id) for change in delta.changes
    ]


def test_candidate_fragment_discovery_rejects_nested_json(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, _, fragment_path = _manifest_repository(tmp_path)
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    fragment["parent_module_id"] = "nested-fragment-scope"
    fragment["nodes"] = [
        {
            **fragment["nodes"][0],
            "id": "nested-fragment-node",
        }
    ]
    fragment["collaborations"] = []
    nested = repository / "architecture-explorer/candidates/nested/fragment.v1.json"
    _write_json(nested, fragment)
    head = _commit(repository, "add nested candidate fragment")

    with pytest.raises(ExplorerInputError, match="nested Candidate Fragment"):
        inspect_content_delta(repository, base_revision=base, head_revision=head)


def test_candidate_fragment_rejects_unknown_evidence(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, _, fragment_path = _manifest_repository(tmp_path)
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    fragment["nodes"][0]["evidence_ids"] = ["evidence:sha256:" + "0" * 64]
    _write_json(fragment_path, fragment)
    head = _commit(repository, "reference unknown Evidence")

    with pytest.raises(ExplorerInputError, match="unknown source evidence"):
        inspect_content_delta(repository, base_revision=base, head_revision=head)


def test_candidate_acceptance_rejects_multiple_contract_status_headings(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.acceptance import validate_acceptance_target
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, manifest_path, v3_path = _candidate_acceptance_repository(tmp_path)
    v3_path.write_text(
        v3_path.read_text(encoding="utf-8")
        + "\n# Architecture Explorer v3 activated acceptance contract\n",
        encoding="utf-8",
    )
    _git(repository, "add", "docs", "architecture-explorer/manifests")
    head = _commit(repository, "add contradictory structured contract status")

    with pytest.raises(ExplorerInputError, match="structured status surface"):
        validate_acceptance_target(
            repository,
            head_revision=head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )


def test_candidate_acceptance_rejects_second_contract_status_section(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.acceptance import validate_acceptance_target
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, manifest_path, v3_path = _candidate_acceptance_repository(tmp_path)
    v3_path.write_text(
        v3_path.read_text(encoding="utf-8")
        + "\n## Contract status\n\n"
        + "- current_contract: architecture-explorer/v3\n"
        + "- target_contract: architecture-explorer/v3\n"
        + "- target_status: current\n"
        + "- effective_baseline: present\n",
        encoding="utf-8",
    )
    _git(repository, "add", "docs", "architecture-explorer/manifests")
    head = _commit(repository, "add second conflicting contract status section")

    with pytest.raises(ExplorerInputError, match="structured status surface"):
        validate_acceptance_target(
            repository,
            head_revision=head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )


def test_candidate_acceptance_rejects_conflicting_free_text_in_status_region(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.acceptance import validate_acceptance_target
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, manifest_path, v3_path = _candidate_acceptance_repository(tmp_path)
    v3_path.write_text(
        v3_path.read_text(encoding="utf-8").replace(
            "## Contract status",
            "v3 is current and v2 is not the sole current contract.\n\n## Contract status",
            1,
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "docs", "architecture-explorer/manifests")
    head = _commit(repository, "add conflicting free-text contract status")

    with pytest.raises(ExplorerInputError, match="structured status surface"):
        validate_acceptance_target(
            repository,
            head_revision=head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )


def test_candidate_acceptance_binds_all_approved_v3_metadata(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.acceptance import validate_acceptance_target
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, manifest_path, v3_path = _candidate_acceptance_repository(tmp_path)
    v3_path.write_text(
        v3_path.read_text(encoding="utf-8").replace(
            "runtime_authority: false",
            "runtime_authority: true",
            1,
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "docs", "architecture-explorer/manifests")
    head = _commit(repository, "tamper with approved v3 metadata")

    with pytest.raises(ExplorerInputError, match="approved trusted metadata"):
        validate_acceptance_target(
            repository,
            head_revision=head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest.update(changes=[]), "exactly match"),
        (
            lambda manifest: manifest["changes"][0].update(after_sha256="0" * 64),
            "exactly match",
        ),
        (
            lambda manifest: manifest["changes"].append(deepcopy(manifest["changes"][0])),
            "duplicate object",
        ),
        (
            lambda manifest: manifest["changes"][0].update(object_type="future-object"),
            "unknown object type",
        ),
    ],
)
def test_manifest_rejects_unmatched_hash_duplicate_and_unknown_changes(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, head, _ = _manifest_repository(tmp_path)
    manifest = inspect_content_delta(
        repository,
        base_revision=base,
        head_revision=head,
    ).to_manifest(delta_id="negative-matrix", target_contract="architecture-explorer/v3")
    mutate(manifest)

    with pytest.raises(ExplorerInputError, match=message):
        validate_content_delta_manifest(
            repository,
            head_revision=head,
            manifest=manifest,
            required_base_revision=base,
        )


@pytest.mark.parametrize("invalid_head", ["HEAD", "0000000", "not-a-git-object"])
def test_manifest_rejects_head_values_that_are_not_full_commit_shas(
    tmp_path: Path,
    invalid_head: str,
) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, head, _ = _manifest_repository(tmp_path)
    manifest = inspect_content_delta(
        repository,
        base_revision=base,
        head_revision=head,
    ).to_manifest(delta_id="invalid-head", target_contract="architecture-explorer/v3")

    with pytest.raises(ExplorerInputError, match="full 40-character lowercase commit SHA"):
        validate_content_delta_manifest(
            repository,
            head_revision=invalid_head,
            manifest=manifest,
            required_base_revision=base,
        )


def test_manifest_rejects_a_full_git_object_that_is_not_a_commit(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, head, fragment_path = _manifest_repository(tmp_path)
    manifest = inspect_content_delta(
        repository,
        base_revision=base,
        head_revision=head,
    ).to_manifest(delta_id="blob-head", target_contract="architecture-explorer/v3")
    blob = _git(repository, "hash-object", str(fragment_path))

    with pytest.raises(ExplorerInputError, match="not a Git commit object"):
        validate_content_delta_manifest(
            repository,
            head_revision=blob,
            manifest=manifest,
            required_base_revision=base,
        )


def test_protected_top_level_module_change_requires_separate_owner_authorization(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, _, head, _ = _manifest_repository(tmp_path)
    modules_path = repository / "architecture-explorer/modules.json"
    modules = json.loads(modules_path.read_text(encoding="utf-8"))
    modules["modules"][0]["responsibility"] = "Unauthorized responsibility change."
    _write_json(modules_path, modules)
    protected_head = _commit(repository, "change protected module")
    manifest = inspect_content_delta(
        repository,
        base_revision=head,
        head_revision=head,
    ).to_manifest(delta_id="protected-change", target_contract="architecture-explorer/v3")

    with pytest.raises(ExplorerInputError, match="independent Owner authorization"):
        validate_content_delta_manifest(
            repository,
            head_revision=protected_head,
            manifest=manifest,
            required_base_revision=head,
        )


def test_formal_relationship_trust_state_is_a_protected_change(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, _, trusted_head, _ = _manifest_repository(tmp_path)
    manifest = inspect_content_delta(
        repository,
        base_revision=trusted_head,
        head_revision=trusted_head,
    ).to_manifest(delta_id="protected-trust", target_contract="architecture-explorer/v3")
    state_path = repository / "output/architecture-explorer/t5-lite/review-state.v1.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        status="needs-review",
        continuity_status="material-change",
        reasons=["test trust-state downgrade"],
    )
    _write_json(state_path, state)
    _git(repository, "add", "-f", "output/architecture-explorer/t5-lite/review-state.v1.json")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "downgrade formal relationship trust",
    )
    downgraded_head = _git(repository, "rev-parse", "HEAD")

    with pytest.raises(ExplorerInputError, match="independent Owner authorization"):
        validate_content_delta_manifest(
            repository,
            head_revision=downgraded_head,
            manifest=manifest,
            required_base_revision=trusted_head,
        )


@pytest.mark.parametrize("protected_type", ["formal-relationship", "formal-evidence"])
def test_protected_formal_object_change_requires_separate_owner_authorization(
    tmp_path: Path,
    protected_type: str,
) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        validate_content_delta_manifest,
    )
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, _, protected_base, _ = _manifest_repository(tmp_path)
    review_path = repository / "architecture-explorer/reviews/supervisor-worker.v1.json"
    review_input_path = repository / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
    if protected_type == "formal-relationship":
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"]["relationship"]["runtime_state"] = "FORGED"
        _write_json(review_path, review)
    else:
        review_input = json.loads(review_input_path.read_text(encoding="utf-8"))
        review_input["evidence"][0]["claim"] = "forged"
        _write_json(review_input_path, review_input)
    protected_head = _commit(repository, f"change {protected_type}")
    manifest = inspect_content_delta(
        repository,
        base_revision=protected_base,
        head_revision=protected_base,
    ).to_manifest(delta_id="protected-formal", target_contract="architecture-explorer/v3")

    with pytest.raises(ExplorerInputError, match="independent Owner authorization"):
        validate_content_delta_manifest(
            repository,
            head_revision=protected_head,
            manifest=manifest,
            required_base_revision=protected_base,
        )


def test_manifest_rejects_a_base_that_is_not_an_ancestor(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, head, _ = _manifest_repository(tmp_path)
    tree = _git(repository, "rev-parse", f"{head}^{{tree}}")
    unrelated = _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit-tree",
        tree,
        "-m",
        "unrelated",
    )

    with pytest.raises(ExplorerInputError, match="must be an ancestor"):
        inspect_content_delta(
            repository,
            base_revision=base,
            head_revision=unrelated,
        )


def test_head_candidate_evidence_must_pass_the_existing_authority_chain(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, _, fragment_path = _manifest_repository(tmp_path)
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    added = next(node for node in fragment["nodes"] if node["id"] == "ticket-59-candidate-b")
    added["evidence_ids"] = ["evidence:does-not-exist"]
    _write_json(fragment_path, fragment)
    invalid_head = _commit(repository, "invalid candidate evidence")

    with pytest.raises(ExplorerInputError, match="unknown source evidence"):
        inspect_content_delta(
            repository,
            base_revision=base,
            head_revision=invalid_head,
        )


def test_canonical_json_sorts_object_keys_preserves_arrays_and_does_not_normalize_unicode() -> None:
    from booktrans.architecture_explorer.content_delta import canonical_json_bytes

    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert canonical_json_bytes({"items": ["a", "b"]}) != canonical_json_bytes(
        {"items": ["b", "a"]}
    )
    assert canonical_json_bytes({"text": "e\u0301"}) != canonical_json_bytes({"text": "é"})


def test_identity_rename_is_a_delete_and_add_not_a_modify(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta

    repository, _, head, fragment_path = _manifest_repository(tmp_path)
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    added = next(node for node in fragment["nodes"] if node["id"] == "ticket-59-candidate-b")
    added["id"] = "candidate-renamed"
    _write_json(fragment_path, fragment)
    renamed_head = _commit(repository, "rename candidate identity")

    changes = inspect_content_delta(
        repository,
        base_revision=head,
        head_revision=renamed_head,
    ).changes
    candidate_changes = [change for change in changes if change.object_type == "candidate-node"]

    assert [(change.operation, change.object_id) for change in candidate_changes] == [
        ("add", "candidate-renamed"),
        ("delete", "ticket-59-candidate-b"),
    ]


def test_entry_action_change_does_not_double_count_as_a_view_change(tmp_path: Path) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta

    repository, _, head, _ = _manifest_repository(tmp_path)
    definitions_path = repository / "architecture-explorer/views/recursive-views.v1.json"
    definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    definitions["entry_actions"][0]["primary_action"]["destination_label"] = (
        "Changed destination label"
    )
    _write_json(definitions_path, definitions)
    changed_head = _commit(repository, "change entry action")

    changes = inspect_content_delta(
        repository,
        base_revision=head,
        head_revision=changed_head,
    ).changes

    assert [(change.object_type, change.object_id) for change in changes] == [
        ("view-entry-action", "production-control::worker-launch-boundary")
    ]


def _review_definition(fragment_path: Path, *, node_id: str) -> dict:
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    evidence_id = next(
        node["evidence_ids"][0]
        for node in fragment["nodes"]
        if node["id"] == "ticket-59-candidate-b"
    )
    return {
        "schema": "architecture-explorer.node-review-definition.v1",
        "node_id": node_id,
        "claims": [{"claim_id": "claim:test", "text": "Test claim"}],
        "classification_reason": "Test-only definition classification.",
        "evidence_ids": [evidence_id],
        "dependencies_and_limits": {
            "dependencies": [],
            "zero_dependency_reason": "No formal architecture dependency is asserted.",
        },
        "retained_unknowns": ["Runtime reachability remains unknown."],
    }


@pytest.mark.parametrize(
    ("definition_kind", "message"),
    [
        ("top-level-module", "unknown Candidate Node"),
        ("missing-semantics", "missing fields"),
    ],
)
def test_node_review_definition_requires_candidate_identity_and_complete_semantics(
    tmp_path: Path,
    definition_kind: str,
    message: str,
) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, _, fragment_path = _manifest_repository(tmp_path)
    if definition_kind == "top-level-module":
        definition = _review_definition(fragment_path, node_id="production-control")
    else:
        definition = {
            "schema": "architecture-explorer.node-review-definition.v1",
            "node_id": "ticket-59-candidate-b",
        }
    definition_path = repository / "architecture-explorer/review-definitions/test.v1.json"
    _write_json(definition_path, definition)
    invalid_head = _commit(repository, f"invalid review definition {definition_kind}")

    with pytest.raises(ExplorerInputError, match=message):
        inspect_content_delta(
            repository,
            base_revision=base,
            head_revision=invalid_head,
        )


def test_valid_node_review_definition_has_stable_candidate_object_identity(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta

    repository, _, head, fragment_path = _manifest_repository(tmp_path)
    definition = _review_definition(fragment_path, node_id="ticket-59-candidate-b")
    definition_path = repository / "architecture-explorer/review-definitions/test.v1.json"
    _write_json(definition_path, definition)
    multi_definition = _review_definition(
        fragment_path,
        node_id="scope-logical-work-order-selection",
    )
    multi_definition["dependencies_and_limits"] = {
        "dependencies": [
            {
                "object_key": "candidate-node::scope-request-context-assembly",
                "claims": ["claim:test"],
                "evidence_ids": list(multi_definition["evidence_ids"]),
            }
        ],
        "zero_dependency_reason": None,
    }
    _write_json(
        repository / "architecture-explorer/review-definitions/multi.v1.json",
        multi_definition,
    )
    definition_head = _commit(repository, "add valid review definitions")

    changes = inspect_content_delta(
        repository,
        base_revision=head,
        head_revision=definition_head,
    ).changes

    assert ("add", "node-review-definition", "candidate-node::ticket-59-candidate-b") in [
        (change.operation, change.object_type, change.object_id) for change in changes
    ]
    assert (
        "add",
        "node-review-definition",
        "candidate-node::scope-logical-work-order-selection",
    ) in [(change.operation, change.object_type, change.object_id) for change in changes]


@pytest.mark.parametrize(
    ("invalid_kind", "message"),
    [
        ("null-claim", "Claim 1 must be an object"),
        ("empty-object-key", "unknown object key"),
        ("empty-dependency-claims", "unique known Claims"),
        ("empty-dependency-evidence", "unique declared Evidence"),
    ],
)
def test_node_review_definition_rejects_incomplete_dependency_bindings(
    tmp_path: Path,
    invalid_kind: str,
    message: str,
) -> None:
    from booktrans.architecture_explorer.content_delta import inspect_content_delta
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository, base, _, fragment_path = _manifest_repository(tmp_path)
    definition = _review_definition(fragment_path, node_id="ticket-59-candidate-b")
    if invalid_kind == "null-claim":
        definition["claims"] = [None]
    else:
        dependency = {
            "object_key": "candidate-node::scope-request-context-assembly",
            "claims": ["claim:test"],
            "evidence_ids": list(definition["evidence_ids"]),
        }
        if invalid_kind == "empty-object-key":
            dependency["object_key"] = ""
        elif invalid_kind == "empty-dependency-claims":
            dependency["claims"] = []
        else:
            dependency["evidence_ids"] = []
        definition["dependencies_and_limits"] = {
            "dependencies": [dependency],
            "zero_dependency_reason": None,
        }
    definition_path = repository / "architecture-explorer/review-definitions/invalid.v1.json"
    _write_json(definition_path, definition)
    invalid_head = _commit(repository, f"invalid dependency binding {invalid_kind}")

    with pytest.raises(ExplorerInputError, match=message):
        inspect_content_delta(
            repository,
            base_revision=base,
            head_revision=invalid_head,
        )


def test_candidate_acceptance_reads_contract_and_manifest_from_the_head_commit(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.acceptance import validate_acceptance_target
    from booktrans.architecture_explorer.generator import ExplorerInputError

    repository = tmp_path / "candidate"
    repository.mkdir()
    _git(repository, "init", "-q")
    object_directory = Path(_git(REPOSITORY, "rev-parse", "--git-path", "objects")).resolve()
    alternates = repository / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_bytes((object_directory.as_posix() + "\n").encode("utf-8"))
    _git(repository, "checkout", "--detach", "6a239f6919cbb791e69e56383b91dbb634aeaf0f")
    (repository / "docs/architecture-explorer-acceptance-v3.md").write_text(
        (REPOSITORY / "docs/architecture-explorer-acceptance-v3.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest_path = "architecture-explorer/manifests/v3-ticket-01.v1.json"
    destination = repository / manifest_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        (REPOSITORY / manifest_path).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md", manifest_path)
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "candidate contract inputs",
    )
    head = _git(repository, "rev-parse", "HEAD")

    result = validate_acceptance_target(
        repository,
        head_revision=head,
        target_contract="architecture-explorer/v3",
        manifest_path=manifest_path,
    )

    assert result.contracts.current_contract == "architecture-explorer/v2"
    assert result.contracts.target_status == "proposed"
    assert result.manifest.changes == ()
    assert result.manifest.object_counts["candidate-node"] == 20
    assert result.manifest.object_counts["candidate-collaboration"] == 9
    assert result.manifest.object_counts["candidate-decomposition"] == 2
    assert result.manifest.object_counts["view"] == 9

    v3_path = repository / "docs/architecture-explorer-acceptance-v3.md"
    original_v3 = v3_path.read_text(encoding="utf-8")
    v3_path.write_text(
        original_v3.replace(
            "status: proposed\n",
            "status: current\nstatus: proposed\n",
            1,
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "duplicate contract status metadata",
    )
    duplicate_metadata_head = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(ExplorerInputError, match="Duplicate contract metadata key.*status"):
        validate_acceptance_target(
            repository,
            head_revision=duplicate_metadata_head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )
    v3_path.write_text(original_v3, encoding="utf-8")
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "restore unique contract metadata",
    )
    v3_path.write_text(
        original_v3.replace(
            "status: proposed\n",
            "status: proposed\nsuperseded_by: architecture-explorer/v4\n",
            1,
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "add contradictory proposed metadata",
    )
    conflicting_metadata_head = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(ExplorerInputError, match="cannot have status field: superseded_by"):
        validate_acceptance_target(
            repository,
            head_revision=conflicting_metadata_head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )
    v3_path.write_text(original_v3, encoding="utf-8")
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "restore proposed metadata",
    )
    head = _git(repository, "rev-parse", "HEAD")

    with pytest.raises(ExplorerInputError, match="Git commit object read failed"):
        validate_acceptance_target(
            repository,
            head_revision=head,
            target_contract="architecture-explorer/v3",
            manifest_path="architecture-explorer/manifests/deleted.v1.json",
        )

    replacement = json.loads(destination.read_text(encoding="utf-8"))
    replacement["head_content_sha256"] = "0" * 64
    _write_json(destination, replacement)
    replacement_head = _commit(repository, "replace manifest with invalid content")
    with pytest.raises(ExplorerInputError, match="Head content digest"):
        validate_acceptance_target(
            repository,
            head_revision=replacement_head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )

    v3_path.write_text(
        v3_path.read_text(encoding="utf-8").replace(
            "- target_status: proposed",
            "- target_status: current",
            1,
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "docs/architecture-explorer-acceptance-v3.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "contradict proposed status",
    )
    contradictory_v3_head = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(ExplorerInputError, match="structured status surface"):
        validate_acceptance_target(
            repository,
            head_revision=contradictory_v3_head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )

    v2_path = repository / "docs/architecture-explorer-acceptance-v2.md"
    v2_path.write_text(v2_path.read_text(encoding="utf-8") + "\nunauthorized\n", encoding="utf-8")
    _git(repository, "add", "docs/architecture-explorer-acceptance-v2.md")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=architecture-explorer@example.invalid",
        "commit",
        "-m",
        "tamper with v2",
    )
    tampered_v2_head = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(ExplorerInputError, match="preserve the complete current v2 contract"):
        validate_acceptance_target(
            repository,
            head_revision=tampered_v2_head,
            target_contract="architecture-explorer/v3",
            manifest_path=manifest_path,
        )


def test_v2_contract_bytes_remain_unchanged_from_the_fixed_base() -> None:
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY),
            "diff",
            "--numstat",
            "6a239f6919cbb791e69e56383b91dbb634aeaf0f",
            "--",
            "docs/architecture-explorer-acceptance-v2.md",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout

    assert diff == ""


def test_versioned_manifest_replacement_or_deletion_does_not_change_runtime_projection(
    tmp_path: Path,
) -> None:
    from booktrans.architecture_explorer.content_delta import (
        inspect_content_delta,
        project_authoritative_commit,
    )

    repository, base, content_head, _ = _manifest_repository(tmp_path)
    manifest_path = repository / "architecture-explorer/manifests/runtime-isolation.v1.json"
    manifest = inspect_content_delta(
        repository,
        base_revision=base,
        head_revision=content_head,
    ).to_manifest(delta_id="runtime-isolation", target_contract="architecture-explorer/v3")
    _write_json(manifest_path, manifest)
    manifest_head = _commit(repository, "add versioned manifest")
    with_manifest = project_authoritative_commit(repository, manifest_head)["view_projection"]

    _write_json(manifest_path, {"replacement": "runtime must ignore this"})
    replacement_head = _commit(repository, "replace versioned manifest")
    with_replacement = project_authoritative_commit(repository, replacement_head)[
        "view_projection"
    ]

    manifest_path.unlink()
    deletion_head = _commit(repository, "delete versioned manifest")
    without_manifest = project_authoritative_commit(repository, deletion_head)["view_projection"]

    assert with_manifest == with_replacement == without_manifest
