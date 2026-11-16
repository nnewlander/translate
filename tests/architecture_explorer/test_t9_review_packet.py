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
EVIDENCE_RECORD_KEYS = {
    "evidence_id",
    "source_role",
    "evidence_kind",
    "path",
    "symbol",
    "structural_anchor",
    "syntax_position",
    "current_location",
    "snippet",
    "normalized_snippet_sha256",
    "file_sha256",
}


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


def _candidate_source_evidence(explorer: dict, evidence_id: str) -> dict:
    matches = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            evidence = value.get("evidence")
            if isinstance(evidence, dict) and evidence.get("evidence_id") == evidence_id:
                matches.append(evidence)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(explorer["source_facts"])
    assert len(matches) == 1
    return matches[0]


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
    assert packet["packet_sha256"] == (
        "2d542b551fd8568b362a286755c288141bf6a9f51cc001141928efafe7f778cd"
    )
    assert packet["inputs"]["formal_evidence_sha256"] == (
        "be75396ea97e1cc19f6ef65a6f55576cc983d66bf84a2d6f46ac7dc3c0566566"
    )
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
    formal_evidence = packet["formal_evidence"]
    assert [item["evidence_id"] for item in formal_evidence] == formal[
        "formal_evidence_ids"
    ]
    assert len(formal_evidence) == 13
    assert all(
        set(item) == EVIDENCE_RECORD_KEYS | {"presentation_zh"}
        for item in formal_evidence
    )
    assert all(
        set(item["presentation_zh"])
        == {"group", "name", "explanation", "supports"}
        for item in formal_evidence
    )
    assert formal_evidence[0] == {
        "evidence_id": "evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13",
        "source_role": "production",
        "evidence_kind": "worker_script_binding",
        "path": "scripts/production/production_campaign_supervisor.ps1",
        "symbol": "$deterministicWorkerScript",
        "structural_anchor": "powershell:assignment:$deterministicWorkerScript",
        "syntax_position": "module/assignment:$deterministicWorkerScript#1",
        "current_location": {"line_start": 52, "line_end": 52},
        "snippet": '$deterministicWorkerScript = Join-Path $Root "scripts\\production\\run_qwen_deterministic_worker.py"\r',
        "normalized_snippet_sha256": "32249443cf0e783808c579a69bf04b14b75eb6b796d756cc562551f2a591ea0a",
        "file_sha256": "63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47",
        "presentation_zh": {
            "group": "supervisor",
            "name": "绑定 Worker 启动脚本",
            "explanation": "Supervisor 把 Python 包装脚本的仓库内路径保存为确定的启动目标。这里证明的是脚本指向关系，不代表脚本已经运行。",
            "supports": "支持“Supervisor 知道应启动哪个 Worker 包装入口”。",
        },
    }

    report = report_bytes.decode("utf-8")
    assert "首次 Owner 单节点审核材料" in report
    assert "本材料不是审核决定" in report
    assert "八类审核声明" in report
    assert "三条候选源码证据" in report
    assert "十三条正式 Evidence" in report
    assert "绑定 Worker 启动脚本" in report
    assert "scripts/production/production_campaign_supervisor.ps1:52-52" in report
    assert '$deterministicWorkerScript = Join-Path $Root "scripts\\production\\run_qwen_deterministic_worker.py"' in report
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


def test_candidate_evidence_projects_only_the_closed_record_fields(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer = _published_explorer()
    production_control = next(
        drilldown
        for drilldown in explorer["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "production-control"
    )
    target = next(
        child
        for child in production_control["children"]
        if child["id"] == "worker-launch-boundary"
    )
    target_id = target["source_assignment"]["evidence_ids"][0]
    selected_evidence = _candidate_source_evidence(explorer, target_id)
    selected_evidence["unreviewed_extra"] = {
        "result": "APPROVED"
    }
    foreign_evidence = deepcopy(selected_evidence)
    foreign_evidence["evidence_id"] = "evidence:sha256:" + "f" * 64
    explorer["source_facts"]["foreign_evidence_fixture"] = {
        "evidence": foreign_evidence
    }
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(explorer, ensure_ascii=False),
        encoding="utf-8",
    )

    output = _run_prepare(repository, explorer_path, "output/projected")
    packet = json.loads((output / "review-packet.json").read_text(encoding="utf-8"))

    assert len(packet["candidate_evidence"]) == 3
    assert all(set(item) == EVIDENCE_RECORD_KEYS for item in packet["candidate_evidence"])
    assert all("unreviewed_extra" not in item for item in packet["candidate_evidence"])
    assert foreign_evidence["evidence_id"] not in {
        item["evidence_id"] for item in packet["candidate_evidence"]
    }
    assert packet["packet_sha256"] == (
        "2d542b551fd8568b362a286755c288141bf6a9f51cc001141928efafe7f778cd"
    )


@pytest.mark.parametrize("mutation", ["foreign", "missing", "extra", "reordered"])
def test_candidate_evidence_rejects_non_authoritative_identity_count_or_order(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer = _published_explorer()
    production_control = next(
        drilldown
        for drilldown in explorer["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "production-control"
    )
    target = next(
        child
        for child in production_control["children"]
        if child["id"] == "worker-launch-boundary"
    )
    evidence_ids = target["source_assignment"]["evidence_ids"]
    if mutation == "foreign":
        evidence_ids[0] = "evidence:sha256:" + "0" * 64
    elif mutation == "missing":
        evidence_ids.pop()
    elif mutation == "extra":
        evidence_ids.append(evidence_ids[0])
    else:
        evidence_ids.reverse()
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(explorer, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ExplorerInputError, match="Candidate Fact mismatch"):
        _run_prepare(repository, explorer_path, "output/rejected")


@pytest.mark.parametrize(
    "mutation",
    ["foreign", "missing", "reordered", "content", "extra-field"],
)
def test_formal_evidence_rejects_identity_count_order_content_or_extra_fields(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer = _published_explorer()
    relationship = next(
        item
        for item in explorer["architecture"]["relationships"]
        if item["id"]
        == "relationship:production-control:launches:translation-execution"
    )
    evidence = relationship["evidence"]
    if mutation == "foreign":
        evidence[0] = deepcopy(relationship["supporting_evidence"][0])
    elif mutation == "missing":
        evidence.pop()
    elif mutation == "reordered":
        evidence.reverse()
    elif mutation == "content":
        evidence[0]["snippet"] += " # unreviewed"
    else:
        evidence[0]["unreviewed_extra"] = True
    explorer_path = repository / "explorer-data.json"
    explorer_path.write_text(
        json.dumps(explorer, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ExplorerInputError, match="formal Evidence"):
        _run_prepare(repository, explorer_path, "output/rejected")


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
