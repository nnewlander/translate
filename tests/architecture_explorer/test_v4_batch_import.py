import json
from copy import deepcopy
from pathlib import Path
import tempfile

import pytest

from booktrans.architecture_explorer.batch_import import import_review_batch
from booktrans.architecture_explorer import review_trust
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.review_source import review_source_sha256
from test_v4_batch_review import _freeze_fixture


NODE = "candidate-node::worker-entry"
COLLABORATION = "candidate-collaboration::relationship-under-review"
DECOMPOSITION = "candidate-decomposition::decomposition-under-review"
PENDING_NODE = "candidate-node::supervisor-boundary"


@pytest.fixture
def output_root():
    # Keep paths short enough for real Windows directory and State writes.
    with tempfile.TemporaryDirectory(prefix=".ae80-", dir=Path.cwd()) as root:
        yield Path(root)


@pytest.fixture
def producer_batch(output_root, monkeypatch):
    from booktrans.architecture_explorer import trusted_batch_review as producer
    from test_v4_trusted_batch_review import build_isolated_handoff

    explorer, nodes, relationships, batch, first, publication, receipts, candidate, _, second = (
        build_isolated_handoff(reverse=True)
    )
    installation = producer.prepare_anchor_installation(candidate, second)
    monkeypatch.setattr(review_trust, "HOST_REVIEW_STATE_DIRECTORY", output_root / "host")
    monkeypatch.setattr(
        review_trust,
        "PRODUCTION_BATCH_INSTALLATION_DIGESTS",
        frozenset({installation["installation_sha256"]}),
    )
    producer.install_anchor_batch(candidate, second)
    installed = producer.host_anchor_installation_path(installation["installation_sha256"])
    return explorer, batch, first, nodes, relationships, publication, receipts, installed


def _import(producer_batch, target, **kwargs):
    return import_review_batch(
        *producer_batch, target, assessed_at="2026-11-26T10:10:00Z", **kwargs
    )


def _state(target, key):
    path = target / "objects" / key.replace("::", "__") / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_producer_output_imports_rejection_and_leaves_undecided_node_pending(producer_batch, output_root):
    nodes = producer_batch[3]
    pending = next(
        definition for definition in nodes["definitions"]
        if definition["object_key"]["node_id"] == "supervisor-boundary"
    )
    del pending["classification_rationale"]
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "imported", result["errors"]
    assert result["imported_objects"] == [COLLABORATION, DECOMPOSITION, NODE]
    assert result["pending_objects"] == [PENDING_NODE]
    assert sorted(path.name for path in (target / "objects").iterdir()) == [
        key.replace("::", "__") for key in [COLLABORATION, DECOMPOSITION, NODE]
    ]
    assert _state(target, NODE)["state"] == "current"
    decision = target / "objects" / NODE.replace("::", "__") / "decision.json"
    assert json.loads(decision.read_text(encoding="utf-8"))["result"] == "REJECTED"
    relationship = _state(target, COLLABORATION)
    assert (relationship["state"], relationship["continuity"], relationship["reasons"]) == (
        "needs-review", "material-change", ["endpoint-rejection:worker-entry:responsibility-invalidated"]
    )


@pytest.mark.parametrize("tamper_both", [False, True])
def test_any_invalid_object_prevents_all_writes(producer_batch, output_root, tamper_both):
    receipts = producer_batch[-2]
    receipts["entries"][NODE]["decision"]["claims"][0]["value"] = "changed"
    if tamper_both:
        receipts["entries"][COLLABORATION]["source"]["comment"]["body"] += "\nchanged"
    target = output_root / "unused-parent" / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "preflight-failed"
    expected = {NODE, COLLABORATION} if tamper_both else {NODE}
    assert {error["object_key"] for error in result["errors"] if error["object_key"]} == expected
    assert not target.parent.exists()


@pytest.mark.parametrize("failure_phase", ["after-object-staged", "before-publish"])
def test_write_failure_is_invisible_then_retry_is_atomic_and_idempotent(
    producer_batch, output_root, failure_phase
):
    target = output_root / "import"

    def interrupt(phase, key):
        if phase == failure_phase:
            assert not target.exists()
            raise OSError("injected write interruption")

    failed = _import(producer_batch, target, fault_injector=interrupt)
    assert failed["status"] == "write-failed"
    assert not target.exists()
    assert not list(output_root.glob(".import.staging-*"))

    assert _import(producer_batch, target)["status"] == "imported"

    def files():
        return {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}

    before = files()
    assert _import(producer_batch, target)["status"] == "already-imported"
    assert files() == before

    state = target / "objects" / NODE.replace("::", "__") / "state.json"
    changed = _state(target, NODE)
    changed["assessed_at"] = "2026-11-26T10:11:00Z"
    state.write_text(json.dumps(changed), encoding="utf-8")
    inconsistent = _import(producer_batch, target)
    assert [error["code"] for error in inconsistent["errors"]] == [
        "EXISTING_IMPORT_INCONSISTENT"
    ]
    assert _state(target, NODE) == changed


def test_installed_file_cannot_replace_independent_host_authorization(
    producer_batch, output_root, monkeypatch
):
    monkeypatch.setattr(review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS", frozenset())
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "preflight-failed"
    assert any(error["code"] == "ANCHOR_INSTALLATION" for error in result["errors"])
    assert not target.exists()


def test_even_host_pinned_partial_batch_cannot_import(producer_batch, output_root, monkeypatch):
    from booktrans.architecture_explorer.trusted_batch_review import host_anchor_installation_path

    installed_path = producer_batch[-1]
    partial = json.loads(installed_path.read_text(encoding="utf-8"))
    del partial["objects"][NODE]
    del partial["installation_sha256"]
    partial["installation_sha256"] = review_source_sha256(partial)
    partial_path = host_anchor_installation_path(partial["installation_sha256"])
    partial_path.write_text(json.dumps(partial), encoding="utf-8")
    monkeypatch.setattr(
        review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS",
        frozenset({partial["installation_sha256"]}),
    )
    target = output_root / "import"
    result = _import((*producer_batch[:-1], partial_path), target)
    assert result["status"] == "preflight-failed"
    assert any(error["code"] == "ANCHOR_INSTALLATION" for error in result["errors"])
    assert not target.exists()


def test_pinned_preparation_copy_cannot_import(producer_batch, output_root):
    prepared = output_root / "prepared.json"
    prepared.write_bytes(producer_batch[-1].read_bytes())
    target = output_root / "import"

    result = _import((*producer_batch[:-1], prepared), target)

    assert result["status"] == "preflight-failed"
    assert any(error["code"] == "ANCHOR_INSTALLATION" for error in result["errors"])
    assert not target.exists()


def test_current_packet_failures_are_aggregated_independently_of_receipts(producer_batch, output_root):
    explorer = producer_batch[0]
    explorer["recursive_candidate_fragments"][0]["relationships"].clear()
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "preflight-failed"
    assert {
        error["object_key"] for error in result["errors"]
        if error["code"] == "CURRENT_PACKET_INVALID"
    } == {COLLABORATION, DECOMPOSITION}
    assert not target.exists()


def test_current_snapshot_mismatch_blocks_every_decided_kind(producer_batch, output_root):
    explorer = producer_batch[0]
    explorer["source_facts"]["source_snapshot"]["revision"] = "f" * 40
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "preflight-failed"
    assert {error["object_key"] for error in result["errors"] if error["object_key"]} == {
        COLLABORATION, DECOMPOSITION, NODE,
    }
    assert not target.exists()


@pytest.mark.parametrize("missing", ["receipt", "publication"])
def test_missing_batch_input_does_not_hide_independent_anchor_error(
    producer_batch, output_root, missing,
):
    from booktrans.architecture_explorer import trusted_batch_review as producer
    from test_v4_trusted_batch_review import FakeGitHubReviewPublisher

    _, batch, first, _, _, publication, receipts, _ = producer_batch
    github = FakeGitHubReviewPublisher()
    github.created = 100
    other = producer.publish_review_comments(batch, first, publication, github)
    receipts["entries"][DECOMPOSITION] = other["entries"][DECOMPOSITION]
    if missing == "receipt":
        del receipts["entries"][NODE]
    else:
        explorer, batch, first, nodes, relationships, _, receipts, installed = producer_batch
        producer_batch = (explorer, batch, first, nodes, relationships, None, receipts, installed)
    del receipts["receipts_sha256"]
    receipts["receipts_sha256"] = review_source_sha256(receipts)
    target = output_root / "unwritten" / "import"
    result = _import(producer_batch, target)
    errors = {(error["code"], error["object_key"]) for error in result["errors"]}
    assert ("RECEIPT_BATCH", None) in errors
    if missing == "receipt":
        assert ("RECEIPT_ENTRY", NODE) in errors
        assert ("RECEIPT_ENTRY", DECOMPOSITION) not in errors
    else:
        assert errors == {
            ("RECEIPT_BATCH", None),
            ("ANCHOR_BINDING", DECOMPOSITION),
            ("OBJECT_VALIDATION", DECOMPOSITION),
        }
    assert ("ANCHOR_BINDING", DECOMPOSITION) in errors
    assert ("OBJECT_VALIDATION", DECOMPOSITION) in errors
    assert result["imported_objects"] == []
    assert not target.parent.exists()


@pytest.mark.parametrize("source_facts", [None, {"source_snapshot": []}, {"source_snapshot": {"worktree": []}}])
def test_malformed_snapshot_is_a_structured_preflight_error(producer_batch, output_root, source_facts):
    producer_batch[0]["source_facts"] = source_facts
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert result["status"] == "preflight-failed"
    assert {item["object_key"] for item in result["errors"] if item["code"] == "SOURCE_SNAPSHOT"} == {
        NODE, COLLABORATION, DECOMPOSITION,
    }
    assert not target.exists()


def test_reports_invalid_definitions_for_multiple_decided_relationships(producer_batch, output_root):
    for definition in producer_batch[4]["definitions"]:
        definition["claim_kinds"] = []
    target = output_root / "import"
    result = _import(producer_batch, target)
    assert {
        error["object_key"] for error in result["errors"] if error["code"] == "DEFINITION_INVALID"
    } == {COLLABORATION, DECOMPOSITION}
    assert not target.exists()


def test_saved_shared_fixture_imports_all_three_decided_object_kinds(
    producer_batch, output_root, monkeypatch
):
    from booktrans.architecture_explorer import trusted_batch_review as producer
    from test_v4_trusted_batch_review import HANDOFF_FIXTURE

    fixture = json.loads(HANDOFF_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fixture_only"] is True
    installed = fixture["installed_batch"]
    monkeypatch.setattr(
        review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS",
        frozenset({installed["installation_sha256"]}),
    )
    candidate = producer.prepare_anchor_confirmation(
        fixture["batch"], fixture["first_confirmation"], fixture["publication"], fixture["receipts"]
    )
    assert producer.install_anchor_batch(candidate, fixture["second_confirmation"]) == installed
    installed_path = producer.host_anchor_installation_path(installed["installation_sha256"])
    inputs = tuple(
        fixture[key] for key in (
            "explorer", "batch", "first_confirmation", "node_definitions",
            "relationship_definitions", "publication", "receipts",
        )
    )
    target = output_root / "import"
    result = _import((*inputs, installed_path), target)
    assert result["status"] == "imported", result["errors"]
    assert result["pending_objects"] == [PENDING_NODE]
    assert result["imported_objects"] == [COLLABORATION, DECOMPOSITION, NODE]
    assert all(_state(target, key)["state"] == "current" for key in result["imported_objects"])
    decision = target / "objects" / COLLABORATION.replace("::", "__") / "decision.json"
    assert json.loads(decision.read_text(encoding="utf-8"))["result"] == "REJECTED"


def test_all_pending_and_deferred_needs_no_installation(producer_batch, output_root):
    explorer, batch, _, nodes, relationships, *_ = producer_batch
    confirmation = _freeze_fixture(batch, {
        key: {
            "result": "DEFERRED" if key == NODE else "PENDING",
            "reason": "等待复核",
        }
        for key in batch["objects"]
    })
    target = output_root / "import"
    result = _import(
        (explorer, batch, confirmation, nodes, relationships, None, None, None), target
    )
    assert result["status"] == "nothing-to-import"
    assert result["pending_objects"] == sorted(batch["objects"])
    assert not target.exists()


@pytest.fixture
def review_commands(producer_batch, output_root, monkeypatch):
    from booktrans.architecture_explorer import review_cli

    explorer, _, _, nodes, relationships, *_ = producer_batch
    base = deepcopy(explorer)
    base["architecture"] = {
        "modules": [{"id": "production-control"}],
        "relationships": [{
            "id": "historical", "source_module": "production-control",
            "target_module": "production-control", "review_status": "candidate",
            "source_continuity": {"status": "current"},
        }],
    }
    base["navigation"]["root"] = {"id": "top-level", "name": "Root"}
    base["navigation"]["drilldowns"][0].update(name="Control", coverage_note="Fixture")
    facts = base["source_facts"]
    fact = facts.pop("fixture")
    facts.update(
        modules=[{"path": fact["evidence"]["path"], "direct_imports": [], "entries": [fact]}],
        powershell_scripts=[], python_definition_index={"files": []},
        python_call_facts={"files": []},
    )
    base["implementation"] = {"revision": explorer["source_facts"]["source_snapshot"]["revision"]}
    base["generation_inputs"] = {}
    # Supply the fixture at the historical source-data boundary. The Review
    # CLI, batch loader, State updates, projections and site writer are real.
    monkeypatch.setattr(review_cli, "publish_reviewed_explorer", lambda *a, **kw: deepcopy(base))
    monkeypatch.setattr(review_cli, "scan_python_source_facts", lambda _: deepcopy(base["source_facts"]))
    monkeypatch.setattr(review_cli, "attach_python_source_facts", lambda data, facts: {**data, "source_facts": facts})

    def put(name, data):
        path = output_root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    base_path = put("explorer.json", base)
    nodes_path = put("nodes.json", nodes)
    relationships_path = put("relationships.json", relationships)
    blank = put("blank.json", {})

    def build(name, *review_args):
        return review_cli.main([
            "build", "--repository", str(output_root), "--snapshot", blank,
            "--mapping", blank, "--candidate", blank, "--review-mapping", blank,
            "--node-review-definitions", nodes_path,
            "--relationship-review-definitions", relationships_path,
            "--web-assets", str(Path.cwd() / "architecture-explorer/web"),
            "--output-directory", f"output/{name}", *review_args,
        ])

    def update(batch_directory, key=NODE):
        put("explorer.json", base)
        if key == NODE:
            action = ["update-node-review-state", "--node-review-definition", nodes_path]
        else:
            action = [
                "update-relationship-review-state", "--review-definition", relationships_path,
                "--node-review-definitions", nodes_path,
            ]
        return review_cli.main([
            *action, "--repository", str(output_root), "--explorer-data", base_path,
            "--review-batch-directory", str(batch_directory), "--review-object-key", key,
            "--assessed-at", "2026-11-26T11:00:00Z",
        ])

    return build, update, base


def test_imported_batch_builds_site_and_keeps_history_after_continuity_update(
    producer_batch, output_root, review_commands,
):
    build, update, base = review_commands
    batch_directory = output_root / "output" / "review"
    assert _import(producer_batch, batch_directory)["status"] == "imported"
    args = ("--review-batch-directory", str(batch_directory))
    assert build("before", *args) == 0
    before = json.loads((output_root / "output/before/explorer-data.json").read_bytes())
    assert before["node_review_projection"]["objects"][NODE]["projection"] == "Current · Rejected"
    assert before["review_projection"]["objects"][DECOMPOSITION]["result"] == "REJECTED"
    assert "view_projection" in before
    decision = batch_directory / "objects" / NODE.replace("::", "__") / "decision.json"
    original = decision.read_bytes()
    worker = next(
        node for node in base["navigation"]["drilldowns"][0]["children"]
        if node["id"] == "worker-entry"
    )
    worker["responsibility"] = "Materially different responsibility."
    assert update(batch_directory) == 0
    assert update(batch_directory, COLLABORATION) == 0
    assert update(batch_directory, DECOMPOSITION) == 0
    assert _state(batch_directory, NODE)["assessed_at"] == "2026-11-26T11:00:00Z"
    assert decision.read_bytes() == original
    assert build("after", *args) == 0
    after = json.loads((output_root / "output/after/explorer-data.json").read_bytes())
    assert after["node_review_projection"]["objects"][NODE]["projection"] == "Historical · Rejected"
    assert after["review_projection"]["objects"][COLLABORATION]["state"] == "needs-review"


def test_even_fully_staged_batch_cannot_build_a_site(producer_batch, output_root, review_commands):
    build, _, _ = review_commands
    target = output_root / "output" / "review"

    def check_before_publish(phase, _):
        if phase == "before-publish":
            staging, = target.parent.glob(".review.staging-*")
            with pytest.raises(ExplorerInputError, match="published review batch"):
                build("staging", "--review-batch-directory", str(staging))

    assert _import(producer_batch, target, fault_injector=check_before_publish)["status"] == "imported"
    assert not (output_root / "output/staging").exists()


@pytest.mark.parametrize("missing", ["member-state", "host-installation"])
def test_partial_or_uninstalled_batch_cannot_build_or_update(
    missing, producer_batch, output_root, review_commands,
):
    build, update, _ = review_commands
    batch_directory = output_root / "output" / "review"
    assert _import(producer_batch, batch_directory)["status"] == "imported"
    if missing == "host-installation":
        producer_batch[-1].unlink()
    else:
        (batch_directory / "objects" / DECOMPOSITION.replace("::", "__") / "state.json").unlink()

    with pytest.raises(ExplorerInputError, match="Unreadable batch artifact"):
        build("incomplete", "--review-batch-directory", str(batch_directory))
    with pytest.raises(ExplorerInputError, match="Unreadable batch artifact"):
        update(batch_directory)
    assert not (output_root / "output/incomplete").exists()


@pytest.mark.parametrize("flag,key", [
    ("--node-review-material-directory", NODE),
    ("--relationship-review-material-directory", COLLABORATION),
])
def test_legacy_material_arguments_reject_batch_decisions_even_with_policy_loaded(
    flag, key, producer_batch, output_root, review_commands,
):
    build, _, _ = review_commands
    batch_directory = output_root / "output" / "review"
    assert _import(producer_batch, batch_directory)["status"] == "imported"
    member = batch_directory / "objects" / key.replace("::", "__")

    with pytest.raises(ExplorerInputError, match="Batch-bound Review Decision"):
        build("legacy", "--review-batch-directory", str(batch_directory), flag, str(member))
    assert not (output_root / "output/legacy").exists()


def test_mixed_deferred_batch_builds_approved_and_rejected_objects(
    producer_batch, output_root, review_commands, monkeypatch,
):
    from booktrans.architecture_explorer import trusted_batch_review as producer
    from test_v4_trusted_batch_review import FakeGitHubReviewPublisher

    explorer, batch, first, nodes, relationships, *_ = producer_batch
    build, _, _ = review_commands
    choices = {
        **first["decisions"],
        PENDING_NODE: {"result": "DEFERRED", "reason": "还需核对源码"},
    }
    first = _freeze_fixture(batch, choices)
    publication = producer.prepare_comment_publication(
        batch, first, repository="nnewlander/translate", issue_number=79, issue_id=6599421144,
    )
    receipts = producer.publish_review_comments(batch, first, publication, FakeGitHubReviewPublisher())
    candidate = producer.prepare_anchor_confirmation(batch, first, publication, receipts)
    second = producer.confirm_anchor_batch(
        candidate, checklist=producer.render_second_confirmation_checklist(candidate),
    )
    installation = producer.prepare_anchor_installation(candidate, second)
    digest = installation["installation_sha256"]
    monkeypatch.setattr(review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS", frozenset({digest}))
    producer.install_anchor_batch(candidate, second)
    target = output_root / "output" / "review"
    result = _import((
        explorer, batch, first, nodes, relationships, publication, receipts,
        producer.host_anchor_installation_path(digest),
    ), target)
    assert result["status"] == "imported", result["errors"]
    assert result["pending_objects"] == [PENDING_NODE]
    assert build("mixed", "--review-batch-directory", str(target)) == 0
    projection = json.loads((output_root / "output/mixed/explorer-data.json").read_bytes())
    assert projection["node_review_projection"]["objects"][PENDING_NODE]["projection"] == "Pending Review"
    relationships = projection["review_projection"]["objects"]
    assert relationships[COLLABORATION]["result"] == "APPROVED"
    assert relationships[DECOMPOSITION]["result"] == "REJECTED"
