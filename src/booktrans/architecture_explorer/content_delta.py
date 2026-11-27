"""Git-object-backed Content Delta Manifest v1 validation."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any, Mapping

from .generator import ExplorerInputError
from .candidate_fragments import (
    CANDIDATE_FRAGMENT_DIRECTORIES,
    apply_candidate_fragments,
    authoritative_candidate_fragment_paths,
)
from .navigation import apply_navigation
from .review_mapping import publish_reviewed_explorer
from .source_scanner import attach_python_source_facts, scan_python_source_facts
from .view_definitions import (
    apply_view_definition_documents,
    authoritative_view_definition_paths,
    discover_view_definition_paths,
)
from .view_projection import apply_view_projection


MANIFEST_SCHEMA = "architecture-explorer.content-delta-manifest.v1"
TARGET_CONTRACT = "architecture-explorer/v3"
FIXED_BASE_REVISION = "6a239f6919cbb791e69e56383b91dbb634aeaf0f"
V4_FIXED_BASE_REVISION = "09aaf4a033123c7eca8d280fa8982e5b0e711b86"
OBJECT_TYPES = frozenset(
    {
        "top-level-module",
        "formal-relationship",
        "formal-evidence",
        "candidate-fragment",
        "candidate-node",
        "candidate-collaboration",
        "candidate-decomposition",
        "view",
        "view-entry-action",
        "node-review-definition",
    }
)
PROTECTED_OBJECT_TYPES = frozenset(
    {"top-level-module", "formal-relationship", "formal-evidence"}
)
_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROJECTION_CACHE: dict[str, dict[str, Any]] = {}
_FORMAL_RELATIONSHIP_CACHE: dict[str, dict[str, Any]] = {}


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the Manifest v1 canonical JSON form without Unicode rewriting."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ExplorerInputError(f"Content object is not canonical JSON: {error}") from error
    return text.encode("utf-8")


def _sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, order=True)
class ContentChange:
    object_type: str
    object_id: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "operation": self.operation,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
        }


@dataclass(frozen=True)
class ContentDelta:
    base_revision: str
    head_revision: str
    base_content_sha256: str
    head_content_sha256: str
    changes: tuple[ContentChange, ...]
    object_counts: dict[str, int]

    def to_manifest(self, *, delta_id: str, target_contract: str) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "delta_id": delta_id,
            "target_contract": target_contract,
            "base_revision": self.base_revision,
            "base_content_sha256": self.base_content_sha256,
            "head_content_sha256": self.head_content_sha256,
            "changes": [change.as_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class ManifestValidationResult:
    base_revision: str
    head_revision: str
    manifest_sha256: str
    base_content_sha256: str
    head_content_sha256: str
    changes: tuple[ContentChange, ...]
    object_counts: dict[str, int]


@dataclass(frozen=True)
class _ContentObject:
    object_type: str
    object_id: str
    payload: Any
    sha256: str


def read_git(repository: Path, *args: str, text: bool = True) -> str | bytes:
    """Read Git data with one stable error and encoding policy."""
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
    )
    if completed.returncode != 0:
        stderr = completed.stderr if text else completed.stderr.decode("utf-8", "replace")
        stdout = completed.stdout if text else completed.stdout.decode("utf-8", "replace")
        raise ExplorerInputError(
            f"Git commit object read failed: {(stderr or stdout).strip()}"
        )
    return completed.stdout


def _validate_commit(repository: Path, revision: str, label: str) -> None:
    if not isinstance(revision, str) or _SHA1.fullmatch(revision) is None:
        raise ExplorerInputError(f"{label} must be a full 40-character lowercase commit SHA")
    object_type = str(read_git(repository, "cat-file", "-t", revision)).strip()
    if object_type != "commit":
        raise ExplorerInputError(f"{label} is not a Git commit object")


def _json_at(repository: Path, revision: str, path: str) -> Any:
    raw = read_git(repository, "show", f"{revision}:{path}", text=False)
    try:
        return json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid UTF-8 JSON at {revision}:{path}: {error}") from error


def _paths_at(repository: Path, revision: str, prefix: str) -> tuple[str, ...]:
    output = str(read_git(repository, "ls-tree", "-r", "--name-only", revision, "--", prefix))
    return tuple(
        path
        for path in output.splitlines()
        if path.endswith(".json") and PurePosixPath(path).as_posix() == path
    )


def _candidate_fragment_paths_at(repository: Path, revision: str) -> tuple[str, ...]:
    output = str(
        read_git(
            repository,
            "ls-tree",
            "-r",
            "--name-only",
            revision,
            "--",
            *(root.as_posix() for root in CANDIDATE_FRAGMENT_DIRECTORIES),
        )
    )
    return authoritative_candidate_fragment_paths(tuple(output.splitlines()))


def _add_object(
    objects: dict[tuple[str, str], _ContentObject],
    object_type: str,
    object_id: Any,
    payload: Any,
) -> None:
    if object_type not in OBJECT_TYPES:
        raise ExplorerInputError(f"Unknown Content Delta object type: {object_type}")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ExplorerInputError(f"{object_type} requires a stable non-empty object id")
    key = (object_type, object_id)
    if key in objects:
        raise ExplorerInputError(f"Duplicate Content Delta object: {object_type}/{object_id}")
    envelope = {"object_type": object_type, "object_id": object_id, "payload": payload}
    objects[key] = _ContentObject(object_type, object_id, payload, _sha256(envelope))


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExplorerInputError(f"{label} must be an array")
    return value


def _validate_review_definition(
    definition: dict[str, Any],
    *,
    path: str,
    candidate_node_ids: set[str],
    evidence_ids: set[str],
    object_keys: set[str],
) -> str:
    required = {
        "schema",
        "node_id",
        "claims",
        "classification_reason",
        "evidence_ids",
        "dependencies_and_limits",
        "retained_unknowns",
    }
    missing = sorted(required - definition.keys())
    if missing:
        raise ExplorerInputError(
            f"Node Review Definition is missing fields at {path}: {', '.join(missing)}"
        )
    if definition.get("schema") != "architecture-explorer.node-review-definition.v1":
        raise ExplorerInputError(f"Unsupported Node Review Definition schema at {path}")
    node_id = definition.get("node_id")
    if node_id not in candidate_node_ids:
        raise ExplorerInputError(
            f"Node Review Definition references unknown Candidate Node: {node_id}"
        )
    claims = _require_list(definition.get("claims"), f"{path} claims")
    if not claims:
        raise ExplorerInputError(f"Node Review Definition needs Claims: {path}")
    claim_ids: list[str] = []
    for position, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            raise ExplorerInputError(
                f"Node Review Definition Claim {position} must be an object: {path}"
            )
        claim_id = claim.get("claim_id")
        claim_text = claim.get("text")
        if (
            not isinstance(claim_id, str)
            or not claim_id.strip()
            or not isinstance(claim_text, str)
            or not claim_text.strip()
        ):
            raise ExplorerInputError(
                f"Node Review Definition Claim {position} needs stable id and text: {path}"
            )
        claim_ids.append(claim_id)
    if len(claim_ids) != len(set(claim_ids)):
        raise ExplorerInputError(f"Node Review Definition Claim ids must be unique: {path}")
    reason = definition.get("classification_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ExplorerInputError(f"Node Review Definition needs a classification reason: {path}")
    declared_evidence = _require_list(definition.get("evidence_ids"), f"{path} evidence_ids")
    if (
        not declared_evidence
        or not all(isinstance(item, str) and item for item in declared_evidence)
        or len(declared_evidence) != len(set(declared_evidence))
    ):
        raise ExplorerInputError(f"Node Review Definition needs unique Evidence ids: {path}")
    unknown_evidence = [item for item in declared_evidence if item not in evidence_ids]
    if unknown_evidence:
        raise ExplorerInputError(
            f"Node Review Definition references unknown Evidence: {unknown_evidence[0]}"
        )
    retained_unknowns = _require_list(
        definition.get("retained_unknowns"), f"{path} retained_unknowns"
    )
    if not retained_unknowns or not all(
        isinstance(item, str) and item.strip() for item in retained_unknowns
    ):
        raise ExplorerInputError(f"Node Review Definition needs retained unknowns: {path}")
    limits = definition.get("dependencies_and_limits")
    if not isinstance(limits, dict) or not isinstance(limits.get("dependencies"), list):
        raise ExplorerInputError(f"Node Review Definition needs DEPENDENCIES_AND_LIMITS: {path}")
    dependencies = limits["dependencies"]
    zero_reason = limits.get("zero_dependency_reason")
    if not dependencies and (not isinstance(zero_reason, str) or not zero_reason.strip()):
        raise ExplorerInputError(
            f"Node Review Definition with zero dependencies needs a structured reason: {path}"
        )
    if dependencies and zero_reason is not None:
        raise ExplorerInputError(
            f"Node Review Definition with dependencies cannot claim a zero-dependency reason: {path}"
        )
    for position, dependency in enumerate(dependencies, 1):
        if not isinstance(dependency, dict) or not {
            "object_key",
            "claims",
            "evidence_ids",
        }.issubset(dependency):
            raise ExplorerInputError(
                f"Node Review Definition dependency {position} is incomplete: {path}"
            )
        object_key = dependency["object_key"]
        if not isinstance(object_key, str) or object_key not in object_keys:
            raise ExplorerInputError(
                f"Node Review Definition dependency {position} has an unknown object key: {path}"
            )
        dependency_claims = _require_list(
            dependency["claims"], f"{path} dependency {position} claims"
        )
        if (
            not dependency_claims
            or not all(isinstance(item, str) and item in claim_ids for item in dependency_claims)
            or len(dependency_claims) != len(set(dependency_claims))
        ):
            raise ExplorerInputError(
                f"Node Review Definition dependency {position} needs unique known Claims: {path}"
            )
        dependency_evidence = _require_list(
            dependency["evidence_ids"], f"{path} dependency {position} evidence_ids"
        )
        if (
            not dependency_evidence
            or not all(item in declared_evidence for item in dependency_evidence)
            or len(dependency_evidence) != len(set(dependency_evidence))
        ):
            raise ExplorerInputError(
                f"Node Review Definition dependency {position} needs unique declared Evidence: {path}"
            )
    return node_id


@contextmanager
def _checked_out_commit(repository: Path, revision: str):
    """Materialize one clean detached tree backed only by repository Git objects."""

    scratch_parent = Path(__file__).resolve().parents[3] / "output"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    # Keep the checkout root short so Windows can materialize deep Git paths.
    with tempfile.TemporaryDirectory(prefix="ae-", dir=scratch_parent) as directory:
        checkout = Path(directory)
        read_git(checkout, "init", "-q")
        object_directory = Path(
            str(read_git(repository, "rev-parse", "--git-path", "objects")).strip()
        )
        if not object_directory.is_absolute():
            object_directory = repository / object_directory
        object_directory = object_directory.resolve()
        alternates = checkout / ".git/objects/info/alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_bytes((object_directory.as_posix() + "\n").encode("utf-8"))
        read_git(checkout, "checkout", "-q", "--detach", revision)
        yield checkout


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid authoritative JSON input {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Authoritative JSON input must be an object: {path}")
    return value


def _publish_reviewed(checkout: Path) -> dict[str, Any]:
    return publish_reviewed_explorer(
        checkout,
        checkout / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        checkout / "architecture-explorer/modules.json",
        _load_json(checkout / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _load_json(checkout / "architecture-explorer/reviews/supervisor-worker.v1.json"),
        _load_json(checkout / "architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=(
            checkout / "output/architecture-explorer/t5-lite/review-state.v1.json"
        ),
    )


def _formal_relationship_payload(relationship: dict[str, Any]) -> dict[str, Any]:
    continuity = relationship.get("source_continuity")
    review_source = relationship.get("review_source")
    return {
        key: deepcopy(relationship.get(key))
        for key in (
            "id",
            "source_module",
            "target_module",
            "direction",
            "type",
            "review_status",
            "review_scope",
            "review_reasons",
            "code_reachability",
            "runtime_state",
            "evidence_ids",
            "architecture_explanation",
            "review_decision",
        )
    } | {
        "review_source": {
            "status": review_source.get("status"),
            "sha256": review_source.get("sha256"),
        }
        if isinstance(review_source, dict)
        else None,
        "source_continuity": {
            "status": continuity.get("status"),
            "review_state": continuity.get("review_state"),
        }
        if isinstance(continuity, dict)
        else None,
    }


def _projected_formal_relationship(repository: Path, revision: str) -> dict[str, Any]:
    if revision in _FORMAL_RELATIONSHIP_CACHE:
        return _FORMAL_RELATIONSHIP_CACHE[revision]
    with _checked_out_commit(repository, revision) as checkout:
        reviewed = _publish_reviewed(checkout)
    relationships = reviewed.get("architecture", {}).get("relationships")
    if not isinstance(relationships, list) or len(relationships) != 1:
        raise ExplorerInputError("Validated Explorer must contain one formal Relationship")
    payload = _formal_relationship_payload(relationships[0])
    _FORMAL_RELATIONSHIP_CACHE[revision] = payload
    return payload


def project_authoritative_commit(repository: Path, revision: str) -> dict[str, Any]:
    """Build the validated runtime Projection for one immutable commit tree."""

    if revision in _PROJECTION_CACHE:
        return _PROJECTION_CACHE[revision]
    with _checked_out_commit(repository, revision) as checkout:
        reviewed = _publish_reviewed(checkout)
        with_facts = attach_python_source_facts(
            reviewed,
            scan_python_source_facts(checkout),
        )
        navigable = apply_navigation(
            with_facts,
            _load_json(checkout / "architecture-explorer/navigation.v1.json"),
        )
        candidate_paths = authoritative_candidate_fragment_paths(
            tuple(
                path.relative_to(checkout).as_posix()
                for root in CANDIDATE_FRAGMENT_DIRECTORIES
                for path in (checkout / Path(root.as_posix())).rglob("*.json")
            )
        )
        fragments = [_load_json(checkout / Path(path)) for path in candidate_paths]
        with_candidates = apply_candidate_fragments(navigable, fragments)
        definition_paths = discover_view_definition_paths(checkout)
        projection = apply_view_projection(
            apply_view_definition_documents(
                with_candidates, [_load_json(path) for path in definition_paths]
            )
        )
        _PROJECTION_CACHE[revision] = projection
        return projection


def _extract_index(
    repository: Path,
    revision: str,
    projection: dict[str, Any],
) -> dict[tuple[str, str], _ContentObject]:
    objects: dict[tuple[str, str], _ContentObject] = {}
    modules = _json_at(repository, revision, "architecture-explorer/modules.json")
    module_items = _require_list(modules.get("modules") if isinstance(modules, dict) else None, "modules")
    if len(module_items) != 7:
        raise ExplorerInputError("Architecture Explorer must define exactly seven top-level modules")
    node_ids: set[str] = set()
    candidate_node_ids: set[str] = set()
    relationship_ids: set[str] = set()
    for module in module_items:
        if not isinstance(module, dict):
            raise ExplorerInputError("Top-level module must be an object")
        module_id = module.get("id")
        if module_id in node_ids:
            raise ExplorerInputError(f"Duplicate Architecture Explorer Node id: {module_id}")
        node_ids.add(module_id)
        _add_object(objects, "top-level-module", module_id, module)

    for path in _candidate_fragment_paths_at(repository, revision):
        fragment = _json_at(repository, revision, path)
        if not isinstance(fragment, dict):
            raise ExplorerInputError(f"Candidate Fragment must be an object: {path}")
        schema = fragment.get("schema")
        if schema == "architecture-explorer.candidate-fragment.v1":
            scope = fragment.get("parent_module_id")
            relationship_items = _require_list(fragment.get("collaborations"), f"{path} collaborations")
        elif schema == "architecture-explorer.candidate-fragment.v2":
            scope = fragment.get("scope_node_id")
            relationship_items = _require_list(fragment.get("relationships"), f"{path} relationships")
        else:
            raise ExplorerInputError(f"Unsupported Candidate Fragment schema at {path}")
        nodes = _require_list(fragment.get("nodes"), f"{path} nodes")
        metadata = {
            key: value
            for key, value in fragment.items()
            if key not in {"nodes", "collaborations", "relationships"}
        }
        _add_object(objects, "candidate-fragment", scope, metadata)
        for node in nodes:
            if not isinstance(node, dict) or node.get("review_status") != "candidate":
                raise ExplorerInputError(f"Candidate Node is invalid in {path}")
            node_id = node.get("id")
            if node_id in node_ids:
                raise ExplorerInputError(f"Duplicate Architecture Explorer Node id: {node_id}")
            node_ids.add(node_id)
            candidate_node_ids.add(node_id)
            _add_object(objects, "candidate-node", node_id, node)
        for relationship in relationship_items:
            if not isinstance(relationship, dict) or relationship.get("review_status") != "candidate":
                raise ExplorerInputError(f"Candidate relationship is invalid in {path}")
            semantic_family = relationship.get("semantic_family", "candidate-collaboration")
            if semantic_family not in {"candidate-collaboration", "candidate-decomposition"}:
                raise ExplorerInputError(f"Unknown Candidate relationship semantic family: {semantic_family}")
            relationship_id = relationship.get("id")
            if relationship_id in relationship_ids:
                raise ExplorerInputError(f"Duplicate Architecture Explorer Relationship id: {relationship_id}")
            relationship_ids.add(relationship_id)
            _add_object(objects, semantic_family, relationship_id, relationship)

    view_records: list[dict[str, Any]] = []
    action_records: list[dict[str, Any]] = []
    view_definition_paths = authoritative_view_definition_paths(
        _paths_at(repository, revision, "architecture-explorer/views")
    )
    for path in view_definition_paths:
        definitions = _json_at(repository, revision, path)
        if not isinstance(definitions, dict) or definitions.get("schema") != "architecture-explorer.view-definitions.v1":
            raise ExplorerInputError(f"Unsupported View Definition schema at {path}")
        action_records.extend(_require_list(definitions.get("entry_actions"), f"{path} entry_actions"))
    projected_views = projection.get("view_projection", {}).get("view_index")
    if not isinstance(projected_views, dict):
        raise ExplorerInputError("Validated View Projection has no View index")
    explicit_action_keys = {
        (action.get("view_id"), action.get("node_id"))
        for action in action_records
        if isinstance(action, dict)
    }
    for projected_view in projected_views.values():
        view = deepcopy(projected_view)
        for appearance in view.get("appearances", []):
            if (
                isinstance(appearance, dict)
                and (view.get("id"), appearance.get("node_id")) in explicit_action_keys
            ):
                appearance.pop("primary_action", None)
        view_records.append(view)
    view_ids: set[str] = set()
    for view in view_records:
        if not isinstance(view, dict):
            raise ExplorerInputError("View must be an object")
        view_id = view.get("id")
        if view_id in view_ids:
            raise ExplorerInputError(f"Duplicate Architecture Explorer View id: {view_id}")
        view_ids.add(view_id)
        _add_object(objects, "view", view_id, view)
    for action in action_records:
        if not isinstance(action, dict):
            raise ExplorerInputError("View entry action must be an object")
        action_id = f"{action.get('view_id')}::{action.get('node_id')}"
        _add_object(objects, "view-entry-action", action_id, action)

    approved_evidence_ids: set[str] = set()
    formal_relationships = projection.get("architecture", {}).get("relationships")
    if not isinstance(formal_relationships, list) or len(formal_relationships) != 1:
        raise ExplorerInputError("Validated Projection must contain one formal Relationship")
    relationship = _formal_relationship_payload(formal_relationships[0])
    relationship_id = relationship.get("id")
    _add_object(objects, "formal-relationship", relationship_id, relationship)
    relationship_ids.add(relationship_id)
    approved_evidence_ids.update(
        _require_list(relationship.get("evidence_ids"), "approved formal Evidence ids")
    )

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for path in _paths_at(repository, revision, "architecture-explorer/review-inputs"):
        review_input = _json_at(repository, revision, path)
        if not isinstance(review_input, dict):
            raise ExplorerInputError(f"Review input must be an object: {path}")
        for evidence in review_input.get("evidence", []):
            if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence_id"), str):
                raise ExplorerInputError(f"Formal Evidence is invalid in {path}")
            evidence_id = evidence["evidence_id"]
            if evidence_id in evidence_by_id and evidence_by_id[evidence_id] != evidence:
                raise ExplorerInputError(f"Conflicting formal Evidence payload: {evidence_id}")
            evidence_by_id[evidence_id] = evidence
    for evidence_id in approved_evidence_ids:
        if evidence_id not in evidence_by_id:
            raise ExplorerInputError(f"Approved formal Evidence is missing: {evidence_id}")
        _add_object(objects, "formal-evidence", evidence_id, evidence_by_id[evidence_id])

    for path in _paths_at(repository, revision, "architecture-explorer/review-definitions"):
        document = _json_at(repository, revision, path)
        if not isinstance(document, dict):
            raise ExplorerInputError(f"Node Review Definition must be an object: {path}")
        if document.get("schema") == "architecture-explorer.node-review-definitions.v1":
            from .node_review_definitions import validate_node_review_definitions

            definitions = validate_node_review_definitions(document)
            available_evidence = (
                set(projection.get("source_evidence_index", {}))
                | set(evidence_by_id)
            )
            for definition in definitions:
                node_id = definition["object_key"]["node_id"]
                if node_id not in candidate_node_ids:
                    raise ExplorerInputError(
                        f"Node Review Definition references unknown Candidate Node: {node_id}"
                    )
                unknown_evidence = [
                    evidence_id
                    for evidence_id in definition["candidate_evidence_ids"]
                    if evidence_id not in available_evidence
                ]
                if unknown_evidence:
                    raise ExplorerInputError(
                        "Node Review Definition references unknown Evidence: "
                        f"{unknown_evidence[0]}"
                    )
                for dependency in definition["dependencies_and_limits"]["dependencies"]:
                    if ("formal-relationship", dependency["object_id"]) not in objects:
                        raise ExplorerInputError(
                            "Node Review Definition dependency has an unknown formal Relationship: "
                            f"{dependency['object_id']}"
                        )
                    unknown_dependency_evidence = [
                        evidence_id
                        for evidence_id in dependency["evidence_ids"]
                        if evidence_id not in available_evidence
                    ]
                    if unknown_dependency_evidence:
                        raise ExplorerInputError(
                            "Node Review Definition dependency references unknown Evidence: "
                            f"{unknown_dependency_evidence[0]}"
                        )
                _add_object(
                    objects,
                    "node-review-definition",
                    f"candidate-node::{node_id}",
                    definition,
                )
            continue
        node_id = _validate_review_definition(
            document,
            path=path,
            candidate_node_ids=candidate_node_ids,
            evidence_ids=set(projection.get("source_evidence_index", {})),
            object_keys={
                f"{object_type}::{object_id}"
                for object_type, object_id in objects
            },
        )
        _add_object(objects, "node-review-definition", f"candidate-node::{node_id}", document)

    for content_object in objects.values():
        if content_object.object_type not in {"candidate-collaboration", "candidate-decomposition"}:
            continue
        relationship = content_object.payload
        if relationship.get("source_node_id") not in node_ids or relationship.get("target_node_id") not in node_ids:
            raise ExplorerInputError(f"Candidate Relationship has an unknown endpoint: {content_object.object_id}")
    for view in view_records:
        if view.get("id") != "top-level" and view.get("scope_node_id") not in node_ids:
            raise ExplorerInputError(f"View has an unknown scope Node: {view.get('id')}")
        for appearance in _require_list(view.get("appearances", []), f"{view.get('id')} appearances"):
            if not isinstance(appearance, dict) or appearance.get("node_id") not in node_ids:
                raise ExplorerInputError(f"View has an unknown Appearance Node: {view.get('id')}")
        if any(item not in relationship_ids for item in _require_list(view.get("relationship_ids", []), f"{view.get('id')} relationship ids")):
            raise ExplorerInputError(f"View has an unknown Candidate Relationship: {view.get('id')}")
    for action in action_records:
        primary_action = action.get("primary_action")
        if (
            action.get("view_id") not in view_ids
            or action.get("node_id") not in node_ids
            or not isinstance(primary_action, dict)
            or primary_action.get("kind") != "open-view"
            or primary_action.get("target_view_id") not in view_ids
        ):
            raise ExplorerInputError(f"View entry action has an invalid reference: {action}")

    adjacency: dict[str, list[str]] = {}
    for content_object in objects.values():
        if content_object.object_type == "candidate-decomposition":
            adjacency.setdefault(content_object.payload["source_node_id"], []).append(
                content_object.payload["target_node_id"]
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ExplorerInputError("Candidate Decomposition must be acyclic")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in adjacency.get(node_id, []):
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in adjacency:
        visit(node_id)
    return objects


def _content_digest(index: Mapping[tuple[str, str], _ContentObject]) -> str:
    summary = [
        {
            "object_type": content_object.object_type,
            "object_id": content_object.object_id,
            "sha256": content_object.sha256,
        }
        for _, content_object in sorted(index.items())
    ]
    return _sha256(summary)


def _protected_index(repository: Path, revision: str) -> dict[tuple[str, str], str]:
    protected: dict[tuple[str, str], str] = {}
    modules = _json_at(repository, revision, "architecture-explorer/modules.json")
    for module in _require_list(modules.get("modules"), "modules"):
        module_id = module.get("id") if isinstance(module, dict) else None
        if not isinstance(module_id, str):
            raise ExplorerInputError("Protected top-level Module has no stable id")
        protected[("top-level-module", module_id)] = _sha256(
            {"object_type": "top-level-module", "object_id": module_id, "payload": module}
        )
    approved_evidence_ids: set[str] = set()
    for path in _paths_at(repository, revision, "architecture-explorer/reviews"):
        review = _json_at(repository, revision, path)
        if not isinstance(review, dict) or review.get("schema") != "architecture-explorer.review-map.v1":
            continue
        decision = review.get("decision")
        relationship = decision.get("relationship") if isinstance(decision, dict) else None
        if not isinstance(relationship, dict):
            raise ExplorerInputError("Protected formal Relationship payload is missing")
        relationship_id = relationship.get("id")
        if relationship_id is None and (
            relationship.get("source_module"),
            relationship.get("target_module"),
            relationship.get("type"),
        ) == ("production-control", "translation-execution", "process-launch"):
            relationship_id = "relationship:production-control:launches:translation-execution"
        if not isinstance(relationship_id, str):
            raise ExplorerInputError("Protected formal Relationship has no stable id")
        protected[("formal-relationship", relationship_id)] = _sha256(
            {
                "object_type": "formal-relationship",
                "object_id": relationship_id,
                "payload": relationship,
            }
        )
        approved_evidence_ids.update(
            _require_list(decision.get("approved_evidence_ids"), "approved formal Evidence ids")
        )
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for path in _paths_at(repository, revision, "architecture-explorer/review-inputs"):
        review_input = _json_at(repository, revision, path)
        if not isinstance(review_input, dict):
            continue
        for evidence in review_input.get("evidence", []):
            if isinstance(evidence, dict) and isinstance(evidence.get("evidence_id"), str):
                evidence_by_id[evidence["evidence_id"]] = evidence
    for evidence_id in approved_evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise ExplorerInputError(f"Protected formal Evidence is missing: {evidence_id}")
        protected[("formal-evidence", evidence_id)] = _sha256(
            {
                "object_type": "formal-evidence",
                "object_id": evidence_id,
                "payload": evidence,
            }
        )
    return protected


def _reject_protected_delta(repository: Path, base_revision: str, head_revision: str) -> None:
    base = _protected_index(repository, base_revision)
    head = _protected_index(repository, head_revision)
    changed = [key for key in sorted(base.keys() | head.keys()) if base.get(key) != head.get(key)]
    if changed:
        identities = ", ".join(f"{object_type}/{object_id}" for object_type, object_id in changed)
        raise ExplorerInputError(
            "Protected Architecture Explorer objects changed and require independent Owner authorization: "
            + identities
        )
    base_relationship = _projected_formal_relationship(repository, base_revision)
    head_relationship = _projected_formal_relationship(repository, head_revision)
    if _sha256(base_relationship) != _sha256(head_relationship):
        raise ExplorerInputError(
            "Protected Architecture Explorer objects changed and require independent Owner authorization: "
            f"formal-relationship/{base_relationship['id']}"
        )


def inspect_content_delta(
    repository: Path,
    *,
    base_revision: str,
    head_revision: str,
) -> ContentDelta:
    """Compute the authoritative object delta between two real Git commits."""

    repository = repository.resolve()
    _validate_commit(repository, base_revision, "Base revision")
    _validate_commit(repository, head_revision, "Head revision")
    ancestor = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            base_revision,
            head_revision,
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ExplorerInputError("Base revision must be an ancestor of Head revision")
    base_projection = project_authoritative_commit(repository, base_revision)
    head_projection = project_authoritative_commit(repository, head_revision)
    base = _extract_index(repository, base_revision, base_projection)
    head = _extract_index(repository, head_revision, head_projection)
    changes = []
    for key in sorted(base.keys() | head.keys()):
        before = base.get(key)
        after = head.get(key)
        if before is None:
            changes.append(ContentChange(key[0], key[1], "add", None, after.sha256))
        elif after is None:
            changes.append(ContentChange(key[0], key[1], "delete", before.sha256, None))
        elif before.sha256 != after.sha256:
            changes.append(ContentChange(key[0], key[1], "modify", before.sha256, after.sha256))
    counts = {object_type: 0 for object_type in OBJECT_TYPES}
    for object_type, _ in head:
        counts[object_type] += 1
    return ContentDelta(
        base_revision=base_revision,
        head_revision=head_revision,
        base_content_sha256=_content_digest(base),
        head_content_sha256=_content_digest(head),
        changes=tuple(changes),
        object_counts=counts,
    )


def _validated_manifest_change(value: Any, position: int) -> ContentChange:
    fields = {"operation", "object_type", "object_id", "before_sha256", "after_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ExplorerInputError(f"Manifest change {position} has invalid fields")
    operation = value["operation"]
    object_type = value["object_type"]
    object_id = value["object_id"]
    before = value["before_sha256"]
    after = value["after_sha256"]
    if operation not in {"add", "modify", "delete"}:
        raise ExplorerInputError(f"Manifest change {position} has an invalid operation")
    if object_type not in OBJECT_TYPES:
        raise ExplorerInputError(f"Manifest change {position} has an unknown object type")
    if not isinstance(object_id, str) or not object_id:
        raise ExplorerInputError(f"Manifest change {position} has an invalid object id")
    if before is not None and (not isinstance(before, str) or _SHA256.fullmatch(before) is None):
        raise ExplorerInputError(f"Manifest change {position} has an invalid before hash")
    if after is not None and (not isinstance(after, str) or _SHA256.fullmatch(after) is None):
        raise ExplorerInputError(f"Manifest change {position} has an invalid after hash")
    if operation == "add" and (before is not None or after is None):
        raise ExplorerInputError("Manifest add requires null before and a valid after hash")
    if operation == "delete" and (before is None or after is not None):
        raise ExplorerInputError("Manifest delete requires a valid before hash and null after")
    if operation == "modify" and (before is None or after is None or before == after):
        raise ExplorerInputError("Manifest modify requires distinct before and after hashes")
    return ContentChange(object_type, object_id, operation, before, after)


def validate_content_delta_manifest(
    repository: Path,
    *,
    head_revision: str,
    manifest: Mapping[str, Any] | None,
    required_base_revision: str | None = None,
    target_contract: str = TARGET_CONTRACT,
) -> ManifestValidationResult:
    """Fail closed unless one Manifest exactly equals the Git object delta."""

    if manifest is None or not isinstance(manifest, Mapping):
        raise ExplorerInputError("Content Delta Manifest v1 is required")
    required_fields = {
        "schema",
        "delta_id",
        "target_contract",
        "base_revision",
        "base_content_sha256",
        "head_content_sha256",
        "changes",
    }
    if set(manifest) != required_fields:
        raise ExplorerInputError("Content Delta Manifest v1 has invalid fields")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ExplorerInputError("Unsupported Content Delta Manifest schema")
    if not isinstance(manifest["delta_id"], str) or not manifest["delta_id"].strip():
        raise ExplorerInputError("Content Delta Manifest requires a stable delta_id")
    if target_contract not in {TARGET_CONTRACT, "architecture-explorer/v4"}:
        raise ExplorerInputError("Unknown Content Delta Manifest target contract")
    if manifest["target_contract"] != target_contract:
        raise ExplorerInputError(f"Content Delta Manifest must target {target_contract}")
    if target_contract == "architecture-explorer/v4":
        if required_base_revision not in {None, V4_FIXED_BASE_REVISION}:
            raise ExplorerInputError("Content Delta Manifest has the wrong fixed Base revision")
        required_base_revision = V4_FIXED_BASE_REVISION
    elif required_base_revision is None:
        required_base_revision = FIXED_BASE_REVISION
    if manifest["base_revision"] != required_base_revision:
        raise ExplorerInputError("Content Delta Manifest has the wrong fixed Base revision")
    for field in ("base_content_sha256", "head_content_sha256"):
        if not isinstance(manifest[field], str) or _SHA256.fullmatch(manifest[field]) is None:
            raise ExplorerInputError(f"Content Delta Manifest has an invalid {field}")
    raw_changes = _require_list(manifest["changes"], "Manifest changes")
    declared = tuple(
        sorted(_validated_manifest_change(value, position) for position, value in enumerate(raw_changes, 1))
    )
    declared_keys = [(change.object_type, change.object_id) for change in declared]
    if len(declared_keys) != len(set(declared_keys)):
        raise ExplorerInputError("Content Delta Manifest contains a duplicate object change")

    _validate_commit(repository.resolve(), required_base_revision, "Base revision")
    _validate_commit(repository.resolve(), head_revision, "Head revision")
    _reject_protected_delta(repository.resolve(), required_base_revision, head_revision)
    actual = inspect_content_delta(
        repository,
        base_revision=required_base_revision,
        head_revision=head_revision,
    )
    protected = [change for change in actual.changes if change.object_type in PROTECTED_OBJECT_TYPES]
    if protected:
        identities = ", ".join(f"{item.object_type}/{item.object_id}" for item in protected)
        raise ExplorerInputError(
            "Protected Architecture Explorer objects changed and require independent Owner authorization: "
            + identities
        )
    if manifest["base_content_sha256"] != actual.base_content_sha256:
        raise ExplorerInputError("Content Delta Manifest Base content digest does not match Git objects")
    if manifest["head_content_sha256"] != actual.head_content_sha256:
        raise ExplorerInputError("Content Delta Manifest Head content digest does not match Git objects")
    if declared != actual.changes:
        raise ExplorerInputError("Content Delta Manifest changes do not exactly match the Git object delta")

    canonical_manifest = dict(manifest)
    canonical_manifest["changes"] = [change.as_dict() for change in declared]
    return ManifestValidationResult(
        base_revision=actual.base_revision,
        head_revision=actual.head_revision,
        manifest_sha256=_sha256(canonical_manifest),
        base_content_sha256=actual.base_content_sha256,
        head_content_sha256=actual.head_content_sha256,
        changes=actual.changes,
        object_counts=actual.object_counts,
    )
