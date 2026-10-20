from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class FileFingerprint:
    sha256: str
    size_bytes: int
    mtime_utc: datetime


class SourceMutatedError(RuntimeError):
    code = "SOURCE_MUTATED"


def fingerprint(path: Path) -> FileFingerprint:
    stat = path.stat()
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return FileFingerprint(
        sha256=digest.hexdigest(),
        size_bytes=stat.st_size,
        mtime_utc=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
    )


def assert_unchanged(before: FileFingerprint, after: FileFingerprint, path: Path) -> None:
    if before.sha256 != after.sha256:
        raise SourceMutatedError(
            "SOURCE_MUTATED: source SHA-256 changed during inspect; investigate writers or external edits. "
            f"path={path} before={before.sha256} after={after.sha256}"
        )
