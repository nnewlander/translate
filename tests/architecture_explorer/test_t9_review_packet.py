from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_cli import main as review_main
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)


REPOSITORY = Path(__file__).resolve().parents[2]
FRAGMENT_NAMES = (
    "document-understanding",
    "scope-and-request",
    "translation-execution",
    "quality-and-repair",
    "production-evidence",
    "production-control",
    "candidate-and-release",
)


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


def _file_sha256(relative_path: str) -> str:
    return hashlib.sha256((REPOSITORY / relative_path).read_bytes()).hexdigest()


def _published_explorer() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        REPOSITORY / "architecture-explorer/modules.json",
        _json("architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _json("architecture-explorer/reviews/supervisor-worker.v1.json"),
        _json("architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=(
            REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
        ),
    )
    with_facts = attach_python_source_facts(reviewed, scan_python_source_facts(REPOSITORY))
    navigable = apply_navigation(
        with_facts,
        _json("architecture-explorer/navigation.v1.json"),
    )
    explorer = apply_candidate_fragments(
        navigable,
        [
            _json(f"architecture-explorer/candidates/{name}.v1.json")
            for name in FRAGMENT_NAMES
        ],
    )
    explorer["generation_inputs"].update(
        {
            "navigation": {
                "path": "navigation.v1.json",
                "sha256": _file_sha256("architecture-explorer/navigation.v1.json"),
            },
            "review_mapping": {
                "path": "supervisor-worker.v1.json",
                "sha256": _file_sha256(
                    "architecture-explorer/reviews/supervisor-worker.v1.json"
                ),
            },
            "review_source": {
                "path": "supervisor-worker.source.v1.json",
                "sha256": _file_sha256(
                    "architecture-explorer/reviews/supervisor-worker.source.v1.json"
                ),
            },
            "candidate_fragments": [
                {
                    "path": f"{name}.v1.json",
                    "sha256": _file_sha256(
                        f"architecture-explorer/candidates/{name}.v1.json"
                    ),
                }
                for name in FRAGMENT_NAMES
            ],
        }
    )
    return deepcopy(explorer)


def _run_prepare(
    repository: Path,
    explorer_path: Path,
    output: str,
    *extra_arguments: str,
) -> Path:
    assert review_main(
        [
            "prepare-node-review",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--output-directory",
            output,
            *extra_arguments,
        ]
    ) == 0
    return repository / output


def test_prepare_node_review_generates_deterministic_neutral_packet_and_report(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(_published_explorer(), ensure_ascii=False),
        encoding="utf-8",
    )

    first = _run_prepare(repository, explorer_path, "output/first")
    second = _run_prepare(repository, explorer_path, "output/second")

    packet_bytes = (first / "review-packet.json").read_bytes()
    report_bytes = (first / "review-packet.md").read_bytes()
    assert packet_bytes == (second / "review-packet.json").read_bytes()
    assert report_bytes == (second / "review-packet.md").read_bytes()
    assert not (first / "github-comment.txt").exists()

    packet = json.loads(packet_bytes)
    assert packet["schema"] == "architecture-explorer.node-review-packet.v1"
    assert packet["object_key"] == {
        "kind": "candidate-node",
        "parent_module_id": "production-control",
        "node_id": "worker-launch-boundary",
    }
    assert packet["implementation_baseline"] == (
        "14073edcea76a2b200bf15aaa4231c13f20e5ec9"
    )
    assert packet["reviewed_source_snapshot"] == {
        "revision": "95e219a926023aff0022dbe0174e9a361e20c8b1",
        "worktree_state": "clean",
    }
    assert packet["allowed_results"] == ["APPROVED", "REJECTED"]
    assert "result" not in packet
    assert "decision" not in packet
    assert "approval_anchor" not in packet
    assert [claim["kind"] for claim in packet["claims"]] == [
        "IDENTITY",
        "NAME",
        "CLASSIFICATION",
        "RESPONSIBILITY",
        "PLAIN_EXPLANATION",
        "SOURCE_ASSIGNMENT",
        "CANDIDATE_EVIDENCE",
        "FORMAL_DEPENDENCY_AND_LIMITS",
    ]
    assert len(packet["candidate_evidence"]) == 3
    formal = packet["claims"][-1]["value"]
    assert len(formal["formal_evidence_ids"]) == 13
    assert formal["retained_unknowns"]

    report = report_bytes.decode("utf-8")
    assert "首次 Owner 单节点审核材料" in report
    assert "本材料不是审核决定" in report
    assert "八类审核声明" in report
    assert "三条候选源码证据" in report
    assert "十三条正式 Evidence" in report
    assert "两阶段人工操作" in report
    assert "--result" in report
    assert "--comment-id" in report
    assert "updated_at" in report
    assert "14073edcea76a2b200bf15aaa4231c13f20e5ec9" in report
    assert "95e219a926023aff0022dbe0174e9a361e20c8b1" in report


@pytest.mark.parametrize("result", ["APPROVED", "REJECTED"])
def test_prepare_node_review_generates_exact_final_comment_only_from_explicit_inputs(
    tmp_path: Path,
    result: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(_published_explorer(), ensure_ascii=False),
        encoding="utf-8",
    )

    output = _run_prepare(
        repository,
        explorer_path,
        f"output/{result.lower()}",
        "--result",
        result,
        "--comment-id",
        "123456789",
    )

    assert (output / "github-comment.txt").read_text(encoding="utf-8") == "\n".join(
        (
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_V1",
            "Decision-ID: github-comment:123456789",
            f"Result: {result}",
            "Object-Key: candidate-node + production-control + worker-launch-boundary",
            "Claims-SHA256: f3252dd5a349b0c2b5902503c22e4cb8be5f568e5045cc63f96b9be6c5208783",
            "Candidate-Fact-SHA256: 0e12aa409d1c94d6f1959c59fcf3c1b6331ef18b055792db0dca460ab1ac0a2f",
            "Reviewed-Revision: 95e219a926023aff0022dbe0174e9a361e20c8b1",
        )
    )
    assert "<" not in (output / "github-comment.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--result", "APPROVED"),
        ("--comment-id", "123456789"),
        ("--result", "REJECTED", "--comment-id", "0"),
        ("--result", "REJECTED", "--comment-id", "-1"),
    ],
)
def test_prepare_node_review_rejects_incomplete_or_invalid_decision_inputs(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(_published_explorer(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ExplorerInputError):
        _run_prepare(
            repository,
            explorer_path,
            "output/rejected",
            *arguments,
        )


@pytest.mark.parametrize("evidence_set", ["candidate", "formal"])
def test_prepare_node_review_rejects_incomplete_closed_evidence_sets(
    tmp_path: Path,
    evidence_set: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer = _published_explorer()
    if evidence_set == "candidate":
        drilldown = next(
            item
            for item in explorer["navigation"]["drilldowns"]
            if item["parent_module_id"] == "production-control"
        )
        target = next(
            child
            for child in drilldown["children"]
            if child["id"] == "worker-launch-boundary"
        )
        target["source_assignment"]["evidence_ids"].pop()
    else:
        relationship = next(
            item
            for item in explorer["architecture"]["relationships"]
            if item["id"]
            == "relationship:production-control:launches:translation-execution"
        )
        relationship["evidence_ids"].pop()
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(explorer, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ExplorerInputError):
        _run_prepare(
            repository,
            explorer_path,
            "output/rejected",
        )


def test_neutral_packet_rejects_an_output_directory_with_a_stale_comment(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    output = repository / "output/reused"
    output.mkdir(parents=True)
    (output / "github-comment.txt").write_text(
        "stale decision body",
        encoding="utf-8",
    )
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(_published_explorer(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ExplorerInputError, match="stale GitHub comment"):
        _run_prepare(repository, explorer_path, "output/reused")
