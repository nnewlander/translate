from __future__ import annotations

import hashlib
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
    production_line = "$authorizedRepository = Get-NormalizedPath 'D:\\translate'"
    test_repository = str(_shared_repository()).replace("'", "''")
    test_line = f"$authorizedRepository = Get-NormalizedPath '{test_repository}'"
    script = PREPARE_SCRIPT.read_text(encoding="utf-8")
    assert script.count(production_line) == 1
    destination.write_text(
        script.replace(production_line, test_line),
        encoding="utf-8",
    )


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


def _prepare_observing_pythonpath(
    worktree: Path,
    initial_pythonpath: str | None,
) -> subprocess.CompletedProcess[str]:
    script = worktree / PREPARE_SCRIPT.relative_to(REPOSITORY)
    initialize = (
        "Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue"
        if initial_pythonpath is None
        else f"$env:PYTHONPATH = '{initial_pythonpath}'"
    )
    command = (
        f"{initialize}; try {{ & '{script}' }} finally {{ "
        "$scriptExitCode = $LASTEXITCODE; "
        "if (Test-Path Env:PYTHONPATH) { "
        "[Console]::Error.WriteLine('PYTHONPATH_AFTER=' + $env:PYTHONPATH) "
        "} else { [Console]::Error.WriteLine('PYTHONPATH_AFTER=<unset>') }; "
        "exit $scriptExitCode }"
    )
    return _run(
        ["pwsh", "-NoProfile", "-Command", command],
        cwd=worktree,
        check=False,
    )


def _install_failing_pip_module(
    worktree: Path,
    *,
    obstruct_rollback: bool = False,
) -> None:
    package = worktree / "src/pip"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    statements = [
        "print('simulated pip check failure')",
        "raise SystemExit(1)",
    ]
    if obstruct_rollback:
        statements[:0] = [
            "import os",
            "from pathlib import Path",
            "environment = Path(os.environ['PYTHONPATH']).parent / '.venv'",
            "os.rmdir(environment)",
            "environment.mkdir()",
        ]
    (package / "__main__.py").write_text(
        "\n".join(statements) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    shared_python = _shared_repository() / ".venv/Scripts/python.exe"
    shared_python_before = _sha256(shared_python)
    (worktree / "src/booktrans/__init__.py").unlink()
    before = _status(worktree)

    result = _prepare_observing_pythonpath(worktree, "owner-pythonpath")

    assert result.returncode == 2
    assert "PROJECT_VENV_UNAVAILABLE" in result.stdout
    assert "booktrans import escaped worktree source" in result.stderr
    assert "PYTHONPATH_AFTER=owner-pythonpath" in result.stderr
    assert not (worktree / ".venv").exists()
    assert _sha256(shared_python) == shared_python_before
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


def test_other_repository_cannot_use_architecture_explorer_authorization(
    tmp_path: Path,
) -> None:
    other_repository = tmp_path / "other-repository"
    _run(
        ["git", "clone", "--no-hardlinks", str(REPOSITORY), str(other_repository)],
        cwd=tmp_path,
    )
    _create_junction(other_repository / ".venv", _shared_repository() / ".venv")
    worktree = other_repository / "worktrees/architecture-explorer-other-repository"
    created = False
    try:
        _git(
            other_repository,
            "worktree",
            "add",
            "--detach",
            str(worktree),
            "HEAD",
        )
        created = True
        shutil.copy2(
            PREPARE_SCRIPT,
            worktree / PREPARE_SCRIPT.relative_to(REPOSITORY),
        )

        result = _prepare(worktree)

        assert result.returncode == 2
        assert "is not the authorized repository" in result.stderr
        assert not (worktree / ".venv").exists()
    finally:
        if created:
            _remove_junction(worktree / ".venv")
            _git(
                other_repository,
                "worktree",
                "remove",
                "--force",
                str(worktree),
            )
        _remove_junction(other_repository / ".venv")


def test_new_junction_is_rolled_back_when_pip_check_fails(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    _install_failing_pip_module(worktree)
    shared_python = _shared_repository() / ".venv/Scripts/python.exe"
    shared_python_before = _sha256(shared_python)
    status_before = _status(worktree)

    result = _prepare_observing_pythonpath(worktree, None)

    assert result.returncode == 2
    assert "pip check failed" in result.stderr
    assert "PYTHONPATH_AFTER=<unset>" in result.stderr
    assert not (worktree / ".venv").exists()
    assert _sha256(shared_python) == shared_python_before
    assert _status(worktree) == status_before


def test_existing_correct_junction_is_not_removed_when_validation_fails(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    shared_environment = _shared_repository() / ".venv"
    junction = worktree / ".venv"
    _create_junction(junction, shared_environment)
    _install_failing_pip_module(worktree)
    shared_python = shared_environment / "Scripts/python.exe"
    shared_python_before = _sha256(shared_python)
    status_before = _status(worktree)

    result = _prepare_observing_pythonpath(worktree, "owner-pythonpath")

    assert result.returncode == 2
    assert "pip check failed" in result.stderr
    assert "PYTHONPATH_AFTER=owner-pythonpath" in result.stderr
    assert os.path.samefile(junction, shared_environment)
    assert _sha256(shared_python) == shared_python_before
    assert _status(worktree) == status_before


def test_failed_rollback_reports_residual_path_without_recursive_delete(
    temporary_architecture_explorer_worktree: Path,
) -> None:
    worktree = temporary_architecture_explorer_worktree
    _install_failing_pip_module(worktree, obstruct_rollback=True)
    shared_python = _shared_repository() / ".venv/Scripts/python.exe"
    shared_python_before = _sha256(shared_python)
    status_before = _status(worktree)

    result = _prepare_observing_pythonpath(worktree, "owner-pythonpath")

    assert result.returncode == 2
    assert "PROJECT_VENV_ROLLBACK_INCOMPLETE" in result.stdout
    assert "Refusing to remove a non-Junction path" in result.stderr
    assert "PYTHONPATH_AFTER=owner-pythonpath" in result.stderr
    assert (worktree / ".venv").is_dir()
    assert _sha256(shared_python) == shared_python_before
    assert _status(worktree) == status_before
