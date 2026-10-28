from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

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
CLI = REPOSITORY / "scripts" / "architecture_explorer" / "generate_top_level.py"


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def _run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _resign_snapshot(snapshot: dict) -> None:
    deterministic_facts = {
        key: snapshot[key]
        for key in ("revision", "worktree", "scanner_version", "input_files")
    }
    canonical = json.dumps(
        deterministic_facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot["facts_sha256"] = hashlib.sha256(canonical).hexdigest()


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
    tampered["revision"] = "0" * 40
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


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda value: value.update(revision=7), "revision"),
        (lambda value: value.update(scanner_version={}), "scanner version"),
        (lambda value: value.update(scanned_at=[]), "scan time"),
        (lambda value: value.update(worktree="dirty"), "worktree"),
        (lambda value: value["worktree"].update(state="unknown"), "worktree state"),
        (lambda value: value["worktree"].update(porcelain=["M malformed"]), "porcelain"),
        (lambda value: value.update(input_files="marker.txt"), "input files"),
        (lambda value: value["input_files"][0].update(path="../marker.txt"), "relative"),
        (lambda value: value["input_files"][0].update(path="C:/marker.txt"), "relative"),
        (lambda value: value["input_files"][0].update(sha256="not-a-hash"), "SHA-256"),
        (lambda value: value["input_files"][0].update(size_bytes=-1), "size"),
        (lambda value: value["input_files"].append(copy.deepcopy(value["input_files"][0])), "unique"),
    ],
)
def test_structurally_invalid_resigned_snapshot_is_rejected(
    tmp_path: Path,
    source_snapshot: Path,
    change,
    message: str,
) -> None:
    snapshot = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")
    change(snapshot)
    _resign_snapshot(snapshot)
    snapshot_path = tmp_path / "invalid-snapshot.json"
    write_json(snapshot_path, snapshot)

    with pytest.raises(ExplorerInputError, match=message):
        build_explorer(snapshot_path, _mapping(tmp_path))


@pytest.mark.parametrize(
    "kind, expected_prefix",
    [("unstaged", " M "), ("staged", "M  "), ("untracked", "?? ")],
)
def test_porcelain_status_columns_are_preserved(
    source_snapshot: Path,
    kind: str,
    expected_prefix: str,
) -> None:
    if kind == "untracked":
        (source_snapshot / "new.txt").write_text("new\n", encoding="utf-8")
    else:
        (source_snapshot / "marker.txt").write_text("changed\n", encoding="utf-8")
        if kind == "staged":
            _git(source_snapshot, "add", "marker.txt")

    snapshot = scan_snapshot(source_snapshot, ["marker.txt"], scanned_at="2026-10-27T00:00:00Z")

    assert any(line.startswith(expected_prefix) for line in snapshot["worktree"]["porcelain"])


def test_real_cli_generates_snapshot_and_offline_site_inside_output(
    source_snapshot: Path,
) -> None:
    snapshot_path = Path("output") / "architecture-explorer" / "snapshot.json"
    site_path = Path("output") / "architecture-explorer" / "site"

    scan = _run_cli(
        "scan",
        "--repository",
        source_snapshot,
        "--source-file",
        "marker.txt",
        "--output",
        snapshot_path,
    )
    assert scan.returncode == 0, scan.stderr

    build = _run_cli(
        "build",
        "--repository",
        source_snapshot,
        "--snapshot",
        source_snapshot / snapshot_path,
        "--mapping",
        MAPPING,
        "--web-assets",
        WEB_ASSETS,
        "--output-directory",
        site_path,
    )
    assert build.returncode == 0, build.stderr
    assert (source_snapshot / site_path / "index.html").is_file()
    assert (source_snapshot / site_path / "explorer-data.js").is_file()


@pytest.mark.parametrize(
    "unsafe_output",
    [
        "snapshot.json",
        "output/../marker.txt",
    ],
)
def test_real_scan_cli_rejects_output_outside_safe_artifact_root(
    source_snapshot: Path,
    unsafe_output: str,
) -> None:
    original = (source_snapshot / "marker.txt").read_bytes()

    result = _run_cli(
        "scan",
        "--repository",
        source_snapshot,
        "--source-file",
        "marker.txt",
        "--output",
        unsafe_output,
    )

    assert result.returncode != 0
    assert (source_snapshot / "marker.txt").read_bytes() == original


def test_real_scan_cli_rejects_absolute_and_overlapping_output(source_snapshot: Path) -> None:
    source = source_snapshot / "output" / "source.txt"
    source.parent.mkdir()
    source.write_text("source\n", encoding="utf-8")

    result = _run_cli(
        "scan",
        "--repository",
        source_snapshot,
        "--source-file",
        "output/source.txt",
        "--output",
        source,
    )

    assert result.returncode != 0
    assert source.read_text(encoding="utf-8") == "source\n"


def test_real_build_cli_rejects_output_directory_overlapping_snapshot(
    source_snapshot: Path,
) -> None:
    snapshot_path = Path("output") / "site" / "snapshot.json"
    scan = _run_cli(
        "scan",
        "--repository",
        source_snapshot,
        "--source-file",
        "marker.txt",
        "--output",
        snapshot_path,
    )
    assert scan.returncode == 0, scan.stderr

    result = _run_cli(
        "build",
        "--repository",
        source_snapshot,
        "--snapshot",
        source_snapshot / snapshot_path,
        "--mapping",
        MAPPING,
        "--web-assets",
        WEB_ASSETS,
        "--output-directory",
        "output/site",
    )

    assert result.returncode != 0
    assert "overlaps an input path" in result.stderr


def test_real_build_cli_does_not_replace_existing_file_with_output_directory(
    source_snapshot: Path,
) -> None:
    snapshot_path = Path("output") / "snapshot.json"
    scan = _run_cli(
        "scan",
        "--repository",
        source_snapshot,
        "--source-file",
        "marker.txt",
        "--output",
        snapshot_path,
    )
    assert scan.returncode == 0, scan.stderr
    protected = source_snapshot / "output" / "site"
    protected.write_text("protected\n", encoding="utf-8")

    result = _run_cli(
        "build",
        "--repository",
        source_snapshot,
        "--snapshot",
        source_snapshot / snapshot_path,
        "--mapping",
        MAPPING,
        "--web-assets",
        WEB_ASSETS,
        "--output-directory",
        "output/site",
    )

    assert result.returncode != 0
    assert protected.read_text(encoding="utf-8") == "protected\n"


def test_real_cli_rejects_link_that_escapes_output_root(
    tmp_path: Path,
    source_snapshot: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output = source_snapshot / "output"
    output.mkdir()
    link = output / "escape"
    created_junction = False
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if sys.platform != "win32":
            pytest.skip(f"directory links are unavailable in this test environment: {error}")
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=False,
            capture_output=True,
        )
        if junction.returncode != 0:
            pytest.skip("directory symlinks and junctions are unavailable in this test environment")
        created_junction = True

    try:
        result = _run_cli(
            "scan",
            "--repository",
            source_snapshot,
            "--source-file",
            "marker.txt",
            "--output",
            "output/escape/snapshot.json",
        )

        assert result.returncode != 0
        assert not (outside / "snapshot.json").exists()
    finally:
        if created_junction:
            link.rmdir()
