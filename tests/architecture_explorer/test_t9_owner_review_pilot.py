from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.review_cli import _main as review_main
from booktrans.architecture_explorer.review_cli import main as production_review_main
from test_t8_node_review import _fixture_materials, _fixture_policy, _published_explorer


_PRODUCTION_REVIEW_SOURCE = {
    "schema": "architecture-explorer.github-review-source.v1",
    "repository": {
        "id": 1425303246,
        "node_id": "R_kgDOVPRmzg",
        "full_name": "nnewlander/translate",
        "html_url": "https://github.com/nnewlander/translate",
        "private": False,
        "owner": {
            "login": "nnewlander",
            "id": 256049772,
            "node_id": "U_kgDOD0MCbA",
            "type": "User",
        },
    },
    "issue": {
        "id": 6413057720,
        "node_id": "I_kwDOVPRmzs8AAAABfj9-uA",
        "number": 38,
        "html_url": "https://github.com/nnewlander/translate/issues/38",
        "repository_url": "https://api.github.com/repos/nnewlander/translate",
        "state": "open",
    },
    "comment": {
        "id": 6944645893,
        "node_id": "IC_kwDOVPRmzs8AAAABne7jBQ",
        "html_url": (
            "https://github.com/nnewlander/translate/issues/38"
            "#issuecomment-6944645893"
        ),
        "issue_url": "https://api.github.com/repos/nnewlander/translate/issues/38",
        "author_association": "OWNER",
        "created_at": "2026-11-17T09:53:48Z",
        "updated_at": "2026-11-17T10:10:19Z",
        "body": "\n".join(
            (
                "ARCHITECTURE_EXPLORER_NODE_REVIEW_V1",
                "Decision-ID: github-comment:6944645893",
                "Result: APPROVED",
                (
                    "Object-Key: candidate-node + production-control + "
                    "worker-launch-boundary"
                ),
                (
                    "Claims-SHA256: "
                    "f3252dd5a349b0c2b5902503c22e4cb8be5f568e5045cc63f96b9be6c5208783"
                ),
                (
                    "Candidate-Fact-SHA256: "
                    "0e12aa409d1c94d6f1959c59fcf3c1b6331ef18b055792db0dca460ab1ac0a2f"
                ),
                (
                    "Reviewed-Revision: "
                    "95e219a926023aff0022dbe0174e9a361e20c8b1"
                ),
            )
        ),
        "user": {
            "login": "nnewlander",
            "id": 256049772,
            "node_id": "U_kgDOD0MCbA",
            "type": "User",
        },
    },
    "permission": {
        "permission": "admin",
        "role_name": "admin",
        "user": {
            "login": "nnewlander",
            "id": 256049772,
            "node_id": "U_kgDOD0MCbA",
            "type": "User",
        },
    },
}


def _production_review_responses() -> dict[str, dict]:
    source = deepcopy(_PRODUCTION_REVIEW_SOURCE)
    return {
        "repos/nnewlander/translate": source["repository"],
        "repos/nnewlander/translate/issues/38": source["issue"],
        "repos/nnewlander/translate/issues/comments/6944645893": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source[
            "permission"
        ],
    }


def test_prepare_draft_is_explicitly_non_authoritative(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_input = repository / "explorer-data.json"
    explorer_input.write_text(json.dumps(_published_explorer()), encoding="utf-8")

    assert review_main(
        [
            "prepare-node-review",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_input),
            "--output-directory",
            "output/draft",
            "--draft",
        ],
        github_api=lambda endpoint: pytest.fail(f"unexpected GitHub call: {endpoint}"),
        node_review_trust_policy=_fixture_policy(),
    ) == 0

    output = repository / "output/draft"
    draft = (output / "github-comment-draft.txt").read_text(encoding="utf-8")
    assert draft == "\n".join(
        (
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_DRAFT_V1",
            "Authority: NONE",
            "Status: NON-AUTHORITATIVE",
            "Object-Key: candidate-node + production-control + worker-launch-boundary",
            "Packet-SHA256: 2d542b551fd8568b362a286755c288141bf6a9f51cc001141928efafe7f778cd",
            "Instruction: Preserve this comment ID; replace this entire draft only after explicit Owner decision authorization.",
        )
    )
    assert "Result:" not in draft
    assert not (output / "github-comment.txt").exists()


def test_final_comment_rejects_an_output_directory_with_a_stale_draft(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_input = repository / "explorer-data.json"
    explorer_input.write_text(json.dumps(_published_explorer()), encoding="utf-8")
    common = [
        "prepare-node-review",
        "--repository",
        str(repository),
        "--explorer-data",
        str(explorer_input),
        "--output-directory",
        "output/review",
    ]
    assert review_main(
        [*common, "--draft"],
        github_api=lambda endpoint: pytest.fail(f"unexpected GitHub call: {endpoint}"),
        node_review_trust_policy=_fixture_policy(),
    ) == 0

    with pytest.raises(ExplorerInputError, match="stale.*draft"):
        review_main(
            [*common, "--result", "APPROVED", "--comment-id", "123456789"],
            github_api=lambda endpoint: pytest.fail(
                f"unexpected GitHub call: {endpoint}"
            ),
            node_review_trust_policy=_fixture_policy(),
        )

    assert not (repository / "output/review/github-comment.txt").exists()


@pytest.mark.parametrize("result", ("APPROVED", "REJECTED"))
def test_preflight_emits_one_untrusted_uninstalled_anchor_candidate(
    tmp_path: Path,
    result: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result=result)
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer), encoding="utf-8")
    calls: list[str] = []
    responses = {
        "repos/nnewlander/translate": source["repository"],
        f"repos/nnewlander/translate/issues/{source['issue']['number']}": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{source['comment']['id']}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    def github_api(endpoint: str) -> dict:
        calls.append(endpoint)
        return deepcopy(responses[endpoint])

    source_output = tmp_path / "output/preflight-source.json"
    candidate_output = tmp_path / "output/anchor-candidate.json"
    assert review_main(
        [
            "preflight-node-review",
            "--repository",
            str(tmp_path),
            "--explorer-data",
            str(explorer_input),
            "--issue",
            str(source["issue"]["number"]),
            "--comment-id",
            str(source["comment"]["id"]),
            "--source-output",
            "output/preflight-source.json",
            "--anchor-candidate-output",
            "output/anchor-candidate.json",
        ],
        github_api=github_api,
        node_review_trust_policy=_fixture_policy(),
    ) == 0

    candidate = json.loads(candidate_output.read_text(encoding="utf-8"))
    assert candidate == {
        "schema": "architecture-explorer.node-review-anchor-candidate.v1",
        "trust_status": "untrusted",
        "installation_status": "uninstalled",
        "repository": "nnewlander/translate",
        "issue_id": source["issue"]["id"],
        "issue_number": source["issue"]["number"],
        "comment_id": source["comment"]["id"],
        "comment_created_at": source["comment"]["created_at"],
        "comment_updated_at": source["comment"]["updated_at"],
        "source_receipt_sha256": decision["source_receipt_sha256"],
    }
    assert json.loads(source_output.read_text(encoding="utf-8")) == source
    assert calls == list(responses)


def test_preflight_rejects_a_comment_body_that_is_not_the_exact_final_body(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    source, _ = _fixture_materials(explorer, result="APPROVED")
    source["comment"]["body"] += "\nchanged after review"
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer), encoding="utf-8")
    responses = {
        "repos/nnewlander/translate": source["repository"],
        f"repos/nnewlander/translate/issues/{source['issue']['number']}": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{source['comment']['id']}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    with pytest.raises(ExplorerInputError, match="exact final body"):
        review_main(
            [
                "preflight-node-review",
                "--repository",
                str(tmp_path),
                "--explorer-data",
                str(explorer_input),
                "--issue",
                str(source["issue"]["number"]),
                "--comment-id",
                str(source["comment"]["id"]),
                "--source-output",
                "output/preflight-source.json",
                "--anchor-candidate-output",
                "output/anchor-candidate.json",
            ],
            github_api=lambda endpoint: deepcopy(responses[endpoint]),
            node_review_trust_policy=_fixture_policy(),
        )

    assert not (tmp_path / "output/preflight-source.json").exists()
    assert not (tmp_path / "output/anchor-candidate.json").exists()


def test_preflight_rejects_an_inconsistent_endpoint_identity(tmp_path: Path) -> None:
    explorer = _published_explorer()
    source, _ = _fixture_materials(explorer, result="REJECTED")
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer), encoding="utf-8")
    requested_issue = source["issue"]["number"] - 1
    responses = {
        "repos/nnewlander/translate": source["repository"],
        f"repos/nnewlander/translate/issues/{requested_issue}": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{source['comment']['id']}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    with pytest.raises(ExplorerInputError, match="requested identity"):
        review_main(
            [
                "preflight-node-review",
                "--repository",
                str(tmp_path),
                "--explorer-data",
                str(explorer_input),
                "--issue",
                str(requested_issue),
                "--comment-id",
                str(source["comment"]["id"]),
                "--source-output",
                "output/preflight-source.json",
                "--anchor-candidate-output",
                "output/anchor-candidate.json",
            ],
            github_api=lambda endpoint: deepcopy(responses[endpoint]),
            node_review_trust_policy=_fixture_policy(),
        )

    assert not (tmp_path / "output/preflight-source.json").exists()
    assert not (tmp_path / "output/anchor-candidate.json").exists()


def test_uninstalled_anchor_candidate_cannot_authorize_production_import(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    source, _ = _fixture_materials(explorer, result="APPROVED")
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer), encoding="utf-8")
    responses = {
        "repos/nnewlander/translate": source["repository"],
        f"repos/nnewlander/translate/issues/{source['issue']['number']}": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{source['comment']['id']}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    def github_api(endpoint: str) -> dict:
        return deepcopy(responses[endpoint])

    assert review_main(
        [
            "preflight-node-review",
            "--repository",
            str(tmp_path),
            "--explorer-data",
            str(explorer_input),
            "--issue",
            str(source["issue"]["number"]),
            "--comment-id",
            str(source["comment"]["id"]),
            "--source-output",
            "output/preflight-source.json",
            "--anchor-candidate-output",
            "output/anchor-candidate.json",
        ],
        github_api=github_api,
        node_review_trust_policy=_fixture_policy(),
    ) == 0

    with pytest.raises(ExplorerInputError, match="independently trusted"):
        production_review_main(
            [
                "import-node-review",
                "--repository",
                str(tmp_path),
                "--explorer-data",
                str(explorer_input),
                "--issue",
                str(source["issue"]["number"]),
                "--comment-id",
                str(source["comment"]["id"]),
                "--decision-output",
                "output/node-review-decision.json",
                "--source-output",
                "output/node-review-source.json",
            ],
            github_api=github_api,
        )

    assert not (tmp_path / "output/node-review-decision.json").exists()
    assert not (tmp_path / "output/node-review-source.json").exists()


def test_live_import_rejects_a_comment_edited_after_the_fixture_anchor(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    source, _ = _fixture_materials(explorer, result="REJECTED")
    source["comment"]["updated_at"] = "2026-11-12T00:00:01Z"
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer), encoding="utf-8")
    responses = {
        "repos/nnewlander/translate": source["repository"],
        f"repos/nnewlander/translate/issues/{source['issue']['number']}": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{source['comment']['id']}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    with pytest.raises(ExplorerInputError, match="independently trusted|untrusted"):
        review_main(
            [
                "import-node-review",
                "--repository",
                str(tmp_path),
                "--explorer-data",
                str(explorer_input),
                "--issue",
                str(source["issue"]["number"]),
                "--comment-id",
                str(source["comment"]["id"]),
                "--decision-output",
                "output/node-review-decision.json",
                "--source-output",
                "output/node-review-source.json",
            ],
            github_api=lambda endpoint: deepcopy(responses[endpoint]),
            node_review_trust_policy=_fixture_policy(),
        )

    assert not (tmp_path / "output/node-review-decision.json").exists()
    assert not (tmp_path / "output/node-review-source.json").exists()


def test_production_import_recognizes_the_exact_fixed_anchor_and_live_fetches(
    tmp_path: Path,
) -> None:
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(_published_explorer()), encoding="utf-8")
    responses = _production_review_responses()
    calls: list[str] = []

    def github_api(endpoint: str) -> dict:
        calls.append(endpoint)
        return deepcopy(responses[endpoint])

    assert production_review_main(
        [
            "import-node-review",
            "--repository",
            str(tmp_path),
            "--explorer-data",
            str(explorer_input),
            "--issue",
            "38",
            "--comment-id",
            "6944645893",
            "--decision-output",
            "output/node-review-decision.json",
            "--source-output",
            "output/node-review-source.json",
        ],
        github_api=github_api,
    ) == 0

    decision = json.loads(
        (tmp_path / "output/node-review-decision.json").read_text(encoding="utf-8")
    )
    assert decision["decision_id"] == "github-comment:6944645893"
    assert decision["result"] == "APPROVED"
    assert decision["source_receipt_sha256"] == (
        "5e185031185c164d2a0d17faaaf6fb121ca328af1bfcd26309365c5e7c464e72"
    )
    assert json.loads(
        (tmp_path / "output/node-review-source.json").read_text(encoding="utf-8")
    ) == _PRODUCTION_REVIEW_SOURCE
    assert calls == list(responses)


@pytest.mark.parametrize(
    "mutation",
    (
        "issue-id",
        "comment-id",
        "comment-created-at",
        "comment-updated-at",
        "receipt-digest",
    ),
)
def test_production_import_rejects_any_fixed_anchor_mismatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(_published_explorer()), encoding="utf-8")
    responses = _production_review_responses()
    if mutation == "issue-id":
        responses["repos/nnewlander/translate/issues/38"]["id"] += 1
    elif mutation == "comment-id":
        responses["repos/nnewlander/translate/issues/comments/6944645893"]["id"] += 1
    elif mutation == "comment-created-at":
        responses["repos/nnewlander/translate/issues/comments/6944645893"][
            "created_at"
        ] = "2026-11-17T09:53:49Z"
    elif mutation == "comment-updated-at":
        responses["repos/nnewlander/translate/issues/comments/6944645893"][
            "updated_at"
        ] = "2026-11-17T10:10:20Z"
    else:
        responses["repos/nnewlander/translate/issues/comments/6944645893"][
            "body"
        ] += "\nchanged after approval"

    with pytest.raises(
        ExplorerInputError,
        match="trusted|untrusted|identity mismatch",
    ):
        production_review_main(
            [
                "import-node-review",
                "--repository",
                str(tmp_path),
                "--explorer-data",
                str(explorer_input),
                "--issue",
                "38",
                "--comment-id",
                "6944645893",
                "--decision-output",
                "output/node-review-decision.json",
                "--source-output",
                "output/node-review-source.json",
            ],
            github_api=lambda endpoint: deepcopy(responses[endpoint]),
        )

    assert not (tmp_path / "output/node-review-decision.json").exists()
    assert not (tmp_path / "output/node-review-source.json").exists()
