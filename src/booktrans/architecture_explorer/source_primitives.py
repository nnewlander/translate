from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .generator import ExplorerInputError


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_output(repository: Path, *args: str) -> str:
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


def stat_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_stable_utf8(
    path: Path,
    relative: str,
    *,
    contract: str,
) -> tuple[str, str, int]:
    """Read one already-contained path once and reject an unstable identity."""

    before_signature = stat_signature(path)
    before_resolved = path.resolve(strict=True)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExplorerInputError(f"{contract} scan input could not be read: {relative}") from error
    try:
        after_signature = stat_signature(path)
        after_resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ExplorerInputError(f"{contract} scan input changed during scan: {relative}") from error
    if (
        before_signature != after_signature
        or before_resolved != after_resolved
        or len(raw) != after_signature[2]
    ):
        raise ExplorerInputError(f"{contract} scan input changed during scan: {relative}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExplorerInputError(f"{contract} scan input is not UTF-8: {relative}") from error
    return text, sha256_bytes(raw), len(raw)


def build_static_evidence(
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
    normalized_hash = sha256_bytes(" ".join(snippet.split()).encode("utf-8"))
    identity = {
        "path": path,
        "evidence_kind": evidence_kind,
        "symbol": symbol,
        "structural_anchor": structural_anchor,
        "syntax_position": syntax_position,
        "normalized_snippet_sha256": normalized_hash,
    }
    return {
        "evidence_id": f"evidence:sha256:{sha256_bytes(canonical_bytes(identity))}",
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


__all__ = [
    "build_static_evidence",
    "canonical_bytes",
    "git_output",
    "read_stable_utf8",
    "sha256_bytes",
    "stat_signature",
]
