from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from booktrans.architecture_explorer.candidate_scanner import AUXILIARY_TEST_FILES, PRODUCTION_FILES


PROJECT = Path(__file__).resolve().parents[2]


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def commit(repository: Path, message: str) -> None:
    git(repository, "add", ".")
    git(
        repository,
        "-c", "user.name=Architecture Explorer Test",
        "-c", "user.email=explorer-test@example.invalid",
        "-c", "commit.gpgSign=false",
        "-c", "core.hooksPath=NUL",
        "commit", "--quiet", "-m", message,
    )


def create_source_repository(root: Path) -> Path:
    repository = root / "source"
    repository.mkdir()
    for relative in (*PRODUCTION_FILES, *AUXILIARY_TEST_FILES):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / relative, destination)
    (repository / ".gitignore").write_text("output/\n", encoding="utf-8")
    git(repository, "init", "--quiet")
    commit(repository, "fixture")
    return repository


__all__ = ["commit", "create_source_repository", "git"]
