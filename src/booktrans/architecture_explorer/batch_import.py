"""Whole-batch validation and atomic import for Candidate review decisions.

The existing object-specific modules remain authoritative for Packet, Decision,
Receipt, Trust Policy, State, and Continuity validation.  This module only
coordinates those public contracts across one exact first-confirmation batch.
"""

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable
from urllib.parse import quote

from .batch_review import decided_objects
from .generator import ExplorerInputError
from .node_review_definitions import (
    NODE_REVIEW_DEFINITIONS_SCHEMA,
    build_node_review_packets,
    project_node_reviews,
    validate_node_review_definitions,
    verify_node_review_v2_integrity,
)
from .node_review_projection import NODE_REVIEW_PROJECTION_SCHEMA
from .node_review_v2_continuity import (
    evaluate_node_review_v2_state,
    update_node_review_v2_state,
)
from .relationship_review import (
    RELATIONSHIP_REVIEW_DEFINITIONS_SCHEMA,
    build_relationship_review_packets,
    evaluate_relationship_review_state,
    stable_review_object_key,
    update_relationship_review_state,
    validate_relationship_review_definitions,
    verify_relationship_review_integrity,
)
from .review_source import review_source_sha256
from .review_trust import ReviewTrustPolicy, match_trusted_review_source


IMPORT_MANIFEST_SCHEMA = "architecture-explorer.batch-import.v1"
FaultInjector = Callable[[str, str | None], None]


def _error(code: str, message: str, object_key: str | None = None) -> dict[str, Any]:
    return {"code": code, "object_key": object_key, "message": message}


def _preflight(
    explorer: dict[str, Any],
    batch: dict[str, Any],
    confirmation: dict[str, Any],
    node_definitions_value: dict[str, Any] | None,
    relationship_definitions_value: dict[str, Any] | None,
    materials_by_key: dict[str, dict[str, Any]] | None,
    anchor_batch: dict[str, Any],
    trust_policy: ReviewTrustPolicy,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    try:
        decisions = decided_objects(confirmation, batch)
    except (ExplorerInputError, KeyError, TypeError) as exc:
        return [_error("FIRST_CONFIRMATION", str(exc))], {}, {}

    decided_keys = set(decisions)
    pending_keys = sorted(set(batch.get("objects", {})) - decided_keys)
    if materials_by_key is not None and set(materials_by_key) != decided_keys:
        errors.append(
            _error(
                "MATERIAL_COMPLETENESS",
                "Decision/source materials must exactly cover every decided object",
            )
        )

    definition_by_key: dict[str, dict[str, Any]] = {}
    for document, schema, validate in (
        (node_definitions_value, NODE_REVIEW_DEFINITIONS_SCHEMA, validate_node_review_definitions),
        (
            relationship_definitions_value,
            RELATIONSHIP_REVIEW_DEFINITIONS_SCHEMA,
            validate_relationship_review_definitions,
        ),
    ):
        if document is None:
            continue
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "definitions"}
            or document["schema"] != schema
            or not isinstance(document["definitions"], list)
        ):
            errors.append(_error("DEFINITIONS_SCHEMA", f"Invalid Definitions document: {schema}"))
            continue
        seen = set()
        for definition in document["definitions"]:
            try:
                key = stable_review_object_key(definition.get("object_key"))
            except (AttributeError, ExplorerInputError, TypeError):
                continue  # Missing Definitions for any affected decided objects are reported below.
            if key not in decided_keys:
                continue
            if key in seen or key in definition_by_key:
                errors.append(_error("DUPLICATE_DEFINITION", "Multiple current Definitions exist", key))
                continue
            seen.add(key)
            try:
                definition_by_key[key] = validate({
                    "schema": schema, "definitions": [definition],
                })[0]
            except (ExplorerInputError, KeyError, TypeError) as exc:
                errors.append(_error("DEFINITION_INVALID", str(exc), key))

    batch_objects = batch["objects"]
    facts = explorer.get("source_facts") if isinstance(explorer, dict) else None
    snapshot = facts.get("source_snapshot") if isinstance(facts, dict) else None
    worktree = snapshot.get("worktree") if isinstance(snapshot, dict) else None
    current_snapshot = (
        {"revision": snapshot.get("revision"), "worktree_state": worktree.get("state")}
        if isinstance(worktree, dict) else None
    )
    anchor_objects = anchor_batch.get("objects", {}) if isinstance(anchor_batch, dict) else {}
    for key in sorted(decided_keys):
        definition = definition_by_key.get(key)
        packet = batch_objects[key]["packet"]
        is_node = key.startswith("candidate-node::")
        if packet["reviewed_source_snapshot"] != current_snapshot:
            errors.append(_error("SOURCE_SNAPSHOT", "Confirmed source snapshot is not current", key))
        if isinstance(explorer, dict) and explorer.get(
            "_node_review_scan_error" if is_node else "_relationship_review_scan_error"
        ):
            errors.append(_error("SOURCE_SCAN", "Current source scan failed", key))
        if definition is None:
            errors.append(_error("DEFINITION_MISSING", "No current Definition exists", key))
        elif current_snapshot is not None:
            build_packets = build_node_review_packets if is_node else build_relationship_review_packets
            try:
                current_packet = build_packets(explorer, [definition])[key]
            except (ExplorerInputError, AttributeError, KeyError, TypeError, ValueError) as exc:
                errors.append(_error("CURRENT_PACKET_INVALID", str(exc), key))
            else:
                if current_packet != packet:
                    errors.append(_error(
                        "PACKET_CHANGED", "Current Packet differs from the confirmed Packet", key
                    ))
        if materials_by_key is None:
            continue
        material = materials_by_key.get(key)
        if not isinstance(material, dict) or set(material) != {"decision", "source"}:
            errors.append(_error("MATERIAL_SCHEMA", "Material must contain only decision and source", key))
            continue
        decision = material.get("decision")
        source = material.get("source")
        if not isinstance(decision, dict) or not isinstance(source, dict):
            errors.append(_error("MATERIAL_SCHEMA", "Decision and source must be objects", key))
            continue
        if decision.get("result") != decisions[key].get("result"):
            errors.append(_error("DECISION_RESULT", "Decision result differs from first confirmation", key))
        if any(decision.get(field) != packet[field] for field in (
            "object_key", "reviewed_source_snapshot", "inputs", "claims",
        )):
            errors.append(_error("DECISION_PACKET", "Decision differs from the confirmed Packet", key))
        entry = anchor_objects.get(key)
        if entry is not None:
            bindings = {
                "object_key": packet["object_key"],
                "decision_id": decision.get("decision_id"),
                "decision_sha256": review_source_sha256(decision),
                "source_receipt_sha256": review_source_sha256(source),
            }
            for field, expected in bindings.items():
                if entry[field] != expected:
                    errors.append(_error("ANCHOR_BINDING", f"Installed Anchor differs: {field}", key))
        if definition is None or entry is None:
            continue
        try:
            if is_node:
                verify_node_review_v2_integrity(
                    definition, decision, source, trust_policy=trust_policy
                )
            else:
                verify_relationship_review_integrity(
                    definition, decision, source, trust_policy=trust_policy
                )
        except (ExplorerInputError, AttributeError, KeyError, TypeError, ValueError) as exc:
            errors.append(_error("OBJECT_VALIDATION", str(exc), key))

    plan = {
        "definitions": definition_by_key,
        "pending_objects": pending_keys,
    }
    return errors, materials_by_key or {}, plan


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _material_directory_name(key: str) -> str:
    kind, object_id = key.split("::", 1)
    return f"{kind}__{quote(object_id, safe='').replace('.', '%2E')}"


@dataclass(frozen=True)
class PublishedReview:
    directory: Path
    decision: dict[str, Any]
    source: dict[str, Any]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExplorerInputError(f"Unreadable batch artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected a batch artifact object: {path}")
    return value


def _read_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "import-manifest.json"
    if directory.is_symlink() or manifest_path.is_symlink() or _inside_staging(directory):
        raise ExplorerInputError("Expected a published review batch directory")
    manifest = _read_json_object(manifest_path)
    fields = {
        "schema", "batch_id", "batch_sha256", "confirmation_sha256",
        "installation_sha256", "assessed_at", "imported_objects",
        "pending_objects", "objects", "import_sha256",
    }
    if (
        set(manifest) != fields
        or manifest["schema"] != IMPORT_MANIFEST_SCHEMA
        or manifest["import_sha256"] != review_source_sha256({
            key: value for key, value in manifest.items() if key != "import_sha256"
        })
    ):
        raise ExplorerInputError("Invalid review batch manifest")
    return manifest


def _inside_staging(directory: Path) -> bool:
    return any(part.startswith(".") and ".staging-" in part for part in directory.resolve().parts)


def _read_batch_files(
    directory: Path,
    manifest: dict[str, Any],
    installed: dict[str, Any],
    policy: ReviewTrustPolicy | None,
    *,
    initial_state: bool = False,
) -> dict[str, PublishedReview]:
    keys = set(installed["objects"])
    if (
        any(manifest[field] != installed[field] for field in (
            "batch_id", "batch_sha256", "confirmation_sha256", "installation_sha256"
        ))
        or manifest["imported_objects"] != sorted(keys)
        or not isinstance(manifest["objects"], dict)
        or set(manifest["objects"]) != keys
    ):
        raise ExplorerInputError("Published review batch differs from the host installation")
    objects_directory = directory / "objects"
    records = {}
    try:
        if objects_directory.is_symlink() or {path.name for path in objects_directory.iterdir()} != {
            _material_directory_name(key) for key in installed["objects"]
        }:
            raise ExplorerInputError("Incomplete published review batch")
    except OSError as exc:
        raise ExplorerInputError("Unreadable published review objects directory") from exc
    for key, anchor in installed["objects"].items():
        material_dir = objects_directory / _material_directory_name(key)
        decision_path, source_path, state_path = (
            material_dir / name for name in ("decision.json", "source.json", "state.json")
        )
        if material_dir.is_symlink() or any(
            path.is_symlink() for path in (decision_path, source_path, state_path)
        ):
            raise ExplorerInputError(f"Linked review batch material: {key}")
        if initial_state:
            try:
                filenames = {path.name for path in material_dir.iterdir()}
            except OSError as exc:
                raise ExplorerInputError(f"Unreadable published review object: {key}") from exc
            if filenames != {"decision.json", "source.json", "state.json"}:
                raise ExplorerInputError(f"Published review batch material differs: {key}")
        decision = _read_json_object(decision_path)
        source = _read_json_object(source_path)
        state = _read_json_object(state_path)
        hashes = manifest["objects"][key]
        if (
            not isinstance(hashes, dict)
            or set(hashes) != {"decision_sha256", "source_receipt_sha256", "state_sha256"}
            or decision.get("object_key") != anchor["object_key"]
            or decision.get("decision_id") != anchor["decision_id"]
            or "batch_publication_sha256" not in decision
            or hashes["decision_sha256"] != anchor["decision_sha256"]
            or hashes["decision_sha256"] != review_source_sha256(decision)
            or hashes["source_receipt_sha256"] != anchor["source_receipt_sha256"]
            or hashes["source_receipt_sha256"] != review_source_sha256(source)
            or (initial_state and hashes["state_sha256"] != review_source_sha256(state))
        ):
            raise ExplorerInputError(f"Published review batch material differs: {key}")
        if policy is not None:
            match_trusted_review_source(source, policy, label=key)
        evaluate = (
            evaluate_node_review_v2_state if key.startswith("candidate-node::")
            else evaluate_relationship_review_state
        )
        try:
            evaluated = evaluate(decision, state_path)
        except (TypeError, ValueError) as exc:
            raise ExplorerInputError(f"Invalid published review State: {key}") from exc
        if any(evaluated.get(field) != state.get(field) for field in (
            "object_key", "decision_id", "continuity", "reasons", "evaluated_source_snapshot",
        )) or evaluated["status"] != state.get("state"):
            raise ExplorerInputError(f"Invalid published review State: {key}")
        records[key] = PublishedReview(material_dir, decision, source)
    return records


def read_published_review_batch(
    directory: Path,
) -> tuple[dict[str, PublishedReview], ReviewTrustPolicy]:
    """Read the complete batch and the corresponding installed trust policy."""
    from .trusted_batch_review import host_anchor_installation_path, installed_anchor_trust_policy

    manifest = _read_manifest(directory)
    installed = _read_json_object(host_anchor_installation_path(manifest["installation_sha256"]))
    if installed.get("installation_sha256") != manifest["installation_sha256"]:
        raise ExplorerInputError("Host installation does not match the manifest digest")
    policy = installed_anchor_trust_policy(installed)
    return _read_batch_files(directory, manifest, installed, policy), policy


def _read_existing_import(
    batch_directory: Path,
    *,
    anchor_batch: dict[str, Any],
    materials_by_key: dict[str, dict[str, Any]],
    pending_objects: list[str],
) -> dict[str, Any] | None:
    try:
        manifest = _read_manifest(batch_directory)
        if manifest["pending_objects"] != pending_objects:
            return None
        stored = _read_batch_files(
            batch_directory, manifest, anchor_batch, None, initial_state=True
        )
        if {
            key: {"decision": record.decision, "source": record.source}
            for key, record in stored.items()
        } != materials_by_key:
            return None
    except ExplorerInputError:
        return None
    return {
        "schema": IMPORT_MANIFEST_SCHEMA,
        "status": "already-imported",
        "batch_id": anchor_batch["batch_id"],
        "imported_objects": sorted(materials_by_key),
        "pending_objects": pending_objects,
        "batch_directory": str(batch_directory),
        "errors": [],
    }


def _import_review_batch(
    explorer: dict[str, Any],
    batch: dict[str, Any],
    confirmation: dict[str, Any],
    node_definitions: dict[str, Any] | None,
    relationship_definitions: dict[str, Any] | None,
    materials_by_key: dict[str, dict[str, Any]] | None,
    anchor_batch: dict[str, Any],
    batch_directory: Path,
    *,
    assessed_at: str,
    trust_policy: ReviewTrustPolicy,
    fault_injector: FaultInjector | None = None,
    input_errors: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Validate a complete batch, then publish all review state atomically."""

    errors, verified_materials, plan = _preflight(
        explorer,
        batch,
        confirmation,
        node_definitions,
        relationship_definitions,
        materials_by_key,
        anchor_batch,
        trust_policy,
    )
    errors = [*input_errors, *errors]
    if errors:
        return {
            "schema": IMPORT_MANIFEST_SCHEMA,
            "status": "preflight-failed",
            "batch_id": anchor_batch.get("batch_id") if isinstance(anchor_batch, dict) else None,
            "imported_objects": [],
            "pending_objects": plan.get("pending_objects", []),
            "batch_directory": str(batch_directory),
            "errors": errors,
        }

    if batch_directory.exists() or batch_directory.is_symlink():
        existing = _read_existing_import(
            batch_directory,
            anchor_batch=anchor_batch,
            materials_by_key=verified_materials,
            pending_objects=plan["pending_objects"],
        )
        if existing is not None:
            return existing
        return {
            "schema": IMPORT_MANIFEST_SCHEMA,
            "status": "preflight-failed",
            "batch_id": anchor_batch["batch_id"],
            "imported_objects": [],
            "pending_objects": plan["pending_objects"],
            "batch_directory": str(batch_directory),
            "errors": [_error("EXISTING_IMPORT_INCONSISTENT", "Existing batch import differs; refusing overwrite")],
        }

    parent = batch_directory.parent
    staging: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{batch_directory.name}.staging-", dir=parent))
        node_materials = {}
        relationship_explorer = explorer
        object_records = {}
        for key in sorted(
            verified_materials,
            key=lambda value: (not value.startswith("candidate-node::"), value),
        ):
            material = verified_materials[key]
            directory = staging / "objects" / _material_directory_name(key)
            directory.mkdir(parents=True)
            _write_json(directory / "decision.json", material["decision"])
            _write_json(directory / "source.json", material["source"])
            definition = plan["definitions"][key]
            if key.startswith("candidate-node::"):
                state = update_node_review_v2_state(
                    explorer,
                    definition,
                    material["decision"],
                    material["source"],
                    directory / "state.json",
                    assessed_at=assessed_at,
                    trust_policy=trust_policy,
                )
                node_materials[key] = {
                    **material,
                    "state": evaluate_node_review_v2_state(
                        material["decision"], directory / "state.json"
                    ),
                }
            else:
                if node_materials and relationship_explorer is explorer:
                    base = dict(explorer)
                    previous_projection = base.pop("node_review_projection", {})
                    projected = project_node_reviews(
                        base,
                        [plan["definitions"][node_key] for node_key in node_materials],
                        node_materials,
                        trust_policy=trust_policy,
                    )["node_review_projection"]
                    if previous_projection.get("schema") == NODE_REVIEW_PROJECTION_SCHEMA:
                        projected["objects"] = {
                            **previous_projection["objects"],
                            **projected["objects"],
                        }
                    relationship_explorer = {**base, "node_review_projection": projected}
                state = update_relationship_review_state(
                    relationship_explorer,
                    definition,
                    material["decision"],
                    material["source"],
                    directory / "state.json",
                    assessed_at=assessed_at,
                    trust_policy=trust_policy,
                )
            object_records[key] = {
                "decision_sha256": review_source_sha256(material["decision"]),
                "source_receipt_sha256": review_source_sha256(material["source"]),
                "state_sha256": review_source_sha256(state),
            }
            if fault_injector is not None:
                fault_injector("after-object-staged", key)
        manifest = {
            "schema": IMPORT_MANIFEST_SCHEMA,
            "batch_id": anchor_batch["batch_id"],
            "batch_sha256": batch["batch_sha256"],
            "confirmation_sha256": confirmation["confirmation_sha256"],
            "installation_sha256": anchor_batch["installation_sha256"],
            "assessed_at": assessed_at,
            "imported_objects": sorted(verified_materials),
            "pending_objects": plan["pending_objects"],
            "objects": object_records,
        }
        manifest["import_sha256"] = review_source_sha256(manifest)
        _write_json(staging / "import-manifest.json", manifest)
        if fault_injector is not None:
            fault_injector("before-publish", None)
        os.rename(staging, batch_directory)
        staging = None
    except Exception as exc:  # write failures are reported at the public workflow seam
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                # A hidden staging directory is never a trusted import.  Keep
                # the original failure visible; a later retry uses a new one.
                pass
        return {
            "schema": IMPORT_MANIFEST_SCHEMA,
            "status": "write-failed",
            "batch_id": anchor_batch["batch_id"],
            "imported_objects": [],
            "pending_objects": plan["pending_objects"],
            "batch_directory": str(batch_directory),
            "errors": [_error("WRITE_FAILED", str(exc))],
        }
    return {
        "schema": IMPORT_MANIFEST_SCHEMA,
        "status": "imported",
        "batch_id": anchor_batch["batch_id"],
        "imported_objects": sorted(verified_materials),
        "pending_objects": plan["pending_objects"],
        "batch_directory": str(batch_directory),
        "errors": [],
    }


def import_review_batch(
    explorer: dict[str, Any],
    batch: dict[str, Any],
    confirmation: dict[str, Any],
    node_definitions: dict[str, Any] | None,
    relationship_definitions: dict[str, Any] | None,
    publication: dict[str, Any] | None,
    receipts: dict[str, Any] | None,
    installed_anchor_path: Path | None,
    batch_directory: Path,
    *,
    assessed_at: str,
    fault_injector: FaultInjector | None = None,
) -> dict[str, Any]:
    """Consume T5 output using only the host-authorized installed batch reader."""
    from .trusted_batch_review import (
        installed_anchor_trust_policy,
        load_installed_anchor_batch,
        prepare_anchor_confirmation,
        review_batch_materials,
    )

    errors = []
    anchor_batch = {}
    trust_policy = ReviewTrustPolicy()
    materials = None
    try:
        decisions = decided_objects(confirmation, batch)
    except (ExplorerInputError, KeyError, TypeError):
        decisions = None  # Reported by the existing T4 preflight below.
    if (
        decisions == {}
        and publication is None
        and receipts is None
        and installed_anchor_path is None
    ):
        return {
            "schema": IMPORT_MANIFEST_SCHEMA,
            "status": "nothing-to-import",
            "batch_id": None,
            "imported_objects": [],
            "pending_objects": sorted(batch["objects"]),
            "batch_directory": str(batch_directory),
            "errors": [],
        }
    if decisions == {}:
        errors.append(_error("NO_DECIDED_OBJECTS", "An all-Pending batch has no import materials"))

    if decisions is not None:
        try:
            if installed_anchor_path is None:
                raise ExplorerInputError("Installed Anchor batch path is required")
            installed = load_installed_anchor_batch(installed_anchor_path, batch, confirmation)
            trust_policy = installed_anchor_trust_policy(installed)
            anchor_batch = installed
        except ExplorerInputError as exc:
            errors.append(_error("ANCHOR_INSTALLATION", str(exc)))

        if not isinstance(publication, dict) or not isinstance(receipts, dict):
            errors.append(_error("RECEIPT_BATCH", "Producer publication or Receipt batch is missing"))
        else:
            try:
                materials = review_batch_materials(batch, confirmation, publication, receipts)
            except (ExplorerInputError, KeyError, TypeError) as exc:
                errors.append(_error("RECEIPT_BATCH", str(exc)))
                try:
                    diagnostics = prepare_anchor_confirmation(
                        batch, confirmation, publication, receipts
                    )
                except (ExplorerInputError, KeyError, TypeError):
                    pass  # The failing producer reader already reported the input error.
                else:
                    object_messages = set()
                    for key, entry in diagnostics["entries"].items():
                        for message in entry["errors"]:
                            errors.append(_error("RECEIPT_ENTRY", message, key))
                            object_messages.add(f"receipt:{key}:{message}")
                    errors.extend(
                        _error("RECEIPT_BATCH", message)
                        for message in diagnostics["errors"]
                        if message not in object_messages
                    )

        # Keep the batch error while checking any available pairs against
        # their installed Anchor, Definition and confirmed Packet.
        if materials is None and isinstance(receipts, dict):
            entries = receipts.get("entries")
            if isinstance(entries, dict):
                materials = {
                    key: {name: entry.get(name) for name in ("decision", "source")}
                    for key, entry in entries.items() if isinstance(entry, dict)
                }

    if not isinstance(assessed_at, str) or not assessed_at.strip():
        errors.append(_error("ASSESSED_AT", "State assessment time is required"))
    if _inside_staging(batch_directory):
        errors.append(_error("BATCH_DIRECTORY", "An import cannot publish inside a staging directory"))
    return _import_review_batch(
        explorer, batch, confirmation, node_definitions, relationship_definitions,
        materials, anchor_batch, batch_directory, assessed_at=assessed_at,
        trust_policy=trust_policy, fault_injector=fault_injector, input_errors=tuple(errors),
    )


__all__ = [
    "IMPORT_MANIFEST_SCHEMA",
    "import_review_batch",
    "read_published_review_batch",
]
