from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from .generator import ExplorerInputError


SCANNER_VERSION = "architecture-explorer-t2/1.1"
CANDIDATE_SCHEMA = "architecture-explorer.relationship-candidate.v1"
PRODUCTION_FILES = (
    "scripts/production/production_campaign_supervisor.ps1",
    "scripts/production/run_qwen_deterministic_worker.py",
    "src/booktrans/production_worker/worker.py",
)
AUXILIARY_TEST_FILES = (
    "tests/production/test_supervisor_launch_boundary.ps1",
    "tests/unit/test_production_entrypoint_bootstrap.py",
    "tests/unit/test_qwen_deterministic_worker.py",
)
SCAN_FILES = (*PRODUCTION_FILES, *AUXILIARY_TEST_FILES)
REQUIRED_EVIDENCE_KINDS = (
    "worker_script_binding",
    "production_argument_vector",
    "process_boundary_definition",
    "process_boundary_invocation",
    "worker_wrapper_import",
    "worker_wrapper_main_call",
    "worker_mode_contract",
    "worker_authorization_contract",
    "worker_argument_parse",
    "worker_canary_exit_guard",
    "worker_id_guard",
    "worker_batch_checkpoint_guard",
    "worker_production_branch",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ExplorerInputError(f"Git metadata unavailable: {detail}")
    return completed.stdout.rstrip("\r\n")


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & 0x400)


def _stat_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _normalized_snippet(snippet: str) -> str:
    return " ".join(snippet.split())


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _evidence(
    *,
    path: str,
    source_role: str,
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
    snippet: str,
    line_start: int,
    line_end: int,
    file_sha256: str,
) -> dict[str, Any]:
    normalized_hash = _sha256(_normalized_snippet(snippet).encode("utf-8"))
    identity = {
        "path": path,
        "evidence_kind": evidence_kind,
        "symbol": symbol,
        "structural_anchor": structural_anchor,
        "syntax_position": syntax_position,
        "normalized_snippet_sha256": normalized_hash,
    }
    return {
        "evidence_id": f"evidence:sha256:{_sha256(_canonical_bytes(identity))}",
        "source_role": source_role,
        "evidence_kind": evidence_kind,
        "path": path,
        "symbol": symbol,
        "structural_anchor": structural_anchor,
        "syntax_position": syntax_position,
        "current_location": {"line_start": line_start, "line_end": line_end},
        "snippet": snippet,
        "normalized_snippet_sha256": normalized_hash,
        "file_sha256": file_sha256,
    }


def _regex_evidence(
    *,
    text: str,
    path: str,
    file_sha256: str,
    pattern: str,
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
    flags: int = 0,
    required_literals: tuple[str, ...] = (),
    source_role: str = "production",
) -> list[dict[str, Any]]:
    found = []
    for ordinal, match in enumerate(re.finditer(pattern, text, flags), start=1):
        snippet = match.group(0)
        if required_literals and not all(literal in snippet for literal in required_literals):
            continue
        start = _line_number(text, match.start())
        end = start + snippet.count("\n")
        found.append(
            _evidence(
                path=path,
                source_role=source_role,
                evidence_kind=evidence_kind,
                symbol=symbol,
                structural_anchor=structural_anchor,
                syntax_position=f"{syntax_position}#{ordinal}",
                snippet=snippet,
                line_start=start,
                line_end=end,
                file_sha256=file_sha256,
            )
        )
    return found


def _powershell_function_evidence(
    *,
    text: str,
    path: str,
    file_sha256: str,
    name: str,
    evidence_kind: str,
    required_literals: tuple[str, ...],
) -> list[dict[str, Any]]:
    pattern = rf"(?ms)^function\s+{re.escape(name)}\b.*?(?=^function\s+|\Z)"
    return _regex_evidence(
        text=text,
        path=path,
        file_sha256=file_sha256,
        pattern=pattern,
        evidence_kind=evidence_kind,
        symbol=name,
        structural_anchor=f"powershell:function:{name}",
        syntax_position=f"function:{name}",
        required_literals=required_literals,
    )


def _node_segment(text: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(text, node)
    if segment is None:
        raise ExplorerInputError("Python AST location did not map to source text")
    return segment


def _python_evidence(
    *,
    text: str,
    path: str,
    file_sha256: str,
    nodes: Iterable[ast.AST],
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
    source_role: str = "production",
) -> list[dict[str, Any]]:
    found = []
    for ordinal, node in enumerate(nodes, start=1):
        snippet = _node_segment(text, node)
        found.append(
            _evidence(
                path=path,
                source_role=source_role,
                evidence_kind=evidence_kind,
                symbol=symbol,
                structural_anchor=structural_anchor,
                syntax_position=f"{syntax_position}#{ordinal}",
                snippet=snippet,
                line_start=int(getattr(node, "lineno")),
                line_end=int(getattr(node, "end_lineno")),
                file_sha256=file_sha256,
            )
        )
    return found


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_args_attribute(node: ast.AST, attribute: str) -> bool:
    return isinstance(node, ast.Attribute) and _is_name(node.value, "args") and node.attr == attribute


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _string_argument(call: ast.Call, position: int = 0) -> str | None:
    if len(call.args) <= position:
        return None
    value = call.args[position]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _python_function(tree: ast.Module, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _is_exact_main_guard(test: ast.AST) -> bool:
    return (
        isinstance(test, ast.Compare)
        and _is_name(test.left, "__name__")
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_system_exit_call_of(statement: ast.stmt, function_name: str) -> bool:
    if not isinstance(statement, ast.Raise) or not isinstance(statement.exc, ast.Call):
        return False
    exit_call = statement.exc
    if not _is_name(exit_call.func, "SystemExit") or len(exit_call.args) != 1:
        return False
    worker_call = exit_call.args[0]
    return (
        isinstance(worker_call, ast.Call)
        and _is_name(worker_call.func, function_name)
        and not worker_call.args
        and not worker_call.keywords
    )


def _wrapper_evidence(text: str, path: str, file_sha256: str) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=path)
    imports: list[tuple[ast.ImportFrom, str]] = [
        (node, alias.asname or alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "booktrans.production_worker.worker"
        for alias in node.names
        if alias.name == "main"
    ]
    guards: list[ast.If] = []
    if len(imports) == 1:
        imported_name = imports[0][1]
        guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and _is_exact_main_guard(node.test)
            and not node.orelse
            and len(node.body) == 1
            and _is_system_exit_call_of(node.body[0], imported_name)
        ]
    return [
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=[node for node, _local_name in imports],
            evidence_kind="worker_wrapper_import",
            symbol="module",
            structural_anchor="python:import:booktrans.production_worker.worker.main",
            syntax_position="module/import:worker.main",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=guards,
            evidence_kind="worker_wrapper_main_call",
            symbol="module",
            structural_anchor="python:main-guard:call-main",
            syntax_position="module/if:__name__==__main__/call:main",
        ),
    ]


def _is_args_parse_assignment(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return False
    if not _is_name(statement.targets[0], "args") or not isinstance(statement.value, ast.Call):
        return False
    parse_call = statement.value
    if not isinstance(parse_call.func, ast.Attribute) or parse_call.func.attr != "parse_args":
        return False
    parser_call = parse_call.func.value
    return (
        isinstance(parser_call, ast.Call)
        and _is_name(parser_call.func, "build_parser")
        and not parser_call.args
        and not parser_call.keywords
        and len(parse_call.args) == 1
        and _is_name(parse_call.args[0], "argv")
    )


def _is_canary_exit_guard(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.If) or statement.orelse or not statement.body:
        return False
    test = statement.test
    return (
        isinstance(test, ast.Compare)
        and _is_args_attribute(test.left, "mode")
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "canary"
        and isinstance(statement.body[-1], ast.Return)
    )


def _is_system_exit_raise(statement: ast.stmt, message: str) -> bool:
    if not isinstance(statement, ast.Raise) or not isinstance(statement.exc, ast.Call):
        return False
    call = statement.exc
    return (
        _is_name(call.func, "SystemExit")
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == message
    )


def _is_worker_id_guard(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.If)
        and not statement.orelse
        and isinstance(statement.test, ast.UnaryOp)
        and isinstance(statement.test.op, ast.Not)
        and _is_args_attribute(statement.test.operand, "worker_id")
        and len(statement.body) == 1
        and _is_system_exit_raise(statement.body[0], "WORKER_ID_REQUIRED")
    )


def _bool_args_attribute(node: ast.AST, attribute: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and _is_name(node.func, "bool")
        and len(node.args) == 1
        and _is_args_attribute(node.args[0], attribute)
        and not node.keywords
    )


def _is_batch_checkpoint_guard(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.If) or statement.orelse or len(statement.body) != 1:
        return False
    test = statement.test
    return (
        isinstance(test, ast.Compare)
        and _bool_args_attribute(test.left, "batch_label")
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.NotEq)
        and len(test.comparators) == 1
        and _bool_args_attribute(test.comparators[0], "checkpoint")
        and _is_system_exit_raise(statement.body[0], "WORKER_LAUNCH_BINDING_INCOMPLETE")
    )


def _is_production_return(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Return) or not isinstance(statement.value, ast.Call):
        return False
    call = statement.value
    if not _is_name(call.func, "run_production_checkpoint"):
        return False
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    return (
        _is_args_attribute(keywords.get("worker_id"), "worker_id")
        and _is_args_attribute(keywords.get("authorize"), "authorize_production_checkpoint_write")
        and _is_args_attribute(keywords.get("batch_label"), "batch_label")
        and _is_args_attribute(keywords.get("checkpoint"), "checkpoint")
    )


def _worker_evidence(text: str, path: str, file_sha256: str) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=path)
    parser_functions = _python_function(tree, "build_parser")
    main_functions = _python_function(tree, "main")
    mode_calls: list[ast.Call] = []
    authorization_calls: list[ast.Call] = []
    argument_parse_nodes: list[ast.AST] = []
    canary_guard_nodes: list[ast.AST] = []
    worker_id_guard_nodes: list[ast.AST] = []
    batch_checkpoint_guard_nodes: list[ast.AST] = []
    production_nodes: list[ast.AST] = []

    for function in parser_functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or _call_name(node) != "add_argument":
                continue
            argument = _string_argument(node)
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            if argument == "--mode":
                choices = keywords.get("choices")
                values = {
                    item.value
                    for item in getattr(choices, "elts", [])
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if values == {"canary", "production"}:
                    mode_calls.append(node)
            if argument == "--authorize-production-checkpoint-write":
                action = keywords.get("action")
                if isinstance(action, ast.Constant) and action.value == "store_true":
                    authorization_calls.append(node)

    for function in main_functions:
        indexed = list(enumerate(function.body))
        parses = [(index, node) for index, node in indexed if _is_args_parse_assignment(node)]
        canary_guards = [(index, node) for index, node in indexed if _is_canary_exit_guard(node)]
        worker_guards = [(index, node) for index, node in indexed if _is_worker_id_guard(node)]
        binding_guards = [(index, node) for index, node in indexed if _is_batch_checkpoint_guard(node)]
        production_returns = [(index, node) for index, node in indexed if _is_production_return(node)]
        argument_parse_nodes.extend(node for _index, node in parses)
        canary_guard_nodes.extend(node for _index, node in canary_guards)
        worker_id_guard_nodes.extend(node for _index, node in worker_guards)
        batch_checkpoint_guard_nodes.extend(node for _index, node in binding_guards)
        if all(len(items) == 1 for items in (parses, canary_guards, worker_guards, binding_guards, production_returns)):
            parse_index = parses[0][0]
            canary_index = canary_guards[0][0]
            worker_index = worker_guards[0][0]
            binding_index = binding_guards[0][0]
            production_index, production_return = production_returns[0]
            if (
                parse_index < canary_index
                and worker_index == canary_index + 1
                and binding_index == worker_index + 1
                and production_index == binding_index + 1
            ):
                production_nodes.append(production_return.value)

    return [
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=mode_calls,
            evidence_kind="worker_mode_contract",
            symbol="build_parser",
            structural_anchor="python:function:build_parser/add_argument:--mode",
            syntax_position="function:build_parser/call:add_argument:--mode",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=authorization_calls,
            evidence_kind="worker_authorization_contract",
            symbol="build_parser",
            structural_anchor="python:function:build_parser/add_argument:--authorize-production-checkpoint-write",
            syntax_position="function:build_parser/call:add_argument:--authorize-production-checkpoint-write",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=argument_parse_nodes,
            evidence_kind="worker_argument_parse",
            symbol="main",
            structural_anchor="python:function:main/assignment:args=parse_args",
            syntax_position="function:main/assignment:args=parse_args",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=canary_guard_nodes,
            evidence_kind="worker_canary_exit_guard",
            symbol="main",
            structural_anchor="python:function:main/if:args.mode==canary/exit",
            syntax_position="function:main/if:args.mode==canary/exit",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=worker_id_guard_nodes,
            evidence_kind="worker_id_guard",
            symbol="main",
            structural_anchor="python:function:main/guard:worker-id-required",
            syntax_position="function:main/guard:worker-id-required",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=batch_checkpoint_guard_nodes,
            evidence_kind="worker_batch_checkpoint_guard",
            symbol="main",
            structural_anchor="python:function:main/guard:batch-checkpoint-pair",
            syntax_position="function:main/guard:batch-checkpoint-pair",
        ),
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=production_nodes,
            evidence_kind="worker_production_branch",
            symbol="main",
            structural_anchor="python:function:main/call:run_production_checkpoint",
            syntax_position="function:main/non-canary/call:run_production_checkpoint",
        ),
    ]


def _auxiliary_python_function_evidence(
    text: str,
    path: str,
    file_sha256: str,
    function_name: str,
    evidence_kind: str,
) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=path)
    return _python_evidence(
        text=text,
        path=path,
        file_sha256=file_sha256,
        nodes=_python_function(tree, function_name),
        evidence_kind=evidence_kind,
        symbol=function_name,
        structural_anchor=f"python:test-function:{function_name}",
        syntax_position=f"module/function:{function_name}",
        source_role="auxiliary_test",
    )


def _validated_source_path(repository: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or not relative_path.parts or ".." in relative_path.parts:
        raise ExplorerInputError(f"T2 scan input must be repository-relative: {relative}")
    candidate = repository.joinpath(*relative_path.parts)
    cursor = repository
    for part in relative_path.parts:
        cursor /= part
        if cursor.is_symlink() or cursor.is_junction() or _is_reparse_point(cursor):
            raise ExplorerInputError(f"T2 scan input path contains a link or reparse point: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ExplorerInputError(f"Required T2 scan input is missing: {relative}") from error
    try:
        resolved.relative_to(repository)
    except ValueError as error:
        raise ExplorerInputError(f"T2 scan input escapes repository: {relative}") from error
    if not candidate.is_file():
        raise ExplorerInputError(f"Required T2 scan input is not a file: {relative}")
    return candidate


def _read_allowed_files(repository: Path) -> dict[str, tuple[str, str, int]]:
    result: dict[str, tuple[str, str, int]] = {}
    for relative in SCAN_FILES:
        path = _validated_source_path(repository, relative)
        before_signature = _stat_signature(path)
        before_resolved = path.resolve(strict=True)
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ExplorerInputError(f"T2 scan input could not be read: {relative}") from error
        try:
            after_signature = _stat_signature(path)
            after_resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise ExplorerInputError(f"T2 scan input changed during scan: {relative}") from error
        if (
            before_signature != after_signature
            or before_resolved != after_resolved
            or len(raw) != after_signature[2]
        ):
            raise ExplorerInputError(f"T2 scan input changed during scan: {relative}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ExplorerInputError(f"T2 scan input is not UTF-8: {relative}") from error
        result[relative] = (text, _sha256(raw), len(raw))
    return result


def _snapshot_from_loaded_files(
    repository: Path,
    files: dict[str, tuple[str, str, int]],
    *,
    scanned_at: str,
) -> dict[str, Any]:
    porcelain = _git(repository, "status", "--porcelain=v1", "--untracked-files=normal")
    worktree = {
        "state": "dirty" if porcelain else "clean",
        "porcelain": porcelain.splitlines(),
    }
    inputs = [
        {"path": path, "sha256": files[path][1], "size_bytes": files[path][2]}
        for path in sorted(files)
    ]
    deterministic_facts = {
        "revision": _git(repository, "rev-parse", "HEAD"),
        "worktree": worktree,
        "scanner_version": SCANNER_VERSION,
        "input_files": inputs,
    }
    return {
        "schema": "architecture-explorer.snapshot.v1",
        **deterministic_facts,
        "scanned_at": scanned_at,
        "facts_sha256": _sha256(_canonical_bytes(deterministic_facts)),
    }


def _collect_evidence(files: dict[str, tuple[str, str, int]]) -> list[dict[str, Any]]:
    supervisor_path = PRODUCTION_FILES[0]
    supervisor, supervisor_hash, _supervisor_size = files[supervisor_path]
    evidence = [
        *_regex_evidence(
            text=supervisor,
            path=supervisor_path,
            file_sha256=supervisor_hash,
            pattern=r'(?m)^\$deterministicWorkerScript\s*=\s*Join-Path\s+\$Root\s+"scripts\\production\\run_qwen_deterministic_worker\.py"\s*$',
            evidence_kind="worker_script_binding",
            symbol="$deterministicWorkerScript",
            structural_anchor="powershell:assignment:$deterministicWorkerScript",
            syntax_position="module/assignment:$deterministicWorkerScript",
        ),
        *_regex_evidence(
            text=supervisor,
            path=supervisor_path,
            file_sha256=supervisor_hash,
            pattern=r"(?ms)^\s*\$workerArgumentVector\s*=\s*@\(.*?^\s*\)\s*\+\s*\$workerArgs\s*$",
            evidence_kind="production_argument_vector",
            symbol="$workerArgumentVector",
            structural_anchor="powershell:assignment:$workerArgumentVector",
            syntax_position="production-loop/assignment:$workerArgumentVector",
            required_literals=(
                "$deterministicWorkerScript",
                "'--root'",
                "'--run-path'",
                "'--worker-id'",
                "'--handoff-path'",
                "'--batch-label'",
                "'--checkpoint'",
                "'--mode', 'production'",
                "'--authorize-production-checkpoint-write'",
            ),
        ),
        *_powershell_function_evidence(
            text=supervisor,
            path=supervisor_path,
            file_sha256=supervisor_hash,
            name="Start-DeterministicWorkerProcess",
            evidence_kind="process_boundary_definition",
            required_literals=(
                "[System.Diagnostics.ProcessStartInfo]::new",
                "$child.Start()",
                "$child.WaitForExit()",
            ),
        ),
        *_regex_evidence(
            text=supervisor,
            path=supervisor_path,
            file_sha256=supervisor_hash,
            pattern=r"(?m)^\s*\$child\s*=\s*Start-DeterministicWorkerProcess\s+\$python\s+\$workerArgumentVector\s+\$Root\s+\$workerStdoutPath\s+\$workerStderrPath\s+\(\[ref\]\$launchDiagnostics\)\s*$",
            evidence_kind="process_boundary_invocation",
            symbol="Start-DeterministicWorkerProcess",
            structural_anchor="powershell:call:Start-DeterministicWorkerProcess",
            syntax_position="production-loop/call:Start-DeterministicWorkerProcess",
        ),
    ]

    wrapper_path = PRODUCTION_FILES[1]
    wrapper, wrapper_hash, _wrapper_size = files[wrapper_path]
    evidence.extend(_wrapper_evidence(wrapper, wrapper_path, wrapper_hash))
    worker_path = PRODUCTION_FILES[2]
    worker, worker_hash, _worker_size = files[worker_path]
    evidence.extend(_worker_evidence(worker, worker_path, worker_hash))

    launch_test_path = AUXILIARY_TEST_FILES[0]
    launch_test, launch_test_hash, _launch_test_size = files[launch_test_path]
    evidence.extend(
        _regex_evidence(
            text=launch_test,
            path=launch_test_path,
            file_sha256=launch_test_hash,
            pattern=r"(?m)^\s*Assert-True\s+\(\$inheritChild\.ExitCode\s+-eq\s+0\s+-and\s+\$inheritDiagnostics\.child_created\)\s+'inherited-environment launch creates child'\s*$",
            evidence_kind="auxiliary_launch_boundary_test",
            symbol="Assert-True",
            structural_anchor="powershell:test-assertion:inherited-child-created",
            syntax_position="script/assert:inherited-child-created",
            source_role="auxiliary_test",
        )
    )
    bootstrap_path = AUXILIARY_TEST_FILES[1]
    bootstrap, bootstrap_hash, _bootstrap_size = files[bootstrap_path]
    evidence.extend(
        _auxiliary_python_function_evidence(
            bootstrap,
            bootstrap_path,
            bootstrap_hash,
            "test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight",
            "auxiliary_wrapper_bootstrap_test",
        )
    )
    package_test_path = AUXILIARY_TEST_FILES[2]
    package_test, package_test_hash, _package_test_size = files[package_test_path]
    evidence.extend(
        _auxiliary_python_function_evidence(
            package_test,
            package_test_path,
            package_test_hash,
            "test_production_worker_package_does_not_call_codex",
            "auxiliary_worker_binding_test",
        )
    )
    return sorted(evidence, key=lambda item: (item["path"], item["current_location"]["line_start"], item["evidence_kind"]))


def _findings(evidence: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    required = {}
    has_ambiguity = False
    has_missing = False
    for kind in REQUIRED_EVIDENCE_KINDS:
        matches = [item["evidence_id"] for item in evidence if item["evidence_kind"] == kind]
        if not matches:
            status = "missing"
            has_missing = True
        elif len(matches) == 1:
            status = "unique"
        else:
            status = "ambiguous"
            has_ambiguity = True
        required[kind] = {"status": status, "evidence_ids": matches}
    outcome = "ambiguous" if has_ambiguity else ("incomplete" if has_missing else "complete")
    return required, outcome


def _report_digest_model(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_revision": candidate["source_snapshot"]["revision"],
        "worktree": candidate["source_snapshot"]["worktree"],
        "scanner_version": candidate["scanner_version"],
        "scan_scope": candidate["scan_scope"],
        "evidence": candidate["evidence"],
        "required_findings": candidate["required_findings"],
        "candidate_relation": candidate["candidate_relation"],
    }


def scan_supervisor_worker_candidate(
    repository: Path,
    *,
    scanned_at: str | None = None,
) -> dict[str, Any]:
    """Scan the closed T2 input set without executing any production entrypoint."""

    repository = repository.resolve(strict=True)
    if not repository.is_dir():
        raise ExplorerInputError(f"T2 repository is not a directory: {repository}")
    scan_time = scanned_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    files = _read_allowed_files(repository)
    snapshot = _snapshot_from_loaded_files(repository, files, scanned_at=scan_time)
    evidence = _collect_evidence(files)
    required_findings, scan_outcome = _findings(evidence)
    production_ids = [
        item["evidence_id"]
        for item in evidence
        if item["source_role"] == "production"
        and required_findings.get(item["evidence_kind"], {}).get("status") == "unique"
    ]
    auxiliary_ids = [item["evidence_id"] for item in evidence if item["source_role"] == "auxiliary_test"]

    if scan_outcome == "complete":
        reachability = {
            "status": "STATIC_CONDITIONAL",
            "proven_conditions": [
                "Supervisor argument vector selects --mode production.",
                "Supervisor argument vector supplies --authorize-production-checkpoint-write.",
                "Supervisor binds batch and checkpoint from the current rebuilt state.",
                "The wrapper imports the exact Worker main and calls it only under __name__ == '__main__'.",
                "Worker main parses args through build_parser().parse_args(argv).",
                "Worker main's exact canary guard exits before the production guard sequence.",
                "Worker main then requires worker-id, then paired batch/checkpoint, before a top-level run_production_checkpoint return.",
            ],
            "retained_unknowns": [
                "Static scanning does not prove that the Supervisor operational loop reaches this launch on any particular run.",
                "Static scanning does not prove that runtime preconditions, credentials, or external services are available.",
                "Parameter business meaning beyond the observed names, literals, and direct bindings remains unreviewed.",
            ],
        }
    else:
        reachability = {
            "status": "UNKNOWN",
            "proven_conditions": [],
            "retained_unknowns": [
                f"Required evidence scan outcome is {scan_outcome}; no complete static reachability claim is made."
            ],
        }

    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "scanned_at": scan_time,
        "source_snapshot": snapshot,
        "scan_scope": {
            "production_files": list(PRODUCTION_FILES),
            "auxiliary_test_files": list(AUXILIARY_TEST_FILES),
            "scope_policy": "closed-world explicit file allowlist; paths validated before one stable content read; no directory traversal",
        },
        "evidence": evidence,
        "required_findings": required_findings,
        "candidate_relation": {
            "status": "candidate",
            "scan_outcome": scan_outcome,
            "review_status": "PENDING_HUMAN_REVIEW",
            "source_module": "production-control",
            "target_module": "translation-execution",
            "direction": "production-control -> translation-execution",
            "proposed_relationship_type": "process-launch",
            "runtime_state": "NOT_COLLECTED",
            "reachability": reachability,
            "production_evidence_ids": production_ids,
            "auxiliary_test_evidence_ids": auxiliary_ids,
        },
    }
    candidate["candidate_report_sha256"] = _sha256(_canonical_bytes(_report_digest_model(candidate)))
    return candidate


__all__ = [
    "AUXILIARY_TEST_FILES",
    "PRODUCTION_FILES",
    "REQUIRED_EVIDENCE_KINDS",
    "SCAN_FILES",
    "SCANNER_VERSION",
    "scan_supervisor_worker_candidate",
]
