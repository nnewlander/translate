from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
PREPARE_SCRIPT = REPOSITORY / "scripts/architecture_explorer/prepare_worktree.ps1"


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", *arguments], cwd=repository)


def _shared_repository() -> Path:
    return Path(
        _git(REPOSITORY, "rev-parse", "--path-format=absolute", "--git-common-dir")
        .stdout.strip()
    ).parent


def _copy_prepare_script(worktree: Path) -> None:
    destination = worktree / PREPARE_SCRIPT.relative_to(REPOSITORY)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PREPARE_SCRIPT, destination)


def _remove_junction(path: Path) -> None:
    if path.exists():
        os.rmdir(path)


def _create_junction(path: Path, target: Path) -> None:
    _run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            f"New-Item -ItemType Junction -Path '{path}' -Target '{target}' | Out-Null",
        ],
        cwd=path.parent,
    )


@pytest.fixture
def temporary_architecture_explorer_worktree() -> Path:
    shared_repository = _shared_repository()
    worktree = (
        shared_repository
        / "worktrees"
        / f"architecture-explorer-prepare-test-{uuid.uuid4().hex}"
    )
    created = False
    try:
        _git(REPOSITORY, "worktree", "add", "--detach", str(worktree), "HEAD")
        created = True
        _copy_prepare_script(worktree)
        yield worktree
    finally:
        if created:
            _remove_junction(worktree / ".venv")
            _git(REPOSITORY, "worktree", "remove", "--force", str(worktree))


def _status(worktree: Path) -> str:
    return _git(worktree, "status", "--porcelain=v1", "--untracked-files=all").stdout


def _prepare(worktree: Path) -> subprocess.CompletedProcess[str]:
    script = worktree / PREPARE_SCRIPT.relative_to(REPOSITORY)
    return _run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=worktree,
        check=False,
    )


def test_missing_worktree_environment_is_created_and_reused_without_git_changes(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    before = _status(worktree)

    created = _prepare(worktree)

    assert created.returncode == 0, created.stderr
    assert "junction=created" in created.stdout
    assert f"booktrans={worktree / 'src/booktrans/__init__.py'}" in created.stdout
    assert "PYTHONPATH_SCOPE=process" in created.stdout
    assert _status(worktree) == before

    reused = _prepare(worktree)

    assert reused.returncode == 0, reused.stderr
    assert "junction=reused" in reused.stdout
    assert _status(worktree) == before


def test_incorrect_worktree_environment_junction_is_rejected_without_overwrite(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    wrong_environment = (
        worktree.parent / f"wrong-environment-{uuid.uuid4().hex}"
    )
    wrong_environment.mkdir()
    junction = worktree / ".venv"
    try:
        _create_junction(junction, wrong_environment)
        before = _status(worktree)
        result = _prepare(worktree)

        assert result.returncode == 2
        assert "PROJECT_VENV_UNAVAILABLE" in result.stdout
        assert "junction has an unexpected target" in result.stderr
        assert os.path.samefile(junction, wrong_environment)
        assert _status(worktree) == before
    finally:
        _remove_junction(junction)
        shutil.rmtree(wrong_environment)


def test_booktrans_import_outside_worktree_source_is_rejected(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    shared_environment = worktree.parent.parent / ".venv"
    junction = worktree / ".venv"
    _create_junction(junction, shared_environment)
    (worktree / "src/booktrans/__init__.py").unlink()
    before = _status(worktree)

    result = _prepare(worktree)

    assert result.returncode == 2
    assert "PROJECT_VENV_UNAVAILABLE" in result.stdout
    assert "booktrans import escaped worktree source" in result.stderr
    assert _status(worktree) == before


def test_existing_environment_directory_is_rejected_without_overwrite(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    environment = worktree / ".venv"
    environment.mkdir()
    marker = environment / "owner-data.txt"
    marker.write_text("preserve", encoding="utf-8")
    before = _status(worktree)
    try:
        result = _prepare(worktree)

        assert result.returncode == 2
        assert "is not a directory junction" in result.stderr
        assert marker.read_text(encoding="utf-8") == "preserve"
        assert _status(worktree) == before
    finally:
        shutil.rmtree(environment)


def test_worktree_outside_authorized_parent_is_rejected() -> None:
    shared_repository = _shared_repository()
    worktree = (
        shared_repository
        / f"architecture-explorer-prepare-outside-{uuid.uuid4().hex}"
    )
    created = False
    try:
        _git(REPOSITORY, "worktree", "add", "--detach", str(worktree), "HEAD")
        created = True
        _copy_prepare_script(worktree)
        before = _status(worktree)

        result = _prepare(worktree)

        assert result.returncode == 2
        assert "outside the authorized Architecture Explorer path" in result.stderr
        assert not (worktree / ".venv").exists()
        assert _status(worktree) == before
    finally:
        if created:
            _git(REPOSITORY, "worktree", "remove", "--force", str(worktree))
