from __future__ import annotations

import ast
import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any

from .candidate_scanner import validated_source_path
from .generator import ExplorerInputError
from .source_primitives import (
    build_static_evidence,
    canonical_bytes as _canonical_bytes,
    git_output as _git,
    read_stable_utf8,
    sha256_bytes as _sha256,
)


SCANNER_VERSION = "architecture-explorer-t6-4/1.0"
SOURCE_FACTS_SCHEMA = "architecture-explorer.python-source-facts.v1"
SOURCE_ROOTS = ("src/booktrans/", "scripts/")
EXCLUDED_PREFIXES = (
    "scripts/architecture_explorer/",
    "src/booktrans/architecture_explorer/",
)
LIMITATIONS = (
    "Direct imports are static source facts; they do not prove runtime reachability or collaboration.",
    "No scanned file is assigned to an approved architecture module.",
    "Dynamic imports, runtime calls, and a complete call graph are not collected.",
    "Files outside the declared scope are unexamined, not historical, inactive, or invalid.",
    "PowerShell facts are limited to the single production Supervisor; no general PowerShell call graph is built.",
    "Dynamic or ambiguous PowerShell paths remain unknown and do not prove script execution or architecture collaboration.",
)
_WORKER_WRAPPER = "scripts/production/run_qwen_deterministic_worker.py"
_SUPERVISOR = "scripts/production/production_campaign_supervisor.ps1"
_SUPERVISOR_ENTRIES = (
    "Get-AutoRecoveryPolicy",
    "Invoke-AuthoritativeCursorDecision",
    "Invoke-AuthoritativeHandoffDecision",
    "Invoke-ControlPlaneShadow",
    "Invoke-CurrentExecutionProjection",
    "Invoke-SupervisorRetryPolicy",
    "Refresh-State",
    "Start-DeterministicWorkerProcess",
)

_POWERSHELL_SOURCE_FACTS_QUERY = r"""
$ErrorActionPreference = 'Stop'
$encoded = [Console]::In.ReadToEnd().Trim()
$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)).TrimStart([char]0xFEFF)
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source,
    [ref]$tokens,
    [ref]$parseErrors
)
$entryNames = @(
__ENTRY_NAMES__
)
$result = [ordered]@{
    parse_errors = @($parseErrors | ForEach-Object {
        [pscustomobject][ordered]@{
            line = $_.Extent.StartLineNumber
            message = $_.Message
            text = $_.Extent.Text
        }
    })
    entries = @()
    bindings = @()
    dot_sources = @()
}
if (@($parseErrors).Count -eq 0) {
    $functions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $entryNames -contains $node.Name
    }, $true))
    foreach ($node in $functions) {
        $result.entries += [pscustomobject][ordered]@{
            symbol = $node.Name
            text = $node.Extent.Text
            line_start = $node.Extent.StartLineNumber
            line_end = $node.Extent.EndLineNumber
        }
    }

    $assignments = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]
    }, $true))
    foreach ($node in $assignments) {
        $joins = @($node.Right.FindAll({
            param($child)
            $child -is [System.Management.Automation.Language.CommandAst] -and
            $child.GetCommandName() -eq 'Join-Path'
        }, $true))
        $candidates = @()
        foreach ($join in $joins) {
            $elements = @($join.CommandElements)
            if (
                $elements.Count -eq 3 -and
                $elements[1] -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $elements[1].VariablePath.UserPath -eq 'Root' -and
                $elements[2] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                $elements[2].Value -like 'scripts\production\*' -and
                $elements[2].Value -match '\.(py|ps1)$'
            ) {
                $candidates += $elements[2].Value
            }
        }
        if ($candidates.Count -gt 0) {
            $known = $joins.Count -eq 1 -and
                $candidates.Count -eq 1 -and
                $node.Right.Extent.Text.Trim() -eq $joins[0].Extent.Text.Trim()
            $result.bindings += [pscustomobject][ordered]@{
                variable = $node.Left.VariablePath.UserPath
                resolution = $(if ($known) { 'known' } else { 'unknown' })
                target_path = $(if ($known) { $candidates[0] } else { $null })
                candidate_paths = @($candidates)
                text = $node.Extent.Text
                line_start = $node.Extent.StartLineNumber
                line_end = $node.Extent.EndLineNumber
            }
        }
    }

    $dotCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Dot
    }, $true))
    foreach ($node in $dotCommands) {
        $elements = @($node.CommandElements)
        $variable = $null
        if (
            $elements.Count -eq 1 -and
            $elements[0] -is [System.Management.Automation.Language.VariableExpressionAst]
        ) {
            $variable = $elements[0].VariablePath.UserPath
        }
        $result.dot_sources += [pscustomobject][ordered]@{
            variable = $variable
            text = $node.Extent.Text
            line_start = $node.Extent.StartLineNumber
            line_end = $node.Extent.EndLineNumber
        }
    }
}
$result | ConvertTo-Json -Compress -Depth 8
"""


def _read_stable_utf8(repository: Path, relative: str) -> tuple[str, str, int]:
    path = validated_source_path(repository, relative)
    return read_stable_utf8(path, relative, contract="T6-1")


def _tracked_python_paths(repository: Path) -> list[str]:
    raw = _git(repository, "ls-files", "-z", "--", "src/booktrans", "scripts")
    paths = {
        item.replace("\\", "/")
        for item in raw.split("\0")
        if item.endswith(".py")
    }
    return sorted(
        path
        for path in paths
        if any(path.startswith(root) for root in SOURCE_ROOTS)
        and not any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
    )


def _module_identity(relative: str) -> tuple[str, str]:
    path = Path(relative)
    without_suffix = list(path.with_suffix("").parts)
    if relative.startswith("src/booktrans/"):
        dotted = without_suffix[1:]
        if dotted[-1] == "__init__":
            dotted.pop()
            kind = "package"
        else:
            kind = "module"
    else:
        dotted = without_suffix
        kind = "script"
    return ".".join(dotted), kind


def _snippet(text: str, node: ast.AST) -> str:
    value = ast.get_source_segment(text, node)
    if value is None:
        raise ExplorerInputError("Python parser could not locate a scanned source fact")
    return value


def _node_evidence(
    text: str,
    relative: str,
    file_sha256: str,
    node: ast.AST,
    *,
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
) -> dict[str, Any]:
    return build_static_evidence(
        path=relative,
        source_role="automatic_source_fact",
        file_sha256=file_sha256,
        evidence_kind=evidence_kind,
        symbol=symbol,
        structural_anchor=structural_anchor,
        syntax_position=syntax_position,
        snippet=_snippet(text, node),
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
    )


def _direct_imports(
    tree: ast.Module,
    text: str,
    relative: str,
    file_sha256: str,
) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []
    for position, node in enumerate(tree.body, start=1):
        if isinstance(node, ast.Import):
            for alias_position, alias in enumerate(node.names, start=1):
                name = alias.name + (f" as {alias.asname}" if alias.asname else "")
                imports.append(
                    {
                        "kind": "import",
                        "module": alias.name,
                        "names": [name],
                        "level": 0,
                        "evidence": _node_evidence(
                            text,
                            relative,
                            file_sha256,
                            node,
                            evidence_kind="direct_import",
                            symbol=alias.name,
                            structural_anchor=f"python:module/import:{alias.name}",
                            syntax_position=f"module:{position}/alias:{alias_position}",
                        ),
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [
                alias.name + (f" as {alias.asname}" if alias.asname else "")
                for alias in node.names
            ]
            imports.append(
                {
                    "kind": "from",
                    "module": module,
                    "names": names,
                    "level": node.level,
                    "evidence": _node_evidence(
                        text,
                        relative,
                        file_sha256,
                        node,
                        evidence_kind="direct_import",
                        symbol=("." * node.level) + module,
                        structural_anchor=f"python:module/import-from:{'.' * node.level}{module}",
                        syntax_position=f"module:{position}",
                    ),
                }
            )
    return imports


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    return (
        isinstance(comparison.left, ast.Name)
        and comparison.left.id == "__name__"
        and len(comparison.ops) == 1
        and isinstance(comparison.ops[0], ast.Eq)
        and len(comparison.comparators) == 1
        and isinstance(comparison.comparators[0], ast.Constant)
        and comparison.comparators[0].value == "__main__"
    )


def _typer_target(node: ast.AST) -> str | None:
    target: ast.expr | None = None
    value: ast.expr | None = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, ast.AnnAssign):
        target, value = node.target, node.value
    if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
        return None
    function = value.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "typer"
        and function.attr == "Typer"
    ):
        return target.id
    return None


def _guard_exits_with_imported_main(node: ast.If) -> bool:
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Raise):
        return False
    raised = node.body[0].exc
    if (
        not isinstance(raised, ast.Call)
        or not isinstance(raised.func, ast.Name)
        or raised.func.id != "SystemExit"
        or len(raised.args) != 1
        or raised.keywords
    ):
        return False
    worker_call = raised.args[0]
    return (
        isinstance(worker_call, ast.Call)
        and isinstance(worker_call.func, ast.Name)
        and worker_call.func.id == "main"
        and not worker_call.args
        and not worker_call.keywords
    )


def _entries(
    tree: ast.Module,
    text: str,
    relative: str,
    file_sha256: str,
    direct_imports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    guards = [node for node in tree.body if _is_main_guard(node)]
    for position, node in enumerate(tree.body, start=1):
        target = _typer_target(node)
        if target is not None:
            entries.append(
                {
                    "kind": "declarative_cli",
                    "symbol": target,
                    "evidence": _node_evidence(
                        text,
                        relative,
                        file_sha256,
                        node,
                        evidence_kind="declarative_cli",
                        symbol=target,
                        structural_anchor=f"python:module/assignment:{target}/call:typer.Typer",
                        syntax_position=f"module:{position}",
                    ),
                }
            )
    for guard_position, node in enumerate(guards, start=1):
        entries.append(
            {
                "kind": "main_guard",
                "symbol": "__main__",
                "evidence": _node_evidence(
                    text,
                    relative,
                    file_sha256,
                    node,
                    evidence_kind="main_guard",
                    symbol="__main__",
                    structural_anchor="python:module/if:__name__==__main__",
                    syntax_position=f"module/main-guard:{guard_position}",
                ),
            }
        )
    if relative.startswith("src/booktrans/") and relative.endswith("/__main__.py") and guards:
        node = guards[0]
        entries.append(
            {
                "kind": "package_entry",
                "symbol": "__main__",
                "evidence": _node_evidence(
                    text,
                    relative,
                    file_sha256,
                    node,
                    evidence_kind="package_entry",
                    symbol="__main__",
                    structural_anchor="python:package-entry/__main__.py",
                    syntax_position="module/main-guard:1",
                ),
            }
        )
    imports_worker_main = any(
        item["kind"] == "from"
        and item["module"] == "booktrans.production_worker.worker"
        and "main" in item["names"]
        for item in direct_imports
    )
    if (
        relative == _WORKER_WRAPPER
        and imports_worker_main
        and guards
        and _guard_exits_with_imported_main(guards[0])
    ):
        node = guards[0]
        entries.append(
            {
                "kind": "deterministic_worker_wrapper",
                "symbol": "main",
                "evidence": _node_evidence(
                    text,
                    relative,
                    file_sha256,
                    node,
                    evidence_kind="deterministic_worker_wrapper",
                    symbol="main",
                    structural_anchor=(
                        "python:worker-wrapper/import:booktrans.production_worker.worker.main/"
                        "main-guard"
                    ),
                    syntax_position="module/main-guard:1",
                ),
            }
        )
    return sorted(entries, key=lambda item: (item["kind"], item["symbol"]))


def _scan_module(repository: Path, relative: str) -> dict[str, Any]:
    text, file_sha256, size_bytes = _read_stable_utf8(repository, relative)
    try:
        tree = ast.parse(text, filename=relative)
    except SyntaxError as error:
        raise ExplorerInputError(
            f"Python syntax error in {relative}:{error.lineno or '?'}:{error.offset or '?'}"
        ) from error
    direct_imports = _direct_imports(tree, text, relative, file_sha256)
    module_id, module_kind = _module_identity(relative)
    return {
        "module_id": module_id,
        "module_kind": module_kind,
        "path": relative,
        "file_sha256": file_sha256,
        "size_bytes": size_bytes,
        "direct_imports": direct_imports,
        "entries": _entries(tree, text, relative, file_sha256, direct_imports),
    }


def _powershell_ast(text: str) -> dict[str, Any]:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    entry_names = "\n".join(f"    '{name}'" for name in _SUPERVISOR_ENTRIES)
    query = _POWERSHELL_SOURCE_FACTS_QUERY.replace("__ENTRY_NAMES__", entry_names)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                query,
            ],
            input=encoded,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise ExplorerInputError("PowerShell AST parser is unavailable") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ExplorerInputError(f"PowerShell AST parser failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ExplorerInputError("PowerShell AST parser returned invalid JSON") from error
    if not isinstance(result, dict):
        raise ExplorerInputError("PowerShell AST parser returned invalid data")
    return result


def _powershell_evidence(
    record: dict[str, Any],
    *,
    file_sha256: str,
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
) -> dict[str, Any]:
    return build_static_evidence(
        path=_SUPERVISOR,
        source_role="automatic_source_fact",
        evidence_kind=evidence_kind,
        symbol=symbol,
        structural_anchor=structural_anchor,
        syntax_position=syntax_position,
        snippet=str(record["text"]),
        line_start=int(record["line_start"]),
        line_end=int(record["line_end"]),
        file_sha256=file_sha256,
    )


def _scan_supervisor(repository: Path) -> list[dict[str, Any]]:
    tracked = _git(repository, "ls-files", "--", _SUPERVISOR).splitlines()
    if _SUPERVISOR not in {item.replace("\\", "/") for item in tracked}:
        return []
    text, file_sha256, size_bytes = _read_stable_utf8(repository, _SUPERVISOR)
    structure = _powershell_ast(text)
    parse_errors = structure.get("parse_errors")
    if not isinstance(parse_errors, list):
        raise ExplorerInputError("PowerShell AST parser omitted parse errors")
    if parse_errors:
        return [
            {
                "path": _SUPERVISOR,
                "file_sha256": file_sha256,
                "size_bytes": size_bytes,
                "parse_status": "parse-error",
                "parse_errors": parse_errors,
                "entries": [],
                "script_bindings": [],
                "dot_sources": [],
            }
        ]
    raw_entries = structure.get("entries")
    raw_bindings = structure.get("bindings")
    raw_dot_sources = structure.get("dot_sources")
    if not all(isinstance(value, list) for value in (raw_entries, raw_bindings, raw_dot_sources)):
        raise ExplorerInputError("PowerShell AST parser omitted source fact collections")

    entries = []
    for record in raw_entries:
        symbol = str(record["symbol"])
        if symbol not in _SUPERVISOR_ENTRIES:
            continue
        entries.append(
            {
                "kind": "function_entry",
                "symbol": symbol,
                "resolution": "known",
                "evidence": _powershell_evidence(
                    record,
                    file_sha256=file_sha256,
                    evidence_kind="powershell_function_entry",
                    symbol=symbol,
                    structural_anchor=f"powershell:function:{symbol}",
                    syntax_position=f"supervisor/function:{symbol}",
                ),
            }
        )

    bindings = []
    bindings_by_variable: dict[str, list[dict[str, Any]]] = {}
    for record in raw_bindings:
        variable = str(record["variable"])
        candidate_paths = sorted(
            {str(item).replace("\\", "/") for item in record.get("candidate_paths", [])}
        )
        resolution = "known" if record.get("resolution") == "known" else "unknown"
        target_path = (
            str(record["target_path"]).replace("\\", "/")
            if resolution == "known" and isinstance(record.get("target_path"), str)
            else None
        )
        fact = {
            "kind": "script_binding",
            "variable": variable,
            "resolution": resolution,
            "target_path": target_path,
            "candidate_paths": candidate_paths,
            "evidence": _powershell_evidence(
                record,
                file_sha256=file_sha256,
                evidence_kind="powershell_script_binding",
                symbol=f"${variable}",
                structural_anchor=f"powershell:assignment:${variable}",
                syntax_position=f"supervisor/assignment:${variable}",
            ),
        }
        bindings.append(fact)
        bindings_by_variable.setdefault(variable, []).append(fact)

    dot_sources = []
    for record in raw_dot_sources:
        variable = record.get("variable")
        matching_bindings = (
            bindings_by_variable.get(variable, []) if isinstance(variable, str) else []
        )
        binding = matching_bindings[0] if len(matching_bindings) == 1 else None
        resolution = "known" if binding and binding["resolution"] == "known" else "unknown"
        dot_sources.append(
            {
                "kind": "dot_source",
                "symbol": f"${variable}" if isinstance(variable, str) else "unknown",
                "resolution": resolution,
                "target_path": binding["target_path"] if resolution == "known" else None,
                "evidence": _powershell_evidence(
                    record,
                    file_sha256=file_sha256,
                    evidence_kind="powershell_dot_source",
                    symbol=f"${variable}" if isinstance(variable, str) else "unknown",
                    structural_anchor="powershell:dot-source",
                    syntax_position="supervisor/dot-source",
                ),
            }
        )

    return [
        {
            "path": _SUPERVISOR,
            "file_sha256": file_sha256,
            "size_bytes": size_bytes,
            "parse_status": "parsed",
            "parse_errors": [],
            "entries": sorted(entries, key=lambda item: item["symbol"]),
            "script_bindings": sorted(bindings, key=lambda item: item["variable"]),
            "dot_sources": dot_sources,
        }
    ]


def scan_python_source_facts(repository: Path) -> dict[str, Any]:
    """Collect shallow static facts without importing or executing scanned files."""

    try:
        repository = repository.resolve(strict=True)
    except FileNotFoundError as error:
        raise ExplorerInputError(f"Source repository is missing: {repository}") from error
    if not repository.is_dir():
        raise ExplorerInputError(f"Source repository is not a directory: {repository}")
    paths = _tracked_python_paths(repository)
    modules = [_scan_module(repository, relative) for relative in paths]
    powershell_scripts = _scan_supervisor(repository)
    porcelain = _git(repository, "status", "--porcelain=v1", "--untracked-files=normal").rstrip(
        "\r\n"
    )
    result = {
        "schema": SOURCE_FACTS_SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "source_snapshot": {
            "revision": _git(repository, "rev-parse", "HEAD").strip(),
            "worktree": {
                "state": "dirty" if porcelain else "clean",
                "porcelain": porcelain.splitlines(),
            },
        },
        "scope": {
            "selection": "Git-tracked first-party Python files under the declared roots",
            "roots": list(SOURCE_ROOTS),
            "excluded_prefixes": list(EXCLUDED_PREFIXES),
            "files_scanned": len(modules),
        },
        "modules": modules,
        "powershell_scope": {
            "selection": "The single production Supervisor script",
            "paths": [_SUPERVISOR],
            "files_scanned": len(powershell_scripts),
        },
        "powershell_scripts": powershell_scripts,
        "limitations": list(LIMITATIONS),
    }
    result["deterministic_facts_sha256"] = _sha256(_canonical_bytes(result))
    return result


def attach_python_source_facts(
    explorer: dict[str, Any],
    source_facts: dict[str, Any],
) -> dict[str, Any]:
    """Bind independently hashed source facts to an Explorer payload."""

    if source_facts.get("schema") != SOURCE_FACTS_SCHEMA:
        raise ExplorerInputError("Unsupported Python source facts schema")
    claimed_digest = source_facts.get("deterministic_facts_sha256")
    digest_input = {
        key: value
        for key, value in source_facts.items()
        if key != "deterministic_facts_sha256"
    }
    if not isinstance(claimed_digest, str) or claimed_digest != _sha256(
        _canonical_bytes(digest_input)
    ):
        raise ExplorerInputError("Python source facts hash does not match its contents")
    explorer_digest = explorer.get("deterministic_content_sha256")
    if not isinstance(explorer_digest, str):
        raise ExplorerInputError("Explorer payload is missing its deterministic content hash")
    result = deepcopy(explorer)
    result["source_facts"] = deepcopy(source_facts)
    result["deterministic_content_sha256"] = _sha256(
        _canonical_bytes(
            {
                "explorer_content_sha256": explorer_digest,
                "python_source_facts_sha256": claimed_digest,
            }
        )
    )
    return result


__all__ = [
    "EXCLUDED_PREFIXES",
    "SCANNER_VERSION",
    "SOURCE_FACTS_SCHEMA",
    "SOURCE_ROOTS",
    "attach_python_source_facts",
    "scan_python_source_facts",
]
