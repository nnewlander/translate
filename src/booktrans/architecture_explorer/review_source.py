from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from typing import Any, Callable

from .generator import ExplorerInputError


REVIEW_SOURCE_SCHEMA = "architecture-explorer.github-review-source.v1"
TRUSTED_REPOSITORY = "nnewlander/translate"
TRUSTED_REPOSITORY_ID = 1425303246
TRUSTED_ISSUE_NUMBER = 3
TRUSTED_ISSUE_ID = 6058497210
TRUSTED_COMMENT_ID = 6516894339
TRUSTED_COMMENT_CREATED_AT = "2026-10-30T06:56:39Z"
TRUSTED_COMMENT_UPDATED_AT = "2026-10-30T08:45:17Z"
TRUSTED_OWNER_LOGIN = "nnewlander"
TRUSTED_OWNER_ID = 256049772

# This digest is pinned after a live GitHub API import.  It is deliberately
# separate from the editable review mapping and covers the normalized source
# receipt, including the original comment body and GitHub identity metadata.
TRUSTED_REVIEW_SOURCE_SHA256 = "10593b61f8db5c6e09e041e59a9929aeceee99d1f2bffd6bbb81de2509d7478e"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def review_source_sha256(source: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(source)).hexdigest()


def _gh_api(endpoint: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["gh", "api", endpoint],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ExplorerInputError(f"GitHub API unavailable for {endpoint}: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ExplorerInputError(f"GitHub API returned invalid JSON for {endpoint}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"GitHub API returned a non-object for {endpoint}")
    return value


def _gh_preflight() -> None:
    for command in (["gh", "--version"], ["gh", "auth", "status"]):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ExplorerInputError(f"GitHub CLI preflight failed: {detail}")
    _gh_api("rate_limit")


def _select(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value.get(key) for key in keys}


def build_review_source_receipt(
    repository: dict[str, Any],
    issue: dict[str, Any],
    comment: dict[str, Any],
    permission: dict[str, Any],
) -> dict[str, Any]:
    owner = repository.get("owner") if isinstance(repository.get("owner"), dict) else {}
    comment_user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    permission_user = permission.get("user") if isinstance(permission.get("user"), dict) else {}
    receipt = {
        "schema": REVIEW_SOURCE_SCHEMA,
        "repository": {
            **_select(repository, ("id", "node_id", "full_name", "html_url", "private")),
            "owner": _select(owner, ("login", "id", "node_id", "type")),
        },
        "issue": _select(issue, ("id", "node_id", "number", "html_url", "repository_url", "state")),
        "comment": {
            **_select(
                comment,
                (
                    "id", "node_id", "html_url", "issue_url", "author_association",
                    "created_at", "updated_at", "body",
                ),
            ),
            "user": _select(comment_user, ("login", "id", "node_id", "type")),
        },
        "permission": {
            **_select(permission, ("permission", "role_name")),
            "user": _select(permission_user, ("login", "id", "node_id", "type")),
        },
    }
    _validate_receipt_identity(receipt)
    return receipt


def _validate_owner_admin_receipt(source: dict[str, Any]) -> None:
    if source.get("schema") != REVIEW_SOURCE_SCHEMA:
        raise ExplorerInputError("Unsupported GitHub review source schema")
    repository = source.get("repository") if isinstance(source.get("repository"), dict) else {}
    owner = repository.get("owner") if isinstance(repository.get("owner"), dict) else {}
    issue = source.get("issue") if isinstance(source.get("issue"), dict) else {}
    comment = source.get("comment") if isinstance(source.get("comment"), dict) else {}
    author = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    permission = source.get("permission") if isinstance(source.get("permission"), dict) else {}
    permission_user = permission.get("user") if isinstance(permission.get("user"), dict) else {}
    issue_number = issue.get("number")
    comment_id = comment.get("id")
    expected_repository_url = f"https://api.github.com/repos/{TRUSTED_REPOSITORY}"
    expected_issue_url = f"https://github.com/{TRUSTED_REPOSITORY}/issues/{issue_number}"
    expected_comment_url = f"{expected_issue_url}#issuecomment-{comment_id}"
    checks = {
        "repository.id": repository.get("id") == TRUSTED_REPOSITORY_ID,
        "repository.full_name": repository.get("full_name") == TRUSTED_REPOSITORY,
        "repository.html_url": repository.get("html_url") == f"https://github.com/{TRUSTED_REPOSITORY}",
        "repository.owner": (
            owner.get("login") == TRUSTED_OWNER_LOGIN
            and owner.get("id") == TRUSTED_OWNER_ID
            and owner.get("type") == "User"
        ),
        "issue.id": isinstance(issue.get("id"), int),
        "issue.number": isinstance(issue_number, int) and issue_number > 0,
        "issue.html_url": issue.get("html_url") == expected_issue_url,
        "issue.repository_url": issue.get("repository_url") == expected_repository_url,
        "comment.id": isinstance(comment_id, int) and comment_id > 0,
        "comment.html_url": comment.get("html_url") == expected_comment_url,
        "comment.issue_url": comment.get("issue_url") == f"{expected_repository_url}/issues/{issue_number}",
        "comment.author": (
            author.get("login") == TRUSTED_OWNER_LOGIN
            and author.get("id") == TRUSTED_OWNER_ID
            and author.get("type") == "User"
        ),
        "comment.author_association": comment.get("author_association") == "OWNER",
        "comment.created_at": isinstance(comment.get("created_at"), str),
        "comment.updated_at": isinstance(comment.get("updated_at"), str),
        "comment.body": isinstance(comment.get("body"), str) and bool(comment.get("body")),
        "permission.subject": (
            permission_user.get("login") == TRUSTED_OWNER_LOGIN
            and permission_user.get("id") == TRUSTED_OWNER_ID
            and permission_user.get("type") == "User"
        ),
        "permission.level": permission.get("permission") == "admin" and permission.get("role_name") == "admin",
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise ExplorerInputError(f"GitHub review source identity mismatch: {', '.join(failures)}")
    try:
        created = datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(comment["updated_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ExplorerInputError("GitHub review source timestamps are invalid") from error
    if updated < created:
        raise ExplorerInputError("GitHub review source update time precedes creation time")


def _validate_receipt_identity(source: dict[str, Any]) -> None:
    _validate_owner_admin_receipt(source)
    issue = source["issue"]
    comment = source["comment"]
    checks = {
        "issue.id": issue.get("id") == TRUSTED_ISSUE_ID,
        "issue.number": issue.get("number") == TRUSTED_ISSUE_NUMBER,
        "comment.id": comment.get("id") == TRUSTED_COMMENT_ID,
        "comment.created_at": comment.get("created_at") == TRUSTED_COMMENT_CREATED_AT,
        "comment.updated_at": comment.get("updated_at") == TRUSTED_COMMENT_UPDATED_AT,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise ExplorerInputError(f"GitHub review source identity mismatch: {', '.join(failures)}")


def fetch_github_review_source(
    repository_name: str = TRUSTED_REPOSITORY,
    issue_number: int = TRUSTED_ISSUE_NUMBER,
    comment_id: int = TRUSTED_COMMENT_ID,
    *,
    api: Callable[[str], dict[str, Any]] = _gh_api,
) -> dict[str, Any]:
    """Fetch the fixed review directly from GitHub; callers cannot supply local API JSON."""

    if (
        repository_name != TRUSTED_REPOSITORY
        or issue_number != TRUSTED_ISSUE_NUMBER
        or comment_id != TRUSTED_COMMENT_ID
    ):
        raise ExplorerInputError("T3 import is restricted to the fixed repository, issue, and comment")
    if api is _gh_api:
        _gh_preflight()
    repository = api(f"repos/{TRUSTED_REPOSITORY}")
    issue = api(f"repos/{TRUSTED_REPOSITORY}/issues/{TRUSTED_ISSUE_NUMBER}")
    comment = api(f"repos/{TRUSTED_REPOSITORY}/issues/comments/{TRUSTED_COMMENT_ID}")
    comment_user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    login = comment_user.get("login")
    if not isinstance(login, str) or not login:
        raise ExplorerInputError("GitHub review comment has no actual author login")
    permission = api(f"repos/{TRUSTED_REPOSITORY}/collaborators/{login}/permission")
    return build_review_source_receipt(repository, issue, comment, permission)


def verify_trusted_review_source(source: dict[str, Any] | None) -> list[str]:
    if source is None:
        return ["trusted_review_source:missing"]
    try:
        _validate_receipt_identity(source)
    except ExplorerInputError as error:
        return [f"trusted_review_source:invalid:{error}"]
    actual = review_source_sha256(source)
    if actual != TRUSTED_REVIEW_SOURCE_SHA256:
        return ["trusted_review_source:digest-mismatch"]
    return []


def verify_owner_admin_review_source(
    source: dict[str, Any] | None,
    *,
    expected_sha256: str,
) -> list[str]:
    """Verify an exact versioned receipt while reusing the T3 Owner/admin gate."""

    if source is None:
        return ["trusted_review_source:missing"]
    try:
        _validate_owner_admin_receipt(source)
    except ExplorerInputError as error:
        return [f"trusted_review_source:invalid:{error}"]
    if review_source_sha256(source) != expected_sha256:
        return ["trusted_review_source:digest-mismatch"]
    return []


__all__ = [
    "REVIEW_SOURCE_SCHEMA",
    "TRUSTED_COMMENT_ID",
    "TRUSTED_ISSUE_NUMBER",
    "TRUSTED_REPOSITORY",
    "TRUSTED_REVIEW_SOURCE_SHA256",
    "build_review_source_receipt",
    "fetch_github_review_source",
    "review_source_sha256",
    "verify_owner_admin_review_source",
    "verify_trusted_review_source",
]
