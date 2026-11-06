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


SCANNER_VERSION = "architecture-explorer-t6-4/1.5"
SOURCE_FACTS_SCHEMA = "architecture-explorer.python-source-facts.v1"
VERIFIED_SOURCE_SCHEMA = "architecture-explorer.verified-source.v1"
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
    "PowerShell known facts require an exact verified Supervisor source SHA-256 match.",
    "Dynamic or ambiguous PowerShell paths remain unknown and do not prove script execution or architecture collaboration.",
)
_WORKER_WRAPPER = "scripts/production/run_qwen_deterministic_worker.py"
_SUPERVISOR = "scripts/production/production_campaign_supervisor.ps1"
_SUPERVISOR_VERIFICATION = (
    "architecture-explorer/verified-sources/production-campaign-supervisor.v1.json"
)
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

function Test-T6ScriptScope($node, $root) {
    $ancestor = $node.Parent
    while ($null -ne $ancestor) {
        if ($ancestor -is [System.Management.Automation.Language.ScriptBlockAst]) {
            return [object]::ReferenceEquals($ancestor, $root)
        }
        $ancestor = $ancestor.Parent
    }
    return $false
}

function Test-T6ConditionalContext($node) {
    $conditionalTypes = @(
        'IfStatementAst',
        'SwitchStatementAst',
        'ForEachStatementAst',
        'ForStatementAst',
        'WhileStatementAst',
        'DoWhileStatementAst',
        'DoUntilStatementAst',
        'TryStatementAst',
        'TrapStatementAst'
    )
    $ancestor = $node.Parent
    while ($null -ne $ancestor) {
        if ($conditionalTypes -contains $ancestor.GetType().Name) { return $true }
        $ancestor = $ancestor.Parent
    }
    return $false
}

function Convert-T6VariableIdentity($node) {
    $path = $node.VariablePath
    $userPath = $path.UserPath
    $separator = $userPath.IndexOf(':')
    $name = if ($separator -ge 0) { $userPath.Substring($separator + 1) } else { $userPath }
    $supported = $path.IsUnqualified -or $path.IsScript -or $path.IsLocal -or $path.IsPrivate
    [pscustomobject][ordered]@{
        name = $name
        scope = $(if ($supported) { 'script' } else { 'unsupported' })
        supported = [bool]$supported
    }
}

function Get-T6CommandArgument($elements, [string]$parameterName, [int]$position) {
    for ($index = 1; $index -lt $elements.Count; $index++) {
        $element = $elements[$index]
        if (
            $element -is [System.Management.Automation.Language.CommandParameterAst] -and
            $element.ParameterName -eq $parameterName
        ) {
            if ($null -ne $element.Argument) { return $element.Argument }
            if ($index + 1 -lt $elements.Count) { return $elements[$index + 1] }
            return $null
        }
    }
    if (
        $position -ge 1 -and
        $position -lt $elements.Count -and
        -not ($elements[$position] -is [System.Management.Automation.Language.CommandParameterAst])
    ) {
        return $elements[$position]
    }
    return $null
}

function Test-T6OpaqueSplat($elements) {
    foreach ($element in $elements) {
        if (
            $element -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $element.Splatted
        ) {
            return $true
        }
    }
    return $false
}

function Test-T6CommandAffectsScriptScope($node, $scopeNode, $root) {
    $rootScope = Test-T6ScriptScope $node $root
    $explicitScope = if (
        $scopeNode -is [System.Management.Automation.Language.StringConstantExpressionAst]
    ) { $scopeNode.Value } else { $null }
    if ($rootScope) {
        return $null -eq $scopeNode -or
            $null -eq $explicitScope -or
            $explicitScope -in @('Script', 'Local', 'Private')
    }
    return $explicitScope -eq 'Script' -or
        ($null -ne $scopeNode -and $null -eq $explicitScope)
}

function Test-T6CommandAffectsCommandLookup($node, $scopeNode, $root) {
    if (
        $scopeNode -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $scopeNode.Value -eq 'Global'
    ) {
        return $true
    }
    return Test-T6CommandAffectsScriptScope $node $scopeNode $root
}

function New-T6ExtentHazard($node, [bool]$sourceOrderKnown) {
    [pscustomobject][ordered]@{
        source_order_known = $sourceOrderKnown
        start_offset = $node.Extent.StartOffset
        text = $node.Extent.Text
        line_start = $node.Extent.StartLineNumber
        line_end = $node.Extent.EndLineNumber
    }
}

function New-T6WriteHazard($variableNode, [string]$operator, $node, [bool]$sourceOrderKnown) {
    $identity = Convert-T6VariableIdentity $variableNode
    [pscustomobject][ordered]@{
        variable = $variableNode.VariablePath.UserPath
        variable_name = $identity.name
        variable_scope = $identity.scope
        operator = $operator
        source_order_known = $sourceOrderKnown
        resolution = 'unknown'
        target_path = $null
        candidate_paths = @()
        start_offset = $node.Extent.StartOffset
        text = $node.Extent.Text
        line_start = $node.Extent.StartLineNumber
        line_end = $node.Extent.EndLineNumber
    }
}

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
    write_hazards = @()
    global_write_hazards = @()
    runtime_value_hazards = @()
    command_resolution_hazards = @()
    dot_sources = @()
}
if (@($parseErrors).Count -eq 0) {
    $functions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $entryNames -contains $node.Name
    }, $true))
    foreach ($node in $functions) {
        $known = (Test-T6ScriptScope $node $ast) -and
            -not (Test-T6ConditionalContext $node)
        $result.entries += [pscustomobject][ordered]@{
            symbol = $node.Name
            resolution = $(if ($known) { 'known' } else { 'unknown' })
            start_offset = $node.Extent.StartOffset
            text = $node.Extent.Text
            line_start = $node.Extent.StartLineNumber
            line_end = $node.Extent.EndLineNumber
        }
    }

    $joinPathFunctions = @($ast.FindAll({
        param($node)
        if (-not ($node -is [System.Management.Automation.Language.FunctionDefinitionAst])) {
            return $false
        }
        $name = $node.Name
        $separator = $name.IndexOf(':')
        $baseName = if ($separator -ge 0) { $name.Substring($separator + 1) } else { $name }
        return $baseName -eq 'Join-Path'
    }, $true))
    foreach ($node in $joinPathFunctions) {
        $rootScope = Test-T6ScriptScope $node $ast
        $scriptQualified = $node.Name -like 'script:*'
        $globalQualified = $node.Name -like 'global:*'
        if (-not $rootScope -and -not $scriptQualified -and -not $globalQualified) { continue }
        $result.command_resolution_hazards += New-T6ExtentHazard $node $rootScope
    }

    $aliasCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -in @(
            'Set-Alias', 'New-Alias', 'Remove-Alias', 'sal', 'nal', 'ral'
        )
    }, $true))
    foreach ($node in $aliasCommands) {
        $elements = @($node.CommandElements)
        $nameNode = Get-T6CommandArgument $elements 'Name' 1
        $scopeNode = Get-T6CommandArgument $elements 'Scope' -1
        $rootScope = Test-T6ScriptScope $node $ast
        if (Test-T6OpaqueSplat $elements) {
            $result.command_resolution_hazards += New-T6ExtentHazard $node $rootScope
            continue
        }
        if (-not (Test-T6CommandAffectsCommandLookup $node $scopeNode $ast)) { continue }
        $staticName = $nameNode -is [System.Management.Automation.Language.StringConstantExpressionAst]
        if ($staticName -and $nameNode.Value -ne 'Join-Path') { continue }
        $result.command_resolution_hazards += New-T6ExtentHazard $node $rootScope
    }

    $providerMutationCommands = @($ast.FindAll({
        param($node)
        if (-not ($node -is [System.Management.Automation.Language.CommandAst])) {
            return $false
        }
        if ($node.GetCommandName() -notin @(
            'Set-Item', 'New-Item', 'Remove-Item', 'Rename-Item',
            'Move-Item', 'Copy-Item', 'si', 'ni', 'ri', 'rni', 'mi', 'cpi'
        )) {
            return $false
        }
        if (Test-T6OpaqueSplat @($node.CommandElements)) { return $true }
        if (Test-T6ScriptScope $node $ast) { return $true }
        foreach ($element in @($node.CommandElements)) {
            if (
                $element -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                $element.Value -match '^(function|alias):[\\/]*(global:|script:)?join-path$'
            ) {
                return $true
            }
        }
        return $false
    }, $true))
    foreach ($node in $providerMutationCommands) {
        $rootScope = Test-T6ScriptScope $node $ast
        $result.command_resolution_hazards += New-T6ExtentHazard $node $rootScope
    }

    $sessionMutationCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -in @('Import-Module', 'ipmo', 'Invoke-Expression', 'iex')
    }, $true))
    foreach ($node in $sessionMutationCommands) {
        $rootScope = Test-T6ScriptScope $node $ast
        $result.command_resolution_hazards += New-T6ExtentHazard $node $rootScope
        if ($node.GetCommandName() -in @('Invoke-Expression', 'iex')) {
            $result.runtime_value_hazards += New-T6ExtentHazard $node $rootScope
        }
    }

    $assignments = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]
    }, $true))
    foreach ($node in $assignments) {
        $identity = Convert-T6VariableIdentity $node.Left
        $rootScope = Test-T6ScriptScope $node $ast
        if (-not $rootScope -and -not $node.Left.VariablePath.IsScript) { continue }
        $conditional = Test-T6ConditionalContext $node
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
        $known = $joins.Count -eq 1 -and
            $candidates.Count -eq 1 -and
            $node.Operator -eq [System.Management.Automation.Language.TokenKind]::Equals -and
            $identity.supported -and
            $rootScope -and
            $node.Right.Extent.Text.Trim() -eq $joins[0].Extent.Text.Trim() -and
            -not $conditional
        $result.bindings += [pscustomobject][ordered]@{
            variable = $node.Left.VariablePath.UserPath
            variable_name = $identity.name
            variable_scope = $identity.scope
            operator = [string]$node.Operator
            source_order_known = [bool]$rootScope
            resolution = $(if ($known) { 'known' } else { 'unknown' })
            target_path = $(if ($known) { $candidates[0] } else { $null })
            candidate_paths = @($candidates)
            start_offset = $node.Extent.StartOffset
            text = $node.Extent.Text
            line_start = $node.Extent.StartLineNumber
            line_end = $node.Extent.EndLineNumber
        }
    }

    $destructuringAssignments = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [System.Management.Automation.Language.ArrayLiteralAst]
    }, $true))
    foreach ($node in $destructuringAssignments) {
        $rootScope = Test-T6ScriptScope $node $ast
        foreach ($variableNode in @($node.Left.Elements)) {
            if (-not ($variableNode -is [System.Management.Automation.Language.VariableExpressionAst])) {
                continue
            }
            if (-not $rootScope -and -not $variableNode.VariablePath.IsScript) { continue }
            $result.write_hazards += New-T6WriteHazard `
                $variableNode 'Destructuring' $node $rootScope
        }
    }

    $foreachStatements = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.ForEachStatementAst]
    }, $true))
    foreach ($node in $foreachStatements) {
        $variableNode = $node.Variable
        $rootScope = Test-T6ScriptScope $node $ast
        if (-not $rootScope -and -not $variableNode.VariablePath.IsScript) { continue }
        $result.write_hazards += New-T6WriteHazard $variableNode 'ForEach' $node $rootScope
    }

    $setVariableCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -in @('Set-Variable', 'sv')
    }, $true))
    foreach ($node in $setVariableCommands) {
        $elements = @($node.CommandElements)
        $nameNode = Get-T6CommandArgument $elements 'Name' 1
        $scopeNode = Get-T6CommandArgument $elements 'Scope' -1
        $rootScope = Test-T6ScriptScope $node $ast
        if (Test-T6OpaqueSplat $elements) {
            $result.global_write_hazards += New-T6ExtentHazard $node $rootScope
            continue
        }
        if (-not (Test-T6CommandAffectsScriptScope $node $scopeNode $ast)) { continue }
        if (-not ($nameNode -is [System.Management.Automation.Language.StringConstantExpressionAst])) {
            $result.global_write_hazards += New-T6ExtentHazard $node $rootScope
            continue
        }
        if ($nameNode.Value.IndexOfAny([char[]]'*?[') -ge 0) {
            $result.global_write_hazards += New-T6ExtentHazard $node $rootScope
            continue
        }
        $userPath = $nameNode.Value.TrimStart('$')
        $separator = $userPath.IndexOf(':')
        $variableName = if ($separator -ge 0) {
            $userPath.Substring($separator + 1)
        } else {
            $userPath
        }
        $result.write_hazards += [pscustomobject][ordered]@{
            variable = $userPath
            variable_name = $variableName
            variable_scope = 'script'
            operator = 'SetVariable'
            source_order_known = [bool]$rootScope
            resolution = 'unknown'
            target_path = $null
            candidate_paths = @()
            start_offset = $node.Extent.StartOffset
            text = $node.Extent.Text
            line_start = $node.Extent.StartLineNumber
            line_end = $node.Extent.EndLineNumber
        }
    }

    $dotCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Dot
    }, $true))
    foreach ($node in $dotCommands) {
        if (-not (Test-T6ScriptScope $node $ast)) { continue }
        $result.command_resolution_hazards += New-T6ExtentHazard $node $true
        $result.runtime_value_hazards += New-T6ExtentHazard $node $true
        $elements = @($node.CommandElements)
        $variable = $null
        $identity = $null
        if (
            $elements.Count -eq 1 -and
            $elements[0] -is [System.Management.Automation.Language.VariableExpressionAst]
        ) {
            $variable = $elements[0].VariablePath.UserPath
            $identity = Convert-T6VariableIdentity $elements[0]
        }
        $result.dot_sources += [pscustomobject][ordered]@{
            variable = $variable
            variable_name = $(if ($null -ne $identity) { $identity.name } else { $null })
            variable_scope = $(if ($null -ne $identity) { $identity.scope } else { 'unsupported' })
            start_offset = $node.Extent.StartOffset
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


def _supervisor_source_version(
    repository: Path,
    actual_sha256: str,
) -> dict[str, str]:
    text, manifest_sha256, _ = _read_stable_utf8(
        repository,
        _SUPERVISOR_VERIFICATION,
    )
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExplorerInputError("Supervisor source verification manifest is invalid JSON") from error
    if not isinstance(manifest, dict):
        raise ExplorerInputError("Supervisor source verification manifest must be an object")
    expected_sha256 = manifest.get("sha256")
    if (
        manifest.get("schema") != VERIFIED_SOURCE_SCHEMA
        or manifest.get("path") != _SUPERVISOR
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ExplorerInputError("Supervisor source verification manifest is invalid")
    return {
        "status": "verified" if actual_sha256 == expected_sha256 else "mismatch",
        "verification_path": _SUPERVISOR_VERIFICATION,
        "verification_manifest_sha256": manifest_sha256,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
    }


def _scan_supervisor(repository: Path) -> list[dict[str, Any]]:
    tracked = _git(repository, "ls-files", "--", _SUPERVISOR).splitlines()
    if _SUPERVISOR not in {item.replace("\\", "/") for item in tracked}:
        return []
    text, file_sha256, size_bytes = _read_stable_utf8(repository, _SUPERVISOR)
    source_version = _supervisor_source_version(repository, file_sha256)
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
                "source_version": source_version,
                "entries": [],
                "script_bindings": [],
                "dot_sources": [],
            }
        ]
    raw_entries = structure.get("entries")
    raw_bindings = structure.get("bindings")
    raw_write_hazards = structure.get("write_hazards")
    raw_global_write_hazards = structure.get("global_write_hazards")
    raw_runtime_value_hazards = structure.get("runtime_value_hazards")
    raw_command_resolution_hazards = structure.get("command_resolution_hazards")
    raw_dot_sources = structure.get("dot_sources")
    if not all(
        isinstance(value, list)
        for value in (
            raw_entries,
            raw_bindings,
            raw_write_hazards,
            raw_global_write_hazards,
            raw_runtime_value_hazards,
            raw_command_resolution_hazards,
            raw_dot_sources,
        )
    ):
        raise ExplorerInputError("PowerShell AST parser omitted source fact collections")
    raw_bindings = [*raw_bindings, *raw_write_hazards]

    entry_names = {name.casefold() for name in _SUPERVISOR_ENTRIES}
    entries = []
    entries_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in raw_entries:
        symbol = str(record["symbol"])
        symbol_key = symbol.casefold()
        if symbol_key not in entry_names:
            continue
        fact = {
            "kind": "function_entry",
            "symbol": symbol,
            "resolution": (
                "known" if record.get("resolution") == "known" else "unknown"
            ),
            "source_order": int(record.get("start_offset", 0)),
            "evidence": _powershell_evidence(
                record,
                file_sha256=file_sha256,
                evidence_kind="powershell_function_entry",
                symbol=symbol,
                structural_anchor=f"powershell:function:{symbol}",
                syntax_position=f"supervisor/function:{symbol}",
            ),
        }
        entries.append(fact)
        entries_by_symbol.setdefault(symbol_key, []).append(fact)

    for matching_entries in entries_by_symbol.values():
        if len(matching_entries) > 1:
            for entry in matching_entries:
                entry["resolution"] = "unknown"

    def variable_key(record: dict[str, Any]) -> str | None:
        name = record.get("variable_name")
        scope = record.get("variable_scope")
        if not isinstance(name, str) or not isinstance(scope, str):
            return None
        return f"{scope}:{name.casefold()}"

    relevant_variable_keys = {
        key
        for record in raw_dot_sources
        if (key := variable_key(record)) is not None
    }
    relevant_variable_keys.update(
        key
        for record in raw_bindings
        if record.get("candidate_paths") and (key := variable_key(record)) is not None
    )

    bindings = []
    bindings_by_variable: dict[str, list[dict[str, Any]]] = {}
    for record in raw_bindings:
        record_key = variable_key(record)
        if record_key not in relevant_variable_keys:
            continue
        variable = str(record["variable"])
        variable_name = str(record.get("variable_name", variable))
        variable_scope = str(record.get("variable_scope", "unsupported"))
        candidate_paths = sorted(
            {str(item).replace("\\", "/") for item in record.get("candidate_paths", [])}
        )
        source_order = int(record.get("start_offset", 0))
        prior_command_hazards = [
            hazard
            for hazard in raw_command_resolution_hazards
            if hazard.get("source_order_known") is not True
            or int(hazard.get("start_offset", 0)) < source_order
        ]
        resolution = (
            "known"
            if record.get("resolution") == "known" and not prior_command_hazards
            else "unknown"
        )
        target_path = (
            str(record["target_path"]).replace("\\", "/")
            if resolution == "known" and isinstance(record.get("target_path"), str)
            else None
        )
        fact = {
            "kind": "script_binding",
            "variable": variable,
            "variable_name": variable_name,
            "variable_scope": variable_scope,
            "operator": str(record.get("operator", "")),
            "source_order": source_order,
            "source_order_known": record.get("source_order_known") is True,
            "resolution": resolution,
            "target_path": target_path,
            "candidate_paths": candidate_paths,
            "_statement_known": resolution == "known",
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
        bindings_by_variable.setdefault(record_key, []).append(fact)

    dot_sources = []
    for record in raw_dot_sources:
        variable = record.get("variable")
        variable_name = record.get("variable_name")
        variable_scope = record.get("variable_scope")
        dot_variable_key = (
            f"{variable_scope}:{variable_name.casefold()}"
            if isinstance(variable_scope, str) and isinstance(variable_name, str)
            else None
        )
        source_order = int(record.get("start_offset", 0))
        matching_bindings = (
            bindings_by_variable.get(dot_variable_key, [])
            if dot_variable_key is not None and variable_scope == "script"
            else []
        )
        prior_bindings = [
            binding
            for binding in matching_bindings
            if not binding["source_order_known"]
            or binding["source_order"] < source_order
        ]
        prior_global_hazards = [
            hazard
            for hazard in raw_global_write_hazards
            if hazard.get("source_order_known") is not True
            or int(hazard.get("start_offset", 0)) < source_order
        ]
        prior_runtime_value_hazards = [
            hazard
            for hazard in raw_runtime_value_hazards
            if hazard.get("source_order_known") is not True
            or int(hazard.get("start_offset", 0)) < source_order
        ]
        binding = prior_bindings[0] if len(prior_bindings) == 1 else None
        resolution = (
            "known"
            if binding
            and binding["_statement_known"]
            and not prior_global_hazards
            and not prior_runtime_value_hazards
            else "unknown"
        )
        dot_sources.append(
            {
                "kind": "dot_source",
                "symbol": f"${variable}" if isinstance(variable, str) else "unknown",
                "source_order": source_order,
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

    for matching_bindings in bindings_by_variable.values():
        if len(matching_bindings) > 1 or raw_global_write_hazards:
            for binding in matching_bindings:
                binding["resolution"] = "unknown"
                binding["target_path"] = None
        for binding in matching_bindings:
            del binding["_statement_known"]

    if source_version["status"] != "verified":
        for fact in [*entries, *bindings, *dot_sources]:
            fact["resolution"] = "unknown"
            if "target_path" in fact:
                fact["target_path"] = None

    return [
        {
            "path": _SUPERVISOR,
            "file_sha256": file_sha256,
            "size_bytes": size_bytes,
            "parse_status": "parsed",
            "parse_errors": [],
            "source_version": source_version,
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
