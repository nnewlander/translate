from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from booktrans.architecture_explorer.review_mapping import import_review_mapping, publish_reviewed_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
CANDIDATE_PATH = REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"
REVIEW_MAP_PATH = REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"
SNAPSHOT_PATH = REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"
MODULE_MAP_PATH = REPOSITORY / "architecture-explorer/modules.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _decision_body(candidate: dict, *, result: str = "APPROVED", evidence_ids: list[str] | None = None) -> str:
    relation = candidate["candidate_relation"]
    evidence = evidence_ids if evidence_ids is not None else relation["production_evidence_ids"]
    lines = [
        "ARCHITECTURE_REVIEW_DECISION",
        f"decision: {result}",
        f"reviewed_source_commit: {candidate['source_snapshot']['revision']}",
        f"reviewed_worktree_state: {candidate['source_snapshot']['worktree']['state']}",
        f"candidate_report_sha256: {candidate['candidate_report_sha256']}",
        f"scanner_version: {candidate['scanner_version']}",
        "approved_evidence_ids:",
        *(f"- {item}" for item in evidence),
        f"source_module: {relation['source_module']}",
        f"target_module: {relation['target_module']}",
        f"direction: {relation['direction']}",
        f"relationship_type: {relation['proposed_relationship_type']}",
        f"approved_reachability: {relation['reachability']['status']}",
        f"runtime_state: {relation['runtime_state']}",
        "retained_unknowns:",
        *(f"- {item}" for item in relation["reachability"]["retained_unknowns"]),
        "review_notes: |",
        "  Exact candidate reviewed by the repository owner.",
    ]
    return "\n".join(lines)


def _comment(candidate: dict, *, body: str | None = None, login: str = "nnewlander", association: str = "OWNER") -> dict:
    return {
        "id": 6516894339,
        "html_url": "https://github.com/nnewlander/translate/issues/3#issuecomment-6516894339",
        "user": {"login": login, "id": 256049772},
        "author_association": association,
        "created_at": "2026-10-30T06:56:39Z",
        "body": body if body is not None else _decision_body(candidate),
    }


def _permission(login: str = "nnewlander", permission: str = "admin") -> dict:
    return {"permission": permission, "user": {"login": login, "id": 256049772}}


def _publish(candidate: dict, review_map: dict, repository: Path = REPOSITORY) -> dict:
    return publish_reviewed_explorer(repository, SNAPSHOT_PATH, MODULE_MAP_PATH, candidate, review_map)


def test_valid_owner_approval_imports_exact_binding_and_publishes_current_reviewed() -> None:
    candidate = _json(CANDIDATE_PATH)
    review_map = import_review_mapping(candidate, _comment(candidate), _permission())

    assert review_map["validation"] == {"status": "valid", "errors": []}
    decision = review_map["decision"]
    assert decision["github"]["actual_author"]["login"] == "nnewlander"
    assert decision["github"]["actual_author"]["author_association"] == "OWNER"
    assert decision["github"]["actual_author"]["repository_permission"] == "admin"
    assert decision["approved_evidence_ids"] == candidate["candidate_relation"]["production_evidence_ids"]
    assert len(decision["approved_evidence_ids"]) == 13

    relation = _publish(candidate, review_map)["architecture"]["relationships"][0]
    assert relation["review_status"] == "current-reviewed"
    assert relation["direction"] == "production-control -> translation-execution"
    assert relation["type"] == "process-launch"
    assert relation["code_reachability"]["status"] == "STATIC_CONDITIONAL"
    assert relation["runtime_state"] == "NOT_COLLECTED"
    assert relation["source_continuity"]["status"] == "exact-match"


def test_no_review_comment_remains_candidate() -> None:
    candidate = _json(CANDIDATE_PATH)
    review_map = import_review_mapping(candidate, None, None)
    assert _publish(candidate, review_map)["architecture"]["relationships"][0]["review_status"] == "candidate"


def test_rejected_owner_decision_requires_review() -> None:
    candidate = _json(CANDIDATE_PATH)
    body = _decision_body(candidate, result="REJECTED")
    review_map = import_review_mapping(candidate, _comment(candidate, body=body), _permission())
    assert review_map["validation"]["status"] == "valid"
    assert _publish(candidate, review_map)["architecture"]["relationships"][0]["review_status"] == "needs-review"


def test_unauthorized_actual_author_cannot_be_overridden_by_claimed_identity() -> None:
    candidate = _json(CANDIDATE_PATH)
    body = _decision_body(candidate) + "\nreviewer: nnewlander\nreviewer_role: OWNER"
    review_map = import_review_mapping(
        candidate,
        _comment(candidate, body=body, login="mallory", association="NONE"),
        _permission(login="mallory", permission="read"),
    )
    assert review_map["decision"] is None
    assert review_map["validation"]["status"] == "invalid"
    assert "not authorized" in review_map["validation"]["errors"][0]
    assert _publish(candidate, review_map)["architecture"]["relationships"][0]["review_status"] == "unknown"


def test_evidence_mismatch_cannot_publish_current_reviewed() -> None:
    candidate = _json(CANDIDATE_PATH)
    wrong_ids = candidate["candidate_relation"]["production_evidence_ids"][:-1]
    body = _decision_body(candidate, evidence_ids=wrong_ids)
    review_map = import_review_mapping(candidate, _comment(candidate, body=body), _permission())
    assert review_map["validation"]["status"] == "invalid"
    assert "approved_evidence_ids:mismatch" in review_map["validation"]["errors"]
    assert _publish(candidate, review_map)["architecture"]["relationships"][0]["review_status"] == "unknown"


def test_reviewed_source_snapshot_mismatch_is_invalid() -> None:
    candidate = _json(CANDIDATE_PATH)
    body = _decision_body(candidate).replace(candidate["source_snapshot"]["revision"], "0" * 40)
    review_map = import_review_mapping(candidate, _comment(candidate, body=body), _permission())
    assert "reviewed_source_commit:mismatch" in review_map["validation"]["errors"]


def test_locally_claimed_valid_mapping_without_owner_api_provenance_is_unknown() -> None:
    candidate = _json(CANDIDATE_PATH)
    review_map = _json(REVIEW_MAP_PATH)
    review_map["decision"]["github"]["actual_author"]["author_association"] = "COLLABORATOR"
    review_map["decision"]["github"]["actual_author"]["repository_permission"] = "push"
    relation = _publish(candidate, review_map)["architecture"]["relationships"][0]
    assert relation["review_status"] == "unknown"
    assert "review_mapping_binding:github_authorization" in relation["review_reasons"]


def test_changed_current_production_source_requires_review(tmp_path: Path, monkeypatch) -> None:
    candidate = _json(CANDIDATE_PATH)
    review_map = _json(REVIEW_MAP_PATH)
    for relative in candidate["scan_scope"]["production_files"]:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, destination)
    changed = tmp_path / candidate["scan_scope"]["production_files"][0]
    changed.write_text(changed.read_text(encoding="utf-8") + "\n# material T3 test change\n", encoding="utf-8")
    monkeypatch.setattr("booktrans.architecture_explorer.review_mapping._git", lambda *_args: "test-value")

    relation = _publish(candidate, review_map, tmp_path)["architecture"]["relationships"][0]
    assert relation["review_status"] == "needs-review"
    assert relation["source_continuity"]["status"] == "material-change"


def test_explorer_only_changes_do_not_invalidate_reviewed_production_snapshot() -> None:
    candidate = _json(CANDIDATE_PATH)
    data = _publish(candidate, _json(REVIEW_MAP_PATH))
    relation = data["architecture"]["relationships"][0]
    assert data["implementation"]["separate_from_reviewed_source_snapshot"] is True
    assert data["implementation"]["revision"] != candidate["source_snapshot"]["revision"] or data["implementation"]["worktree_state"] == "dirty"
    assert relation["source_continuity"]["status"] == "exact-match"
    assert relation["review_status"] == "current-reviewed"


def test_committed_review_map_and_offline_ui_expose_t3_contract() -> None:
    review_map = _json(REVIEW_MAP_PATH)
    assert review_map["schema"] == "architecture-explorer.review-map.v1"
    assert review_map["data_contract_version"] == "architecture-explorer.t3-t5.v1"
    assert review_map["decision"]["github"]["comment_url"].endswith("#issuecomment-6516894339")
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(encoding="utf-8")
    page = (REPOSITORY / "architecture-explorer/web/index.html").read_text(encoding="utf-8")
    assert "复制定位信息" in app
    assert "workflow-toggle" in page
    assert "relationship-detail" in page
