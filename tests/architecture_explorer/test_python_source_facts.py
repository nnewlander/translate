from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
import sys

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.cli import main as explorer_main
from booktrans.architecture_explorer.source_scanner import scan_python_source_facts


PROJECT = Path(__file__).resolve().parents[2]
MODULE_MAP = PROJECT / "architecture-explorer/modules.json"
WEB_ASSETS = PROJECT / "architecture-explorer/web"


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write(repository: Path, relative: str, source: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.fixture()
def source_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    _write(repository, "src/booktrans/__init__.py", "\"\"\"fixture package\"\"\"\n")
    _write(
        repository,
        "src/booktrans/cli.py",
        "import typer\n"
        "from booktrans.domain import models as domain_models\n\n"
        "app = typer.Typer()\n",
    )
    _write(
        repository,
        "src/booktrans/__main__.py",
        "from booktrans.main import main\n\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n",
    )
    _write(
        repository,
        "scripts/production/run_qwen_deterministic_worker.py",
        "from booktrans.production_worker.worker import main\n\n"
        "if __name__ == \"__main__\":\n"
        "    raise SystemExit(main())\n",
    )
    _write(
        repository,
        "src/booktrans/architecture_explorer/internal.py",
        "raise RuntimeError('Explorer implementation must be excluded')\n",
    )
    _write(
        repository,
        "scripts/architecture_explorer/internal.py",
        "raise RuntimeError('Explorer script must be excluded')\n",
    )
    _write(
        repository,
        "tests/test_not_first_party.py",
        "raise RuntimeError('tests are outside the T6-1 scan scope')\n",
    )
    _git(repository, "init", "--quiet")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=explorer-test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=NUL",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return repository


def test_fixed_source_scan_is_stable_shallow_and_does_not_assign_architecture(
    source_repository: Path,
) -> None:
    first = scan_python_source_facts(source_repository)
    second = scan_python_source_facts(source_repository)

    assert first == second
    assert first["schema"] == "architecture-explorer.python-source-facts.v1"
    assert first["scope"]["files_scanned"] == 4
    assert first["scope"]["excluded_prefixes"] == [
        "scripts/architecture_explorer/",
        "src/booktrans/architecture_explorer/",
    ]
    assert first["limitations"] == [
        "Direct imports are static source facts; they do not prove runtime reachability or collaboration.",
        "No scanned file is assigned to an approved architecture module.",
        "Dynamic imports, runtime calls, and a complete call graph are not collected.",
        "Files outside the declared scope are unexamined, not historical, inactive, or invalid.",
    ]

    modules = {item["path"]: item for item in first["modules"]}
    assert list(modules) == sorted(modules)
    assert "src/booktrans/architecture_explorer/internal.py" not in modules
    assert "scripts/architecture_explorer/internal.py" not in modules
    assert "tests/test_not_first_party.py" not in modules
    assert all("architecture_module" not in item for item in modules.values())

    cli = modules["src/booktrans/cli.py"]
    assert cli["module_id"] == "booktrans.cli"
    assert cli["file_sha256"]
    assert [(item["kind"], item["module"], item["names"]) for item in cli["direct_imports"]] == [
        ("import", "typer", ["typer"]),
        ("from", "booktrans.domain", ["models as domain_models"]),
    ]
    assert [item["kind"] for item in cli["entries"]] == ["declarative_cli"]

    package_entry = modules["src/booktrans/__main__.py"]
    assert {item["kind"] for item in package_entry["entries"]} == {
        "main_guard",
        "package_entry",
    }
    worker = modules["scripts/production/run_qwen_deterministic_worker.py"]
    assert worker["module_id"] == "scripts.production.run_qwen_deterministic_worker"
    assert {item["kind"] for item in worker["entries"]} == {
        "deterministic_worker_wrapper",
        "main_guard",
    }
    for module in modules.values():
        for fact in [*module["direct_imports"], *module["entries"]]:
            evidence = fact["evidence"]
            assert evidence["evidence_id"].startswith("evidence:sha256:")
            assert evidence["path"] == module["path"]
            assert evidence["current_location"]["line_start"] >= 1
            assert evidence["snippet"]


def test_scanner_parses_without_executing_source(source_repository: Path) -> None:
    _write(
        source_repository,
        "src/booktrans/dangerous.py",
        "raise RuntimeError('must never execute')\nimport os\n",
    )
    _git(source_repository, "add", "src/booktrans/dangerous.py")

    result = scan_python_source_facts(source_repository)

    dangerous = next(
        item for item in result["modules"] if item["path"] == "src/booktrans/dangerous.py"
    )
    assert [(item["kind"], item["module"]) for item in dangerous["direct_imports"]] == [
        ("import", "os")
    ]


def test_evidence_ids_ignore_blank_line_moves_but_file_hash_changes(
    source_repository: Path,
) -> None:
    first = scan_python_source_facts(source_repository)
    path = source_repository / "src/booktrans/cli.py"
    path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    second = scan_python_source_facts(source_repository)
    first_cli = next(item for item in first["modules"] if item["path"] == "src/booktrans/cli.py")
    second_cli = next(item for item in second["modules"] if item["path"] == "src/booktrans/cli.py")

    assert first_cli["file_sha256"] != second_cli["file_sha256"]
    assert [item["evidence"]["evidence_id"] for item in first_cli["direct_imports"]] == [
        item["evidence"]["evidence_id"] for item in second_cli["direct_imports"]
    ]
    assert [item["evidence"]["evidence_id"] for item in first_cli["entries"]] == [
        item["evidence"]["evidence_id"] for item in second_cli["entries"]
    ]


def test_worker_wrapper_entry_requires_the_imported_main_call(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/run_qwen_deterministic_worker.py",
        "from booktrans.production_worker.worker import main\n\n"
        "if __name__ == \"__main__\":\n"
        "    print('not the imported worker main')\n",
    )

    result = scan_python_source_facts(source_repository)

    worker = next(
        item
        for item in result["modules"]
        if item["path"] == "scripts/production/run_qwen_deterministic_worker.py"
    )
    assert [item["kind"] for item in worker["entries"]] == ["main_guard"]


def test_syntax_error_fails_the_whole_scan_conservatively(source_repository: Path) -> None:
    _write(source_repository, "src/booktrans/broken.py", "def broken(:\n")
    _git(source_repository, "add", "src/booktrans/broken.py")

    with pytest.raises(ExplorerInputError, match="syntax error.*src/booktrans/broken.py"):
        scan_python_source_facts(source_repository)


def test_file_change_during_single_read_fails_conservatively(
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.read_bytes
    changed = False

    def changing_read(path: Path) -> bytes:
        nonlocal changed
        raw = original(path)
        if path.name == "cli.py" and not changed:
            changed = True
            path.write_bytes(raw + b"\n# changed during scan\n")
        return raw

    monkeypatch.setattr(Path, "read_bytes", changing_read)

    with pytest.raises(ExplorerInputError, match="changed during scan.*src/booktrans/cli.py"):
        scan_python_source_facts(source_repository)


def test_linked_source_path_is_rejected_before_external_file_is_read(
    source_repository: Path,
    tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows reparse-point regression")
    linked = source_repository / "src/booktrans/linked"
    _write(source_repository, "src/booktrans/linked/module.py", "import pathlib\n")
    _git(source_repository, "add", "src/booktrans/linked/module.py")
    _git(
        source_repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=explorer-test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=NUL",
        "commit",
        "--quiet",
        "-m",
        "linked fixture",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.py").write_text(
        "raise RuntimeError('external file must not be read')\n",
        encoding="utf-8",
    )
    for child in linked.iterdir():
        child.unlink()
    linked.rmdir()
    junction = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if junction.returncode != 0:
        pytest.skip("Junction creation is unavailable in this test environment")
    try:
        with pytest.raises(ExplorerInputError, match="link|reparse"):
            scan_python_source_facts(source_repository)
    finally:
        linked.rmdir()


def test_offline_build_adds_source_facts_without_changing_seven_module_trust_state(
    source_repository: Path,
) -> None:
    assert explorer_main(
        [
            "scan",
            "--repository",
            str(source_repository),
            "--source-file",
            "src/booktrans/__init__.py",
            "--output",
            "output/snapshot.json",
        ]
    ) == 0
    assert explorer_main(
        [
            "build",
            "--repository",
            str(source_repository),
            "--snapshot",
            str(source_repository / "output/snapshot.json"),
            "--mapping",
            str(MODULE_MAP),
            "--web-assets",
            str(WEB_ASSETS),
            "--output-directory",
            "output/site",
        ]
    ) == 0

    data = json.loads(
        (source_repository / "output/site/explorer-data.json").read_text(encoding="utf-8")
    )
    assert data["source_facts"]["scope"]["files_scanned"] == 4
    assert len(data["architecture"]["modules"]) == 7
    assert all(
        module["source_coverage"] == {"status": "unverified", "evidence": []}
        for module in data["architecture"]["modules"]
    )
    assert "relationships" not in data["architecture"]
    assert "source_facts" not in data["architecture"]
    page = (source_repository / "output/site/index.html").read_text(encoding="utf-8")
    app = (source_repository / "output/site/app.js").read_text(encoding="utf-8")
    assert 'id="source-facts"' in page
    assert 'id="source-module-select"' in page
    assert "renderSourceModule" in app


def test_real_browser_shows_one_source_module_on_demand(source_repository: Path) -> None:
    browser_candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    browser = next((path for path in browser_candidates if path.is_file()), None)
    if browser is None:
        pytest.skip("Chrome or Edge headless browser is unavailable")
    assert explorer_main(
        [
            "scan", "--repository", str(source_repository),
            "--source-file", "src/booktrans/__init__.py",
            "--output", "output/snapshot.json",
        ]
    ) == 0
    assert explorer_main(
        [
            "build", "--repository", str(source_repository),
            "--snapshot", str(source_repository / "output/snapshot.json"),
            "--mapping", str(MODULE_MAP),
            "--web-assets", str(WEB_ASSETS),
            "--output-directory", "output/site",
        ]
    ) == 0
    site = source_repository / "output/site"
    shutil.copyfile(
        PROJECT / "tests/architecture_explorer/t6_source_facts_dom.js",
        site / "t6_source_facts_dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t6_source_facts_dom.js"></script></body>'
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-breakpad",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            f"--user-data-dir={source_repository / 'browser-profile'}",
            "--virtual-time-budget=2000",
            "--dump-dom",
            page.resolve().as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'data-t6-source-facts="pass"' in completed.stdout, completed.stdout
