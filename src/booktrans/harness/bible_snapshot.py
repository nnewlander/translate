"""Small immutable-snapshot proof for per-run Translation Bible use."""

from hashlib import sha256
from pathlib import Path
import shutil


def freeze_translation_bible(canonical: Path, snapshot: Path) -> str:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(canonical, snapshot)
    return snapshot_sha256(snapshot)


def snapshot_sha256(snapshot: Path) -> str:
    return sha256(snapshot.read_bytes()).hexdigest()


def assert_snapshot_unchanged(snapshot: Path, expected_sha256: str) -> None:
    actual = snapshot_sha256(snapshot)
    if actual != expected_sha256:
        raise ValueError(f"BIBLE_SNAPSHOT_MUTATED: path={snapshot}; expected={expected_sha256}; actual={actual}")
