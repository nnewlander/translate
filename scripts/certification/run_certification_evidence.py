"""Non-authoritative, create-once evidence for existing certification commands."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Sequence


def _windows_process_snapshot() -> dict[int, int]:
    """Return pid -> parent pid using Toolhelp, without a PowerShell/CIM dependency."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                   ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                   ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                   ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                   ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return {}
    try:
        entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
        result: dict[int, int] = {}
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return result
        while True:
            result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return result
    finally:
        kernel32.CloseHandle(snapshot)


def _tracked_process_tree(root_pid: int) -> list[int]:
    parents = _windows_process_snapshot()
    tracked = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parents.items():
            if parent_pid in tracked and pid not in tracked:
                tracked.add(pid); changed = True
    return sorted(tracked)


def _pid_exists(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False)
    return str(pid) in result.stdout


def _verify_terminated(pids: Sequence[int], *, timeout_seconds: float = 5.0) -> list[int]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        survivors = [pid for pid in pids if _pid_exists(pid)]
        if not survivors or time.monotonic() >= deadline:
            return survivors
        time.sleep(0.1)

AUTHORITY = "NON_AUTHORITATIVE_CERTIFICATION_AUDIT_EVIDENCE"
ROLE = "NONE"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_baseline_digest(manifest_path: Path) -> str:
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"CODE_BASELINE_MANIFEST_INVALID:{manifest_path}") from exc
    if not isinstance(entries, list):
        raise RuntimeError(f"CODE_BASELINE_MANIFEST_INVALID:{manifest_path}")
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def book_source_binding(book_source: Path, selection: Path, manifest: Path) -> dict[str, str]:
    for path in (book_source, selection, manifest):
        if not path.is_file():
            raise RuntimeError(f"BOOK_SOURCE_BINDING_MISSING:{path}")
    return {"book_source_sha256": sha256_file(book_source), "selection_sha256": sha256_file(selection), "batch_manifest_sha256": sha256_file(manifest)}


def _identity(*, suite: str, command: Sequence[str], cwd: Path, book: dict[str, str], code_digest: str) -> dict[str, Any]:
    return {"suite": suite, "command": list(command), "working_directory": str(cwd.resolve()), "book_source_binding": book, "code_baseline_digest": code_digest, "authority": AUTHORITY, "runtime_transition_role": ROLE}


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RuntimeError(f"CERTIFICATION_RECEIPT_CONFLICT:{path}")


def run_evidence(*, receipt: Path, suite: str, command: Sequence[str], cwd: Path, book_source: Path, selection: Path, batch_manifest: Path, code_manifest: Path, timeout_seconds: float | None = None) -> dict[str, Any]:
    if not command:
        raise RuntimeError("CERTIFICATION_COMMAND_MISSING")
    if not cwd.is_dir():
        raise RuntimeError(f"CERTIFICATION_WORKING_DIRECTORY_MISSING:{cwd}")
    book = book_source_binding(book_source, selection, batch_manifest)
    identity = _identity(suite=suite, command=command, cwd=cwd, book=book, code_digest=code_baseline_digest(code_manifest))
    if receipt.exists():
        try:
            existing = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"CERTIFICATION_RECEIPT_INVALID:{receipt}") from exc
        if {key: existing.get(key) for key in identity} != identity:
            raise RuntimeError(f"CERTIFICATION_RECEIPT_CONFLICT:{receipt}")
        return {"reused": True, "receipt": existing, "verdict": existing.get("verdict")}
    started = time.time()
    launch_error: str | None = None; terminal_state = "PASS"; cleanup = "NOT_REQUIRED"
    tracked_pids: list[int] = []; surviving_pids: list[int] = []; cleanup_verified = True
    stem = receipt.with_suffix("")
    stdout_path, stderr_path = stem.with_suffix(".stdout.log"), stem.with_suffix(".stderr.log")
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            child = subprocess.Popen(list(command), cwd=cwd, stdout=stdout, stderr=stderr)
            try: exit_code = child.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                tracked_pids = _tracked_process_tree(child.pid)
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, check=False)
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
                surviving_pids = _verify_terminated(tracked_pids)
                cleanup_verified = not surviving_pids
                exit_code, terminal_state, cleanup = None, "TIMEOUT", ("PASS" if cleanup_verified else "FAIL")
    except OSError as exc:
        exit_code, launch_error, terminal_state = 127, f"{type(exc).__name__}:{exc}", "LAUNCH_FAILURE"
    ended = time.time()
    if not stdout_path.exists(): stdout_path.touch()
    if not stderr_path.exists(): stderr_path.touch()
    if terminal_state == "PASS" and exit_code != 0: terminal_state = "FAIL"
    verdict = "PASS" if terminal_state == "PASS" else "FAIL"
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    evidence = {**identity, "artifact_type": "CERTIFICATION_EVIDENCE_RECEIPT", "schema_version": 1,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)), "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended)), "duration_seconds": round(ended-started, 3),
                "real_child_exit_code": exit_code, "child_launch_error": launch_error, "process_tree_cleanup": cleanup, "cleanup_verified": cleanup_verified, "tracked_pids": tracked_pids, "surviving_pids": surviving_pids, "stdout_path": str(stdout_path), "stdout_sha256": sha256_file(stdout_path), "stderr_path": str(stderr_path), "stderr_sha256": sha256_file(stderr_path),
                "terminal_state": terminal_state, "test_counts": {"passed": sum(int(value) for value in re.findall(r"(\d+)\s+passed", stdout_text.lower())), "failed": sum(int(value) for value in re.findall(r"(\d+)\s+failed", stdout_text.lower()))}, "verdict": verdict}
    _write_once(receipt, (json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return {"reused": False, "receipt": evidence, "verdict": verdict}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path); parser.add_argument("--suite", required=True); parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--book-source", required=True, type=Path); parser.add_argument("--selection", required=True, type=Path); parser.add_argument("--batch-manifest", required=True, type=Path); parser.add_argument("--code-manifest", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER); args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        outcome = run_evidence(receipt=args.receipt, suite=args.suite, command=command, cwd=args.cwd, book_source=args.book_source, selection=args.selection, batch_manifest=args.batch_manifest, code_manifest=args.code_manifest, timeout_seconds=args.timeout_seconds)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr); return 2
    print(json.dumps({"receipt": str(args.receipt), "reused": outcome["reused"], "verdict": outcome["verdict"]}, ensure_ascii=False))
    return 0 if outcome["verdict"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
