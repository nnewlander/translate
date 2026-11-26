"""Local durable writes for trusted batch review artifacts."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

from .generator import ExplorerInputError


def write_review_artifact(path: Path, value: dict) -> None:
    if path.name.lower().endswith(".pending"):
        raise ExplorerInputError("Pending paths are reserved for review staging")
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".pending", delete=False
    ) as staging:
        pending = Path(staging.name)
    try:
        with pending.open("wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            # Atomic no-clobber publication on the same local filesystem.
            if os.name == "nt":
                os.rename(pending, path)
            else:
                os.link(pending, path)
        except FileExistsError as error:
            if not path.is_file() or path.read_bytes() != content:
                raise ExplorerInputError("Existing review artifact differs") from error
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def local_publication_lock(path: Path):
    """Nonblocking OS lock. The process closing the file releases the lock."""
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ExplorerInputError("Another local process is publishing to this Issue") from error
        yield
