from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .generator import ExplorerInputError, scan_snapshot


SCANNER_VERSION = "architecture-explorer-t2/1.0"
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
    "worker_production_branch",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def _wrapper_evidence(text: str, path: str, file_sha256: str) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=path)
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "booktrans.production_worker.worker"
        and any(alias.name == "main" for alias in node.names)
    ]
    guards = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        calls_main = any(
            isinstance(child, ast.Call) and _call_name(child) == "main"
            for child in ast.walk(node)
        )
        if calls_main and "__name__" in _node_segment(text, node) and "__main__" in _node_segment(text, node):
            guards.append(node)
    return [
        *_python_evidence(
            text=text,
            path=path,
            file_sha256=file_sha256,
            nodes=imports,
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


def _worker_evidence(text: str, path: str, file_sha256: str) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=path)
    parser_functions = _python_function(tree, "build_parser")
    main_functions = _python_function(tree, "main")
    mode_calls: list[ast.Call] = []
    authorization_calls: list[ast.Call] = []
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
        has_canary_guard = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and _is_args_attribute(node.test.left, "mode")
            and any(isinstance(item, ast.Constant) and item.value == "canary" for item in node.test.comparators)
            for node in ast.walk(function)
        )
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or _call_name(node) != "run_production_checkpoint":
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            authorize = keywords.get("authorize")
            if has_canary_guard and authorize is not None and _is_args_attribute(authorize, "authorize_production_checkpoint_write"):
                production_nodes.append(node)

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


def _read_allowed_files(repository: Path) -> dict[str, tuple[str, str]]:
    result = {}
    for relative in SCAN_FILES:
        path = repository / relative
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise ExplorerInputError(f"Required T2 scan input is missing: {relative}") from error
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ExplorerInputError(f"T2 scan input is not UTF-8: {relative}") from error
        result[relative] = (text, _sha256(raw))
    return result


def _collect_evidence(files: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    supervisor_path = PRODUCTION_FILES[0]
    supervisor, supervisor_hash = files[supervisor_path]
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
    wrapper, wrapper_hash = files[wrapper_path]
    evidence.extend(_wrapper_evidence(wrapper, wrapper_path, wrapper_hash))
    worker_path = PRODUCTION_FILES[2]
    worker, worker_hash = files[worker_path]
    evidence.extend(_worker_evidence(worker, worker_path, worker_hash))

    launch_test_path = AUXILIARY_TEST_FILES[0]
    launch_test, launch_test_hash = files[launch_test_path]
    evidence.extend(
        _regex_evidence(
            text=launch_test,
            path=launch_test_path,
            file_sha256=launch_test_hash,
            pattern=r"(?m)^\s*'SUPERVISOR_LAUNCH_BOUNDARY_TESTS_PASS'\s*$",
            evidence_kind="auxiliary_launch_boundary_test",
            symbol="script",
            structural_anchor="powershell:test-success-marker:SUPERVISOR_LAUNCH_BOUNDARY_TESTS_PASS",
            syntax_position="script/success-marker",
            source_role="auxiliary_test",
        )
    )
    bootstrap_path = AUXILIARY_TEST_FILES[1]
    bootstrap, bootstrap_hash = files[bootstrap_path]
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
    package_test, package_test_hash = files[package_test_path]
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

    repository = repository.resolve()
    files = _read_allowed_files(repository)
    snapshot = scan_snapshot(repository, list(SCAN_FILES), scanned_at=scanned_at)
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
                "Worker main routes non-canary mode to run_production_checkpoint after worker-id and paired batch/checkpoint guards.",
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
        "scanned_at": scanned_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_snapshot": snapshot,
        "scan_scope": {
            "production_files": list(PRODUCTION_FILES),
            "auxiliary_test_files": list(AUXILIARY_TEST_FILES),
            "scope_policy": "closed-world explicit file allowlist; no directory traversal",
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


def render_candidate_report(candidate: dict[str, Any]) -> str:
    relation = candidate["candidate_relation"]
    snapshot = candidate["source_snapshot"]
    lines = [
        "# Architecture Explorer T2：Supervisor → Worker 候选证据报告",
        "",
        "> 本报告由确定性扫描器从机器候选记录生成。它是待人工审核的候选材料，不是架构批准。",
        "",
        "## 扫描元数据",
        "",
        f"- 源码 Commit：`{snapshot['revision']}`",
        f"- Worktree 状态：`{snapshot['worktree']['state']}`",
        f"- 扫描器：`{candidate['scanner_version']}`",
        f"- 扫描时间：`{candidate['scanned_at']}`",
        f"- 候选报告摘要 SHA-256：`{candidate['candidate_report_sha256']}`",
        f"- 扫描结果：`{relation['scan_outcome']}`",
        "",
        "## 封闭扫描范围",
        "",
    ]
    for path in candidate["scan_scope"]["production_files"]:
        lines.append(f"- 生产源码：`{path}`")
    for path in candidate["scan_scope"]["auxiliary_test_files"]:
        lines.append(f"- 辅助测试：`{path}`（只作辅助证据）")
    lines.extend(
        [
            "",
            "## 候选关系（未审核）",
            "",
            f"- 状态：`{relation['status']}` / `{relation['review_status']}`",
            f"- 建议方向：`{relation['direction']}`",
            f"- 建议关系类型：`{relation['proposed_relationship_type']}`",
            f"- 静态可达状态：`{relation['reachability']['status']}`",
            f"- 真实运行状态：`{relation['runtime_state']}`",
            "",
            "### 静态扫描能够陈述的条件",
            "",
        ]
    )
    proven = relation["reachability"]["proven_conditions"]
    lines.extend(f"- {item}" for item in proven)
    if not proven:
        lines.append("- 无；必需证据不完整或存在歧义。")
    lines.extend(["", "### 保留未知事项", ""])
    lines.extend(f"- {item}" for item in relation["reachability"]["retained_unknowns"])
    lines.extend(
        [
            "",
            "## 必需证据判定",
            "",
            "| 证据种类 | 扫描状态 | 证据 ID |",
            "|---|---|---|",
        ]
    )
    for kind, finding in candidate["required_findings"].items():
        ids = "<br>".join(f"`{item}`" for item in finding["evidence_ids"]) or "—"
        lines.append(f"| `{kind}` | `{finding['status']}` | {ids} |")
    lines.extend(["", "## 源码证据", ""])
    for item in candidate["evidence"]:
        role = "生产事实" if item["source_role"] == "production" else "辅助测试"
        location = item["current_location"]
        lines.extend(
            [
                f"### `{item['evidence_kind']}` — {role}",
                "",
                f"- Evidence ID：`{item['evidence_id']}`",
                f"- 位置：`{item['path']}:{location['line_start']}`",
                f"- Symbol：`{item['symbol']}`",
                f"- 结构锚点：`{item['structural_anchor']}`",
                f"- 语法位置：`{item['syntax_position']}`",
                f"- 片段 SHA-256：`{item['normalized_snippet_sha256']}`",
                f"- 文件 SHA-256：`{item['file_sha256']}`",
                "",
                "```text",
                item["snippet"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 人工审核门禁",
            "",
            "以下模板只能由授权审核者在 GitHub Issue #3 中提交。实施 Agent 不得填写批准结论。",
            "",
            "```text",
            "ARCHITECTURE_REVIEW_DECISION",
            "decision: APPROVED | REJECTED",
            f"reviewed_source_commit: {snapshot['revision']}",
            f"reviewed_worktree_state: {snapshot['worktree']['state']}",
            f"candidate_report_sha256: {candidate['candidate_report_sha256']}",
            f"scanner_version: {candidate['scanner_version']}",
            "approved_evidence_ids:",
            "  - <authorized reviewer selects exact IDs>",
            "source_module: production-control",
            "target_module: translation-execution",
            "direction: production-control -> translation-execution",
            "relationship_type: process-launch",
            "approved_reachability: <condition or UNKNOWN>",
            "runtime_state: NOT_COLLECTED",
            "retained_unknowns: <explicit list>",
            "review_notes: <notes>",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_candidate_outputs(candidate: dict[str, Any], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    machine_path = output_directory / "candidate.json"
    report_path = output_directory / "candidate-review.md"
    machine_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_candidate_report(candidate), encoding="utf-8")
    return machine_path, report_path
