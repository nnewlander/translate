"""Supervisor/canary entry for allowlisted recovery.  Codex is never invoked."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.domain.calibration import CalibrationRequest, TranslationResult  # noqa: E402
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy  # noqa: E402
from booktrans.production_worker.deterministic_recovery import (  # noqa: E402
    CodexMustNotRun,
    execute_recovery_decision,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handler-kind", required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--serialized_repair_contract_json", default="")
    parser.add_argument("--handoff-path")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--run-path")
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--forbid-production-write", action="store_true")
    parser.add_argument("--isolated-request")
    parser.add_argument("--isolated-raw")
    parser.add_argument("--isolated-evidence")
    args = parser.parse_args(argv)

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    probe = CodexMustNotRun()
    contract = {}
    if args.serialized_repair_contract_json.strip():
        loaded = json.loads(args.serialized_repair_contract_json)
        if isinstance(loaded, dict):
            contract = loaded

    if args.forbid_production_write and not args.isolated_request:
        payload = {
            "status": "BLOCKED",
            "reason": "PRODUCTION_RECOVERY_WRITE_FORBIDDEN",
            "handler_kind": args.handler_kind,
            "policy_id": args.policy_id,
            "codex_exec_count": probe.exec_count,
            "translation_api_calls": 0,
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 3

    if not args.isolated_request or not args.isolated_raw or not args.isolated_evidence:
        payload = {
            "status": "BLOCKED",
            "reason": "ISOLATED_RECOVERY_FIXTURE_REQUIRED",
            "handler_kind": args.handler_kind,
            "policy_id": args.policy_id,
            "codex_exec_count": probe.exec_count,
            "translation_api_calls": 0,
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 3

    request = CalibrationRequest.model_validate_json(Path(args.isolated_request).read_text(encoding="utf-8"))
    raw = TranslationResult.model_validate_json(Path(args.isolated_raw).read_text(encoding="utf-8"))
    evidence = json.loads(Path(args.isolated_evidence).read_text(encoding="utf-8"))
    decision = decide_recovery_policy(evidence)
    if str(decision.get("handler_kind") or "") != args.handler_kind or str(decision.get("policy_id") or "") != args.policy_id:
        payload = {
            "status": "BLOCKED",
            "reason": "POLICY_DISPATCH_MISMATCH",
            "handler_kind": args.handler_kind,
            "policy_id": args.policy_id,
            "codex_exec_count": probe.exec_count,
            "translation_api_calls": 0,
            "decision": decision,
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 3
    if decision.get("repair_contract") is None and contract:
        decision = dict(decision)
        decision["repair_contract"] = contract

    payload = execute_recovery_decision(
        decision,
        request=request,
        raw=raw,
        view=None,
        known_unit_ids={request.unit_uid},
        qa_fn=_missing_qa,
        repair_provider=None,
        source_sha_valid=bool(evidence.get("source_sha_valid", True)),
        raw_sha_valid=bool(evidence.get("raw_sha_valid", True)),
        codex=probe,
    )
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if payload.get("status") == "PASS" else 3


def _missing_qa(*args, **kwargs):
    raise RuntimeError("ISOLATED_RECOVERY_QA_MUST_BE_INJECTED_IN_TESTS")


if __name__ == "__main__":
    raise SystemExit(main())
