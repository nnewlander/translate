from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_scanner import PRODUCTION_FILES, scan_supervisor_worker_candidate
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.review_cli import main as review_main
from booktrans.architecture_explorer.review_mapping import import_review_mapping
from booktrans.architecture_explorer import review_source
from booktrans.architecture_explorer.review_source import review_source_sha256
from t5_lite_fixture import commit, create_source_repository, git, git_output


PROJECT = Path(__file__).resolve().parents[2]
CANDIDATE = PROJECT / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP = PROJECT / "architecture-explorer/reviews/supervisor-worker.v1.json"
REVIEW_SOURCE = PROJECT / "architecture-explorer/reviews/supervisor-worker.source.v1.json"
SNAPSHOT = PROJECT / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP = PROJECT / "architecture-explorer/modules.json"
NAVIGATION = PROJECT / "architecture-explorer/navigation.v1.json"
WEB_ASSETS = PROJECT / "architecture-explorer/web"


def _update_args(repository: Path) -> list[str]:
    return [
        "update-review-state",
        "--repository", str(repository),
        "--candidate", str(CANDIDATE),
        "--review-mapping", str(REVIEW_MAP),
        "--review-source", str(REVIEW_SOURCE),
        "--review-state", "output/review-state.json",
        "--assessed-at", "2026-11-05T00:00:00Z",
    ]


def _build_args(
    repository: Path,
    *,
    with_state: bool = True,
    with_navigation: bool = True,
    output: str = "output/site",
) -> list[str]:
    args = [
        "build",
        "--repository", str(repository),
        "--snapshot", str(SNAPSHOT),
        "--mapping", str(MODULE_MAP),
        "--candidate", str(CANDIDATE),
        "--review-mapping", str(REVIEW_MAP),
        "--review-source", str(REVIEW_SOURCE),
        "--web-assets", str(WEB_ASSETS),
        "--output-directory", output,
    ]
    if with_navigation:
        args.extend(["--navigation", str(NAVIGATION)])
    if with_state:
        args.extend(["--review-state", "output/review-state.json"])
    return args


def _decision_body(candidate: dict) -> str:
    relation = candidate["candidate_relation"]
    return "\n".join(
        [
            "ARCHITECTURE_REVIEW_DECISION",
            "decision: APPROVED",
            f"reviewed_source_commit: {candidate['source_snapshot']['revision']}",
            f"reviewed_worktree_state: {candidate['source_snapshot']['worktree']['state']}",
            f"candidate_report_sha256: {candidate['candidate_report_sha256']}",
            f"scanner_version: {candidate['scanner_version']}",
            "approved_evidence_ids:",
            *(f"- {item}" for item in relation["production_evidence_ids"]),
            f"source_module: {relation['source_module']}",
            f"target_module: {relation['target_module']}",
            f"direction: {relation['direction']}",
            f"relationship_type: {relation['proposed_relationship_type']}",
            f"approved_reachability: {relation['reachability']['status']}",
            f"runtime_state: {relation['runtime_state']}",
            "retained_unknowns:",
            *(f"- {item}" for item in relation["reachability"]["retained_unknowns"]),
            "review_notes: |",
            "  New trusted T3 review for the restored source.",
        ]
    )


def _new_trusted_review(monkeypatch, candidate: dict) -> tuple[dict, dict]:
    source = json.loads(REVIEW_SOURCE.read_text(encoding="utf-8"))
    comment_id = 6516895999
    created_at = "2026-11-05T01:00:00Z"
    source["comment"].update(
        {
            "id": comment_id,
            "html_url": f"https://github.com/nnewlander/translate/issues/3#issuecomment-{comment_id}",
            "created_at": created_at,
            "updated_at": created_at,
            "body": _decision_body(candidate),
        }
    )
    monkeypatch.setattr(review_source, "TRUSTED_COMMENT_ID", comment_id)
    monkeypatch.setattr(review_source, "TRUSTED_COMMENT_CREATED_AT", created_at)
    monkeypatch.setattr(review_source, "TRUSTED_COMMENT_UPDATED_AT", created_at)
    monkeypatch.setattr(
        review_source,
        "TRUSTED_REVIEW_SOURCE_SHA256",
        review_source_sha256(source),
    )
    mapping = import_review_mapping(candidate, source["comment"], source["permission"])
    assert mapping["validation"] == {"status": "valid", "errors": []}
    return mapping, source


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_update_review_state_explicitly_initializes_current_state(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"

    assert review_main(_update_args(repository)) == 0

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "schema": "architecture-explorer.review-state.v1",
        "relationship_id": "relationship:production-control:launches:translation-execution",
        "review_decision_id": "github-comment:6516894339",
        "status": "current-reviewed",
        "continuity_status": "exact-match",
        "assessed_at": "2026-11-05T00:00:00Z",
        "reasons": [],
    }
    assert "approved_evidence_ids" not in state
    assert "history" not in state
    assert "head" not in state


def test_build_is_read_only_and_t4_uses_current_exact_state(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    before = state_path.read_bytes()

    assert review_main(_build_args(repository)) == 0

    assert state_path.read_bytes() == before
    data = json.loads((repository / "output/site/explorer-data.json").read_text(encoding="utf-8"))
    relationship = data["architecture"]["relationships"][0]
    boundary = next(
        item
        for item in data["navigation"]["drilldowns"][0]["children"]
        if item["id"] == "worker-launch-boundary"
    )
    assert relationship["review_status"] == "current-reviewed"
    assert relationship["source_continuity"]["status"] == "exact-match"
    assert boundary["source_assignment"]["status"] == "partial"


@pytest.mark.parametrize("state_kind", ["missing", "corrupt", "inconsistent"])
def test_missing_or_corrupt_state_allows_only_historical_view(
    tmp_path: Path,
    state_kind: str,
) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    if state_kind == "corrupt":
        state_path.parent.mkdir(parents=True)
        state_path.write_text("{broken", encoding="utf-8")
    elif state_kind == "inconsistent":
        _write_json(state_path, {"schema": "architecture-explorer.review-state.v1"})
    before = state_path.read_bytes() if state_path.exists() else None

    assert review_main(
        _build_args(repository, with_navigation=False, output="output/historical")
    ) == 0

    after = state_path.read_bytes() if state_path.exists() else None
    assert after == before
    data = json.loads(
        (repository / "output/historical/explorer-data.json").read_text(encoding="utf-8")
    )
    relationship = data["architecture"]["relationships"][0]
    assert relationship["review_status"] == "historical-reviewed"
    assert relationship["review_scope"] == "historical-t3-snapshot"
    assert relationship["source_continuity"]["review_state"] == state_kind


def test_explicit_update_refuses_to_overwrite_corrupt_state(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{broken", encoding="utf-8")
    before = state_path.read_bytes()

    with pytest.raises(ExplorerInputError, match="corrupt; refusing overwrite"):
        review_main(_update_args(repository))

    assert state_path.read_bytes() == before


def test_blank_lines_keep_current_review_and_update_locations(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    assert review_main(_update_args(repository)) == 0
    for relative in PRODUCTION_FILES[1:]:
        path = repository / relative
        path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    commit(repository, "move evidence lines")

    assert review_main(_build_args(repository, output="output/moved")) == 0

    data = json.loads((repository / "output/moved/explorer-data.json").read_text(encoding="utf-8"))
    relationship = data["architecture"]["relationships"][0]
    assert relationship["review_status"] == "current-reviewed"
    assert relationship["source_continuity"]["status"] == "exact-match"


def test_moved_build_parser_keeps_current_review_and_t4_partial(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    assert review_main(_update_args(repository)) == 0
    original_ids = scan_supervisor_worker_candidate(repository)["candidate_relation"][
        "production_evidence_ids"
    ]
    worker = repository / PRODUCTION_FILES[2]
    source = worker.read_text(encoding="utf-8")
    parser_start = source.index("\ndef build_parser()")
    main_start = source.index("\ndef main(", parser_start)
    parser_block = source[parser_start:main_start]
    worker.write_text(
        source[:parser_start] + source[main_start:].rstrip() + "\n\n" + parser_block.lstrip(),
        encoding="utf-8",
    )
    commit(repository, "move complete build_parser function after main")

    moved = scan_supervisor_worker_candidate(repository)
    moved_ids = moved["candidate_relation"]["production_evidence_ids"]
    assert moved["candidate_relation"]["scan_outcome"] == "complete"
    assert len(moved_ids) == len(set(moved_ids)) == 13
    assert set(moved_ids) == set(original_ids)
    assert moved_ids != original_ids

    assert review_main(_build_args(repository, output="output/moved-function")) == 0

    data = json.loads(
        (repository / "output/moved-function/explorer-data.json").read_text(encoding="utf-8")
    )
    relationship = data["architecture"]["relationships"][0]
    boundary = next(
        item
        for item in data["navigation"]["drilldowns"][0]["children"]
        if item["id"] == "worker-launch-boundary"
    )
    assert relationship["review_status"] == "current-reviewed"
    assert relationship["source_continuity"]["status"] == "exact-match"
    assert relationship["evidence_ids"] == original_ids
    assert boundary["source_assignment"]["status"] == "partial"


def test_complete_scan_with_changed_evidence_id_requires_review(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    assert review_main(_update_args(repository)) == 0
    original_ids = scan_supervisor_worker_candidate(repository)["candidate_relation"][
        "production_evidence_ids"
    ]
    worker = repository / PRODUCTION_FILES[2]
    source = worker.read_text(encoding="utf-8")
    worker.write_text(
        source.replace(
            'parser.add_argument("--mode", choices=("canary", "production"), default="canary")',
            'parser.add_argument("--mode", default="canary", choices=("canary", "production"))',
        ),
        encoding="utf-8",
    )
    commit(repository, "change one evidence identity")

    changed = scan_supervisor_worker_candidate(repository)
    changed_ids = changed["candidate_relation"]["production_evidence_ids"]
    assert changed["candidate_relation"]["scan_outcome"] == "complete"
    assert len(changed_ids) == len(set(changed_ids)) == 13
    assert set(changed_ids) != set(original_ids)

    assert review_main(
        _build_args(repository, with_navigation=False, output="output/changed-id")
    ) == 0

    data = json.loads(
        (repository / "output/changed-id/explorer-data.json").read_text(encoding="utf-8")
    )
    relationship = data["architecture"]["relationships"][0]
    assert relationship["review_status"] == "needs-review"
    assert relationship["source_continuity"]["status"] == "material-change"


def test_recorded_invalidation_survives_source_restoration(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    worker = repository / PRODUCTION_FILES[2]
    original = worker.read_text(encoding="utf-8")
    worker.write_text(
        original.replace("return run_production_checkpoint(", "return other_checkpoint("),
        encoding="utf-8",
    )
    commit(repository, "invalidate reviewed worker branch")

    assert review_main(_update_args(repository)) == 0
    invalid = json.loads(state_path.read_text(encoding="utf-8"))
    assert invalid["status"] == "needs-review"
    worker.write_text(original, encoding="utf-8")
    commit(repository, "restore reviewed worker branch")

    assert review_main(
        _build_args(repository, with_navigation=False, output="output/restored")
    ) == 0
    data = json.loads((repository / "output/restored/explorer-data.json").read_text(encoding="utf-8"))
    relationship = data["architecture"]["relationships"][0]
    assert relationship["review_status"] == "needs-review"
    with pytest.raises(ExplorerInputError, match="partial source assignment requires"):
        review_main(_build_args(repository, output="output/restored-navigation"))
    assert review_main(_update_args(repository)) == 0
    still_invalid = json.loads(state_path.read_text(encoding="utf-8"))
    assert still_invalid["status"] == "needs-review"
    assert still_invalid["reasons"] == invalid["reasons"]


def test_committed_invalidation_persists_in_clean_worktree(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    git(repository, "add", "-f", "output/review-state.json")
    commit(repository, "record current review state")
    worker = repository / PRODUCTION_FILES[2]
    original = worker.read_text(encoding="utf-8")
    worker.write_text(
        original.replace("return run_production_checkpoint(", "return other_checkpoint("),
        encoding="utf-8",
    )
    assert review_main(_update_args(repository)) == 0
    commit(repository, "record invalid review state")
    invalid = json.loads(state_path.read_text(encoding="utf-8"))
    assert invalid["status"] == "needs-review"
    worker.write_text(original, encoding="utf-8")
    commit(repository, "restore source without clearing review state")
    assert git_output(repository, "status", "--porcelain") == ""

    checkout = tmp_path / "clean-checkout"
    git(repository, "worktree", "add", "--detach", str(checkout), "HEAD")
    try:
        assert git_output(checkout, "status", "--porcelain") == ""
        assert review_main(
            _build_args(checkout, with_navigation=False, output="output/clean-build")
        ) == 0
        data = json.loads(
            (checkout / "output/clean-build/explorer-data.json").read_text(encoding="utf-8")
        )
        relationship = data["architecture"]["relationships"][0]
        assert relationship["review_status"] == "needs-review"
        assert relationship["source_continuity"]["status"] == "exact-match"
        assert relationship["source_continuity"]["review_state"] == "valid"
    finally:
        git(repository, "worktree", "remove", "--force", str(checkout))


def test_unrecorded_change_can_disappear_after_source_restoration(tmp_path: Path) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    recorded = state_path.read_bytes()
    worker = repository / PRODUCTION_FILES[2]
    original = worker.read_text(encoding="utf-8")
    worker.write_text(
        original.replace("return run_production_checkpoint(", "return other_checkpoint("),
        encoding="utf-8",
    )
    commit(repository, "temporarily invalidate evidence")

    assert review_main(
        _build_args(repository, with_navigation=False, output="output/unrecorded-invalid")
    ) == 0
    invalid_data = json.loads(
        (repository / "output/unrecorded-invalid/explorer-data.json").read_text(encoding="utf-8")
    )
    assert invalid_data["architecture"]["relationships"][0]["review_status"] == "needs-review"
    assert state_path.read_bytes() == recorded
    worker.write_text(original, encoding="utf-8")
    commit(repository, "restore before recording invalidation")

    assert review_main(_build_args(repository, output="output/unrecorded-restored")) == 0
    restored_data = json.loads(
        (repository / "output/unrecorded-restored/explorer-data.json").read_text(encoding="utf-8")
    )
    assert restored_data["architecture"]["relationships"][0]["review_status"] == "current-reviewed"
    assert state_path.read_bytes() == recorded


@pytest.mark.parametrize(
    ("change", "expected_status", "expected_continuity"),
    [
        ("missing", "needs-review", "missing"),
        ("ambiguous", "unknown", "ambiguous"),
        ("parse-failure", "unknown", "parse-failure"),
    ],
)
def test_explicit_update_records_missing_ambiguous_and_parse_failure(
    tmp_path: Path,
    change: str,
    expected_status: str,
    expected_continuity: str,
) -> None:
    repository = create_source_repository(tmp_path)
    assert review_main(_update_args(repository)) == 0
    worker = repository / PRODUCTION_FILES[2]
    text = worker.read_text(encoding="utf-8")
    if change == "missing":
        text = text.replace(
            '    parser.add_argument("--authorize-production-checkpoint-write", action="store_true")\n',
            "",
        )
    elif change == "ambiguous":
        line = '    parser.add_argument("--mode", choices=("canary", "production"), default="canary")\n'
        text = text.replace(line, line + line)
    else:
        text = "def broken(:\n"
    worker.write_text(text, encoding="utf-8")
    commit(repository, f"fixture {change}")

    assert review_main(_update_args(repository)) == 0

    state = json.loads((repository / "output/review-state.json").read_text(encoding="utf-8"))
    assert state["status"] == expected_status
    assert state["continuity_status"] == expected_continuity
    assert state["reasons"]


def test_new_trusted_t3_decision_can_restore_recorded_invalidation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    worker = repository / PRODUCTION_FILES[2]
    original = worker.read_text(encoding="utf-8")
    worker.write_text(
        original.replace("return run_production_checkpoint(", "return other_checkpoint("),
        encoding="utf-8",
    )
    commit(repository, "invalidate old decision")
    assert review_main(_update_args(repository)) == 0
    old_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert old_state["status"] == "needs-review"
    worker.write_text(
        original.replace(
            'parser.add_argument("--mode", choices=("canary", "production"), default="canary")',
            'parser.add_argument("--mode", default="canary", choices=("canary", "production"))',
        ),
        encoding="utf-8",
    )
    commit(repository, "make reviewed evidence identity change")
    candidate = scan_supervisor_worker_candidate(
        repository,
        scanned_at="2026-11-05T01:01:00Z",
    )
    original_ids = json.loads(CANDIDATE.read_text(encoding="utf-8"))[
        "candidate_relation"
    ]["production_evidence_ids"]
    assert candidate["candidate_relation"]["production_evidence_ids"] != original_ids
    assert candidate["candidate_relation"]["scan_outcome"] == "complete"
    mapping, source = _new_trusted_review(monkeypatch, candidate)
    candidate_path = repository / "output/new-candidate.json"
    mapping_path = repository / "output/new-mapping.json"
    source_path = repository / "output/new-source.json"
    _write_json(candidate_path, candidate)
    _write_json(mapping_path, mapping)
    _write_json(source_path, source)

    assert review_main(
        [
            "update-review-state",
            "--repository", str(repository),
            "--candidate", str(candidate_path),
            "--review-mapping", str(mapping_path),
            "--review-source", str(source_path),
            "--review-state", "output/review-state.json",
            "--assessed-at", "2026-11-05T01:02:00Z",
        ]
    ) == 0

    new_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert new_state["status"] == "current-reviewed"
    assert new_state["continuity_status"] == "exact-match"
    assert new_state["review_decision_id"] == "github-comment:6516895999"


def test_untrusted_new_t3_source_cannot_replace_review_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = create_source_repository(tmp_path)
    state_path = repository / "output/review-state.json"
    assert review_main(_update_args(repository)) == 0
    before = state_path.read_bytes()
    candidate = scan_supervisor_worker_candidate(repository)
    mapping, source = _new_trusted_review(monkeypatch, candidate)
    forged = deepcopy(source)
    forged["comment"]["body"] += "\nforged: true"
    candidate_path = repository / "output/new-candidate.json"
    mapping_path = repository / "output/new-mapping.json"
    source_path = repository / "output/forged-source.json"
    _write_json(candidate_path, candidate)
    _write_json(mapping_path, mapping)
    _write_json(source_path, forged)

    with pytest.raises(ExplorerInputError, match="trusted T3 review source"):
        review_main(
            [
                "update-review-state",
                "--repository", str(repository),
                "--candidate", str(candidate_path),
                "--review-mapping", str(mapping_path),
                "--review-source", str(source_path),
                "--review-state", "output/review-state.json",
                "--assessed-at", "2026-11-05T01:03:00Z",
            ]
        )
    assert state_path.read_bytes() == before
