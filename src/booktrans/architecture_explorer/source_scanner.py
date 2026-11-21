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
DEFINITION_INDEX_SCHEMA = "architecture-explorer.python-definition-index.v1"
CALL_FACTS_SCHEMA = "architecture-explorer.python-call-facts.v1"
CALL_FACT_PATHS = (
    "src/booktrans/production_worker/pipeline.py",
    "src/booktrans/providers/router.py",
    "src/booktrans/providers/qwen_mt.py",
    "src/booktrans/providers/qwen_max.py",
)
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
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
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


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.records: list[tuple[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, ast.AST | None]] = []
        self._parents: list[ast.AST] = []

    def _record(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.records.append((node, self._parents[-1] if self._parents else None))
        self._parents.append(node)
        self.generic_visit(node)
        self._parents.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node)


def _definition_kind(node: ast.AST, parent: ast.AST | None) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(parent, ast.ClassDef):
        return "method"
    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "nested-function"
    return "function"


def _definition_evidence(
    text: str,
    relative: str,
    file_sha256: str,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    qualified_symbol: str,
    structural_anchor: str,
    syntax_position: str,
) -> dict[str, Any]:
    start = min(node.decorator_list, key=lambda item: (item.lineno, item.col_offset), default=node)
    extent = ast.Constant()
    extent.lineno = start.lineno
    extent.col_offset = (
        max(0, start.col_offset - 1) if node.decorator_list else start.col_offset
    )
    extent.end_lineno = node.end_lineno or node.lineno
    extent.end_col_offset = node.end_col_offset if node.end_col_offset is not None else node.col_offset
    evidence = build_static_evidence(
        path=relative,
        source_role="automatic_source_fact",
        file_sha256=file_sha256,
        evidence_kind="python_definition",
        symbol=qualified_symbol,
        structural_anchor=structural_anchor,
        syntax_position=syntax_position,
        snippet=_snippet(text, extent),
        line_start=extent.lineno,
        line_end=extent.end_lineno,
    )
    evidence["current_location"] = {
        "line_start": extent.lineno,
        "column_start": extent.col_offset,
        "line_end": extent.end_lineno,
        "column_end": extent.end_col_offset,
    }
    return evidence


def _definitions(
    tree: ast.Module,
    text: str,
    relative: str,
    module_id: str,
    file_sha256: str,
) -> list[dict[str, Any]]:
    collector = _DefinitionCollector()
    collector.visit(tree)
    by_parent_and_name: dict[tuple[int | None, str], list[ast.AST]] = {}
    for node, parent in collector.records:
        by_parent_and_name.setdefault((id(parent) if parent else None, node.name), []).append(node)

    occurrences: dict[int, tuple[int, int]] = {}
    for siblings in by_parent_and_name.values():
        total = len(siblings)
        for occurrence, node in enumerate(siblings, start=1):
            occurrences[id(node)] = (occurrence, total)

    qualified: dict[int, str] = {}
    lexical_occurrence_paths: dict[int, tuple[str, ...]] = {}
    definitions: list[dict[str, Any]] = []
    for node, parent in collector.records:
        occurrence, total = occurrences[id(node)]
        local_name = node.name
        parent_symbol = qualified[id(parent)] if parent is not None else module_id
        qualified_symbol = f"{parent_symbol}.{local_name}"
        qualified[id(node)] = qualified_symbol
        kind = _definition_kind(node, parent)
        occurrence_segment = f"{occurrence}-of-{total}"
        lexical_occurrence_path = (
            (*lexical_occurrence_paths[id(parent)], occurrence_segment)
            if parent is not None
            else (occurrence_segment,)
        )
        lexical_occurrence_paths[id(node)] = lexical_occurrence_path
        lexical_anchor = "/".join(lexical_occurrence_path)
        structural_anchor = (
            f"python:definition:{kind}:{qualified_symbol}/lexical:{lexical_anchor}"
        )
        syntax_position = (
            f"{parent_symbol}/{kind}:{local_name}/lexical:{lexical_anchor}"
        )
        evidence = _definition_evidence(
            text,
            relative,
            file_sha256,
            node,
            qualified_symbol=qualified_symbol,
            structural_anchor=structural_anchor,
            syntax_position=syntax_position,
        )
        definitions.append(
            {
                "kind": kind,
                "qualified_symbol": qualified_symbol,
                "parent_symbol": parent_symbol,
                "occurrence": occurrence,
                "lexical_occurrence_path": list(lexical_occurrence_path),
                "evidence": evidence,
            }
        )
    return definitions


class _CallCollector(ast.NodeVisitor):
    def __init__(self, definition_nodes: set[int]) -> None:
        self.definition_nodes = definition_nodes
        self.definition_stack: list[ast.AST] = []
        self.lookup_stack: list[ast.AST] = []
        self.records: list[tuple[ast.Call, ast.AST, tuple[ast.AST, ...]]] = []

    def _visit_function_definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if id(node) not in self.definition_nodes:
            self.generic_visit(node)
            return
        self.definition_stack.append(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for argument in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None and argument.annotation is not None:
                self.visit(argument.annotation)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        self.lookup_stack.append(node)
        for statement in node.body:
            self.visit(statement)
        self.lookup_stack.pop()
        self.definition_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if id(node) not in self.definition_nodes:
            self.generic_visit(node)
            return
        self.definition_stack.append(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        self.lookup_stack.append(node)
        for statement in node.body:
            self.visit(statement)
        self.lookup_stack.pop()
        self.definition_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.definition_stack:
            self.records.append(
                (node, self.definition_stack[-1], tuple(self.lookup_stack))
            )
        self.generic_visit(node)


class _ScopeBindings(ast.NodeVisitor):
    """Collect names which make a direct-name resolution unsafe in one scope."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.has_star_import = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.names.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.has_star_import = True
            else:
                self.names.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)


def _function_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    collector = _ScopeBindings()
    for argument in [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]:
        collector.visit(argument)
    if node.args.vararg is not None:
        collector.visit(node.args.vararg)
    if node.args.kwarg is not None:
        collector.visit(node.args.kwarg)
    for statement in node.body:
        collector.visit(statement)
    return collector.names


def _class_bindings(node: ast.ClassDef) -> set[str]:
    collector = _ScopeBindings()
    for statement in node.body:
        collector.visit(statement)
    return collector.names


def _lookup_bindings(
    scope_stack: tuple[ast.AST, ...],
    bindings_by_scope: dict[int, set[str]],
) -> set[str]:
    if not scope_stack:
        return set()
    innermost = scope_stack[-1]
    lookup_scopes = [
        scope
        for scope in scope_stack
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if isinstance(innermost, ast.ClassDef):
        lookup_scopes.append(innermost)
    return {
        name
        for scope in lookup_scopes
        for name in bindings_by_scope[id(scope)]
    }


def _module_bindings(tree: ast.Module, module_id: str) -> set[str]:
    collector = _ScopeBindings()
    for statement in tree.body:
        if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(statement, ast.ImportFrom):
            imported_module = _absolute_import_module(
                module_id,
                statement.module or "",
                statement.level,
            )
            if imported_module is not None and imported_module.startswith("booktrans"):
                continue
        collector.visit(statement)
    return collector.names


def _absolute_import_module(module_id: str, imported: str, level: int) -> str | None:
    if level == 0:
        return imported
    package = module_id.rsplit(".", 1)[0]
    parts = package.split(".") if package else []
    if level > len(parts):
        return None
    prefix = parts[: len(parts) - level + 1]
    if imported:
        prefix.extend(imported.split("."))
    return ".".join(prefix)


def _explicit_import_bindings(tree: ast.Module, module_id: str) -> tuple[dict[str, list[tuple[str, str]]], bool]:
    bindings: dict[str, list[tuple[str, str]]] = {}
    module_scope = _ScopeBindings()
    for statement in tree.body:
        if not isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            module_scope.visit(statement)
    has_star_import = module_scope.has_star_import
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom):
            continue
        imported_module = _absolute_import_module(module_id, statement.module or "", statement.level)
        for alias in statement.names:
            if alias.name == "*":
                has_star_import = True
                continue
            if imported_module is None or not imported_module.startswith("booktrans"):
                continue
            bindings.setdefault(alias.asname or alias.name, []).append(
                (imported_module, alias.name)
            )
    return bindings, has_star_import


def _direct_name_resolution(
    name: str,
    *,
    definitions_by_symbol: dict[str, list[dict[str, Any]]],
    local_definitions: dict[str, list[dict[str, Any]]],
    imports: dict[str, list[tuple[str, str]]],
    module_bindings: set[str],
    has_star_import: bool,
    enclosing_bindings: set[str],
) -> tuple[str, str, str | None]:
    if name in enclosing_bindings:
        return "dynamic-or-ambiguous", "name-is-parameter-or-rebound-in-enclosing-scope", None
    if has_star_import:
        return "dynamic-or-ambiguous", "star-import-makes-name-resolution-ambiguous", None
    local_matches = local_definitions.get(name, [])
    imported_matches = imports.get(name, [])
    if len(local_matches) == 1 and not imported_matches and name not in module_bindings:
        return (
            "exact-local",
            "unique-unshadowed-same-module-definition",
            local_matches[0]["evidence"]["evidence_id"],
        )
    if local_matches:
        return "dynamic-or-ambiguous", "local-definition-binding-is-not-unique", None
    if len(imported_matches) == 1 and name not in module_bindings:
        imported_module, imported_name = imported_matches[0]
        targets = definitions_by_symbol.get(f"{imported_module}.{imported_name}", [])
        if len(targets) == 1:
            return (
                "exact-import",
                "unique-unshadowed-explicit-first-party-import",
                targets[0]["evidence"]["evidence_id"],
            )
        return "dynamic-or-ambiguous", "imported-definition-target-is-not-unique", None
    if imported_matches:
        return "dynamic-or-ambiguous", "import-binding-is-rebound-or-ambiguous", None
    return "dynamic-or-ambiguous", "no-unique-static-definition-binding", None


def _call_resolution(
    call: ast.Call,
    enclosing: ast.AST,
    *,
    module_id: str,
    definitions_by_symbol: dict[str, list[dict[str, Any]]],
    local_definitions: dict[str, list[dict[str, Any]]],
    imports: dict[str, list[tuple[str, str]]],
    module_bindings: set[str],
    has_star_import: bool,
    enclosing_bindings: set[str],
) -> tuple[str, str, str | None]:
    del enclosing, module_id
    function = call.func
    if isinstance(function, ast.Attribute):
        return "unresolved-dispatch", "runtime-receiver-attribute-dispatch", None
    if not isinstance(function, ast.Name):
        return "dynamic-or-ambiguous", "dynamic-or-unsupported-callee-shape", None
    return _direct_name_resolution(
        function.id,
        definitions_by_symbol=definitions_by_symbol,
        local_definitions=local_definitions,
        imports=imports,
        module_bindings=module_bindings,
        has_star_import=has_star_import,
        enclosing_bindings=enclosing_bindings,
    )


def _projected_import_bindings(
    module: dict[str, Any],
) -> tuple[dict[str, list[tuple[str, str]]], set[str], bool]:
    imports: dict[str, list[tuple[str, str]]] = {}
    module_bindings: set[str] = set()
    has_star_import = False
    module_id = module.get("module_id")
    direct_imports = module.get("direct_imports")
    if not isinstance(module_id, str) or not isinstance(direct_imports, list):
        raise ExplorerInputError("invalid Python call facts import bindings")
    for direct_import in direct_imports:
        if not isinstance(direct_import, dict) or not isinstance(direct_import.get("names"), list):
            raise ExplorerInputError("invalid Python call facts import bindings")
        kind = direct_import.get("kind")
        names = direct_import["names"]
        if not all(isinstance(name, str) and name for name in names):
            raise ExplorerInputError("invalid Python call facts import bindings")
        if kind == "import":
            for name in names:
                imported_name, separator, alias = name.partition(" as ")
                module_bindings.add(alias if separator else imported_name.split(".", 1)[0])
            continue
        if kind != "from" or not isinstance(direct_import.get("module"), str) or not isinstance(direct_import.get("level"), int):
            raise ExplorerInputError("invalid Python call facts import bindings")
        imported_module = _absolute_import_module(
            module_id,
            direct_import["module"],
            direct_import["level"],
        )
        for name in names:
            imported_name, separator, alias = name.partition(" as ")
            if imported_name == "*":
                has_star_import = True
            elif imported_module is not None and imported_module.startswith("booktrans"):
                imports.setdefault(alias if separator else imported_name, []).append(
                    (imported_module, imported_name)
                )
            else:
                module_bindings.add(alias if separator else imported_name)
    return imports, module_bindings, has_star_import


def _call_evidence(
    text: str,
    relative: str,
    file_sha256: str,
    node: ast.Call,
    *,
    callee: str,
    structural_anchor: str,
    syntax_position: str,
) -> dict[str, Any]:
    evidence = build_static_evidence(
        path=relative,
        source_role="automatic_source_fact",
        file_sha256=file_sha256,
        evidence_kind="python_call",
        symbol=callee,
        structural_anchor=structural_anchor,
        syntax_position=syntax_position,
        snippet=_snippet(text, node),
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
    )
    evidence["current_location"] = {
        "line_start": node.lineno,
        "column_start": node.col_offset,
        "line_end": node.end_lineno or node.lineno,
        "column_end": node.end_col_offset if node.end_col_offset is not None else node.col_offset,
    }
    return evidence


def _module_call_facts(
    module: dict[str, Any],
    definitions_by_symbol: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    tree = module["_tree"]
    text = module["_text"]
    definition_collector = _DefinitionCollector()
    definition_collector.visit(tree)
    definition_nodes = [node for node, _ in definition_collector.records]
    definition_facts = module["definitions"]
    if len(definition_nodes) != len(definition_facts):
        raise ExplorerInputError(f"Python definition/call projection mismatch: {module['path']}")
    fact_by_node = dict(zip(map(id, definition_nodes), definition_facts, strict=True))
    call_collector = _CallCollector(set(fact_by_node))
    call_collector.visit(tree)
    records = sorted(
        call_collector.records,
        key=lambda item: (item[0].lineno, item[0].col_offset, item[0].end_lineno or item[0].lineno),
    )
    call_syntax = {id(call): _snippet(text, call.func) for call, _, _ in records}
    groups: dict[tuple[int, str], list[ast.Call]] = {}
    for call, enclosing, _ in records:
        groups.setdefault((id(enclosing), call_syntax[id(call)]), []).append(call)
    occurrences: dict[int, tuple[int, int]] = {}
    for calls in groups.values():
        total = len(calls)
        for occurrence, call in enumerate(calls, start=1):
            occurrences[id(call)] = (occurrence, total)

    module_id = module["module_id"]
    local_definitions: dict[str, list[dict[str, Any]]] = {}
    for definition in definition_facts:
        if definition["parent_symbol"] == module_id:
            name = definition["qualified_symbol"].rsplit(".", 1)[-1]
            local_definitions.setdefault(name, []).append(definition)
    imports, has_star_import = _explicit_import_bindings(tree, module_id)
    module_bindings = _module_bindings(tree, module_id)
    bindings_by_scope = {
        id(node): (
            _function_bindings(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else _class_bindings(node)
        )
        for node in definition_nodes
    }
    calls: list[dict[str, Any]] = []
    for call, enclosing, lookup_stack in records:
        enclosing_fact = fact_by_node[id(enclosing)]
        callee = call_syntax[id(call)]
        enclosing_bindings = _lookup_bindings(lookup_stack, bindings_by_scope)
        occurrence, total = occurrences[id(call)]
        discriminator = f"{occurrence}-of-{total}"
        structural_anchor = (
            f"python:call:{enclosing_fact['evidence']['evidence_id']}/"
            f"callee:{callee}/occurrence:{discriminator}"
        )
        syntax_position = (
            f"{enclosing_fact['qualified_symbol']}/call:{callee}/occurrence:{discriminator}"
        )
        resolution, reason, target = _call_resolution(
            call,
            enclosing,
            module_id=module_id,
            definitions_by_symbol=definitions_by_symbol,
            local_definitions=local_definitions,
            imports=imports,
            module_bindings=module_bindings,
            has_star_import=has_star_import,
            enclosing_bindings=enclosing_bindings,
        )
        fact = {
            "enclosing_definition_evidence_id": enclosing_fact["evidence"]["evidence_id"],
            "enclosing_bindings": sorted(enclosing_bindings),
            "callee": callee,
            "resolution": resolution,
            "resolution_reason": reason,
            "occurrence": occurrence,
            "occurrence_discriminator": discriminator,
            "evidence": _call_evidence(
                text,
                module["path"],
                module["file_sha256"],
                call,
                callee=callee,
                structural_anchor=structural_anchor,
                syntax_position=syntax_position,
            ),
        }
        if target is not None:
            fact["target_definition_evidence_id"] = target
        calls.append(fact)
    return calls, sorted(module_bindings), has_star_import


def _call_facts(modules: list[dict[str, Any]]) -> dict[str, Any]:
    definitions_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        for definition in module["definitions"]:
            definitions_by_symbol.setdefault(definition["qualified_symbol"], []).append(definition)
    modules_by_path = {module["path"]: module for module in modules}
    files = []
    for path in CALL_FACT_PATHS:
        module = modules_by_path.get(path)
        if module is None:
            continue
        calls, module_bindings, has_star_import = _module_call_facts(
            module,
            definitions_by_symbol,
        )
        files.append(
            {
                "path": path,
                "module_id": module["module_id"],
                "file_sha256": module["file_sha256"],
                "module_bindings": module_bindings,
                "has_star_import": has_star_import,
                "calls": calls,
            }
        )
    result = {
        "schema": CALL_FACTS_SCHEMA,
        "scope": {
            "selection": "The closed T10 translation-execution call-facts allowlist",
            "paths": list(CALL_FACT_PATHS),
            "files_scanned": len(files),
        },
        "files": files,
        "limitations": [
            "Calls are static syntax facts; they do not prove runtime execution.",
            "Exact targets are limited to unique unshadowed local or explicit first-party import bindings.",
            "Runtime receiver dispatch remains unresolved and does not infer Provider implementations.",
        ],
    }
    result["deterministic_facts_sha256"] = _sha256(_canonical_bytes(result))
    return result


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
        "definitions": _definitions(tree, text, relative, module_id, file_sha256),
        "_tree": tree,
        "_text": text,
    }


def _definition_index(modules: list[dict[str, Any]]) -> dict[str, Any]:
    managed_file_manifest = [
        {"path": module["path"], "file_sha256": module["file_sha256"]}
        for module in modules
    ]
    files = [
        {
            "path": module["path"],
            "module_id": module["module_id"],
            "file_sha256": module["file_sha256"],
            "definitions": module["definitions"],
        }
        for module in modules
    ]
    definitions = [definition for file in files for definition in file["definitions"]]
    classes = sum(definition["kind"] == "class" for definition in definitions)
    result = {
        "schema": DEFINITION_INDEX_SCHEMA,
        "scope": {
            "selection": "The managed Python scope inherited from Source Facts",
            "files_scanned": len(files),
            "managed_files_sha256": _sha256(_canonical_bytes(managed_file_manifest)),
        },
        "summary": {
            "classes": classes,
            "functions_and_methods": len(definitions) - classes,
            "definitions": len(definitions),
        },
        "files": files,
        "limitations": [
            "Definitions are static lexical facts; they do not prove runtime execution.",
            "An indexed file is not thereby assigned to an approved architecture module.",
        ],
    }
    result["deterministic_facts_sha256"] = _sha256(_canonical_bytes(result))
    return result


def _validate_definition_index(source_facts: dict[str, Any]) -> None:
    index = source_facts.get("python_definition_index")
    if not isinstance(index, dict) or index.get("schema") != DEFINITION_INDEX_SCHEMA:
        raise ExplorerInputError("invalid Python definition index schema")
    claimed_digest = index.get("deterministic_facts_sha256")
    digest_input = {
        key: value for key, value in index.items() if key != "deterministic_facts_sha256"
    }
    if not isinstance(claimed_digest, str) or claimed_digest != _sha256(
        _canonical_bytes(digest_input)
    ):
        raise ExplorerInputError("invalid Python definition index hash")
    files = index.get("files")
    modules = source_facts.get("modules")
    if not isinstance(files, list) or not isinstance(modules, list):
        raise ExplorerInputError("invalid Python definition index files")
    module_identity = {
        (module.get("path"), module.get("module_id"), module.get("file_sha256"))
        for module in modules
        if isinstance(module, dict)
    }
    if len(module_identity) != len(modules) or len(files) != len(modules):
        raise ExplorerInputError("invalid Python definition index file scope")
    managed_file_manifest = [
        {"path": module["path"], "file_sha256": module["file_sha256"]}
        for module in modules
    ]
    if index.get("scope", {}).get("managed_files_sha256") != _sha256(
        _canonical_bytes(managed_file_manifest)
    ):
        raise ExplorerInputError("invalid Python definition index managed file identity")
    projected_identities = [
        (file.get("path"), file.get("module_id"), file.get("file_sha256"))
        for file in files
        if isinstance(file, dict)
    ]
    if (
        len(projected_identities) != len(files)
        or len(set(projected_identities)) != len(projected_identities)
        or set(projected_identities) != module_identity
    ):
        raise ExplorerInputError("invalid Python definition index file inventory")
    allowed_kinds = {"class", "function", "method", "nested-function"}
    definitions: list[dict[str, Any]] = []
    for file in files:
        if not isinstance(file, dict) or (
            file.get("path"), file.get("module_id"), file.get("file_sha256")
        ) not in module_identity:
            raise ExplorerInputError("invalid Python definition index file identity")
        file_definitions = file.get("definitions")
        if not isinstance(file_definitions, list):
            raise ExplorerInputError("invalid Python definition index definitions")
        for definition in file_definitions:
            if not isinstance(definition, dict):
                raise ExplorerInputError("invalid Python definition index fact")
            if definition.get("kind") not in allowed_kinds or not all(
                isinstance(definition.get(key), str) and definition[key]
                for key in ("qualified_symbol", "parent_symbol")
            ) or not isinstance(definition.get("occurrence"), int) or definition["occurrence"] < 1:
                raise ExplorerInputError("invalid Python definition index fact")
            lexical_occurrence_path = definition.get("lexical_occurrence_path")
            if not isinstance(lexical_occurrence_path, list) or not lexical_occurrence_path or not all(
                isinstance(segment, str) and segment
                for segment in lexical_occurrence_path
            ):
                raise ExplorerInputError("invalid Python definition index lexical occurrence path")
            parsed_occurrences: list[tuple[int, int]] = []
            for segment in lexical_occurrence_path:
                occurrence_text, separator, total_text = segment.partition("-of-")
                if (
                    separator != "-of-"
                    or not occurrence_text.isdigit()
                    or not total_text.isdigit()
                ):
                    raise ExplorerInputError(
                        "invalid Python definition index lexical occurrence path"
                    )
                segment_occurrence = int(occurrence_text)
                segment_total = int(total_text)
                if (
                    segment_occurrence < 1
                    or segment_total < 1
                    or segment_occurrence > segment_total
                ):
                    raise ExplorerInputError(
                        "invalid Python definition index lexical occurrence path"
                    )
                parsed_occurrences.append((segment_occurrence, segment_total))
            if parsed_occurrences[-1][0] != definition["occurrence"]:
                raise ExplorerInputError(
                    "invalid Python definition index lexical occurrence path"
                )
            module_prefix = f"{file['module_id']}."
            qualified_symbol = definition["qualified_symbol"]
            if not qualified_symbol.startswith(module_prefix):
                raise ExplorerInputError("invalid Python definition index symbol")
            lexical_names = qualified_symbol[len(module_prefix) :].split(".")
            if len(lexical_names) != len(lexical_occurrence_path) or any(
                not name for name in lexical_names
            ):
                raise ExplorerInputError("invalid Python definition index symbol")
            expected_parent_symbol = (
                file["module_id"]
                if len(lexical_names) == 1
                else f"{file['module_id']}.{'.'.join(lexical_names[:-1])}"
            )
            if definition["parent_symbol"] != expected_parent_symbol:
                raise ExplorerInputError("invalid Python definition index parent symbol")
            lexical_anchor = "/".join(lexical_occurrence_path)
            expected_structural_anchor = (
                f"python:definition:{definition['kind']}:{qualified_symbol}/"
                f"lexical:{lexical_anchor}"
            )
            expected_syntax_position = (
                f"{expected_parent_symbol}/{definition['kind']}:{lexical_names[-1]}/"
                f"lexical:{lexical_anchor}"
            )
            evidence = definition.get("evidence")
            if not isinstance(evidence, dict) or evidence.get("evidence_kind") != "python_definition":
                raise ExplorerInputError("invalid Python definition index evidence")
            if evidence.get("path") != file["path"] or evidence.get("file_sha256") != file["file_sha256"]:
                raise ExplorerInputError("invalid Python definition index evidence identity")
            if not all(
                isinstance(evidence.get(key), str) and evidence[key]
                for key in (
                    "evidence_id",
                    "structural_anchor",
                    "syntax_position",
                    "snippet",
                    "normalized_snippet_sha256",
                )
            ):
                raise ExplorerInputError("invalid Python definition index evidence")
            location = evidence.get("current_location")
            if not isinstance(location, dict) or not all(
                isinstance(location.get(key), int)
                for key in ("line_start", "column_start", "line_end", "column_end")
            ):
                raise ExplorerInputError("invalid Python definition index location")
            if (
                evidence.get("source_role") != "automatic_source_fact"
                or evidence.get("symbol") != qualified_symbol
                or evidence.get("structural_anchor") != expected_structural_anchor
                or evidence.get("syntax_position") != expected_syntax_position
            ):
                raise ExplorerInputError("invalid Python definition index evidence identity")
            rebuilt_evidence = build_static_evidence(
                path=file["path"],
                source_role="automatic_source_fact",
                evidence_kind="python_definition",
                symbol=qualified_symbol,
                structural_anchor=expected_structural_anchor,
                syntax_position=expected_syntax_position,
                snippet=evidence["snippet"],
                line_start=location["line_start"],
                line_end=location["line_end"],
                file_sha256=file["file_sha256"],
            )
            if (
                evidence.get("normalized_snippet_sha256")
                != rebuilt_evidence["normalized_snippet_sha256"]
                or evidence.get("evidence_id") != rebuilt_evidence["evidence_id"]
            ):
                raise ExplorerInputError("invalid Python definition index evidence identity")
            definitions.append(definition)
    summary = index.get("summary")
    classes = sum(definition["kind"] == "class" for definition in definitions)
    expected_summary = {
        "classes": classes,
        "functions_and_methods": len(definitions) - classes,
        "definitions": len(definitions),
    }
    if summary != expected_summary or index.get("scope", {}).get("files_scanned") != len(files):
        raise ExplorerInputError("invalid Python definition index summary")


def _validate_call_facts(source_facts: dict[str, Any]) -> None:
    extension = source_facts.get("python_call_facts")
    if not isinstance(extension, dict) or extension.get("schema") != CALL_FACTS_SCHEMA:
        raise ExplorerInputError("invalid Python call facts schema")
    claimed_digest = extension.get("deterministic_facts_sha256")
    digest_input = {
        key: value
        for key, value in extension.items()
        if key != "deterministic_facts_sha256"
    }
    if not isinstance(claimed_digest, str) or claimed_digest != _sha256(
        _canonical_bytes(digest_input)
    ):
        raise ExplorerInputError("invalid Python call facts hash")
    scope = extension.get("scope")
    files = extension.get("files")
    modules = source_facts.get("modules")
    if (
        not isinstance(scope, dict)
        or scope.get("selection")
        != "The closed T10 translation-execution call-facts allowlist"
        or scope.get("paths") != list(CALL_FACT_PATHS)
        or not isinstance(files, list)
        or not isinstance(modules, list)
        or scope.get("files_scanned") != len(files)
    ):
        raise ExplorerInputError("invalid Python call facts scope")
    module_by_path = {
        module.get("path"): module for module in modules if isinstance(module, dict)
    }
    expected_paths = [path for path in CALL_FACT_PATHS if path in module_by_path]
    if [file.get("path") for file in files if isinstance(file, dict)] != expected_paths:
        raise ExplorerInputError("invalid Python call facts file inventory")

    definition_index = source_facts.get("python_definition_index", {})
    definitions_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    definitions_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for definition_file in definition_index.get("files", []):
        for definition in definition_file.get("definitions", []):
            evidence_id = definition.get("evidence", {}).get("evidence_id")
            if isinstance(evidence_id, str):
                definitions_by_id[evidence_id] = (definition_file, definition)
            qualified_symbol = definition.get("qualified_symbol")
            if isinstance(qualified_symbol, str):
                definitions_by_symbol.setdefault(qualified_symbol, []).append(definition)
    allowed_resolutions = {
        "exact-local",
        "exact-import",
        "unresolved-dispatch",
        "dynamic-or-ambiguous",
    }
    exact_resolutions = {"exact-local", "exact-import"}
    reasons_by_resolution = {
        "exact-local": {"unique-unshadowed-same-module-definition"},
        "exact-import": {"unique-unshadowed-explicit-first-party-import"},
        "unresolved-dispatch": {"runtime-receiver-attribute-dispatch"},
        "dynamic-or-ambiguous": {
            "dynamic-or-unsupported-callee-shape",
            "name-is-parameter-or-rebound-in-enclosing-scope",
            "star-import-makes-name-resolution-ambiguous",
            "local-definition-binding-is-not-unique",
            "imported-definition-target-is-not-unique",
            "import-binding-is-rebound-or-ambiguous",
            "no-unique-static-definition-binding",
        },
    }
    evidence_ids: set[str] = set()
    for file in files:
        if not isinstance(file, dict):
            raise ExplorerInputError("invalid Python call facts file")
        module = module_by_path.get(file.get("path"))
        if (
            not isinstance(module, dict)
            or file.get("module_id") != module.get("module_id")
            or file.get("file_sha256") != module.get("file_sha256")
            or not isinstance(file.get("calls"), list)
        ):
            raise ExplorerInputError("invalid Python call facts file identity")
        persisted_module_bindings = file.get("module_bindings")
        if (
            not isinstance(persisted_module_bindings, list)
            or not all(
                isinstance(binding, str) and binding
                for binding in persisted_module_bindings
            )
            or persisted_module_bindings != sorted(set(persisted_module_bindings))
        ):
            raise ExplorerInputError("invalid Python call facts module bindings")
        persisted_has_star_import = file.get("has_star_import")
        if type(persisted_has_star_import) is not bool:
            raise ExplorerInputError("invalid Python call facts star import flag")
        module_id = file["module_id"]
        local_definitions: dict[str, list[dict[str, Any]]] = {}
        for definition_file, definition in definitions_by_id.values():
            if (
                definition_file.get("path") == file["path"]
                and definition.get("parent_symbol") == module_id
            ):
                local_name = definition["qualified_symbol"].rsplit(".", 1)[-1]
                local_definitions.setdefault(local_name, []).append(definition)
        imports, projected_module_bindings, projected_has_star_import = (
            _projected_import_bindings(module)
        )
        module_bindings = set(persisted_module_bindings) | projected_module_bindings
        has_star_import = persisted_has_star_import or projected_has_star_import
        previous_location: tuple[int, int] | None = None
        for call in file["calls"]:
            if not isinstance(call, dict):
                raise ExplorerInputError("invalid Python call facts record")
            parent_id = call.get("enclosing_definition_evidence_id")
            parent = definitions_by_id.get(parent_id)
            if parent is None or parent[0].get("path") != file["path"]:
                raise ExplorerInputError("invalid Python call facts enclosing definition")
            callee = call.get("callee")
            resolution = call.get("resolution")
            reason = call.get("resolution_reason")
            occurrence = call.get("occurrence")
            discriminator = call.get("occurrence_discriminator")
            if (
                not isinstance(callee, str)
                or not callee
                or resolution not in allowed_resolutions
                or not isinstance(reason, str)
                or reason not in reasons_by_resolution.get(resolution, set())
                or not isinstance(occurrence, int)
                or occurrence < 1
                or not isinstance(discriminator, str)
            ):
                raise ExplorerInputError("invalid Python call facts record")
            occurrence_text, separator, total_text = discriminator.partition("-of-")
            if (
                separator != "-of-"
                or not occurrence_text.isdigit()
                or not total_text.isdigit()
                or int(occurrence_text) != occurrence
                or int(total_text) < occurrence
            ):
                raise ExplorerInputError("invalid Python call facts occurrence")
            target_id = call.get("target_definition_evidence_id")
            enclosing_bindings = call.get("enclosing_bindings")
            if (
                not isinstance(enclosing_bindings, list)
                or not all(
                    isinstance(binding, str) and binding
                    for binding in enclosing_bindings
                )
                or enclosing_bindings != sorted(set(enclosing_bindings))
            ):
                raise ExplorerInputError("invalid Python call facts enclosing bindings")
            if resolution in exact_resolutions:
                if not isinstance(target_id, str) or target_id not in definitions_by_id:
                    raise ExplorerInputError("invalid Python call facts exact target")
                if (
                    resolution == "exact-local"
                    and definitions_by_id[target_id][0].get("path") != file["path"]
                ):
                    raise ExplorerInputError("invalid Python call facts local target")
                expected_resolution, expected_reason, expected_target_id = (
                    _direct_name_resolution(
                        callee,
                        definitions_by_symbol=definitions_by_symbol,
                        local_definitions=local_definitions,
                        imports=imports,
                        module_bindings=module_bindings,
                        has_star_import=has_star_import,
                        enclosing_bindings=set(enclosing_bindings),
                    )
                )
                if (
                    resolution != expected_resolution
                    or reason != expected_reason
                    or target_id != expected_target_id
                ):
                    raise ExplorerInputError("invalid Python call facts exact target")
            elif "target_definition_evidence_id" in call:
                raise ExplorerInputError("invalid Python call facts unresolved target")
            parent_definition = parent[1]
            expected_structural_anchor = (
                f"python:call:{parent_id}/callee:{callee}/occurrence:{discriminator}"
            )
            expected_syntax_position = (
                f"{parent_definition['qualified_symbol']}/call:{callee}/"
                f"occurrence:{discriminator}"
            )
            evidence = call.get("evidence")
            if not isinstance(evidence, dict):
                raise ExplorerInputError("invalid Python call facts evidence")
            location = evidence.get("current_location")
            if not isinstance(location, dict) or not all(
                isinstance(location.get(key), int)
                for key in ("line_start", "column_start", "line_end", "column_end")
            ):
                raise ExplorerInputError("invalid Python call facts location")
            if (
                evidence.get("source_role") != "automatic_source_fact"
                or evidence.get("evidence_kind") != "python_call"
                or evidence.get("path") != file["path"]
                or evidence.get("file_sha256") != file["file_sha256"]
                or evidence.get("symbol") != callee
                or evidence.get("structural_anchor") != expected_structural_anchor
                or evidence.get("syntax_position") != expected_syntax_position
                or not isinstance(evidence.get("snippet"), str)
                or not evidence["snippet"]
            ):
                raise ExplorerInputError("invalid Python call facts evidence identity")
            rebuilt = build_static_evidence(
                path=file["path"],
                source_role="automatic_source_fact",
                evidence_kind="python_call",
                symbol=callee,
                structural_anchor=expected_structural_anchor,
                syntax_position=expected_syntax_position,
                snippet=evidence["snippet"],
                line_start=location["line_start"],
                line_end=location["line_end"],
                file_sha256=file["file_sha256"],
            )
            evidence_id = evidence.get("evidence_id")
            if (
                evidence.get("normalized_snippet_sha256")
                != rebuilt["normalized_snippet_sha256"]
                or evidence_id != rebuilt["evidence_id"]
                or evidence_id in evidence_ids
            ):
                raise ExplorerInputError("invalid Python call facts evidence identity")
            evidence_ids.add(evidence_id)
            current_location = (location["line_start"], location["column_start"])
            if previous_location is not None and current_location < previous_location:
                raise ExplorerInputError("invalid Python call facts source order")
            previous_location = current_location


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
    definition_index = _definition_index(modules)
    call_facts = _call_facts(modules)
    for module in modules:
        del module["definitions"]
        del module["_tree"]
        del module["_text"]
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
        "python_definition_index": definition_index,
        "python_call_facts": call_facts,
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
    _validate_definition_index(source_facts)
    _validate_call_facts(source_facts)
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
    "CALL_FACTS_SCHEMA",
    "CALL_FACT_PATHS",
    "DEFINITION_INDEX_SCHEMA",
    "EXCLUDED_PREFIXES",
    "SCANNER_VERSION",
    "SOURCE_FACTS_SCHEMA",
    "SOURCE_ROOTS",
    "attach_python_source_facts",
    "scan_python_source_facts",
]
