from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import booktrans.architecture_explorer as explorer
import pytest
from booktrans.architecture_explorer.candidate_scanner import PRODUCTION_FILES
from booktrans.architecture_explorer.review_cli import _parser
from booktrans.architecture_explorer.review_mapping import _production_continuity, publish_reviewed_explorer
from booktrans.architecture_explorer.review_source import (
    TRUSTED_REVIEW_SOURCE_SHA256,
    fetch_github_review_source,
    review_source_sha256,
)


REPOSITORY = Path(__file__).resolve().parents[2]
CANDIDATE_PATH = REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP_PATH = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"
REVIEW_SOURCE_PATH = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"
SNAPSHOT_PATH = REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP_PATH = REPOSITORY / "architecture-explorer/modules.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _publish(review_map: dict, review_source: dict | None = None) -> dict:
    return publish_reviewed_explorer(
        REPOSITORY,
        SNAPSHOT_PATH,
        MODULE_MAP_PATH,
        _json(CANDIDATE_PATH),
        review_map,
        review_source,
    )


def test_offline_build_without_independent_github_source_cannot_publish_current_reviewed() -> None:
    relation = _publish(_json(REVIEW_MAP_PATH))["architecture"]["relationships"][0]
    assert relation["review_status"] != "current-reviewed"


def test_real_versioned_github_source_rebuilds_mapping_and_allows_current_reviewed() -> None:
    source = _json(REVIEW_SOURCE_PATH)
    assert review_source_sha256(source) == TRUSTED_REVIEW_SOURCE_SHA256
    relation = _publish(_json(REVIEW_MAP_PATH), source)["architecture"]["relationships"][0]
    assert relation["review_status"] == "current-reviewed"
    assert relation["review_source"] == {
        "status": "trusted",
        "sha256": TRUSTED_REVIEW_SOURCE_SHA256,
        "errors": [],
    }


def test_locally_forged_comment_identity_cannot_publish_current_reviewed() -> None:
    review_map = _json(REVIEW_MAP_PATH)
    review_map["decision"]["github"]["actual_author"]["login"] = "forged-owner"
    review_map["decision"]["github"]["actual_author"]["github_user_id"] = 999999999
    relation = _publish(review_map, _json(REVIEW_SOURCE_PATH))["architecture"]["relationships"][0]
    assert relation["review_status"] != "current-reviewed"


def test_locally_modified_review_decision_text_cannot_publish_current_reviewed() -> None:
    review_map = _json(REVIEW_MAP_PATH)
    review_map["decision"]["review_notes"] = "locally rewritten approval"
    relation = _publish(review_map, _json(REVIEW_SOURCE_PATH))["architecture"]["relationships"][0]
    assert relation["review_status"] != "current-reviewed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (("comment", "user", "login"), "forged-owner"),
        (("comment", "id"), 1),
        (("comment", "body"), "ARCHITECTURE_REVIEW_DECISION\ndecision: APPROVED\nforged: yes"),
    ],
)
def test_tampered_github_source_receipt_cannot_publish_current_reviewed(field: tuple[str, ...], value: object) -> None:
    source = _json(REVIEW_SOURCE_PATH)
    target = source
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    relation = _publish(_json(REVIEW_MAP_PATH), source)["architecture"]["relationships"][0]
    assert relation["review_status"] == "unknown"
    assert relation["review_source"]["status"] == "untrusted"


def _candidate_for_path(path: str, digest: str) -> dict:
    return {
        "scan_scope": {"production_files": [path]},
        "source_snapshot": {"input_files": [{"path": path, "sha256": digest}]},
    }


def test_continuity_rejects_parent_escape_before_reading(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    protected = tmp_path / "protected.txt"
    protected.write_text("must not be read", encoding="utf-8")
    digest = hashlib.sha256(protected.read_bytes()).hexdigest()
    original = Path.read_bytes
    protected_reads: list[Path] = []

    def monitored_read(path: Path) -> bytes:
        if path.resolve() == protected.resolve():
            protected_reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", monitored_read)
    status, _checks = _production_continuity(repository, _candidate_for_path("../protected.txt", digest))
    assert status == "parse-failure"
    assert protected_reads == []


def test_continuity_rejects_non_allowlisted_local_file_before_reading(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    protected = repository / "local-secret.txt"
    protected.write_text("must not be read", encoding="utf-8")
    digest = hashlib.sha256(protected.read_bytes()).hexdigest()
    original = Path.read_bytes
    protected_reads: list[Path] = []

    def monitored_read(path: Path) -> bytes:
        if path.resolve() == protected.resolve():
            protected_reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", monitored_read)
    status, _checks = _production_continuity(repository, _candidate_for_path("local-secret.txt", digest))
    assert status == "parse-failure"
    assert protected_reads == []


def test_continuity_rejects_absolute_candidate_path_before_reading(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    protected = tmp_path / "absolute-secret.txt"
    protected.write_text("must not be read", encoding="utf-8")
    digest = hashlib.sha256(protected.read_bytes()).hexdigest()
    original = Path.read_bytes
    protected_reads: list[Path] = []

    def monitored_read(path: Path) -> bytes:
        if path.resolve() == protected.resolve():
            protected_reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", monitored_read)
    status, _checks = _production_continuity(repository, _candidate_for_path(str(protected.resolve()), digest))
    assert status == "parse-failure"
    assert protected_reads == []


def test_continuity_rejects_linked_allowlisted_source_before_reading(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    inputs = []
    for relative in PRODUCTION_FILES:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
        inputs.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    linked = repository / PRODUCTION_FILES[0]
    original_is_symlink = Path.is_symlink
    original_read = Path.read_bytes
    linked_reads: list[Path] = []

    def simulated_symlink(path: Path) -> bool:
        return path == linked or original_is_symlink(path)

    def monitored_read(path: Path) -> bytes:
        if path == linked:
            linked_reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_symlink)
    monkeypatch.setattr(Path, "read_bytes", monitored_read)
    candidate = {
        "scan_scope": {"production_files": list(PRODUCTION_FILES)},
        "source_snapshot": {"input_files": inputs},
    }
    status, checks = _production_continuity(repository, candidate)
    assert status == "parse-failure"
    assert checks[0]["status"] == "parse-failure"
    assert linked_reads == []


def test_online_import_fetches_fixed_github_endpoints_without_local_api_json() -> None:
    source = _json(REVIEW_SOURCE_PATH)
    responses = {
        "repos/nnewlander/translate": source["repository"],
        "repos/nnewlander/translate/issues/3": source["issue"],
        "repos/nnewlander/translate/issues/comments/6516894339": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }
    calls: list[str] = []

    def fake_api(endpoint: str) -> dict:
        calls.append(endpoint)
        return deepcopy(responses[endpoint])

    assert fetch_github_review_source(api=fake_api) == source
    assert calls == list(responses)
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "import-review", "--repository", ".", "--candidate", "candidate.json",
                "--comment", "forged.json", "--permission", "forged-permission.json",
                "--output", "output/map.json", "--source-output", "output/source.json",
            ]
        )


def test_public_api_keeps_t1_t2_and_t3_exports() -> None:
    expected = {
        "build_explorer",
        "scan_snapshot",
        "scan_supervisor_worker_candidate",
        "render_candidate_report",
        "import_review_mapping",
        "parse_review_decision",
        "publish_reviewed_explorer",
    }
    assert expected <= set(explorer.__all__)
    assert all(hasattr(explorer, name) for name in expected)
