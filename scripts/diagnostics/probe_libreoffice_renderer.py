"""Bounded Windows LibreOffice conversion diagnosis for Booktrans.

This is a diagnostic-only runner.  Each invocation writes to a new isolated
probe directory; it never reads or writes production campaign state and never
touches a source/checkpoint artifact.  A timed-out child is terminated by its
own Popen PID tree only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import zipfile


DEFAULT_SOFFICE = Path(r"C:\Program Files\LibreOffice\program\soffice.com")
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 5.0
DEFAULT_NO_PROGRESS_WINDOW_SECONDS = 120.0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def safe_child_environment(pythonhome: str | None) -> dict[str, str]:
    """Sanitize only the LibreOffice child environment, not the user session."""
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    if pythonhome is not None:
        environment["PYTHONHOME"] = pythonhome
    return environment


def conversion_ready(result: dict[str, Any]) -> bool:
    """Return true only for a completed conversion with a durable PDF proof.

    The executable existing, a zero exit code, or an output directory alone
    are intentionally insufficient.  This predicate is diagnostic-only and
    does not participate in production campaign state decisions.
    """
    return (
        result.get("exit_code") == 0
        and result.get("timed_out") is False
        and result.get("pdf_exists") is True
        and isinstance(result.get("pdf_size_bytes"), int)
        and result["pdf_size_bytes"] > 0
        and isinstance(result.get("pdf_sha256"), str)
        and len(result["pdf_sha256"]) == 64
    )


def process_snapshot() -> dict[str, Any]:
    """Read-only LibreOffice process facts for diagnostic context."""
    command = (
        "Get-Process -Name soffice,soffice.bin -ErrorAction SilentlyContinue | "
        "Select-Object Id,ProcessName,CPU,WorkingSet64,StartTime | "
        "ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        raw = completed.stdout.strip()
        processes = [] if not raw else json.loads(raw)
        if isinstance(processes, dict):
            processes = [processes]
        return {
            "command": command,
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
            "processes": processes,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}:{exc}", "processes": []}


def owned_process_metrics(pid: int) -> dict[str, Any] | None:
    """Read metrics for exactly this runner's Popen PID, never by name."""
    command = (
        f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "if ($null -ne $p) { $p | Select-Object Id,ProcessName,CPU,WorkingSet64,StartTime | ConvertTo-Json -Compress }"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        raw = completed.stdout.strip()
        if completed.returncode != 0 or not raw:
            return None
        value = json.loads(raw)
        return {
            "pid": value.get("Id"),
            "process_name": value.get("ProcessName"),
            "cpu_seconds": value.get("CPU"),
            "working_set_bytes": value.get("WorkingSet64"),
            "start_time": value.get("StartTime"),
        }
    except Exception:
        return None


def launched_libreoffice_metrics(started: datetime) -> dict[str, Any]:
    """Aggregate only LibreOffice processes launched by this probe window.

    `soffice.com` is commonly an idle launcher while `soffice.bin` performs
    the conversion.  Sampling only the launcher would incorrectly describe a
    CPU-active conversion as idle.  Start-time binding keeps unrelated user
    LibreOffice sessions out of the telemetry; it is observational only and
    is never used to choose a cleanup target.
    """
    snapshot = process_snapshot()
    earliest_ms = int((started.timestamp() - 2.0) * 1000)
    included: list[dict[str, Any]] = []
    for process in snapshot.get("processes", []):
        raw_start = str(process.get("StartTime", ""))
        digits = raw_start.removeprefix("/Date(").split(")", 1)[0]
        try:
            start_ms = int(digits)
        except ValueError:
            continue
        if start_ms >= earliest_ms:
            included.append(
                {
                    "pid": process.get("Id"),
                    "process_name": process.get("ProcessName"),
                    "cpu_seconds": process.get("CPU"),
                    "working_set_bytes": process.get("WorkingSet64"),
                    "start_time": raw_start,
                }
            )
    cpu_values = [float(item["cpu_seconds"]) for item in included if item.get("cpu_seconds") is not None]
    memory_values = [int(item["working_set_bytes"]) for item in included if item.get("working_set_bytes") is not None]
    return {
        "processes": included,
        "cpu_seconds": round(sum(cpu_values), 6) if cpu_values else None,
        "working_set_bytes": sum(memory_values) if memory_values else None,
        "query_exit_code": snapshot.get("exit_code"),
        "query_error": snapshot.get("error") or snapshot.get("stderr"),
    }


def activity_snapshot(*, output: Path, profile: Path) -> dict[str, Any]:
    """Bounded file activity facts from this probe's private directories."""
    roots = (("output", output), ("profile_temp", profile / "temp"), ("profile_cache", profile / "cache"))
    files: list[dict[str, Any]] = []
    total_bytes = 0
    total_files = 0
    for label, root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            total_files += 1
            total_bytes += stat.st_size
            files.append({"area": label, "path": str(path.relative_to(root)), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    files.sort(key=lambda item: (item["mtime_ns"], item["path"]), reverse=True)
    # Keep telemetry compact while retaining a deterministic change fingerprint.
    fingerprint = sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"file_count": total_files, "total_bytes": total_bytes, "recent_files": files[:20], "fingerprint": fingerprint}


def classify_runtime_outcome(
    *,
    result: dict[str, Any],
    telemetry: list[dict[str, Any]],
    no_progress_window_seconds: float,
) -> str:
    """Classify observed execution; never treat elapsed time alone as a hang."""
    if conversion_ready(result):
        return "SUCCESS"
    if any(str(path).lower().endswith(".dmp") for path in result.get("profile_crash_artifacts", [])):
        return "CRASH_NO_PDF"
    if not result.get("timed_out"):
        return "PROCESS_EXIT_NO_PDF"
    observed = [item for item in telemetry if item.get("process_alive") and item.get("cpu_delta_seconds") is not None]
    if len(observed) < 2:
        return "TIMEOUT_INSUFFICIENT_PROGRESS_TELEMETRY"
    window_start = observed[-1]["elapsed_seconds"] - no_progress_window_seconds
    window = [item for item in observed if item["elapsed_seconds"] >= window_start]
    if len(window) < 2:
        return "TIMEOUT_INSUFFICIENT_PROGRESS_TELEMETRY"
    cpu_idle = all(abs(float(item.get("cpu_delta_seconds") or 0.0)) <= 0.01 for item in window[1:])
    memory_idle = all(abs(int(item.get("working_set_delta_bytes") or 0)) <= 1024 * 1024 for item in window[1:])
    file_idle = len({item["activity"]["fingerprint"] for item in window}) == 1
    return "HANG_NO_PROGRESS" if cpu_idle and memory_idle and file_idle else "TIMEOUT_WITH_ACTIVITY"


def renderer_readiness_levels(results: list[dict[str, Any]]) -> dict[str, bool]:
    """Separate a trivial executable check from production visual readiness."""
    minimal = [item for item in results if str(item.get("name", "")).startswith("minimal-")]
    representative = [
        item
        for item in results
        if str(item.get("name", "")).startswith(("real-", "source-"))
    ]
    return {
        "basic_renderer_ready": bool(minimal) and all(conversion_ready(item) for item in minimal),
        "representative_renderer_ready": bool(representative) and all(conversion_ready(item) for item in representative),
    }


def terminate_owned_tree(pid: int) -> dict[str, Any]:
    """Kill only the tree rooted at this runner-owned Popen PID."""
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
    )
    return {"command": ["taskkill", "/PID", str(pid), "/T", "/F"], "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def run_conversion(
    *,
    soffice: Path,
    document: Path,
    probe_root: Path,
    name: str,
    pythonhome: str | None,
    timeout_seconds: float,
    sample_interval_seconds: float,
    no_progress_window_seconds: float,
) -> dict[str, Any]:
    case_root = probe_root / name
    profile = case_root / "lo-profile"
    output = case_root / "output"
    profile.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    environment = safe_child_environment(pythonhome)
    command = [
        str(soffice),
        "--headless",
        f"-env:UserInstallation={file_uri(profile)}",
        "--convert-to",
        "pdf",
        "--outdir",
        # soffice.com resolves a relative --outdir against its own program
        # directory, not this runner's cwd.  This must stay absolute.
        str(output.resolve()),
        str(document.resolve()),
    ]
    started = datetime.now(timezone.utc)
    before = process_snapshot()
    child = subprocess.Popen(
        command,
        cwd=str(soffice.parent),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    timed_out = False
    cleanup: dict[str, Any] | None = None
    telemetry_path = case_root / "progress_telemetry.jsonl"
    telemetry: list[dict[str, Any]] = []
    previous_tree_metrics: dict[str, Any] | None = None

    def sample() -> dict[str, Any]:
        nonlocal previous_tree_metrics
        now = datetime.now(timezone.utc)
        metrics = owned_process_metrics(child.pid)
        tree_metrics = launched_libreoffice_metrics(started)
        cpu_delta = None
        memory_delta = None
        if previous_tree_metrics is not None:
            if tree_metrics.get("cpu_seconds") is not None and previous_tree_metrics.get("cpu_seconds") is not None:
                cpu_delta = round(float(tree_metrics["cpu_seconds"]) - float(previous_tree_metrics["cpu_seconds"]), 6)
            if tree_metrics.get("working_set_bytes") is not None and previous_tree_metrics.get("working_set_bytes") is not None:
                memory_delta = int(tree_metrics["working_set_bytes"]) - int(previous_tree_metrics["working_set_bytes"])
        previous_tree_metrics = tree_metrics
        record = {
            "timestamp": now.isoformat(),
            "elapsed_seconds": round((now - started).total_seconds(), 6),
            "owned_pid": child.pid,
            "process_alive": child.poll() is None,
            "process_returncode": child.poll(),
            "owned_process": metrics,
            "owned_process_tree": tree_metrics,
            "cpu_delta_seconds": cpu_delta,
            "working_set_delta_bytes": memory_delta,
            "activity": activity_snapshot(output=output, profile=profile),
        }
        telemetry.append(record)
        with telemetry_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    next_sample = time.monotonic()
    deadline = next_sample + timeout_seconds
    while child.poll() is None:
        current = time.monotonic()
        if current >= next_sample:
            sample()
            next_sample = current + sample_interval_seconds
        if current >= deadline:
            timed_out = True
            cleanup = terminate_owned_tree(child.pid)
            try:
                child.kill()  # Popen PID only; taskkill above owns its child tree.
            except ProcessLookupError:
                pass
            break
        time.sleep(min(0.25, max(0.01, next_sample - current), max(0.01, deadline - current)))

    # Preserve a final observation even if the process exits immediately.
    sample()
    try:
        stdout, stderr = child.communicate(timeout=5)
    except subprocess.TimeoutExpired as after_cleanup:
        # A detached soffice.bin can retain an inherited pipe after the owned
        # process tree is gone.  Never let that keep a bounded probe alive.
        stdout, stderr = _timeout_text(after_cleanup.stdout), _timeout_text(after_cleanup.stderr)
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()
    elapsed_seconds = (datetime.now(timezone.utc) - started).total_seconds()
    (case_root / "stdout.txt").write_text(stdout, encoding="utf-8", newline="\n")
    (case_root / "stderr.txt").write_text(stderr, encoding="utf-8", newline="\n")
    pdfs = sorted(output.glob("*.pdf"))
    pdf = pdfs[0] if len(pdfs) == 1 else None
    crash_files = [
        str(path.relative_to(case_root))
        for path in sorted(profile.rglob("*"))
        if path.is_file() and ("crash" in str(path).lower() or path.name.lower() in {"dump.ini", "crashlog.txt"})
    ]
    result = {
        "name": name,
        "document": str(document.resolve()),
        "document_sha256": sha256_file(document),
        "soffice": str(soffice),
        "cwd": str(soffice.parent),
        "command": command,
        "profile": str(profile),
        "pythonhome_mode": "UNSET" if pythonhome is None else "SENTINEL",
        "pythonhome_value": pythonhome,
        "started_at": started.isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "owned_pid": child.pid,
        "exit_code": child.returncode,
        "timed_out": timed_out,
        "owned_tree_cleanup": cleanup,
        "sample_interval_seconds": sample_interval_seconds,
        "no_progress_window_seconds": no_progress_window_seconds,
        "telemetry_path": str(telemetry_path.resolve()),
        "stdout_path": str((case_root / "stdout.txt").resolve()),
        "stderr_path": str((case_root / "stderr.txt").resolve()),
        "pdf_paths": [str(value.resolve()) for value in pdfs],
        "pdf_exists": pdf is not None and pdf.is_file(),
        "pdf_size_bytes": None if pdf is None else pdf.stat().st_size,
        "pdf_sha256": None if pdf is None else sha256_file(pdf),
        "profile_crash_artifacts": crash_files,
        "process_snapshot_before_path": str((case_root / "process_before.json").resolve()),
        "process_snapshot_after_path": str((case_root / "process_after.json").resolve()),
    }
    result["renderer_ready"] = conversion_ready(result)
    result["runtime_outcome"] = classify_runtime_outcome(
        result=result,
        telemetry=telemetry,
        no_progress_window_seconds=no_progress_window_seconds,
    )
    (case_root / "process_before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (case_root / "process_after.json").write_text(json.dumps(process_snapshot(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (case_root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def write_minimal_docx(path: Path) -> None:
    """Create a dependency-free DOCX control; no production source is used."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>""")
        archive.writestr("_rels/.rels", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/></Relationships>""")
        archive.writestr("word/document.xml", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>LibreOffice renderer control sentence.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>""")


def inspect_installation(installation: Path) -> dict[str, Any]:
    program = installation / "program"
    patterns = ("python-core*", "python.exe", "python*.dll", "pyuno*", "uno*", "fundamental.ini", "bootstrap.ini", "*.ini")
    found = {pattern: [str(path.resolve()) for path in program.glob(pattern)] for pattern in patterns}
    required = {
        "soffice.com": (program / "soffice.com").is_file(),
        "soffice.exe": (program / "soffice.exe").is_file(),
        "fundamental.ini": (program / "fundamental.ini").is_file(),
        "bootstrap.ini": (program / "bootstrap.ini").is_file(),
        "python-core": any(program.glob("python-core*")),
        "python313.dll": (program / "python313.dll").is_file(),
        "pyuno.pyd": (program / "pyuno.pyd").is_file(),
    }
    return {"installation": str(installation), "required": required, "found": found, "appears_complete": all(required.values())}


def event_log_excerpt(since: datetime) -> dict[str, Any]:
    """Best-effort, read-only Application-log collection around this probe."""
    command = ["wevtutil", "qe", "Application", "/rd:true", "/c:150", "/f:RenderedText"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        blocks = completed.stdout.split("\r\n\r\n")
        matches = [block for block in blocks if any(token in block.lower() for token in ("soffice", "libreoffice", "application error"))]
        return {"command": command, "exit_code": completed.returncode, "stderr": completed.stderr, "matches": matches[:20], "since": since.isoformat()}
    except Exception as exc:
        return {"command": command, "error": f"{type(exc).__name__}:{exc}", "since": since.isoformat(), "matches": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", type=Path, required=True, help="Existing smoke candidate; read only.")
    parser.add_argument("--source", type=Path, help="Optional immutable source DOCX read-only control.")
    parser.add_argument("--soffice", type=Path, default=DEFAULT_SOFFICE)
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--sample-interval-seconds", type=float, default=DEFAULT_SAMPLE_INTERVAL_SECONDS)
    parser.add_argument("--no-progress-window-seconds", type=float, default=DEFAULT_NO_PROGRESS_WINDOW_SECONDS)
    parser.add_argument(
        "--pythonhome-mode",
        choices=("unset", "sentinel", "both"),
        default="both",
        help="Use unset for representative probes once the two-variant experiment is complete.",
    )
    parser.add_argument("--case", action="append", choices=("minimal", "real", "source"), help="Run only named case(s); defaults to minimal and real, plus source when supplied.")
    args = parser.parse_args()
    if (
        not args.soffice.is_file()
        or not args.real.is_file()
        or args.timeout_seconds <= 0
        or args.sample_interval_seconds <= 0
        or args.no_progress_window_seconds <= 0
    ):
        raise SystemExit("RENDERER_PROBE_INPUT_MISSING")
    probe = args.runs / f"renderer-probe-{utc_stamp()}"
    probe.mkdir(parents=True, exist_ok=False)
    minimal = probe / "minimal-control.docx"
    write_minimal_docx(minimal)
    started = datetime.now(timezone.utc)
    results = []
    documents = {"minimal": minimal, "real": args.real}
    if args.source:
        documents["source"] = args.source
    selected_cases = args.case or list(documents)
    if any(name not in documents for name in selected_cases):
        raise SystemExit("RENDERER_PROBE_CASE_INPUT_MISSING")
    modes = (("pythonhome-unset", None),) if args.pythonhome_mode == "unset" else (("pythonhome-sentinel", "hello"),) if args.pythonhome_mode == "sentinel" else (("pythonhome-unset", None), ("pythonhome-sentinel", "hello"))
    for mode, value in modes:
        for label in selected_cases:
            document = documents[label]
            results.append(
                run_conversion(
                    soffice=args.soffice,
                    document=document,
                    probe_root=probe,
                    name=f"{label}-{mode}",
                    pythonhome=value,
                    timeout_seconds=args.timeout_seconds,
                    sample_interval_seconds=args.sample_interval_seconds,
                    no_progress_window_seconds=args.no_progress_window_seconds,
                )
            )
    report = {
        "artifact_type": "LIBREOFFICE_RENDERER_PROBE",
        "schema_version": 1,
        "probe_root": str(probe.resolve()),
        "started_at": started.isoformat(),
        "installation": inspect_installation(args.soffice.parent.parent),
        "results": results,
        "readiness": renderer_readiness_levels(results),
        "application_event_log": event_log_excerpt(started),
    }
    (probe / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"probe_root": str(probe.resolve()), "results": [{key: item[key] for key in ("name", "exit_code", "timed_out", "pdf_exists", "pdf_size_bytes", "runtime_outcome")} for item in results]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
