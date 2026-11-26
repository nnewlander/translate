"""Trusted comment publication and approval-anchor batch workflow.

The first confirmation remains an offline content binding.  This module turns
only its decided objects into exact per-object publication material; later
stages attach real GitHub receipts and a separately confirmed anchor batch.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any, Protocol

from .batch_review import decided_objects
from . import review_trust
from .review_artifacts import write_review_artifact
from .generator import ExplorerInputError
from .node_review import NODE_REVIEW_IMPORT_NOTE
from .node_review_definitions import (
    NODE_REVIEW_DECISION_V2_SCHEMA,
    REJECTION_BASIS_SCHEMA,
    rejected_node_review_claim_hashes,
    render_node_review_decision_body,
)
from .relationship_review import (
    RELATIONSHIP_REVIEW_DECISION_SCHEMA,
    RELATIONSHIP_REVIEW_IMPORT_NOTE,
    render_relationship_review_decision_body,
)
from .review_source import (
    TRUSTED_OWNER_ID,
    TRUSTED_OWNER_LOGIN,
    TRUSTED_REPOSITORY,
    TRUSTED_REPOSITORY_ID,
    build_review_source_receipt,
    review_source_sha256,
)
from .review_trust import (
    ReviewApprovalAnchor,
    ReviewTrustPolicy,
    match_trusted_review_source,
)


PUBLICATION_SCHEMA = "architecture-explorer.batch-comment-publication.v1"
RECEIPT_BATCH_SCHEMA = "architecture-explorer.batch-comment-receipts.v1"
ANCHOR_CONFIRMATION_CANDIDATE_SCHEMA = (
    "architecture-explorer.batch-anchor-confirmation-candidate.v1"
)
SECOND_CONFIRMATION_SCHEMA = "architecture-explorer.batch-anchor-confirmation.v1"
INSTALLED_ANCHOR_BATCH_SCHEMA = "architecture-explorer.batch-anchor-installation.v1"
_PUBLICATION_LOCK = Lock()


class ReviewCommentPublisher(Protocol):
    """The narrow external seam used by the trusted publication workflow."""

    def authenticated_user(self) -> dict[str, Any]: ...

    def collaborator_permission(self, login: str) -> dict[str, Any]: ...

    def list_issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, Any]]: ...

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> dict[str, Any]: ...

    def update_issue_comment(
        self, repository: str, comment_id: int, body: str
    ) -> dict[str, Any]: ...

    def fetch_repository(self, repository: str) -> dict[str, Any]: ...

    def fetch_issue(self, repository: str, issue_number: int) -> dict[str, Any]: ...

    def fetch_comment(self, repository: str, comment_id: int) -> dict[str, Any]: ...


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _comment_header(kind: str) -> str:
    if kind == "candidate-node":
        return "ARCHITECTURE_EXPLORER_NODE_REVIEW_V2"
    if kind in {"candidate-collaboration", "candidate-decomposition"}:
        return "ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1"
    raise ExplorerInputError(f"Unsupported batch review object kind: {kind}")


def _draft_body(key: str, publication_key: str, decision: dict[str, Any]) -> str:
    return "\n".join(
        (
            _comment_header(decision["object_key"]["kind"]),
            "Publication-State: DRAFT",
            f"Publication-Key: {publication_key}",
            f"Object-Key: {key}",
            f"Result: {decision['result']}",
            "Owner-Decision: "
            + json.dumps(
                {
                    name: decision[name]
                    for name in ("result", "reason", "scope", "rejection_basis")
                    if name in decision
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            f"Packet-SHA256: {decision['packet_sha256']}",
            f"Claims-SHA256: {decision['inputs']['claims_sha256']}",
            f"Reviewed-Revision: {decision['reviewed_source_snapshot']['revision']}",
        )
    )


def _published_body(
    key: str,
    publication_key: str,
    decision: dict[str, Any],
    *,
    comment_id: int,
) -> str:
    material = _decision_material(decision, publication_key, comment_id)
    render = (
        render_node_review_decision_body
        if key.startswith("candidate-node::")
        else render_relationship_review_decision_body
    )
    return render(material)


def _decision_material(decision, publication_key, comment_id, source_digest=None):
    is_node = decision["object_key"]["kind"] == "candidate-node"
    material = {
        name: deepcopy(decision[name])
        for name in ("object_key", "result", "reviewed_source_snapshot", "inputs", "claims")
    }
    material.update(
        schema=NODE_REVIEW_DECISION_V2_SCHEMA if is_node else RELATIONSHIP_REVIEW_DECISION_SCHEMA,
        decision_id=f"github-comment:{comment_id}",
        review_notes=NODE_REVIEW_IMPORT_NOTE if is_node else RELATIONSHIP_REVIEW_IMPORT_NOTE,
        source_receipt_sha256=source_digest,
        batch_publication_sha256=publication_key,
    )
    if is_node and "rejection_basis" in decision:
        basis = decision["rejection_basis"]
        if isinstance(basis, dict) and set(basis) == {"claim_kinds"}:
            kinds = basis["claim_kinds"]
            claims = {claim["kind"]: claim for claim in material["claims"]}
            if (
                not isinstance(kinds, list) or not kinds
                or any(not isinstance(kind, str) or kind not in claims for kind in kinds)
                or len(set(kinds)) != len(kinds)
            ):
                raise ExplorerInputError("Invalid first-confirmed Node rejection claims")
            basis = {
                "schema": REJECTION_BASIS_SCHEMA,
                "rejected_claims": [
                    {"claim_sha256": _canonical_sha256(claims[kind]),
                     "reason_code": "OWNER_REJECTED", "explanation": decision["reason"]}
                    for kind in kinds
                ],
            }
        material["rejection_basis"] = deepcopy(basis)
        rejected_node_review_claim_hashes(material)
    return material


def _validate_publication(publication: dict[str, Any]) -> None:
    expected = {
        "schema",
        "repository",
        "issue_number",
        "issue_id",
        "batch_sha256",
        "first_confirmation_sha256",
        "objects",
        "publication_sha256",
    }
    if (
        not isinstance(publication, dict)
        or set(publication) != expected
        or publication.get("schema") != PUBLICATION_SCHEMA
        or publication.get("repository") != TRUSTED_REPOSITORY
        or not isinstance(publication.get("objects"), dict)
        or not publication["objects"]
        or publication.get("publication_sha256")
        != _canonical_sha256(
            {key: value for key, value in publication.items() if key != "publication_sha256"}
        )
    ):
        raise ExplorerInputError("Comment publication material changed")


def _validate_publisher_identity(github: ReviewCommentPublisher) -> None:
    actor = github.authenticated_user()
    if (
        not isinstance(actor, dict)
        or actor.get("login") != TRUSTED_OWNER_LOGIN
        or actor.get("id") != TRUSTED_OWNER_ID
        or actor.get("type") != "User"
    ):
        raise ExplorerInputError("GitHub publisher is not the trusted Owner identity")
    _validated_publisher_permission(github)


def _validated_publisher_permission(github: ReviewCommentPublisher) -> dict[str, Any]:
    permission = github.collaborator_permission(TRUSTED_OWNER_LOGIN)
    permission_user = permission.get("user") if isinstance(permission, dict) else {}
    if (
        not isinstance(permission, dict)
        or not isinstance(permission_user, dict)
        or permission.get("permission") != "admin"
        or permission.get("role_name") != "admin"
        or permission_user.get("login") != TRUSTED_OWNER_LOGIN
        or permission_user.get("id") != TRUSTED_OWNER_ID
        or permission_user.get("type") != "User"
    ):
        raise ExplorerInputError("GitHub publisher does not have trusted admin permission")
    return permission


def publish_review_comments(
    batch: dict[str, Any],
    first_confirmation: dict[str, Any],
    publication: dict[str, Any],
    github: ReviewCommentPublisher,
) -> dict[str, Any]:
    """Publish sequentially on this host. The live adapter also locks across processes."""
    if not _PUBLICATION_LOCK.acquire(blocking=False):
        raise ExplorerInputError("Another local publication is in progress")
    try:
        _validate_publication(publication)
        issue = publication["issue_number"]
        if type(issue) is not int or issue <= 0:
            raise ExplorerInputError("Invalid GitHub Issue target")
        lock = getattr(github, "publication_lock", None)
        if lock is not None and (type(publication["issue_id"]) is not int or publication["issue_id"] <= 0):
            raise ExplorerInputError("Live publication requires a bound GitHub Issue ID")
        with lock(issue) if lock is not None else nullcontext():
            return _publish_review_comments(batch, first_confirmation, publication, github)
    finally:
        _PUBLICATION_LOCK.release()


def _publish_review_comments(
    batch: dict[str, Any],
    first_confirmation: dict[str, Any],
    publication: dict[str, Any],
    github: ReviewCommentPublisher,
) -> dict[str, Any]:
    """Publish or safely reuse one exact GitHub comment per decided object."""

    _validate_publication(publication)
    expected_publication = prepare_comment_publication(
        batch,
        first_confirmation,
        repository=publication["repository"],
        issue_number=publication["issue_number"],
        issue_id=publication["issue_id"],
    )
    if publication != expected_publication:
        raise ExplorerInputError("Comment publication differs from confirmed material")
    _validate_publisher_identity(github)
    repository_name = publication["repository"]
    issue_number = publication["issue_number"]
    repository = github.fetch_repository(repository_name)
    issue = github.fetch_issue(repository_name, issue_number)
    owner = repository.get("owner") or {}
    base = f"https://api.github.com/repos/{repository_name}"
    if (
        repository.get("id") != TRUSTED_REPOSITORY_ID
        or repository.get("full_name") != repository_name
        or repository.get("html_url") != f"https://github.com/{repository_name}"
        or not isinstance(owner, dict)
        or (owner.get("login"), owner.get("id"), owner.get("type"))
        != (TRUSTED_OWNER_LOGIN, TRUSTED_OWNER_ID, "User")
        or type(issue.get("id")) is not int or issue["id"] <= 0
        or (publication["issue_id"] is not None and issue["id"] != publication["issue_id"])
        or issue.get("number") != issue_number
        or issue.get("repository_url") != base
        or issue.get("html_url") != f"https://github.com/{repository_name}/issues/{issue_number}"
        or "pull_request" in issue
    ):
        raise ExplorerInputError("GitHub repository or issue target mismatch")
    permission = _validated_publisher_permission(github)

    def checked_source(comment, comment_id, body):
        if (type(comment_id) is not int or comment_id <= 0
            or not isinstance(comment, dict) or type(comment.get("id")) is not int
            or comment["id"] != comment_id or comment.get("body") != body):
            raise ExplorerInputError("GitHub comment target ID or content differs")
        return build_review_source_receipt(
            repository, issue, comment, permission, fixed_t3_identity=False
        )

    comments = github.list_issue_comments(repository_name, issue_number)
    entries: dict[str, dict[str, Any]] = {}
    existing = {}
    for key, item in publication["objects"].items():
        kind, identity = key.split("::", 1)
        markers = {f"Object-Key: {key}", f"Object-Key: {kind} + {identity}"}
        related = [
            comment
            for comment in comments
            if isinstance(comment, dict)
            and isinstance(comment.get("body"), str)
            and markers.intersection(comment["body"].splitlines())
            and comment["body"].startswith(_comment_header(item["decision"]["object_key"]["kind"]))
        ]
        if len(related) > 1:
            raise ExplorerInputError(f"Multiple GitHub review comments exist for {key}")
        if related:
            comment = related[0]
            if f"Publication-Key: {item['publication_key']}" not in comment["body"]:
                raise ExplorerInputError(f"Existing GitHub review comment content differs: {key}")
            comment_id = comment.get("id")
            if type(comment_id) is not int or comment_id <= 0:
                raise ExplorerInputError(f"GitHub review comment has no valid ID: {key}")
            checked_source(comment, comment_id, comment["body"])
            checked_source(github.fetch_comment(repository_name, comment_id), comment_id, comment["body"])
            expected = _published_body(key, item["publication_key"], item["decision"], comment_id=comment_id)
            if comment["body"] not in (item["draft_body"], expected):
                raise ExplorerInputError(f"Existing GitHub review comment content differs: {key}")
            existing[key] = comment

    for key, item in publication["objects"].items():
        comment = existing.get(key)
        if comment is None:
            permission = _validated_publisher_permission(github)
            record_intent = getattr(github, "record_post_intent", None)
            if record_intent is not None:
                record_intent(item["publication_key"])
            comment = github.create_issue_comment(
                repository_name, issue_number, item["draft_body"]
            )
            checked_source(comment, comment.get("id"), item["draft_body"])
            checked_source(
                github.fetch_comment(repository_name, comment["id"]),
                comment["id"], item["draft_body"],
            )

        comment_id = comment.get("id")
        if not isinstance(comment_id, int) or isinstance(comment_id, bool) or comment_id <= 0:
            raise ExplorerInputError(f"GitHub review comment has no valid ID: {key}")
        final_body = _published_body(
            key,
            item["publication_key"],
            item["decision"],
            comment_id=comment_id,
        )
        if comment.get("body") == item["draft_body"]:
            permission = _validated_publisher_permission(github)
            comment = github.update_issue_comment(repository_name, comment_id, final_body)
            checked_source(comment, comment_id, final_body)
        elif comment.get("body") != final_body:
            raise ExplorerInputError(f"Existing GitHub review comment content differs: {key}")

        comment = github.fetch_comment(repository_name, comment_id)
        source = checked_source(comment, comment_id, final_body)
        source_digest = review_source_sha256(source)
        entries[key] = {
            "publication_key": item["publication_key"],
            "first_decision_sha256": first_confirmation["bindings"][key]["decision_sha256"],
            "decision": _decision_material(item["decision"], item["publication_key"], comment_id, source_digest),
            "packet_sha256": item["decision"]["packet_sha256"],
            "comment_body_sha256": hashlib.sha256(final_body.encode("utf-8")).hexdigest(),
            "source_receipt_sha256": source_digest,
            "source": source,
        }

    receipts = {
        "schema": RECEIPT_BATCH_SCHEMA,
        "publication_sha256": publication["publication_sha256"],
        "batch_sha256": publication["batch_sha256"],
        "first_confirmation_sha256": publication["first_confirmation_sha256"],
        "entries": entries,
    }
    receipts["receipts_sha256"] = _canonical_sha256(receipts)
    return receipts


def _candidate_anchor(source: dict[str, Any], source_digest: str) -> ReviewApprovalAnchor:
    issue = source.get("issue") if isinstance(source.get("issue"), dict) else {}
    comment = source.get("comment") if isinstance(source.get("comment"), dict) else {}
    return ReviewApprovalAnchor(
        issue_id=issue.get("id"),
        issue_number=issue.get("number"),
        comment_id=comment.get("id"),
        comment_created_at=comment.get("created_at"),
        comment_updated_at=comment.get("updated_at"),
        source_receipt_sha256=source_digest,
    )


def _anchor_dict(anchor: ReviewApprovalAnchor) -> dict[str, Any]:
    return {
        "issue_id": anchor.issue_id,
        "issue_number": anchor.issue_number,
        "comment_id": anchor.comment_id,
        "comment_created_at": anchor.comment_created_at,
        "comment_updated_at": anchor.comment_updated_at,
        "source_receipt_sha256": anchor.source_receipt_sha256,
    }


def prepare_anchor_confirmation(
    batch: dict[str, Any],
    first_confirmation: dict[str, Any],
    publication: dict[str, Any],
    receipts: dict[str, Any],
    *,
    github: ReviewCommentPublisher | None = None,
) -> dict[str, Any]:
    """Recheck published receipts and prepare the exact second confirmation set."""

    _validate_publication(publication)
    errors: list[str] = []
    expected_publication = prepare_comment_publication(
        batch,
        first_confirmation,
        repository=publication["repository"],
        issue_number=publication["issue_number"],
        issue_id=publication["issue_id"],
    )
    if publication != expected_publication:
        errors.append("publication:confirmed-material-mismatch")
        publication = expected_publication
    expected_receipt_fields = {
        "schema",
        "publication_sha256",
        "batch_sha256",
        "first_confirmation_sha256",
        "entries",
        "receipts_sha256",
    }
    if not isinstance(receipts, dict) or set(receipts) != expected_receipt_fields:
        errors.append("receipt-batch:invalid-schema")
        receipts = receipts if isinstance(receipts, dict) else {}
    if receipts.get("schema") != RECEIPT_BATCH_SCHEMA:
        errors.append("receipt-batch:schema-mismatch")
    if receipts.get("publication_sha256") != publication["publication_sha256"]:
        errors.append("receipt-batch:publication-mismatch")
    if receipts.get("batch_sha256") != publication["batch_sha256"]:
        errors.append("receipt-batch:batch-mismatch")
    if receipts.get("first_confirmation_sha256") != publication["first_confirmation_sha256"]:
        errors.append("receipt-batch:first-confirmation-mismatch")
    if receipts.get("receipts_sha256") != _canonical_sha256(
        {key: value for key, value in receipts.items() if key != "receipts_sha256"}
    ):
        errors.append("receipt-batch:content-changed")
    receipt_entries = receipts.get("entries")
    if not isinstance(receipt_entries, dict):
        errors.append("receipt-batch:invalid-entries")
        receipt_entries = {}
    if github is not None:
        try:
            _validate_publisher_identity(github)
            repository = github.fetch_repository(publication["repository"])
            issue = github.fetch_issue(publication["repository"], publication["issue_number"])
            live_permission = _validated_publisher_permission(github)
        except (ExplorerInputError, OSError) as error:
            errors.append(f"receipt-batch:live-source-unavailable:{error}")
            github = None
    extra = sorted(set(receipt_entries) - set(publication["objects"]), key=str)
    errors.extend(f"receipt:{key}:unexpected" for key in extra)

    entries: dict[str, dict[str, Any]] = {}
    seen_comments: set[tuple[int, int]] = set()
    for key, publication_item in publication["objects"].items():
        receipt = receipt_entries.get(key)
        if not isinstance(receipt, dict) or set(receipt) != {
            "publication_key", "first_decision_sha256", "decision", "packet_sha256",
            "comment_body_sha256", "source_receipt_sha256", "source",
        }:
            entries[key] = {
                "decision": deepcopy(publication_item["decision"]),
                "display_name": batch["objects"][key]["analysis"]["display_name"],
                "errors": ["invalid" if key in receipt_entries else "missing"],
            }
            continue
        source = receipt.get("source") if isinstance(receipt.get("source"), dict) else {}
        source_digest = receipt.get("source_receipt_sha256")
        item_errors: list[str] = []
        source_issue = source.get("issue") if isinstance(source.get("issue"), dict) else {}
        issue_id = source_issue.get("id")
        valid_issue_id = type(issue_id) is int and issue_id > 0
        if not valid_issue_id:
            item_errors.append("issue-id-invalid")
        elif source_issue.get("number") != publication["issue_number"] or (
            publication["issue_id"] is not None and source_issue.get("id") != publication["issue_id"]
        ):
            item_errors.append("issue-mismatch")
        if receipt.get("publication_key") != publication_item["publication_key"]:
            item_errors.append("publication-key-mismatch")
        if receipt.get("first_decision_sha256") != first_confirmation["bindings"][key]["decision_sha256"]:
            item_errors.append("decision-mismatch")
        if receipt.get("packet_sha256") != publication_item["decision"]["packet_sha256"]:
            item_errors.append("packet-mismatch")
        if not isinstance(source_digest, str) or source_digest != review_source_sha256(source):
            item_errors.append("source-receipt-mismatch")
        anchor: ReviewApprovalAnchor | None = None
        try:
            anchor = _candidate_anchor(source, source_digest)
            match_trusted_review_source(
                source,
                ReviewTrustPolicy((anchor,)),
                label=f"Batch review {key}",
            )
        except (ExplorerInputError, TypeError) as error:
            item_errors.append(f"source-untrusted:{error}")
        comment = source.get("comment") if isinstance(source.get("comment"), dict) else {}
        comment_id = comment.get("id")
        valid_comment_id = type(comment_id) is int and comment_id > 0
        if valid_comment_id:
            if github is not None:
                try:
                    live = build_review_source_receipt(
                        repository, issue, github.fetch_comment(publication["repository"], comment_id),
                        live_permission, fixed_t3_identity=False,
                    )
                    if live != source:
                        item_errors.append("live-receipt-mismatch")
                except (ExplorerInputError, OSError, ValueError, TypeError) as error:
                    item_errors.append(f"live-receipt-unavailable:{error}")
            expected_body = _published_body(
                key,
                publication_item["publication_key"],
                publication_item["decision"],
                comment_id=comment_id,
            )
            expected_decision = _decision_material(
                publication_item["decision"], publication_item["publication_key"], comment_id, source_digest
            )
            if receipt.get("decision") != expected_decision:
                item_errors.append("final-decision-mismatch")
            if comment.get("body") != expected_body:
                item_errors.append("comment-body-mismatch")
            if receipt.get("comment_body_sha256") != hashlib.sha256(
                expected_body.encode("utf-8")
            ).hexdigest():
                item_errors.append("comment-body-digest-mismatch")
        else:
            item_errors.append("comment-id-invalid")
        if valid_issue_id and valid_comment_id:
            identity = (issue_id, comment_id)
            if identity in seen_comments:
                item_errors.append("comment-identity-duplicate")
            seen_comments.add(identity)
        author = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        receipt_permission = source.get("permission") if isinstance(source.get("permission"), dict) else {}
        entries[key] = {
            "decision": deepcopy(publication_item["decision"]),
            "display_name": batch["objects"][key]["analysis"]["display_name"],
            "publication_key": publication_item["publication_key"],
            "decision_sha256": _canonical_sha256(receipt.get("decision")),
            "packet_sha256": receipt.get("packet_sha256"),
            "comment_body_sha256": receipt.get("comment_body_sha256"),
            "source_receipt_sha256": source_digest,
            "comment": {
                "decision_id": f"github-comment:{comment_id}",
                "id": comment_id,
                "html_url": comment.get("html_url"),
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "body": comment.get("body"),
                "author_login": author.get("login"),
                "author_association": comment.get("author_association"),
                "permission": receipt_permission.get("permission"),
                "role_name": receipt_permission.get("role_name"),
            },
            "anchor": _anchor_dict(anchor) if anchor is not None and not item_errors else None,
            "errors": item_errors,
        }

    errors.extend(
        f"receipt:{key}:{error}" for key, entry in entries.items() for error in entry["errors"]
    )
    candidate = {
        "schema": ANCHOR_CONFIRMATION_CANDIDATE_SCHEMA,
        "status": "READY" if not errors else "BLOCKED",
        "publication_sha256": publication["publication_sha256"],
        "receipts_sha256": receipts.get("receipts_sha256"),
        "batch_sha256": publication["batch_sha256"],
        "first_confirmation_sha256": publication["first_confirmation_sha256"],
        "object_keys": list(publication["objects"]),
        "entries": entries,
        "errors": errors,
    }
    candidate["candidate_sha256"] = _canonical_sha256(candidate)
    return candidate


def review_batch_materials(batch, first_confirmation, publication, receipts):
    """Provide T6's existing Decision/source records without importing them."""
    candidate = prepare_anchor_confirmation(batch, first_confirmation, publication, receipts)
    if candidate["status"] != "READY":
        raise ExplorerInputError("Review batch receipts are blocked")
    return {
        key: {name: deepcopy(entry[name]) for name in ("decision", "source")}
        for key, entry in receipts["entries"].items()
    }


def render_second_confirmation_checklist(candidate: dict[str, Any]) -> str:
    """Render the concise Owner-facing check before exact anchor installation."""

    lines = [
        "# 第二次确认：可信评论与精确 Anchor 清单",
        "",
        "> 评论发布和 Receipt 均不等于 Anchor 授权。请独立核对本批精确集合；本次确认不覆盖未来评论或其他对象。",
        "",
        f"- 状态：**{candidate.get('status')}**",
        f"- 首次确认：`{candidate.get('first_confirmation_sha256')}`",
        f"- Receipt 批次：`{candidate.get('receipts_sha256')}`",
        "",
    ]
    for number, key in enumerate(candidate.get("object_keys", []), start=1):
        entry = candidate.get("entries", {}).get(key, {})
        comment = entry.get("comment", {})
        decision = entry.get("decision", {})
        lines.extend(
            [
                f"## {number}. {entry.get('display_name', key)} — `{key}`",
                "",
                f"- 决定：**{decision.get('result')}**；理由：{decision.get('reason')}",
                f"- 评论：`{comment.get('decision_id')}`；{comment.get('html_url')}",
                f"- 作者：{comment.get('author_login')}（{comment.get('author_association')} / {comment.get('permission')}）",
                f"- 时间：创建 `{comment.get('created_at')}`；更新 `{comment.get('updated_at')}`",
                f"- Receipt：`{entry.get('source_receipt_sha256')}`",
                f"- 评论正文 SHA-256：`{entry.get('comment_body_sha256')}`",
                *[f"> {line}" for line in str(comment.get("body") or "").splitlines()],
                f"- Anchor：`{json.dumps(entry.get('anchor'), ensure_ascii=False, sort_keys=True)}`",
                "- 异常：" + ("；".join(entry.get("errors", [])) or "无"),
                "",
            ]
        )
    lines.extend(
        [
            "## 本批精确授权集合",
            "",
            (
                f"本次授权仅覆盖以下 {len(candidate.get('object_keys', []))} 个 Anchor："
                if candidate.get("status") == "READY"
                else "批次存在异常，无法授权安装。"
            ),
            "",
            *[f"- `{key}` → `{candidate.get('entries', {}).get(key, {}).get('comment', {}).get('decision_id')}`" for key in candidate.get("object_keys", [])],
            "",
            "## 异常汇总",
            "",
            *(candidate.get("errors") or ["异常：无"]),
            "",
            "请在核对无误后，一次确认授权宿主登记并安装本批精确 Anchor。该授权仅适用于此清单。",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_confirmation_candidate(candidate: dict[str, Any]) -> None:
    expected = {
        "schema",
        "status",
        "publication_sha256",
        "receipts_sha256",
        "batch_sha256",
        "first_confirmation_sha256",
        "object_keys",
        "entries",
        "errors",
        "candidate_sha256",
    }
    if (
        not isinstance(candidate, dict)
        or set(candidate) != expected
        or candidate.get("schema") != ANCHOR_CONFIRMATION_CANDIDATE_SCHEMA
        or candidate.get("candidate_sha256")
        != _canonical_sha256(
            {key: value for key, value in candidate.items() if key != "candidate_sha256"}
        )
    ):
        raise ExplorerInputError("Anchor confirmation candidate content changed")
    object_keys = candidate.get("object_keys")
    entries = candidate.get("entries")
    if (
        not isinstance(object_keys, list)
        or not object_keys
        or object_keys != sorted(set(object_keys))
        or not isinstance(entries, dict)
        or set(entries) != set(object_keys)
    ):
        raise ExplorerInputError("Anchor confirmation candidate object set is incomplete")


def confirm_anchor_batch(
    candidate: dict[str, Any],
    *,
    checklist: str,
) -> dict[str, Any]:
    """Freeze the exact READY checklist after the Owner's second confirmation."""

    _validate_confirmation_candidate(candidate)
    if candidate["status"] != "READY" or candidate["errors"]:
        raise ExplorerInputError("Blocked anchor candidate cannot receive second confirmation")
    expected_checklist = render_second_confirmation_checklist(candidate)
    if checklist != expected_checklist:
        raise ExplorerInputError("Second confirmation checklist content changed")
    confirmation = {
        "schema": SECOND_CONFIRMATION_SCHEMA,
        "candidate_sha256": candidate["candidate_sha256"],
        "batch_sha256": candidate["batch_sha256"],
        "first_confirmation_sha256": candidate["first_confirmation_sha256"],
        "receipts_sha256": candidate["receipts_sha256"],
        "object_keys": deepcopy(candidate["object_keys"]),
        "anchors": {
            key: deepcopy(candidate["entries"][key]["anchor"])
            for key in candidate["object_keys"]
        },
        "checklist_sha256": hashlib.sha256(checklist.encode("utf-8")).hexdigest(),
    }
    confirmation["second_confirmation_sha256"] = _canonical_sha256(confirmation)
    return confirmation


def _expected_second_confirmation(candidate: dict[str, Any]) -> dict[str, Any]:
    return confirm_anchor_batch(
        candidate,
        checklist=render_second_confirmation_checklist(candidate),
    )


def _validate_second_confirmation(
    candidate: dict[str, Any],
    second_confirmation: dict[str, Any],
) -> None:
    try:
        expected = _expected_second_confirmation(candidate)
    except ExplorerInputError as error:
        raise ExplorerInputError(f"Invalid second confirmation candidate: {error}") from error
    if second_confirmation != expected:
        raise ExplorerInputError("Exact second confirmation is required before anchor installation")


def prepare_anchor_installation(candidate, second_confirmation):
    """Prepare the exact host authorization digest and T6 handoff content."""
    _validate_second_confirmation(candidate, second_confirmation)
    objects = {}
    for key in candidate["object_keys"]:
        entry = candidate["entries"][key]
        objects[key] = {
            "object_key": deepcopy(entry["decision"]["object_key"]),
            "decision_id": entry["comment"]["decision_id"],
            "decision_sha256": entry["decision_sha256"],
            "source_receipt_sha256": entry["source_receipt_sha256"],
            "anchor": deepcopy(entry["anchor"]),
        }
    installation = {
        "schema": INSTALLED_ANCHOR_BATCH_SCHEMA,
        "status": "installed",
        "batch_id": second_confirmation["second_confirmation_sha256"],
        "batch_sha256": candidate["batch_sha256"],
        "confirmation_sha256": candidate["first_confirmation_sha256"],
        "objects": objects,
    }
    installation["installation_sha256"] = _canonical_sha256(installation)
    _validate_installed_anchor_batch(installation)
    return installation


def _validate_installed_anchor_batch(installed):
    digests = {"batch_id", "batch_sha256", "confirmation_sha256", "installation_sha256"}
    if (
        not isinstance(installed, dict)
        or set(installed) != digests | {"schema", "status", "objects"}
        or installed["schema"] != INSTALLED_ANCHOR_BATCH_SCHEMA
        or installed["status"] != "installed"
        or not all(_is_sha256(installed[name]) for name in digests)
        or installed["installation_sha256"] != _canonical_sha256(
            {key: value for key, value in installed.items() if key != "installation_sha256"}
        )
        or not isinstance(installed["objects"], dict)
        or not installed["objects"]
    ):
        raise ExplorerInputError("Installed anchor batch is incomplete or changed")
    identities = set()
    issues = set()
    for key, entry in installed["objects"].items():
        fields = {"object_key", "decision_id", "decision_sha256", "source_receipt_sha256", "anchor"}
        try:
            if not isinstance(entry, dict) or set(entry) != fields:
                raise ValueError
            anchor = ReviewApprovalAnchor(**entry["anchor"])
            object_key = entry["object_key"]
            kind = object_key["kind"]
            id_field = "node_id" if kind == "candidate-node" else "object_id"
            if (
                kind not in {"candidate-node", "candidate-collaboration", "candidate-decomposition"}
                or key != f"{kind}::{object_key[id_field]}"
                or entry["decision_id"] != f"github-comment:{anchor.comment_id}"
                or not _is_sha256(entry["decision_sha256"])
                or not _is_sha256(entry["source_receipt_sha256"])
                or entry["source_receipt_sha256"] != anchor.source_receipt_sha256
                or not all(type(value) is int and value > 0 for value in (
                    anchor.issue_id, anchor.issue_number, anchor.comment_id
                ))
            ):
                raise ValueError
            created = datetime.fromisoformat(anchor.comment_created_at)
            updated = datetime.fromisoformat(anchor.comment_updated_at)
            if created.tzinfo is None or updated.tzinfo is None or updated < created:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise ExplorerInputError(f"Installed anchor entry is invalid: {key}") from error
        if anchor.comment_id in identities:
            raise ExplorerInputError("Installed anchor batch reuses a comment identity")
        identities.add(anchor.comment_id)
        issues.add((anchor.issue_id, anchor.issue_number))
    if len(issues) != 1:
        raise ExplorerInputError("Installed anchor batch spans different Issues")


def _authorized_installation_path(installed):
    _validate_installed_anchor_batch(installed)
    if installed["installation_sha256"] not in review_trust.PRODUCTION_BATCH_INSTALLATION_DIGESTS:
        raise ExplorerInputError("Exact batch requires independent host installation authorization")
    return host_anchor_installation_path(installed["installation_sha256"])


def host_anchor_installation_path(installation_sha256: str) -> Path:
    """Return the formal path for an installation digest."""
    if not _is_sha256(installation_sha256):
        raise ExplorerInputError("Invalid host installation digest")
    return (
        review_trust.HOST_REVIEW_STATE_DIRECTORY / "installed-anchors"
        / f"{installation_sha256}.json"
    )


def _read_installation(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ExplorerInputError(f"Host installation is unavailable: {path}") from error


def load_installed_anchor_batch(path, batch, first_confirmation):
    """Read the host-installed batch with its independent T4 coverage check."""
    if Path(path).name.lower().endswith(".pending"):
        raise ExplorerInputError("Pending anchor installation is not an installed batch")
    installed = _read_installation(path)
    expected_path = _authorized_installation_path(installed)
    if Path(path).absolute() != expected_path or expected_path.is_symlink():
        raise ExplorerInputError("Batch is outside the formal host installation location")
    if (
        installed["batch_sha256"] != batch["batch_sha256"]
        or installed["confirmation_sha256"] != first_confirmation["confirmation_sha256"]
        or set(installed["objects"]) != set(decided_objects(first_confirmation, batch))
    ):
        raise ExplorerInputError("Installed anchor batch differs from the first confirmation")
    return installed


def installed_anchor_trust_policy(installed):
    """Load only anchors from the exact batch published in host storage."""
    path = _authorized_installation_path(installed)
    if path.is_symlink() or _read_installation(path) != installed:
        raise ExplorerInputError("Formal host installation differs from the authorized batch")
    return ReviewTrustPolicy(tuple(
        ReviewApprovalAnchor(**entry["anchor"]) for entry in installed["objects"].values()
    ))


def install_anchor_batch(candidate, second_confirmation, path=None):
    """Publish the independently authorized installation without overwriting."""
    installed = prepare_anchor_installation(candidate, second_confirmation)
    target = _authorized_installation_path(installed)
    if (path is not None and Path(path).absolute() != target) or target.is_symlink():
        raise ExplorerInputError("Batch must use the formal host installation location")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_review_artifact(target, installed)
    return installed


def prepare_comment_publication(
    batch: dict[str, Any],
    first_confirmation: dict[str, Any],
    *,
    repository: str,
    issue_number: int,
    issue_id: int | None = None,
) -> dict[str, Any]:
    """Bind decided T4 objects to deterministic, per-object comment drafts."""

    if repository != TRUSTED_REPOSITORY:
        raise ExplorerInputError("Comment publication is restricted to the trusted repository")
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number <= 0:
        raise ExplorerInputError("Comment publication needs a positive GitHub issue number")
    if issue_id is not None and (type(issue_id) is not int or issue_id <= 0):
        raise ExplorerInputError("Comment publication needs a positive GitHub issue ID")

    decisions = decided_objects(first_confirmation, batch)
    if not decisions:
        raise ExplorerInputError("Comment publication needs at least one decided object")
    objects: dict[str, dict[str, Any]] = {}
    for key in sorted(decisions):
        packet = batch["objects"][key]["packet"]
        decision = {
            "object_key": deepcopy(packet["object_key"]),
            **deepcopy(decisions[key]),
            "packet_sha256": packet["packet_sha256"],
            "reviewed_source_snapshot": deepcopy(packet["reviewed_source_snapshot"]),
            "inputs": deepcopy(packet["inputs"]),
            "claims": deepcopy(packet["claims"]),
        }
        publication_key = _canonical_sha256(
            {
                "batch_sha256": batch["batch_sha256"],
                "first_confirmation_sha256": first_confirmation["confirmation_sha256"],
                "repository": repository,
                "issue_number": issue_number,
                "issue_id": issue_id,
                "object_key": key,
                "decision": decision,
            }
        )
        _decision_material(decision, publication_key, 1)
        objects[key] = {
            "publication_key": publication_key,
            "decision": decision,
            "draft_body": _draft_body(key, publication_key, decision),
        }

    publication = {
        "schema": PUBLICATION_SCHEMA,
        "repository": repository,
        "issue_number": issue_number,
        "issue_id": issue_id,
        "batch_sha256": batch["batch_sha256"],
        "first_confirmation_sha256": first_confirmation["confirmation_sha256"],
        "objects": objects,
    }
    publication["publication_sha256"] = _canonical_sha256(publication)
    return publication


__all__ = [
    "PUBLICATION_SCHEMA",
    "RECEIPT_BATCH_SCHEMA",
    "ReviewCommentPublisher",
    "ANCHOR_CONFIRMATION_CANDIDATE_SCHEMA",
    "INSTALLED_ANCHOR_BATCH_SCHEMA",
    "SECOND_CONFIRMATION_SCHEMA",
    "confirm_anchor_batch",
    "host_anchor_installation_path",
    "install_anchor_batch",
    "installed_anchor_trust_policy",
    "load_installed_anchor_batch",
    "prepare_anchor_confirmation",
    "prepare_anchor_installation",
    "prepare_comment_publication",
    "publish_review_comments",
    "render_second_confirmation_checklist",
    "review_batch_materials",
]
