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
        "PowerShell facts are limited to the single production Supervisor; no general PowerShell call graph is built.",
        "Dynamic or ambiguous PowerShell paths remain unknown and do not prove script execution or architecture collaboration.",
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


def test_supervisor_powershell_ast_reports_only_static_entries_and_bindings(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "param([string]$StateRebuildScript)\n"
        "$stateScript = if ($StateRebuildScript) { $StateRebuildScript } else { "
        "Join-Path $Root 'scripts\\production\\rebuild_production_campaign_state.py' }\n"
        "$controlPlaneAdapterScript = Join-Path $Root "
        "'scripts\\production\\control_plane_decision_adapters.ps1'\n"
        "$deterministicWorkerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "$promptPath = Join-Path $Root 'scripts\\production\\production_worker_prompt.txt'\n"
        "$ambiguousScript = Join-Path $Root 'scripts\\production\\first.ps1'\n"
        "$ambiguousScript = Join-Path $Root 'scripts\\production\\second.ps1'\n"
        ". $controlPlaneAdapterScript\n"
        ". $ambiguousScript\n"
        "function Start-DeterministicWorkerProcess { throw 'must never execute' }\n"
        "function Refresh-State { throw 'must never execute' }\n"
        "function Unrelated-Helper { throw 'outside the bounded entry set' }\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    assert result["powershell_scope"] == {
        "selection": "The single production Supervisor script",
        "paths": ["scripts/production/production_campaign_supervisor.ps1"],
        "files_scanned": 1,
    }
    supervisor = result["powershell_scripts"][0]
    assert supervisor["path"] == "scripts/production/production_campaign_supervisor.ps1"
    assert supervisor["parse_status"] == "parsed"
    assert [item["symbol"] for item in supervisor["entries"]] == [
        "Refresh-State",
        "Start-DeterministicWorkerProcess",
    ]
    bindings = {item["variable"]: item for item in supervisor["script_bindings"]}
    assert bindings["controlPlaneAdapterScript"]["resolution"] == "known"
    assert bindings["controlPlaneAdapterScript"]["target_path"] == (
        "scripts/production/control_plane_decision_adapters.ps1"
    )
    assert bindings["deterministicWorkerScript"]["resolution"] == "known"
    assert "promptPath" not in bindings
    assert bindings["stateScript"]["resolution"] == "unknown"
    assert bindings["stateScript"]["target_path"] is None
    assert bindings["stateScript"]["candidate_paths"] == [
        "scripts/production/rebuild_production_campaign_state.py"
    ]
    ambiguous_bindings = [
        item
        for item in supervisor["script_bindings"]
        if item["variable"] == "ambiguousScript"
    ]
    assert len(ambiguous_bindings) == 2
    assert all(item["resolution"] == "unknown" for item in ambiguous_bindings)
    assert all(item["target_path"] is None for item in ambiguous_bindings)
    dot_sources = {item["symbol"]: item for item in supervisor["dot_sources"]}
    assert dot_sources["$controlPlaneAdapterScript"]["resolution"] == "known"
    assert dot_sources["$controlPlaneAdapterScript"]["target_path"] == (
        "scripts/production/control_plane_decision_adapters.ps1"
    )
    assert dot_sources["$ambiguousScript"]["resolution"] == "unknown"
    assert dot_sources["$ambiguousScript"]["target_path"] is None
    for fact in [
        *supervisor["entries"],
        *supervisor["script_bindings"],
        *supervisor["dot_sources"],
    ]:
        assert fact["evidence"]["path"] == supervisor["path"]
        assert fact["evidence"]["snippet"]


def test_supervisor_powershell_parse_errors_remain_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root 'scripts\\production\\worker.py'\n"
        "function Refresh-State {\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    assert supervisor["parse_status"] == "parse-error"
    assert supervisor["parse_errors"]
    assert supervisor["entries"] == []
    assert supervisor["script_bindings"] == []
    assert supervisor["dot_sources"] == []


def test_supervisor_conditional_binding_does_not_make_dot_source_known(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "if ($flag) {\n"
        "    $workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "}\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["variable"] == "workerScript"
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    dot_source = supervisor["dot_sources"][0]
    assert dot_source["symbol"] == "$workerScript"
    assert dot_source["resolution"] == "unknown"
    assert dot_source["target_path"] is None


def test_supervisor_function_local_binding_does_not_pollute_script_dot_source(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$WorkerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "function Set-LocalWorker {\n"
        "    $WORKERSCRIPT = Join-Path $Root 'scripts\\production\\local_worker.py'\n"
        "    . $WORKERSCRIPT\n"
        "}\n"
        ". $workerscript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    assert [item["target_path"] for item in supervisor["script_bindings"]] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert len(supervisor["dot_sources"]) == 1
    dot_source = supervisor["dot_sources"][0]
    assert dot_source["symbol"] == "$workerscript"
    assert dot_source["resolution"] == "known"
    assert dot_source["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )


def test_supervisor_nested_script_scope_write_invalidates_root_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "function Set-WorkerOverride {\n"
        "    $script:WORKERSCRIPT = Join-Path $Root "
        "'scripts\\production\\alternate_worker.py'\n"
        "}\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert {tuple(item["candidate_paths"]) for item in bindings} == {
        ("scripts/production/alternate_worker.py",),
        ("scripts/production/run_qwen_deterministic_worker.py",),
    }
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"
    assert supervisor["dot_sources"][0]["target_path"] is None


def test_supervisor_foreach_target_invalidates_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "foreach ($WORKERSCRIPT in @('dynamic')) { Write-Output $WORKERSCRIPT }\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert {item["operator"] for item in bindings} == {"Equals", "ForEach"}
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_destructuring_target_invalidates_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "$WORKERSCRIPT, $other = @('dynamic', 'ignored')\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert {item["operator"] for item in bindings} == {"Equals", "Destructuring"}
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_set_variable_target_invalidates_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "Set-Variable -Name WORKERSCRIPT -Value 'dynamic'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert {item["operator"] for item in bindings} == {"Equals", "SetVariable"}
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_dynamic_set_variable_name_invalidates_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "$targetName = 'workerScript'\n"
        "Set-Variable -Name $targetName -Value 'dynamic'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_set_variable_wildcard_name_invalidates_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "Set-Variable -Name 'worker*' -Value 'dynamic'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_join_path_function_shadow_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "function Join-Path { return 'scripts\\production\\alternate.py' }\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_nested_global_join_path_function_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "& { function global:Join-Path { return 'alternate' } }\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_join_path_alias_change_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "Set-Alias -Name Join-Path -Value CustomJoinPath\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_global_join_path_alias_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "Set-Alias -Name Join-Path -Value Write-Output -Scope Global\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_join_path_function_provider_change_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "Set-Item Function:\\Join-Path -Value { return 'alternate' }\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_nested_global_join_path_provider_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "& { Set-Item Function:\\global:Join-Path -Value { return 'alternate' } }\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_dynamic_item_path_before_join_path_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$providerPath = 'Function:\\Join-Path'\n"
        "Set-Item $providerPath -Value { return 'alternate' }\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_import_before_join_path_keeps_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "Import-Module $customCommands\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"


def test_supervisor_invoke_expression_between_binding_and_use_invalidates_dot_source(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "Invoke-Expression '$workerScript = Get-ConfiguredWorkerScript'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "known"
    assert binding["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )
    dot_source = supervisor["dot_sources"][0]
    assert dot_source["resolution"] == "unknown"
    assert dot_source["target_path"] is None


def test_supervisor_prior_dot_source_invalidates_later_binding_use(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". { $workerScript = Get-ConfiguredWorkerScript }\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["resolution"] == "known"
    assert binding["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )
    assert [item["resolution"] for item in supervisor["dot_sources"]] == [
        "unknown",
        "unknown",
    ]
    assert supervisor["dot_sources"][1]["target_path"] is None


def test_supervisor_dot_source_before_join_path_keeps_later_binding_unknown(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$bootstrapScript = Join-Path $Root 'scripts\\production\\bootstrap.ps1'\n"
        ". $bootstrapScript\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = {item["variable"]: item for item in supervisor["script_bindings"]}
    assert bindings["bootstrapScript"]["resolution"] == "known"
    assert bindings["workerScript"]["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert bindings["workerScript"]["resolution"] == "unknown"
    assert [item["resolution"] for item in supervisor["dot_sources"]] == [
        "known",
        "unknown",
    ]


def test_current_supervisor_retains_supported_known_facts() -> None:
    result = scan_python_source_facts(PROJECT)

    supervisor = result["powershell_scripts"][0]
    bindings = {item["variable"]: item for item in supervisor["script_bindings"]}
    assert bindings["controlPlaneAdapterScript"]["resolution"] == "known"
    assert bindings["controlPlaneAdapterScript"]["target_path"] == (
        "scripts/production/control_plane_decision_adapters.ps1"
    )
    assert bindings["deterministicWorkerScript"]["resolution"] == "known"
    assert bindings["deterministicWorkerScript"]["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )
    control_plane_dot_source = next(
        item
        for item in supervisor["dot_sources"]
        if item["symbol"] == "$controlPlaneAdapterScript"
    )
    assert control_plane_dot_source["resolution"] == "known"
    assert control_plane_dot_source["target_path"] == (
        "scripts/production/control_plane_decision_adapters.ps1"
    )
    entries = {item["symbol"]: item for item in supervisor["entries"]}
    assert entries["Start-DeterministicWorkerProcess"]["resolution"] == "known"
    assert entries["Refresh-State"]["resolution"] == "known"


def test_supervisor_variable_binding_ambiguity_is_case_insensitive(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$WorkerScript = Join-Path $Root 'scripts\\production\\first.py'\n"
        "$workerscript = Join-Path $Root 'scripts\\production\\second.py'\n"
        ". $workerscript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert {item["variable"] for item in bindings} == {"WorkerScript", "workerscript"}
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert all(item["target_path"] is None for item in bindings)
    assert {tuple(item["candidate_paths"]) for item in bindings} == {
        ("scripts/production/first.py",),
        ("scripts/production/second.py",),
    }
    dot_source = supervisor["dot_sources"][0]
    assert dot_source["symbol"] == "$workerscript"
    assert dot_source["resolution"] == "unknown"
    assert dot_source["target_path"] is None


def test_supervisor_augmented_assignment_never_establishes_known_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript += Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["operator"] == "PlusEquals"
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"
    assert supervisor["dot_sources"][0]["target_path"] is None


def test_supervisor_root_scope_qualifiers_share_case_insensitive_variable_identity(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$script:WorkerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        ". $local:workerscript\n"
        ". $private:WORKERSCRIPT\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["variable"] == "script:WorkerScript"
    assert binding["variable_scope"] == "script"
    assert binding["variable_name"] == "WorkerScript"
    assert binding["resolution"] == "known"
    assert binding["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )
    assert [item["symbol"] for item in supervisor["dot_sources"]] == [
        "$local:workerscript",
        "$private:WORKERSCRIPT",
    ]
    first_dot_source, second_dot_source = supervisor["dot_sources"]
    assert first_dot_source["resolution"] == "known"
    assert first_dot_source["target_path"] == (
        "scripts/production/run_qwen_deterministic_worker.py"
    )
    assert second_dot_source["resolution"] == "unknown"
    assert second_dot_source["target_path"] is None


def test_supervisor_dynamic_override_invalidates_prior_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n"
        "$workerScript = Get-ConfiguredWorkerScript\n"
        ". $workerScript\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 2
    assert bindings[0]["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert bindings[1]["candidate_paths"] == []
    assert bindings[1]["operator"] == "Equals"
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert supervisor["dot_sources"][0]["resolution"] == "unknown"
    assert supervisor["dot_sources"][0]["target_path"] is None


def test_supervisor_dot_source_cannot_use_or_establish_a_future_static_binding(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        ". $workerScript\n"
        "$workerScript = Join-Path $Root "
        "'scripts\\production\\run_qwen_deterministic_worker.py'\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    binding = supervisor["script_bindings"][0]
    assert binding["candidate_paths"] == [
        "scripts/production/run_qwen_deterministic_worker.py"
    ]
    assert binding["resolution"] == "unknown"
    assert binding["target_path"] is None
    dot_source = supervisor["dot_sources"][0]
    assert dot_source["symbol"] == "$workerScript"
    assert dot_source["resolution"] == "unknown"
    assert dot_source["target_path"] is None


def test_supervisor_whitelist_entries_require_unconditional_root_definitions(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        "function Container {\n"
        "    function Refresh-State { return 'nested' }\n"
        "}\n"
        "if ($flag) {\n"
        "    function Start-DeterministicWorkerProcess { return 'conditional' }\n"
        "}\n"
        "function invoke-controlplaneshadow { return 'root' }\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    entries = {item["symbol"].casefold(): item for item in supervisor["entries"]}
    assert entries["refresh-state"]["resolution"] == "unknown"
    assert entries["start-deterministicworkerprocess"]["resolution"] == "unknown"
    root = entries["invoke-controlplaneshadow"]
    assert root["symbol"] == "invoke-controlplaneshadow"
    assert root["resolution"] == "known"
    assert "function invoke-controlplaneshadow" in root["evidence"]["snippet"]


def test_supervisor_combined_ambiguity_never_produces_false_known_facts(
    source_repository: Path,
) -> None:
    _write(
        source_repository,
        "scripts/production/production_campaign_supervisor.ps1",
        ". $WorkerScript\n"
        "$script:workerscript = Join-Path $Root 'scripts\\production\\first.py'\n"
        "$local:WORKERSCRIPT = Get-ConfiguredWorkerScript\n"
        "$private:workerScript += Join-Path $Root 'scripts\\production\\extra.py'\n"
        "if ($flag) {\n"
        "    $workerscript = Join-Path $Root 'scripts\\production\\conditional.py'\n"
        "}\n"
        ". $WORKERSCRIPT\n"
        "function Container { function Refresh-State { return 'nested' } }\n"
        "if ($flag) { function Start-DeterministicWorkerProcess { return 'conditional' } }\n",
    )
    _git(
        source_repository,
        "add",
        "scripts/production/production_campaign_supervisor.ps1",
    )

    result = scan_python_source_facts(source_repository)

    supervisor = result["powershell_scripts"][0]
    bindings = supervisor["script_bindings"]
    assert len(bindings) == 4
    assert {item["operator"] for item in bindings} == {"Equals", "PlusEquals"}
    assert {tuple(item["candidate_paths"]) for item in bindings} == {
        (),
        ("scripts/production/conditional.py",),
        ("scripts/production/extra.py",),
        ("scripts/production/first.py",),
    }
    assert all(item["variable_scope"] == "script" for item in bindings)
    assert all(item["resolution"] == "unknown" for item in bindings)
    assert all(item["target_path"] is None for item in bindings)
    assert [item["resolution"] for item in supervisor["dot_sources"]] == [
        "unknown",
        "unknown",
    ]
    assert all(item["target_path"] is None for item in supervisor["dot_sources"])
    entries = {item["symbol"]: item for item in supervisor["entries"]}
    assert entries["Refresh-State"]["resolution"] == "unknown"
    assert entries["Start-DeterministicWorkerProcess"]["resolution"] == "unknown"


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
