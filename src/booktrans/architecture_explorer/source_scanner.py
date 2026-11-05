from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .candidate_scanner import validated_source_path
from .generator import ExplorerInputError


SCANNER_VERSION = "architecture-explorer-t6-1/1.0"
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
)
_WORKER_WRAPPER = "scripts/production/run_qwen_deterministic_worker.py"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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
    return completed.stdout


def _stat_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _read_stable_utf8(repository: Path, relative: str) -> tuple[str, str, int]:
    path = validated_source_path(repository, relative)
    before_signature = _stat_signature(path)
    before_resolved = path.resolve(strict=True)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExplorerInputError(f"T6-1 scan input could not be read: {relative}") from error
    try:
        after_signature = _stat_signature(path)
        after_resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ExplorerInputError(f"T6-1 scan input changed during scan: {relative}") from error
    if (
        before_signature != after_signature
        or before_resolved != after_resolved
        or len(raw) != after_signature[2]
    ):
        raise ExplorerInputError(f"T6-1 scan input changed during scan: {relative}")
    try:
        return raw.decode("utf-8"), _sha256(raw), len(raw)
    except UnicodeDecodeError as error:
        raise ExplorerInputError(f"T6-1 scan input is not UTF-8: {relative}") from error


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


def _evidence(
    *,
    relative: str,
    file_sha256: str,
    evidence_kind: str,
    symbol: str,
    structural_anchor: str,
    syntax_position: str,
    snippet: str,
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    normalized_hash = _sha256(" ".join(snippet.split()).encode("utf-8"))
    identity = {
        "path": relative,
        "evidence_kind": evidence_kind,
        "symbol": symbol,
        "structural_anchor": structural_anchor,
        "syntax_position": syntax_position,
        "normalized_snippet_sha256": normalized_hash,
    }
    return {
        "evidence_id": f"evidence:sha256:{_sha256(_canonical_bytes(identity))}",
        "source_role": "automatic_source_fact",
        "evidence_kind": evidence_kind,
        "path": relative,
        "symbol": symbol,
        "structural_anchor": structural_anchor,
        "syntax_position": syntax_position,
        "current_location": {"line_start": line_start, "line_end": line_end},
        "snippet": snippet,
        "normalized_snippet_sha256": normalized_hash,
        "file_sha256": file_sha256,
    }


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
    return _evidence(
        relative=relative,
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
