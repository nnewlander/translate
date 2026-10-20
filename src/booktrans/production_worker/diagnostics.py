"""Canary-only stdout milestones, session telemetry, and hang diagnostics.

This module is isolated telemetry.  It does not write production evidence,
take the production writer lock, or change Provider/Harness contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping, TextIO

from booktrans.production_worker.artifacts import assert_record_safe


DEFAULT_WATCHDOG_SECONDS = 120.0
TELEMETRY_FILENAMES = frozenset({"canary_session.json"})
FORBIDDEN_EMIT_FRAGMENTS = ("api_key", "authorization", "bearer ", "dashscope", "secret")
RESUME_ALLOWED_FILENAMES = frozenset(
    {
        "canary_session.json",
        "canary_gate.json",
        "canary_report.json",
        "canary_report.raw_recovery.json",
        "canary_continuation_report.json",
        "canary_progress.jsonl",
        "checkpoint_evidence.json",
        "secret_guard_diagnosis.json",
        "recovery_policy_decision.json",
        "translation_request.jsonl",
        "translation_raw_response.jsonl",
        "translation_raw_qa_report.json",
        "translation_provider_provenance.jsonl",
        "translation_normalized_response.jsonl",
        "translation_normalization_report.json",
        "translation_normalized_qa_report.json",
        "translation_repaired_response.jsonl",
        "translation_final_qa_report.json",
        "translation_continuation_working.jsonl",
        "semantic_review_report.json",
        "units",
        "reviews",
        "repair_contracts",
        "repair_candidates",
        "semantic_repair_contracts",
        "semantic_repair_candidates",
        "re_reviews",
        "continuation",
        "review_contexts",
        "reviews_v2",
        "re_reviews_v2",
        "repair_contracts_v2",
        "repair_candidates_v2",
        "review_groups",
        "reviews_v3",
        "re_reviews_v3",
        "repair_contracts_v3",
        "repair_candidates_v3",
        "re_reviews_v3_supersession",
        "re_reviews_v4",
        "group_repair_eligibility",
        "group_reviews",
        "group_repair_contracts",
        "group_repair_candidates",
        "group_re_reviews",
        "human_adjudications",
        "human_approved_candidates",
        "canary_group_repair_report.json",
        "canary_human_adjudication_report.json",
        "translation_d050_aware_qa_report.json",
        "canary_semantic_review_report.json",
        "canary_semantic_review_group_report.json",
        "canary_corrected_second_review_report.json",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_milestone(stage: str, *, index: int | None = None, total: int | None = None, **fields: Any) -> str:
    prefix = ""
    if index is not None and total is not None:
        prefix = f"[{index:02d}/{total:02d}] "
    extras = " ".join(f"{key}={_safe_field(value)}" for key, value in fields.items() if value is not None and value != "")
    line = f"{prefix}{stage}"
    return f"{line} {extras}".rstrip()


def _safe_field(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    lowered = text.casefold()
    for fragment in FORBIDDEN_EMIT_FRAGMENTS:
        if fragment in lowered:
            return "<redacted>"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def emit_milestone(
    stage: str,
    *,
    stream: TextIO | None = None,
    index: int | None = None,
    total: int | None = None,
    **fields: Any,
) -> str:
    line = format_milestone(stage, index=index, total=total, **fields)
    lowered = line.casefold()
    for fragment in FORBIDDEN_EMIT_FRAGMENTS:
        if fragment in lowered:
            raise ValueError("CANARY_MILESTONE_SECRET_BLOCKED")
    handle = sys.stdout if stream is None else stream
    print(line, file=handle, flush=True)
    return line


def write_session(path: Path, payload: Mapping[str, Any]) -> None:
    """Derived canary telemetry; it is overwritten as stages advance."""
    record = dict(payload)
    record.setdefault("artifact_type", "QWEN_CANARY_SESSION")
    record.setdefault("schema_version", 1)
    record["updated_at"] = utc_now()
    assert_record_safe(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run_bounded_subprocess(
    argv: list[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Fail closed on timeout.  Canary must not wait on a child forever."""
    if timeout_seconds <= 0:
        raise ValueError("SUBPROCESS_TIMEOUT_INVALID")
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            timeout=timeout_seconds,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"SUBPROCESS_TIMEOUT:{timeout_seconds}:{argv[:2]}") from exc


class CanaryDiagnostics:
    """Stdout milestones plus a repeating traceback watchdog."""

    def __init__(
        self,
        output_dir: Path,
        *,
        stream: TextIO | None = None,
        watchdog_seconds: float = DEFAULT_WATCHDOG_SECONDS,
        watchdog_file: TextIO | None = None,
        stage_hooks: Mapping[str, Callable[[], None]] | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.stream = sys.stdout if stream is None else stream
        self.watchdog_seconds = watchdog_seconds
        self.watchdog_file = sys.stderr if watchdog_file is None else watchdog_file
        self.stage_hooks = dict(stage_hooks or {})
        self.session_path = output_dir / "canary_session.json"
        self.session: dict[str, Any] = {
            "artifact_type": "QWEN_CANARY_SESSION",
            "schema_version": 1,
            "status": "RUNNING",
            "current_stage": "CANARY_START",
            "started_at": utc_now(),
        }
        self._watchdog_armed = False

    def arm_watchdog(self) -> None:
        if self.watchdog_seconds <= 0:
            return
        faulthandler.enable(file=self.watchdog_file, all_threads=True)
        faulthandler.dump_traceback_later(self.watchdog_seconds, repeat=True, file=self.watchdog_file)
        self._watchdog_armed = True

    def cancel_watchdog(self) -> None:
        if self._watchdog_armed:
            faulthandler.cancel_dump_traceback_later()
            self._watchdog_armed = False

    def emit(self, stage: str, *, index: int | None = None, total: int | None = None, **fields: Any) -> str:
        line = emit_milestone(stage, stream=self.stream, index=index, total=total, **fields)
        self.session["current_stage"] = stage
        if index is not None:
            self.session["current_index"] = index
        if fields:
            self.session["current_fields"] = {key: _safe_field(value) for key, value in fields.items()}
        self.flush_session()
        hook = self.stage_hooks.get(stage)
        if hook is not None:
            hook()
        return line

    def fail(self, stage: str, reason: str) -> None:
        self.session["status"] = "FAILED"
        self.session["failure_reason"] = _safe_field(reason)
        self.emit(stage, reason=_safe_field(reason))

    def complete(self, *, status: str = "COMPLETE") -> None:
        self.session["status"] = status
        self.flush_session()

    def flush_session(self) -> None:
        write_session(self.session_path, self.session)
