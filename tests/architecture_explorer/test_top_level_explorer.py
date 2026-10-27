from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from booktrans.architecture_explorer.generator import (
    ExplorerInputError,
    build_explorer,
    scan_snapshot,
    write_json,
    write_site,
)


REPOSITORY = Path(__file__).resolve().parents[2]
MAPPING = REPOSITORY / "architecture-explorer" / "modules.json"
WEB_ASSETS = REPOSITORY / "architecture-explorer" / "web"


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


@pytest.fixture()
def source_snapshot(tmp_path: Path) -> Path:
    repository = tmp_path / "source-snapshot"
    repository.mkdir()
    (repository / "marker.txt").write_text("immutable source snapshot\n", encoding="utf-8")
    _git(repository, "init", "--quiet")
    _git(repository, "add", "marker.txt")
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


def _mapping(tmp_path: Path, mutate=None) -> Path:
    value = json.loads(MAPPING.read_text(encoding="utf-8"))
    if mutate:
        mutate(value)
    destination = tmp_path / "module-map.json"
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def _build(tmp_path: Path, source_snapshot: Path, mapping_path: Path):
    snapshot = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, snapshot)
    return build_explorer(snapshot_path, mapping_path)


def test_generates_seven_approved_modules_with_separate_unverified_coverage(
    tmp_path: Path, source_snapshot: Path
) -> None:
    data = _build(tmp_path, source_snapshot, _mapping(tmp_path))

    modules = data["architecture"]["modules"]
    assert len(modules) == 7
    assert all(module["logical_definition"]["status"] == "approved" for module in modules)
    assert all(module["source_coverage"] == {"status": "unverified", "evidence": []} for module in modules)
    assert data["snapshot"]["worktree"]["state"] == "clean"
    assert data["snapshot"]["input_files"][0]["path"] == "marker.txt"
    assert data["generation_inputs"]["mapping"]["sha256"]


def test_same_inputs_have_same_facts_and_review_states_despite_scan_time(
    tmp_path: Path, source_snapshot: Path
) -> None:
    mapping_path = _mapping(tmp_path)
    first = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")
    second = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T01:00:00Z")
    assert first["facts_sha256"] == second["facts_sha256"]

    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    write_json(first_path, first)
    write_json(second_path, second)
    first_data = build_explorer(first_path, mapping_path)
    second_data = build_explorer(second_path, mapping_path)
    assert first_data["architecture"] == second_data["architecture"]
    assert first_data["deterministic_content_sha256"] == second_data["deterministic_content_sha256"]


def test_mapping_change_flows_to_generated_page_without_frontend_module_list(
    tmp_path: Path, source_snapshot: Path
) -> None:
    renamed = "文档语义与稳定定位"
    mapping_path = _mapping(tmp_path, lambda value: value["modules"][0].update(name=renamed))
    data = _build(tmp_path, source_snapshot, mapping_path)
    site = tmp_path / "site"
    write_site(data, site, WEB_ASSETS)

    assert renamed in (site / "explorer-data.js").read_text(encoding="utf-8")
    assert renamed not in (site / "app.js").read_text(encoding="utf-8")
    assert "文档理解与定位" not in (site / "index.html").read_text(encoding="utf-8")
    assert (site / "index.html").is_file()


def test_generated_site_has_only_local_assets_and_no_runtime_fetch(
    tmp_path: Path, source_snapshot: Path
) -> None:
    data = _build(tmp_path, source_snapshot, _mapping(tmp_path))
    site = tmp_path / "site"
    write_site(data, site, WEB_ASSETS)

    combined = "\n".join(
        (site / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js", "styles.css", "explorer-data.js")
    )
    assert "http://" not in combined
    assert "https://" not in combined
    assert "fetch(" not in combined
    assert "XMLHttpRequest" not in combined
    assert 'src="explorer-data.js"' in combined


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["modules"].pop(), "exactly seven"),
        (
            lambda value: value["modules"][0]["source_coverage"].update(
                status="unverified", evidence=[{"path": "unreviewed.py"}]
            ),
            "cannot claim source evidence",
        ),
        (
            lambda value: value["modules"][0]["logical_definition"].update(status="candidate"),
            "approved logical definition",
        ),
    ],
)
def test_invalid_human_mapping_is_rejected(
    tmp_path: Path, source_snapshot: Path, mutate, message: str
) -> None:
    with pytest.raises(ExplorerInputError, match=message):
        _build(tmp_path, source_snapshot, _mapping(tmp_path, mutate))


def test_snapshot_facts_are_independent_and_tamper_evident(tmp_path: Path, source_snapshot: Path) -> None:
    mapping_path = _mapping(tmp_path)
    snapshot = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")
    tampered = copy.deepcopy(snapshot)
    tampered["revision"] = "not-the-scanned-revision"
    snapshot_path = tmp_path / "snapshot.json"
    write_json(snapshot_path, tampered)

    with pytest.raises(ExplorerInputError, match="facts hash"):
        build_explorer(snapshot_path, mapping_path)


def test_scanner_reads_only_explicit_files(tmp_path: Path, source_snapshot: Path) -> None:
    forbidden = source_snapshot / "large-production-output"
    forbidden.mkdir()
    (forbidden / "must-not-be-read.bin").write_bytes(b"not an input")

    snapshot = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")

    assert [item["path"] for item in snapshot["input_files"]] == ["marker.txt"]
