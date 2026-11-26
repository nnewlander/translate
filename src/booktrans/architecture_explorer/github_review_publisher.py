"""Narrow ``gh api`` adapter for trusted Architecture Explorer comments."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import review_trust
from .generator import ExplorerInputError
from .review_artifacts import local_publication_lock
from .review_source import TRUSTED_REPOSITORY


class GhCliReviewPublisher:
    """Use the already-authenticated GitHub CLI without accepting credentials."""

    def __init__(self, state_directory: Path | None = None):
        self.state_directory = (
            state_directory if state_directory is not None
            else review_trust.HOST_REVIEW_STATE_DIRECTORY / "publication"
        )

    def publication_lock(self, issue_number: int):
        self.state_directory.mkdir(parents=True, exist_ok=True)
        return local_publication_lock(self.state_directory / f"issue-{issue_number}.lock")

    def record_post_intent(self, publication_key: str) -> None:
        intent = self.state_directory / f"{publication_key}.post-intent"
        try:
            with intent.open("xb") as output:
                output.write(b"POST may have been attempted\n")
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as error:
            raise ExplorerInputError(
                "Previous comment creation is uncertain and no matching comment is visible"
            ) from error

    def _run_json(self, arguments: list[str]) -> Any:
        completed = subprocess.run(
            ["gh", "api", "--hostname", "github.com", *arguments[1:]],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ExplorerInputError(f"GitHub CLI request failed: {detail}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ExplorerInputError("GitHub CLI returned invalid JSON") from error

    def authenticated_user(self) -> dict[str, Any]:
        for command in (["--version"], ["auth", "status", "--hostname", "github.com"]):
            completed = subprocess.run(
                ["gh", *command],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if completed.returncode:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise ExplorerInputError(f"GitHub CLI preflight failed: {detail}")
        self._run_json(["api", "rate_limit"])
        value = self._run_json(["api", "user"])
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub authenticated user response is invalid")
        return value

    def collaborator_permission(self, login: str) -> dict[str, Any]:
        value = self._run_json(
            ["api", f"repos/{TRUSTED_REPOSITORY}/collaborators/{login}/permission"]
        )
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub collaborator permission response is invalid")
        return value

    def list_issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, Any]]:
        pages = self._run_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/issues/{issue_number}/comments?per_page=100",
            ]
        )
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ExplorerInputError("GitHub issue comments response is invalid")
        if any(not isinstance(comment, dict) for page in pages for comment in page):
            raise ExplorerInputError("GitHub issue comments contain an invalid record")
        return [comment for page in pages for comment in page]

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        value = self._run_json(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repository}/issues/{issue_number}/comments",
                "--raw-field",
                f"body={body}",
            ]
        )
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub create-comment response is invalid")
        return value

    def update_issue_comment(
        self, repository: str, comment_id: int, body: str
    ) -> dict[str, Any]:
        value = self._run_json(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/issues/comments/{comment_id}",
                "--raw-field",
                f"body={body}",
            ]
        )
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub update-comment response is invalid")
        return value

    def fetch_repository(self, repository: str) -> dict[str, Any]:
        value = self._run_json(["api", f"repos/{repository}"])
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub repository response is invalid")
        return value

    def fetch_issue(self, repository: str, issue_number: int) -> dict[str, Any]:
        value = self._run_json(["api", f"repos/{repository}/issues/{issue_number}"])
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub issue response is invalid")
        return value

    def fetch_comment(self, repository: str, comment_id: int) -> dict[str, Any]:
        value = self._run_json(
            ["api", f"repos/{repository}/issues/comments/{comment_id}"]
        )
        if not isinstance(value, dict):
            raise ExplorerInputError("GitHub comment response is invalid")
        return value


__all__ = ["GhCliReviewPublisher"]
