from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path
import shutil

import pytest

import booktrans.architecture_explorer as explorer_api
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.node_review import (
    _verify_node_review_materials,
    import_node_review_from_github,
    verify_node_review_materials,
)
from booktrans.architecture_explorer.review_cli import (
    _main as _review_main_with_test_policy,
    _parser,
    main as review_main,
)
from test_t8_node_review import (
    _canonical_sha256,
    _fixture_materials,
    _fixture_policy,
    _fixture_source,
    _published_explorer,
    _source_evidence_record,
    _target,
    REPOSITORY,
)
from t5_lite_fixture import commit, create_source_repository


def test_supported_python_api_has_no_projection_or_trust_root_injection() -> None:
    assert not hasattr(explorer_api, "apply_node_review")
    assert "trust_policy" not in inspect.signature(
        import_node_review_from_github
    ).parameters
    assert "trust_policy" not in inspect.signature(
        verify_node_review_materials
    ).parameters
    assert "node_review_trust_policy" not in inspect.signature(review_main).parameters


def test_locally_self_signed_owner_admin_receipt_cannot_grant_current() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)

    with pytest.raises(ExplorerInputError, match="independently trusted"):
        verify_node_review_materials(
            explorer,
            decision,
            source,
        )


def test_public_cli_exposes_node_review_import_before_offline_build() -> None:
    commands = _parser()._subparsers._group_actions[0].choices

    assert "import-node-review" in commands


def test_relationship_and_claims_cannot_self_prove_formal_dependency() -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    relationship = explorer["architecture"]["relationships"][0]
    forged_evidence_ids = list(reversed(relationship["evidence_ids"]))
    relationship["review_decision"] = deepcopy(relationship["review_decision"])
    relationship["review_decision"]["decision_id"] = "github-comment:forged"
    relationship["review_decision"]["reviewed_source_snapshot"]["revision"] = "f" * 40
    relationship["evidence_ids"] = forged_evidence_ids

    with pytest.raises(ExplorerInputError, match="authoritative T3"):
        _verify_node_review_materials(
            explorer,
            decision,
            source,
            trust_policy=_fixture_policy(),
        )


@pytest.mark.parametrize(
    ("decision_result", "expected_projection"),
    (("APPROVED", "Current · Approved"), ("REJECTED", "Current · Rejected")),
)
def test_github_import_flows_through_offline_build_to_generated_site(
    tmp_path: Path,
    decision_result: str,
    expected_projection: str,
) -> None:
    repository = create_source_repository(tmp_path)
    for relative in (
        "scripts/production/production_campaign_supervisor.ps1",
        "src/booktrans/ooxml/extractor.py",
        "src/booktrans/ooxml/translation_view.py",
        "scripts/write_full_book_translation_scope.py",
        "src/booktrans/production_worker/selection.py",
        "src/booktrans/production_worker/pipeline.py",
        "src/booktrans/production_worker/qa.py",
        "src/booktrans/production_worker/semantic_review_run.py",
        "src/booktrans/providers/qwen_max.py",
        "src/booktrans/providers/qwen_mt.py",
        "src/booktrans/providers/repair_gate.py",
        "src/booktrans/providers/router.py",
        "src/booktrans/production_worker/production_checkpoint.py",
        "src/booktrans/harness/tagged_roundtrip.py",
        "src/booktrans/harness/package_preservation.py",
        "src/booktrans/ooxml/tagged_projection.py",
        "scripts/advance_full_book_batch013.py",
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, destination)
    commit(repository, "add T8 source facts")

    explorer = _published_explorer()
    source, _ = _fixture_materials(explorer, result=decision_result)
    policy = _fixture_policy()
    comment_id = source["comment"]["id"]
    explorer_input = tmp_path / "explorer-data.json"
    explorer_input.write_text(json.dumps(explorer, ensure_ascii=False), encoding="utf-8")
    decision_path = repository / "output/node-review-decision.json"
    source_path = repository / "output/node-review-source.json"
    decision_output = Path("output/node-review-decision.json")
    source_output = Path("output/node-review-source.json")

    responses = {
        "repos/nnewlander/translate": source["repository"],
        "repos/nnewlander/translate/issues/999999": source["issue"],
        f"repos/nnewlander/translate/issues/comments/{comment_id}": source["comment"],
        "repos/nnewlander/translate/collaborators/nnewlander/permission": source["permission"],
    }

    def github_api(endpoint: str) -> dict:
        return deepcopy(responses[endpoint])

    import_args = [
        "import-node-review",
        "--repository", str(repository),
        "--explorer-data", str(explorer_input),
        "--issue", "999999",
        "--comment-id", str(comment_id),
        "--decision-output", str(decision_output),
        "--source-output", str(source_output),
    ]
    with pytest.raises(ExplorerInputError, match="independently trusted"):
        review_main(import_args, github_api=github_api)

    if decision_result == "APPROVED":
        evidence_id = _target(explorer)["source_assignment"]["evidence_ids"][0]
        for mutation in (
            "snippet",
            "normalized-snippet-hash",
            "structural-anchor",
            "role",
            "duplicate-id",
        ):
            tampered = deepcopy(explorer)
            evidence = _source_evidence_record(tampered, evidence_id)
            if mutation == "snippet":
                evidence["snippet"] += "\n# changed launch behavior"
            elif mutation == "normalized-snippet-hash":
                evidence["normalized_snippet_sha256"] = "0" * 64
            elif mutation == "structural-anchor":
                evidence["structural_anchor"] = "powershell:function:NearbyLaunch"
            elif mutation == "role":
                evidence["evidence_kind"] = "powershell_script_binding"
            else:
                tampered["source_facts"]["modules"][0]["entries"].append(
                    {"kind": "duplicate-fixture", "evidence": deepcopy(evidence)}
                )
            explorer_input.write_text(
                json.dumps(tampered, ensure_ascii=False),
                encoding="utf-8",
            )
            with pytest.raises(
                ExplorerInputError,
                match="Evidence|duplicate|ambiguous",
            ):
                _review_main_with_test_policy(
                    import_args,
                    github_api=github_api,
                    node_review_trust_policy=policy,
                )
            assert not decision_path.exists()
            assert not source_path.exists()

        relocatable = deepcopy(explorer)
        evidence = _source_evidence_record(relocatable, evidence_id)
        evidence["current_location"] = {"line_start": 1224, "line_end": 1291}
        evidence["file_sha256"] = "f" * 64
        unrelated = next(
            item
            for module in relocatable["source_facts"]["modules"]
            for collection in (module.get("entries", []), module.get("direct_imports", []))
            for item in collection
            if isinstance(item.get("evidence"), dict)
            and item["evidence"].get("evidence_id")
            not in _target(relocatable)["source_assignment"]["evidence_ids"]
        )["evidence"]
        unrelated["snippet"] = "unrelated source fact changed"
        unrelated["file_sha256"] = "e" * 64
        explorer_input.write_text(
            json.dumps(relocatable, ensure_ascii=False),
            encoding="utf-8",
        )

    assert _review_main_with_test_policy(
        import_args,
        github_api=github_api,
        node_review_trust_policy=policy,
    ) == 0

    review_state = repository / "output/review-state.json"
    review_state_args = [
        "update-review-state",
        "--repository", str(repository),
        "--candidate", str(REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        "--review-mapping", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"),
        "--review-source", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        "--review-state", "output/review-state.json",
        "--assessed-at", "2026-11-12T00:00:00Z",
    ]
    assert review_main(review_state_args) == 0

    site = repository / "output/site"
    fragments = [
        REPOSITORY / f"architecture-explorer/candidates/{name}.v1.json"
        for name in (
            "document-understanding",
            "scope-and-request",
            "translation-execution",
            "quality-and-repair",
            "production-evidence",
            "production-control",
            "candidate-and-release",
        )
    ]
    build_args = [
        "build",
        "--repository", str(repository),
        "--snapshot", str(REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json"),
        "--mapping", str(REPOSITORY / "architecture-explorer/modules.json"),
        "--candidate", str(REPOSITORY / "architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        "--review-mapping", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.v1.json"),
        "--review-source", str(REPOSITORY / "architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        "--review-state", str(review_state),
        "--navigation", str(REPOSITORY / "architecture-explorer/navigation.v1.json"),
    ]
    for fragment in fragments:
        build_args.extend(("--candidate-fragment", str(fragment)))
    build_args.extend(
        (
            "--node-review-decision", str(decision_path),
            "--node-review-source", str(source_path),
            "--web-assets", str(REPOSITORY / "architecture-explorer/web"),
            "--output-directory", "output/site",
        )
    )
    if decision_result == "APPROVED":
        decision_only_args = list(build_args)
        source_option = decision_only_args.index("--node-review-source")
        del decision_only_args[source_option : source_option + 2]
        with pytest.raises(ExplorerInputError, match="supplied together"):
            review_main(decision_only_args)

        source_only_args = list(build_args)
        decision_option = source_only_args.index("--node-review-decision")
        del source_only_args[decision_option : decision_option + 2]
        with pytest.raises(ExplorerInputError, match="supplied together"):
            review_main(source_only_args)

    with pytest.raises(ExplorerInputError, match="independently trusted"):
        review_main(build_args)

    assert _review_main_with_test_policy(
        build_args,
        node_review_trust_policy=policy,
    ) == 0

    generated = json.loads((site / "explorer-data.json").read_text(encoding="utf-8"))
    target = next(
        node
        for drilldown in generated["navigation"]["drilldowns"]
        if drilldown["parent_module_id"] == "production-control"
        for node in drilldown["children"]
        if node["id"] == "worker-launch-boundary"
    )
    assert target["node_review"]["projection"] == expected_projection
    assert expected_projection in (site / "explorer-data.js").read_text(encoding="utf-8")

    tampered_source = json.loads(source_path.read_text(encoding="utf-8"))
    tampered_source["permission"]["permission"] = "write"
    source_path.write_text(
        json.dumps(tampered_source, ensure_ascii=False),
        encoding="utf-8",
    )
    tampered_decision = json.loads(decision_path.read_text(encoding="utf-8"))
    tampered_decision["source_receipt_sha256"] = _canonical_sha256(tampered_source)
    decision_path.write_text(
        json.dumps(tampered_decision, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ExplorerInputError, match="untrusted"):
        _review_main_with_test_policy(
            build_args,
            node_review_trust_policy=policy,
        )
