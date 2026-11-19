from __future__ import annotations

from typing import Any

from .generator import ExplorerInputError


SOURCE_EVIDENCE_INDEX_SCHEMA = "architecture-explorer.source-evidence-index.v1"


def build_source_evidence_index(source_facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index exact Evidence identities without copying authoritative Source Facts."""

    if not isinstance(source_facts, dict):
        raise ExplorerInputError("Source Evidence index requires Source Facts")
    index: dict[str, dict[str, Any]] = {}

    def add(evidence: object, reference: dict[str, Any]) -> None:
        evidence_id = evidence.get("evidence_id") if isinstance(evidence, dict) else None
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ExplorerInputError("Source Evidence record needs an Evidence ID")
        if evidence_id in index:
            raise ExplorerInputError(f"Source Evidence ID must be unique: {evidence_id}")
        index[evidence_id] = {
            **reference,
            "evidence_kind": evidence.get("evidence_kind"),
        }

    modules = source_facts.get("modules")
    if not isinstance(modules, list):
        raise ExplorerInputError("Source Evidence index requires Python module facts")
    for module_index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ExplorerInputError("Source Evidence Python module must be an object")
        for collection in ("direct_imports", "entries"):
            records = module.get(collection)
            if not isinstance(records, list):
                raise ExplorerInputError(
                    f"Source Evidence Python module {collection} must be a list"
                )
            for record_index, record in enumerate(records):
                add(
                    record.get("evidence") if isinstance(record, dict) else None,
                    {
                        "record_source": f"source_facts.modules.{collection}",
                        "module_index": module_index,
                        "record_index": record_index,
                        "displayable": True,
                    },
                )

    scripts = source_facts.get("powershell_scripts")
    if not isinstance(scripts, list):
        raise ExplorerInputError("Source Evidence index requires PowerShell facts")
    for script_index, script in enumerate(scripts):
        if not isinstance(script, dict):
            raise ExplorerInputError("Source Evidence PowerShell script must be an object")
        for collection in ("entries", "script_bindings", "dot_sources"):
            records = script.get(collection)
            if not isinstance(records, list):
                raise ExplorerInputError(
                    f"Source Evidence PowerShell {collection} must be a list"
                )
            for record_index, record in enumerate(records):
                if not isinstance(record, dict) or record.get("resolution") != "known":
                    continue
                add(
                    record.get("evidence"),
                    {
                        "record_source": f"source_facts.powershell_scripts.{collection}",
                        "script_index": script_index,
                        "record_index": record_index,
                        "displayable": True,
                    },
                )

    definition_index = source_facts.get("python_definition_index")
    definition_files = (
        definition_index.get("files")
        if isinstance(definition_index, dict)
        else None
    )
    if not isinstance(definition_files, list):
        raise ExplorerInputError("Source Evidence index requires a Definition Index")
    for file_index, source_file in enumerate(definition_files):
        definitions = source_file.get("definitions") if isinstance(source_file, dict) else None
        if not isinstance(definitions, list):
            raise ExplorerInputError("Source Evidence definitions must be a list")
        for record_index, definition in enumerate(definitions):
            add(
                definition.get("evidence") if isinstance(definition, dict) else None,
                {
                    "record_source": "source_facts.python_definition_index.files.definitions",
                    "file_index": file_index,
                    "record_index": record_index,
                    "displayable": True,
                    "source_trail_kind": "definition",
                },
            )

    call_facts = source_facts.get("python_call_facts")
    call_files = call_facts.get("files") if isinstance(call_facts, dict) else None
    if not isinstance(call_files, list):
        raise ExplorerInputError("Source Evidence index requires Call Facts")
    for file_index, source_file in enumerate(call_files):
        calls = source_file.get("calls") if isinstance(source_file, dict) else None
        if not isinstance(calls, list):
            raise ExplorerInputError("Source Evidence calls must be a list")
        for record_index, call in enumerate(calls):
            enclosing_id = (
                call.get("enclosing_definition_evidence_id")
                if isinstance(call, dict)
                else None
            )
            enclosing = index.get(enclosing_id)
            navigable = (
                enclosing is not None
                and enclosing.get("source_trail_kind") == "definition"
            )
            reference = {
                "record_source": "source_facts.python_call_facts.files.calls",
                "file_index": file_index,
                "record_index": record_index,
                "displayable": True,
            }
            if navigable:
                reference["source_trail_kind"] = "call"
                reference["enclosing_definition_evidence_id"] = enclosing_id
            add(call.get("evidence") if isinstance(call, dict) else None, reference)
    return index


__all__ = ["SOURCE_EVIDENCE_INDEX_SCHEMA", "build_source_evidence_index"]
