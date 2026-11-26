from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import importlib.util
import os
import sys
import tempfile
from threading import Event
from pathlib import Path

import pytest

from booktrans.architecture_explorer import review_artifacts, review_trust, trusted_batch_review_cli
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.github_review_publisher import GhCliReviewPublisher
from booktrans.architecture_explorer.review_source import review_source_sha256
from booktrans.architecture_explorer.trusted_batch_review import (
    confirm_anchor_batch,
    host_anchor_installation_path,
    install_anchor_batch,
    installed_anchor_trust_policy,
    load_installed_anchor_batch,
    prepare_anchor_confirmation,
    prepare_anchor_installation,
    prepare_comment_publication,
    publish_review_comments,
    render_second_confirmation_checklist,
    review_batch_materials,
)
from booktrans.architecture_explorer.trusted_batch_review_cli import (
    main as trusted_batch_main,
)

from test_v4_batch_review import _batch, _freeze_fixture
from test_v4_relationship_review import _definition, _explorer, _node_definition
from booktrans.architecture_explorer.batch_review import prepare_batch_review


def build_isolated_handoff(*, reverse=False):
    explorer = _explorer()
    relationships = explorer["recursive_candidate_fragments"][0]["relationships"]
    decomposition = deepcopy(relationships[0])
    decomposition.update(id="decomposition-under-review", semantic_family="candidate-decomposition")
    relationships.append(decomposition)
    nodes = _node_definition()
    nodes["definitions"].extend(_node_definition("supervisor-boundary")["definitions"])
    definitions = _definition()
    definitions["definitions"].extend(_definition(
        kind="candidate-decomposition", object_id="decomposition-under-review"
    )["definitions"])
    template = _batch()["objects"]["candidate-node::worker-entry"]["analysis"]
    names = {
        "candidate-node::worker-entry": "Worker 入口",
        "candidate-node::supervisor-boundary": "Supervisor 边界",
        "candidate-collaboration::relationship-under-review": "候选协作",
        "candidate-decomposition::decomposition-under-review": "候选分解",
    }
    analysis = {key: {**template, "display_name": name} for key, name in names.items()}
    batch = prepare_batch_review(explorer, nodes, definitions, analysis)
    decisions = {}
    for key in names:
        if key.endswith("::supervisor-boundary"):
            decisions[key] = {"result": "PENDING", "reason": "仍待审核"}
        elif key.startswith("candidate-collaboration") != reverse:
            decisions[key] = {"result": "REJECTED", "reason": "证据不足"}
        else:
            decisions[key] = {"result": "APPROVED", "reason": "认可完整静态范围", "scope": "FULL_PACKET"}
    if reverse:
        decisions["candidate-node::worker-entry"]["rejection_basis"] = {
            "claim_kinds": ["RESPONSIBILITY"]
        }
    first = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch, first, repository="nnewlander/translate", issue_number=79, issue_id=6599421144,
    )
    receipts = publish_review_comments(batch, first, publication, FakeGitHubReviewPublisher())
    candidate = prepare_anchor_confirmation(batch, first, publication, receipts)
    checklist = render_second_confirmation_checklist(candidate)
    second = confirm_anchor_batch(candidate, checklist=checklist)
    return (explorer, nodes, definitions, batch, first, publication,
            receipts, candidate, checklist, second)


def _pin_fixture(monkeypatch, installation):
    monkeypatch.setattr(review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS",
                        frozenset({installation["installation_sha256"]}))


@pytest.fixture(autouse=True)
def isolated_host_storage(monkeypatch, request):
    if "tmp_path" in request.fixturenames:
        monkeypatch.setattr(
            review_trust, "HOST_REVIEW_STATE_DIRECTORY", request.getfixturevalue("tmp_path") / "host"
        )


def _formal_path(installation):
    return host_anchor_installation_path(installation["installation_sha256"])


class FakeGitHubReviewPublisher:
    def __init__(self):
        self.comments = {}
        self.created = 0
        self.updated = 0
        self.actor = {"login": "nnewlander", "id": 256049772, "node_id": "owner-node", "type": "User"}
        self.permission = {"permission": "admin", "role_name": "admin", "user": deepcopy(self.actor)}
        self.repository = {
            "id": 1425303246,
            "node_id": "repo-node",
            "full_name": "nnewlander/translate",
            "html_url": "https://github.com/nnewlander/translate",
            "private": False,
            "owner": deepcopy(self.actor),
        }
        self.issue = {
            "id": 6599421144,
            "node_id": "issue-node",
            "number": 79,
            "html_url": "https://github.com/nnewlander/translate/issues/79",
            "repository_url": "https://api.github.com/repos/nnewlander/translate",
            "state": "open",
        }

    def authenticated_user(self):
        return deepcopy(self.actor)

    def collaborator_permission(self, login):
        assert login == self.actor["login"]
        return deepcopy(self.permission)

    def list_issue_comments(self, repository, issue_number):
        assert repository == "nnewlander/translate" and issue_number == 79
        return [deepcopy(value) for value in self.comments.values()]

    def create_issue_comment(self, repository, issue_number, body):
        self.created += 1
        comment_id = 9000 + self.created
        comment = {
            "id": comment_id,
            "node_id": f"comment-{comment_id}",
            "html_url": f"https://github.com/nnewlander/translate/issues/79#issuecomment-{comment_id}",
            "issue_url": "https://api.github.com/repos/nnewlander/translate/issues/79",
            "author_association": "OWNER",
            "created_at": "2026-11-26T10:00:00Z",
            "updated_at": "2026-11-26T10:00:00Z",
            "body": body,
            "user": deepcopy(self.actor),
        }
        self.comments[comment_id] = comment
        return deepcopy(comment)

    def update_issue_comment(self, repository, comment_id, body):
        self.updated += 1
        self.comments[comment_id]["body"] = body
        self.comments[comment_id]["updated_at"] = "2026-11-26T10:01:00Z"
        return deepcopy(self.comments[comment_id])

    def fetch_repository(self, repository):
        return deepcopy(self.repository)

    def fetch_issue(self, repository, issue_number):
        return deepcopy(self.issue)

    def fetch_comment(self, repository, comment_id):
        return deepcopy(self.comments[comment_id])


def _confirmed_publication():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    return batch, confirmation, publication


def test_wrong_live_issue_prevents_all_comment_writes():
    batch, first, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    github.issue["number"] = 80
    with pytest.raises(ExplorerInputError, match="target"):
        publish_review_comments(batch, first, publication, github)
    assert not github.comments


def test_latest_permission_downgrade_prevents_every_comment_write():
    class DowngradedPublisher(FakeGitHubReviewPublisher):
        reads = 0

        def collaborator_permission(self, login):
            value = super().collaborator_permission(login)
            self.reads += 1
            if self.reads > 1:
                value.update(permission="write", role_name="write")
            return value

    batch, first, publication = _confirmed_publication()
    github = DowngradedPublisher()
    with pytest.raises(ExplorerInputError):
        publish_review_comments(batch, first, publication, github)
    assert (github.created, github.updated) == (0, 0)


def test_invalid_receipt_under_expected_key_blocks_second_confirmation():
    batch, first, publication = _confirmed_publication()
    receipts = publish_review_comments(batch, first, publication, FakeGitHubReviewPublisher())
    key = next(iter(receipts["entries"]))
    receipts["entries"][key] = None
    del receipts["receipts_sha256"]
    receipts["receipts_sha256"] = review_source_sha256(receipts)
    candidate = prepare_anchor_confirmation(batch, first, publication, receipts)
    assert candidate["status"] == "BLOCKED"
    assert f"receipt:{key}:invalid" in candidate["errors"]


@pytest.mark.parametrize(("change", "expected"), [
    ("missing", "missing"), ("null", "invalid"),
    ("issue", "issue-id-invalid"), ("comment", "comment-id-invalid"),
])
def test_blocked_checklist_reports_the_object_receipt_error(change, expected):
    batch, first, publication = _confirmed_publication()
    receipts = publish_review_comments(batch, first, publication, FakeGitHubReviewPublisher())
    key = next(iter(receipts["entries"]))
    if change == "missing":
        del receipts["entries"][key]
    elif change == "null":
        receipts["entries"][key] = None
    else:
        receipts["entries"][key]["source"][change]["id"] = []
    del receipts["receipts_sha256"]
    receipts["receipts_sha256"] = review_source_sha256(receipts)

    candidate = prepare_anchor_confirmation(batch, first, publication, receipts)
    assert candidate["status"] == "BLOCKED"
    assert expected in candidate["entries"][key]["errors"]
    rows, summary = render_second_confirmation_checklist(candidate).split("## 异常汇总")
    row = rows.split(f"`{key}`", 1)[1].split("## ", 1)[0]
    assert any(line.startswith("- 异常：") and expected in line for line in row.splitlines())
    assert f"receipt:{key}:{expected}" in summary


def test_untrusted_existing_draft_is_never_patched():
    batch, first, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    item = next(iter(publication["objects"].values()))
    draft = github.create_issue_comment("nnewlander/translate", 79, item["draft_body"])
    github.comments[draft["id"]]["author_association"] = "MEMBER"
    with pytest.raises(ExplorerInputError, match="author_association"):
        publish_review_comments(batch, first, publication, github)
    assert (github.created, github.updated) == (1, 0)


def test_patch_response_with_different_comment_id_stops_the_batch():
    batch, first, publication = _confirmed_publication()

    class WrongPatchResponse(FakeGitHubReviewPublisher):
        def update_issue_comment(self, repository, comment_id, body):
            response = super().update_issue_comment(repository, comment_id, body)
            response["id"] += 100
            return response

    github = WrongPatchResponse()
    with pytest.raises(ExplorerInputError, match="target ID"):
        publish_review_comments(batch, first, publication, github)
    assert (github.created, github.updated) == (1, 1)


def test_concurrent_publication_cannot_post_from_the_same_empty_list():
    batch, first, publication = _confirmed_publication()
    listed, resume = Event(), Event()

    class PausingPublisher(FakeGitHubReviewPublisher):
        def list_issue_comments(self, *args):
            result = super().list_issue_comments(*args)
            listed.set()
            assert resume.wait(5)
            return result

    github = PausingPublisher()
    with ThreadPoolExecutor() as pool:
        active = pool.submit(publish_review_comments, batch, first, publication, github)
        try:
            assert listed.wait(5)
            with pytest.raises(ExplorerInputError, match="publication is in progress"):
                publish_review_comments(batch, first, publication, github)
        finally:
            resume.set()
        assert len(active.result()["entries"]) == 2
    assert github.created == 2


def test_ambiguous_post_requires_the_existing_comment_to_be_visible(tmp_path):
    batch, first, publication = _confirmed_publication()
    journal = GhCliReviewPublisher(tmp_path)

    class LostPostResponse(FakeGitHubReviewPublisher):
        hidden = True

        def record_post_intent(self, publication_key):
            journal.record_post_intent(publication_key)

        def list_issue_comments(self, *args):
            return [] if self.hidden else super().list_issue_comments(*args)

        def create_issue_comment(self, *args):
            comment = super().create_issue_comment(*args)
            if self.hidden:
                raise OSError("response lost")
            return comment

    github = LostPostResponse()
    with pytest.raises(OSError, match="response lost"):
        publish_review_comments(batch, first, publication, github)
    with pytest.raises(ExplorerInputError, match="creation is uncertain"):
        publish_review_comments(batch, first, publication, github)
    assert github.created == 1
    github.hidden = False
    assert len(publish_review_comments(batch, first, publication, github)["entries"]) == 2
    assert github.created == 2


def test_live_comment_change_blocks_confirmation_even_with_an_intact_receipt():
    batch, first, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    receipts = publish_review_comments(batch, first, publication, github)
    github.comments[9001]["body"] += "\nChanged after publication"
    candidate = prepare_anchor_confirmation(batch, first, publication, receipts, github=github)
    assert candidate["status"] == "BLOCKED"
    assert any("live-receipt-mismatch" in error for error in candidate["errors"])


def test_first_receipt_with_write_permission_does_not_implicate_second_receipt(tmp_path):
    batch, first, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    receipts = publish_review_comments(batch, first, publication, github)
    bad_key, good_key = receipts["entries"]
    bad_receipt = receipts["entries"][bad_key]
    bad_receipt["source"]["permission"].update(permission="write", role_name="write")
    bad_receipt["source_receipt_sha256"] = review_source_sha256(bad_receipt["source"])
    del receipts["receipts_sha256"]
    receipts["receipts_sha256"] = review_source_sha256(receipts)

    candidate = prepare_anchor_confirmation(batch, first, publication, receipts, github=github)
    assert candidate["status"] == "BLOCKED"
    assert any("permission.level" in error for error in candidate["entries"][bad_key]["errors"])
    assert candidate["entries"][good_key]["errors"] == []
    assert all(error.startswith(f"receipt:{bad_key}:") for error in candidate["errors"])
    checklist = render_second_confirmation_checklist(candidate)
    with pytest.raises(ExplorerInputError, match="Blocked anchor candidate"):
        confirm_anchor_batch(candidate, checklist=checklist)
    target = tmp_path / "installation.json"
    with pytest.raises(ExplorerInputError, match="Blocked anchor candidate"):
        install_anchor_batch(candidate, {}, target)
    assert not target.exists()


def test_first_confirmation_prepares_one_exact_comment_for_each_decided_object():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    confirmation = _freeze_fixture(batch, decisions)

    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )

    assert publication["schema"] == "architecture-explorer.batch-comment-publication.v1"
    assert publication["batch_sha256"] == batch["batch_sha256"]
    assert publication["first_confirmation_sha256"] == confirmation["confirmation_sha256"]
    assert list(publication["objects"]) == [
        "candidate-collaboration::relationship-under-review",
        "candidate-node::worker-entry",
    ]
    node = publication["objects"]["candidate-node::worker-entry"]
    assert node["decision"]["result"] == "APPROVED"
    assert node["decision"]["reason"] == "认可完整 Packet 的静态范围"
    assert node["decision"]["packet_sha256"] == batch["objects"]["candidate-node::worker-entry"]["packet"]["packet_sha256"]
    assert "ARCHITECTURE_EXPLORER_NODE_REVIEW_V2" in node["draft_body"]
    assert "candidate-node::worker-entry" in node["draft_body"]
    assert "认可完整 Packet 的静态范围" in node["draft_body"]
    relationship = publication["objects"]["candidate-collaboration::relationship-under-review"]
    assert "ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1" in relationship["draft_body"]
    assert relationship["decision"]["result"] == "REJECTED"
    assert publication["publication_sha256"]


def test_publication_uses_real_owner_admin_receipts_and_reuses_exact_comments():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    github = FakeGitHubReviewPublisher()

    confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    receipts = publish_review_comments(batch, confirmation, publication, github)

    assert receipts["schema"] == "architecture-explorer.batch-comment-receipts.v1"
    assert list(receipts["entries"]) == list(publication["objects"])
    assert github.created == 2 and github.updated == 2
    for key, entry in receipts["entries"].items():
        source = entry["source"]
        assert source["comment"]["user"]["login"] == "nnewlander"
        assert source["permission"]["permission"] == "admin"
        assert source["comment"]["body"].startswith(
            "ARCHITECTURE_EXPLORER_"
        )
        assert f"Publication-Key: {entry['publication_key']}" in source["comment"]["body"]
        assert f"Decision-ID: github-comment:{source['comment']['id']}" in source["comment"]["body"]
        assert entry["source_receipt_sha256"] == review_source_sha256(source)
        assert entry["publication_key"] == publication["objects"][key]["publication_key"]

    repeated = publish_review_comments(batch, confirmation, publication, github)
    assert repeated == receipts
    assert github.created == 2 and github.updated == 2


def test_publication_rechecks_confirmed_material_even_if_local_digest_is_recomputed():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    altered = deepcopy(publication)
    altered["objects"]["candidate-node::worker-entry"]["decision"]["reason"] = "扩大为未来对象的概括批准"
    altered["publication_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in altered.items() if key != "publication_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    github = FakeGitHubReviewPublisher()

    with pytest.raises(ExplorerInputError, match="confirmed material"):
        publish_review_comments(batch, confirmation, altered, github)

    assert github.created == 0


def test_trusted_receipts_produce_a_readable_second_confirmation_and_exact_anchors():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    receipts = publish_review_comments(
        batch,
        confirmation,
        publication,
        FakeGitHubReviewPublisher(),
    )

    candidate = prepare_anchor_confirmation(batch, confirmation, publication, receipts)
    checklist = render_second_confirmation_checklist(candidate)

    assert candidate["schema"] == "architecture-explorer.batch-anchor-confirmation-candidate.v1"
    assert candidate["status"] == "READY"
    assert candidate["errors"] == []
    assert list(candidate["entries"]) == list(publication["objects"])
    assert "# 第二次确认：可信评论与精确 Anchor 清单" in checklist
    assert "candidate-node::worker-entry" in checklist
    assert "github-comment:9002" in checklist
    assert "nnewlander（OWNER / admin）" in checklist
    assert "2026-11-26T10:00:00Z" in checklist
    assert "2026-11-26T10:01:00Z" in checklist
    assert receipts["entries"]["candidate-node::worker-entry"]["source_receipt_sha256"] in checklist
    assert "异常：无" in checklist
    assert "本次授权仅覆盖以下 2 个 Anchor" in checklist


def test_second_confirmation_reports_a_receipt_from_the_wrong_issue_as_blocked():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    receipts = publish_review_comments(
        batch,
        confirmation,
        publication,
        FakeGitHubReviewPublisher(),
    )
    altered = deepcopy(receipts)
    key = "candidate-node::worker-entry"
    entry = altered["entries"][key]
    source = entry["source"]
    comment_id = source["comment"]["id"]
    source["issue"].update(
        {
            "id": 6599422057,
            "number": 80,
            "html_url": "https://github.com/nnewlander/translate/issues/80",
        }
    )
    source["comment"]["html_url"] = (
        f"https://github.com/nnewlander/translate/issues/80#issuecomment-{comment_id}"
    )
    source["comment"]["issue_url"] = (
        "https://api.github.com/repos/nnewlander/translate/issues/80"
    )
    entry["source_receipt_sha256"] = review_source_sha256(source)
    altered["receipts_sha256"] = hashlib.sha256(
        json.dumps(
            {name: value for name, value in altered.items() if name != "receipts_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    candidate = prepare_anchor_confirmation(batch, confirmation, publication, altered)
    checklist = render_second_confirmation_checklist(candidate)

    assert candidate["status"] == "BLOCKED"
    assert f"receipt:{key}:issue-mismatch" in candidate["errors"]
    assert "issue-mismatch" in checklist


def test_only_exact_second_confirmation_installs_one_complete_anchor_batch(tmp_path, monkeypatch):
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    first_confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        first_confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    receipts = publish_review_comments(
        batch,
        first_confirmation,
        publication,
        FakeGitHubReviewPublisher(),
    )
    candidate = prepare_anchor_confirmation(
        batch, first_confirmation, publication, receipts
    )
    checklist = render_second_confirmation_checklist(candidate)
    second_confirmation = confirm_anchor_batch(candidate, checklist=checklist)
    target = tmp_path / "installed-anchor-batch.v1.json"

    with pytest.raises(ExplorerInputError, match="second confirmation"):
        install_anchor_batch(candidate, {}, target)
    assert not target.exists()
    proposed = prepare_anchor_installation(candidate, second_confirmation)
    with pytest.raises(ExplorerInputError, match="independent host"):
        install_anchor_batch(candidate, second_confirmation, target)
    with pytest.raises(ExplorerInputError, match="independent host"):
        installed_anchor_trust_policy(proposed)
    _pin_fixture(monkeypatch, proposed)
    caller_file = tmp_path / "prepared-installation.json"
    caller_file.write_text(json.dumps(proposed), encoding="utf-8")
    with pytest.raises(ExplorerInputError, match="host installation"):
        load_installed_anchor_batch(caller_file, batch, first_confirmation)
    with pytest.raises(ExplorerInputError, match="(?i)host installation"):
        installed_anchor_trust_policy(proposed)

    target = _formal_path(proposed)
    installed = install_anchor_batch(candidate, second_confirmation, target)

    assert installed["schema"] == "architecture-explorer.batch-anchor-installation.v1"
    assert installed["status"] == "installed"
    assert set(installed["objects"]) == set(candidate["object_keys"])
    assert load_installed_anchor_batch(target, batch, first_confirmation) == installed
    assert len(installed_anchor_trust_policy(installed).anchors) == 2
    assert install_anchor_batch(candidate, second_confirmation, target) == installed


def test_interrupted_install_never_exposes_pending_file_as_a_complete_batch_and_retries(
    tmp_path, monkeypatch
):
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    first_confirmation = _freeze_fixture(batch, decisions)
    publication = prepare_comment_publication(
        batch,
        first_confirmation,
        repository="nnewlander/translate",
        issue_number=79,
    )
    receipts = publish_review_comments(
        batch,
        first_confirmation,
        publication,
        FakeGitHubReviewPublisher(),
    )
    candidate = prepare_anchor_confirmation(
        batch, first_confirmation, publication, receipts
    )
    second_confirmation = confirm_anchor_batch(
        candidate,
        checklist=render_second_confirmation_checklist(candidate),
    )
    proposed = prepare_anchor_installation(candidate, second_confirmation)
    target = _formal_path(proposed)
    _pin_fixture(monkeypatch, proposed)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            review_artifacts.os,
            "rename" if os.name == "nt" else "link",
            lambda source, destination: (_ for _ in ()).throw(OSError("injected interruption")),
        )
        with pytest.raises(OSError, match="injected interruption"):
            install_anchor_batch(candidate, second_confirmation, target)

    pending = target.with_name(target.name + ".pending")
    assert not target.exists()
    pending.write_text("interrupted staging", encoding="utf-8")
    with pytest.raises(ExplorerInputError, match="Pending"):
        load_installed_anchor_batch(pending, batch, first_confirmation)

    installed = install_anchor_batch(candidate, second_confirmation, target)
    assert installed["status"] == "installed"


def test_concurrent_distinct_installations_never_overwrite(tmp_path, monkeypatch):
    left = build_isolated_handoff()
    right = build_isolated_handoff(reverse=True)
    candidates = [(item[7], item[9]) for item in (left, right)]
    proposed = [prepare_anchor_installation(*item) for item in candidates]
    monkeypatch.setattr(
        review_trust, "PRODUCTION_BATCH_INSTALLATION_DIGESTS",
        frozenset(item["installation_sha256"] for item in proposed),
    )
    target = _formal_path(proposed[0])
    with ThreadPoolExecutor() as pool:
        jobs = [pool.submit(install_anchor_batch, *item, target) for item in [*candidates, candidates[0]]]
        winners = []
        for job in jobs:
            try:
                winners.append(job.result())
            except ExplorerInputError as error:
                assert "formal host installation" in str(error)
    assert winners == [proposed[0], proposed[0]]
    assert json.loads(target.read_text(encoding="utf-8")) == winners[0]


def test_cli_reuses_unresolved_post_across_working_directories_and_temp(tmp_path, monkeypatch):
    batch, first, publication = _confirmed_publication()
    publication = prepare_comment_publication(
        batch, first, repository="nnewlander/translate", issue_number=79, issue_id=6599421144
    )
    paths = {}
    for name, value in (("batch", batch), ("first-confirmation", first), ("publication", publication)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    args = ["publish-comments"]
    for name, path in paths.items():
        args.extend((f"--{name}", str(path)))
    args.extend(("--output", str(tmp_path / "receipts.json"),
                 "--confirmed-publication-sha256", publication["publication_sha256"]))

    class UncertainPublisher(FakeGitHubReviewPublisher):
        def list_issue_comments(self, *args):
            return []

        def create_issue_comment(self, *args):
            super().create_issue_comment(*args)
            raise OSError("POST response lost")

    remote = UncertainPublisher()

    def adapter(*args):
        journal = GhCliReviewPublisher(*args)
        remote.publication_lock = journal.publication_lock
        remote.record_post_intent = journal.record_post_intent
        return remote

    monkeypatch.setattr(trusted_batch_review_cli, "GhCliReviewPublisher", adapter)
    for index, expected in enumerate(("POST response lost", "creation is uncertain")):
        current = tmp_path / f"worktree-{index}"
        current.mkdir()
        temp = tmp_path / f"temp-{index}"
        temp.mkdir()
        monkeypatch.chdir(current)
        monkeypatch.setenv("TEMP", str(temp))
        monkeypatch.setenv("TMP", str(temp))
        monkeypatch.setattr(tempfile, "tempdir", None)
        with pytest.raises((OSError, ExplorerInputError), match=expected):
            trusted_batch_main(args)
    assert remote.created == 1


def test_cli_carries_confirmed_material_through_second_confirmation_and_install(tmp_path, monkeypatch):
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {
            "result": "APPROVED",
            "reason": "认可完整 Packet 的静态范围",
            "scope": "FULL_PACKET",
        },
        "candidate-collaboration::relationship-under-review": {
            "result": "REJECTED",
            "reason": "现有 Evidence 不支持该协作解释",
        },
    }
    first_confirmation = _freeze_fixture(batch, decisions)
    batch_path = tmp_path / "batch.json"
    first_path = tmp_path / "first-confirmation.json"
    publication_path = tmp_path / "publication.json"
    receipts_path = tmp_path / "receipts.json"
    candidate_path = tmp_path / "anchor-candidate.json"
    checklist_path = tmp_path / "second-confirmation.md"
    second_path = tmp_path / "second-confirmation.json"
    installed_path = tmp_path / "installed-anchor-batch.v1.json"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    first_path.write_text(json.dumps(first_confirmation), encoding="utf-8")

    assert trusted_batch_main(
        [
            "prepare-publication",
            "--batch",
            str(batch_path),
            "--first-confirmation",
            str(first_path),
            "--issue-number",
            "79",
            "--issue-id",
            "6599421144",
            "--output",
            str(publication_path),
        ]
    ) == 0
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    github = FakeGitHubReviewPublisher()
    publish_args = [
        "publish-comments", "--batch", str(batch_path), "--first-confirmation", str(first_path),
        "--publication", str(publication_path), "--output", str(receipts_path),
        "--confirmed-publication-sha256", publication["publication_sha256"],
    ]
    with pytest.raises(ExplorerInputError, match="exact confirmed publication"):
        trusted_batch_main([*publish_args[:-1], "0" * 64], github=github)
    assert github.created == 0
    assert trusted_batch_main(publish_args, github=github) == 0
    assert trusted_batch_main(publish_args, github=github) == 0
    assert github.created == 2
    assert trusted_batch_main(
        [
            "prepare-anchor-confirmation",
            "--batch",
            str(batch_path),
            "--first-confirmation",
            str(first_path),
            "--publication",
            str(publication_path),
            "--receipts",
            str(receipts_path),
            "--output",
            str(candidate_path),
            "--checklist-output",
            str(checklist_path),
        ],
        github=github,
    ) == 0
    assert trusted_batch_main(
        [
            "confirm-anchors",
            "--candidate",
            str(candidate_path),
            "--checklist",
            str(checklist_path),
            "--output",
            str(second_path),
        ]
    ) == 0
    proposed = prepare_anchor_installation(
        json.loads(candidate_path.read_text(encoding="utf-8")),
        json.loads(second_path.read_text(encoding="utf-8")),
    )
    _pin_fixture(monkeypatch, proposed)
    installed_path = _formal_path(proposed)
    assert trusted_batch_main(
        [
            "install-anchors",
            "--candidate",
            str(candidate_path),
            "--second-confirmation",
            str(second_path),
            "--output",
            str(installed_path),
        ]
    ) == 0
    assert load_installed_anchor_batch(installed_path, batch, first_confirmation)["status"] == "installed"


def test_untrusted_publisher_is_rejected_before_any_comment_is_created():
    batch, confirmation, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    github.actor = {
        "login": "review-bot",
        "id": 1,
        "node_id": "bot-node",
        "type": "Bot",
    }

    with pytest.raises(ExplorerInputError, match="trusted Owner"):
        publish_review_comments(batch, confirmation, publication, github)

    assert github.created == 0 and github.comments == {}


def test_existing_comment_with_same_object_but_different_content_stops_publication():
    batch, confirmation, publication = _confirmed_publication()
    github = FakeGitHubReviewPublisher()
    key = "candidate-collaboration::relationship-under-review"
    draft = publication["objects"][key]["draft_body"]
    github.create_issue_comment(
        "nnewlander/translate",
        79,
        draft.replace("Result: REJECTED", "Result: APPROVED"),
    )

    with pytest.raises(ExplorerInputError, match="content differs"):
        publish_review_comments(batch, confirmation, publication, github)

    assert github.created == 1


def test_publication_interruption_reuses_the_existing_draft_instead_of_duplicating():
    batch, confirmation, publication = _confirmed_publication()

    class InterruptedPublisher(FakeGitHubReviewPublisher):
        interrupted = False

        def update_issue_comment(self, repository, comment_id, body):
            if not self.interrupted:
                self.interrupted = True
                raise OSError("injected comment update interruption")
            return super().update_issue_comment(repository, comment_id, body)

    github = InterruptedPublisher()
    with pytest.raises(OSError, match="injected comment update interruption"):
        publish_review_comments(batch, confirmation, publication, github)
    assert github.created == 1

    receipts = publish_review_comments(batch, confirmation, publication, github)

    assert len(receipts["entries"]) == 2
    assert github.created == 2
    assert len(github.comments) == 2


def test_partial_installed_batch_is_rejected_even_with_a_recomputed_digest(tmp_path, monkeypatch):
    batch, first_confirmation, publication = _confirmed_publication()
    receipts = publish_review_comments(
        batch,
        first_confirmation,
        publication,
        FakeGitHubReviewPublisher(),
    )
    candidate = prepare_anchor_confirmation(
        batch, first_confirmation, publication, receipts
    )
    second_confirmation = confirm_anchor_batch(
        candidate,
        checklist=render_second_confirmation_checklist(candidate),
    )
    path = tmp_path / "installed-anchor-batch.v1.json"
    partial = prepare_anchor_installation(candidate, second_confirmation)
    partial["objects"].pop(next(iter(partial["objects"])))
    partial["installation_sha256"] = hashlib.sha256(
        json.dumps(
            {name: value for name, value in partial.items() if name != "installation_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    path = _formal_path(partial)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(partial), encoding="utf-8")
    _pin_fixture(monkeypatch, partial)

    with pytest.raises(ExplorerInputError, match="differs from the first confirmation"):
        load_installed_anchor_batch(path, batch, first_confirmation)


HANDOFF_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "architecture_explorer" / "v4_ticket_05" / "handoff.v1.json"
)


def isolated_handoff_fixture():
    explorer, nodes, definitions, batch, first, publication, receipts, candidate, checklist, second = (
        build_isolated_handoff()
    )
    return {
        "fixture_only": True, "explorer": explorer, "node_definitions": nodes,
        "relationship_definitions": definitions, "batch": batch, "first_confirmation": first,
        "publication": publication, "receipts": receipts, "second_confirmation": second,
        "installed_batch": prepare_anchor_installation(candidate, second), "checklist": checklist,
    }


def test_ticket_80_shared_fixture_replays_producer_and_verifies_its_content():
    saved = HANDOFF_FIXTURE.read_text(encoding="utf-8")
    assert "\ufffd" not in saved
    assert json.loads(saved) == isolated_handoff_fixture()


def test_installed_batch_rejects_malformed_anchor_identity_even_with_valid_batch_digest(tmp_path):
    fixture = isolated_handoff_fixture()
    installed = fixture["installed_batch"]
    first = next(iter(installed["objects"]))
    installed["objects"][first]["anchor"]["comment_id"] = "9001"
    del installed["installation_sha256"]
    installed["installation_sha256"] = review_source_sha256(installed)
    path = tmp_path / "installed-anchor-batch.v1.json"
    path.write_text(json.dumps(installed), encoding="utf-8")
    with pytest.raises(ExplorerInputError, match="entry is invalid"):
        load_installed_anchor_batch(path, fixture["batch"], fixture["first_confirmation"])


@pytest.mark.parametrize("reverse", [False, True])
def test_producer_installation_passes_real_t6_consumer(tmp_path, monkeypatch, reverse):
    consumer_path = os.environ.get("ARCHITECTURE_EXPLORER_T6_CONSUMER")
    if consumer_path is None:
        pytest.skip("Set the #80 batch_import.py path for cross-worktree integration")
    spec = importlib.util.spec_from_file_location(
        "booktrans.architecture_explorer.batch_import", consumer_path,
    )
    assert spec and spec.loader
    consumer = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, consumer)
    spec.loader.exec_module(consumer)
    explorer, nodes, definitions, batch, first, publication, receipts, candidate, _, second = (
        build_isolated_handoff(reverse=reverse)
    )
    proposed = prepare_anchor_installation(candidate, second)
    _pin_fixture(monkeypatch, proposed)
    installation_path = _formal_path(proposed)
    caller_file = tmp_path / "prepared-installation.json"
    caller_file.write_text(json.dumps(proposed), encoding="utf-8")
    denied = consumer.import_review_batch(
        explorer, batch, first, nodes, definitions, publication, receipts, caller_file,
        tmp_path / "denied", assessed_at="2026-11-26T11:00:00Z",
    )
    assert denied["status"] == "preflight-failed", denied
    assert not (tmp_path / "denied").exists()
    with pytest.raises(ExplorerInputError, match="(?i)host installation"):
        load_installed_anchor_batch(installation_path, batch, first)
    install_anchor_batch(candidate, second, installation_path)
    installed = load_installed_anchor_batch(installation_path, batch, first)
    assert len(installed_anchor_trust_policy(installed).anchors) == 3
    assert set(review_batch_materials(batch, first, publication, receipts)) == set(installed["objects"])
    result = consumer.import_review_batch(
        explorer, batch, first, nodes, definitions, publication, receipts, installation_path,
        tmp_path / "imported", assessed_at="2026-11-26T11:00:00Z",
    )
    assert result["status"] == "imported", result["errors"]
    assert set(result["imported_objects"]) == set(publication["objects"])
    assert result["pending_objects"] == ["candidate-node::supervisor-boundary"]
    replayed = consumer.import_review_batch(
        explorer, batch, first, nodes, definitions, publication, receipts, installation_path,
        tmp_path / "imported", assessed_at="2026-11-26T11:00:00Z",
    )
    assert replayed["status"] == "already-imported", replayed
