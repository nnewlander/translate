"""Create and verify the immutable, recoverable B034 certification baseline."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(r"D:\translate")
ARCHIVE = Path(r"D:\booktrans-baselines")
DIRS = ("src", "scripts", "tests", "config", "docs")
FILES = (
    "AGENTS.md", "pyproject.toml", "README.md", "Start-Booktrans-Production.cmd",
    "Stop-Booktrans-Production.cmd", "Stop-Booktrans-Production.ps1",
    "Watch-Booktrans-Production.cmd", "Watch-Booktrans-Production.ps1",
)
EXCLUDED = {"runs", "output", "_test_tmp", "runs_test_tmp", ".venv", ".pytest_cache", "__pycache__", "tmp"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root: Path):
    for relative_root in DIRS:
        base = root / relative_root
        if base.is_dir():
            yield from (
                path for path in base.rglob("*")
                if path.is_file() and not any(
                    part in EXCLUDED or part.startswith(".pytest")
                    for part in path.relative_to(root).parts
                )
            )
    for name in FILES:
        path = root / name
        if path.is_file():
            yield path


def manifest(root: Path) -> list[dict[str, object]]:
    return [
        {"relative_path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(files(root), key=lambda item: item.relative_to(root).as_posix())
    ]


def main() -> None:
    entries = manifest(ROOT)
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    source_digest = hashlib.sha256(payload).hexdigest()
    baseline_id = f"booktrans-b034-baseline-{source_digest[:16]}"
    target = ARCHIVE / baseline_id / "snapshot"
    manifest_path = target.parent / "source_manifest.json"
    if target.exists():
        raise RuntimeError("B034_BASELINE_ALREADY_EXISTS")
    for entry in entries:
        source = ROOT / str(entry["relative_path"])
        destination = target / str(entry["relative_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    manifest_path.write_bytes(payload + b"\n")
    restored = manifest(target)
    mismatches = sum(left != right for left, right in zip(entries, restored, strict=True))
    if mismatches or len(entries) != len(restored):
        raise RuntimeError("B034_BASELINE_RESTORE_MISMATCH")
    print(json.dumps({
        "baseline_id": baseline_id,
        "source_digest": source_digest,
        "file_count": len(entries),
        "sha_mismatch": 0,
        "manifest": str(manifest_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
