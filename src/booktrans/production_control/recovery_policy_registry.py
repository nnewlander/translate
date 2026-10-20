"""Pure closed-world recovery-policy registry decision adapter."""

from __future__ import annotations

from typing import Any, Mapping


def _deny(reason: str) -> dict[str, Any]:
    return {"family": "UNKNOWN", "policy_id": None, "eligible": False, "handler_kind": None, "attempt_budget": 0, "repair_contract": None, "postconditions": [], "escalation_code": "BLOCK", "denial_reason": reason}


def _runtime_limits(evidence: Mapping[str, Any], default_backoff: list[int]) -> tuple[int, list[int]] | None:
    budget = evidence.get("attempt_budget_override", 3)
    backoff = evidence.get("retry_backoff_seconds", default_backoff)
    if not isinstance(budget, int) or budget < 1 or not isinstance(backoff, list) or not backoff or not all(isinstance(value, int) and value >= 0 for value in backoff):
        return None
    return budget, backoff


def decide_recovery_policy(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a handler contract; never inspect disk, invoke a worker, or repair."""
    if evidence.get("validation_denial"):
        return _deny(str(evidence["validation_denial"]))
    if not evidence.get("source_sha_valid", False):
        return _deny("SOURCE_SHA_MISMATCH")
    if evidence.get("raw_required", False) and not evidence.get("raw_sha_valid", False):
        return _deny("IMMUTABLE_RAW_SHA_MISMATCH")
    classification = str(evidence.get("failure_classification", ""))
    codes = set(evidence.get("qa_codes") or [])
    sparse = bool(evidence.get("sparse_local", False))
    exact = evidence.get("repair_contract")
    if evidence.get("triage_denial"):
        return _deny(f"FINAL_QA_TRIAGE:{evidence['triage_denial']}")
    if classification == "PROVIDER_TRANSIENT":
        if evidence.get("raw_required", False) and evidence.get("raw_sha_valid", False):
            return _deny("PROVIDER_TRANSIENT_AFTER_RAW")
        if not evidence.get("safe_unchanged_cursor", False):
            return _deny("TRANSIENT_RETRY_PRECONDITIONS_FAILED")
        limits = _runtime_limits(evidence, [5, 15, 30])
        if limits is None:
            return _deny("RUNTIME_RETRY_LIMITS_INVALID")
        budget, backoff = limits
        return {
            "family": "RUNTIME_TRANSIENT",
            "policy_id": "PROVIDER_TRANSIENT",
            "eligible": True,
            "handler_kind": "SUPERVISOR_RETRY",
            "attempt_budget": budget,
            "repair_contract": {
                "backoff_seconds": backoff,
                "retry_reason": "PROVIDER_TRANSIENT",
                "event_label": "RETRYABLE_RUNTIME",
                "exhaustion_code": "PROVIDER_TRANSIENT_RETRY_EXHAUSTED",
            },
            "postconditions": ["safe_unchanged_cursor", "no_raw_written"],
            "escalation_code": "RUNTIME_GUARD",
            "denial_reason": None,
        }
    if classification in {"PROVIDER_AUTH_FAILURE", "PROVIDER_CONFIGURATION_MISSING"}:
        return _deny(classification)
    if classification == "PROVIDER_CONTRACT_FAILURE":
        return _deny("PROVIDER_CONTRACT_FAILURE")
    if classification == "CODEX_MODEL_REFRESH_TIMEOUT":
        if not evidence.get("safe_unchanged_cursor", False):
            return _deny("TRANSIENT_RETRY_PRECONDITIONS_FAILED")
        limits = _runtime_limits(evidence, [5, 15, 30])
        if limits is None: return _deny("RUNTIME_RETRY_LIMITS_INVALID")
        budget, backoff = limits
        return {"family": "RUNTIME_TRANSIENT", "policy_id": "MODEL_REFRESH_TIMEOUT", "eligible": True, "handler_kind": "SUPERVISOR_RETRY", "attempt_budget": budget, "repair_contract": {"backoff_seconds": backoff, "retry_reason": "CODEX_MODEL_REFRESH_TIMEOUT", "event_label": "RETRYABLE_RUNTIME", "exhaustion_code": "CODEX_MODEL_REFRESH_TIMEOUT_RETRY_EXHAUSTED"}, "postconditions": ["safe_unchanged_cursor"], "escalation_code": "RUNTIME_GUARD", "denial_reason": None}
    if classification == "NO_PROGRESS_CLEAN_EXIT":
        if not evidence.get("safe_unchanged_cursor", False):
            return _deny("NO_PROGRESS_RETRY_PRECONDITIONS_FAILED")
        limits = _runtime_limits(evidence, [3, 10])
        if limits is None: return _deny("RUNTIME_RETRY_LIMITS_INVALID")
        budget, backoff = limits
        return {"family": "RUNTIME_TRANSIENT", "policy_id": "NO_PROGRESS_CLEAN_EXIT", "eligible": True, "handler_kind": "SUPERVISOR_RETRY", "attempt_budget": budget, "repair_contract": {"backoff_seconds": backoff, "retry_reason": "NO_PROGRESS_CLEAN_EXIT", "event_label": "NO_PROGRESS", "exhaustion_code": "NO_PROGRESS_RETRY_EXHAUSTED"}, "postconditions": ["safe_unchanged_cursor"], "escalation_code": "RUNTIME_GUARD", "denial_reason": None}
    if classification == "EVIDENCE_PROVIDER_UNAVAILABLE":
        if not evidence.get("evidence_requirement_valid", False):
            return _deny("EVIDENCE_REQUIREMENT_INVALID")
        if not isinstance(exact, Mapping):
            return _deny("EVIDENCE_REQUIREMENT_CONTRACT_MISSING")
        required_keys = {"source_pdf_path", "pdf_sha256", "page_numbers", "logical_work_order_ids", "evidence_requirement"}
        if not required_keys.issubset(exact) or not isinstance(exact.get("page_numbers"), list) or not exact["page_numbers"]:
            return _deny("EVIDENCE_REQUIREMENT_CONTRACT_INVALID")
        # Infrastructure re-probe is deliberately shorter than generic CLI
        # retries: it is deterministic and must not burn unattended time.
        provider_evidence = dict(evidence)
        provider_evidence.setdefault("attempt_budget_override", 2)
        limits = _runtime_limits(provider_evidence, [2, 8])
        if limits is None:
            return _deny("EVIDENCE_PROVIDER_RETRY_LIMITS_INVALID")
        budget, backoff = limits
        contract = dict(exact)
        contract.update({"backoff_seconds": backoff, "retry_reason": "EVIDENCE_PROVIDER_UNAVAILABLE", "event_label": "EVIDENCE_PROVIDER_RETRY", "exhaustion_code": "EVIDENCE_INFRASTRUCTURE_UNAVAILABLE"})
        return {"family": "EVIDENCE_INFRASTRUCTURE", "policy_id": "PDF_EVIDENCE_PROVIDER_RECOVERY", "eligible": True, "handler_kind": "SUPERVISOR_RETRY", "attempt_budget": budget, "repair_contract": contract, "postconditions": ["source_pdf_sha_unchanged", "exact_evidence_requirement_valid"], "escalation_code": "BLOCK", "denial_reason": None}
    if classification.startswith("D050_PREFLIGHT_REQUIRED"):
        if not evidence.get("exact_d050_binding", False):
            return _deny("D050_EXACT_EVIDENCE_REQUIRED")
        return {"family": "EVIDENCE_PREFLIGHT_REQUIRED", "policy_id": "D050_EXACT_EVIDENCE", "eligible": True, "handler_kind": "DETERMINISTIC_EVIDENCE", "attempt_budget": 2, "repair_contract": exact, "postconditions": ["exact_logical_work_order_and_unit_uid_binding", "source_sha_unchanged"], "escalation_code": "AUTO_RECOVERY_EXHAUSTED", "denial_reason": None}
    if classification in {"FINAL_DETERMINISTIC_QA_FAILED", "DOI_PRESERVATION"} or classification.startswith("DETERMINISTIC_QA_FAILURE:") or classification.startswith("LOCAL_DETERMINISTIC_QA_FAILURE"):
        if not sparse:
            return _deny("QA_FAILURE_NOT_SPARSE")
        if codes == {"NUMBER_PRESERVATION"} and evidence.get("deterministic_index_locator_recovery") and exact:
            policy = "INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION"
            handler = "CURRENT_CHECKPOINT_RESUME"
        elif codes == {"NUMBER_PRESERVATION"} and exact:
            policy = "NUMERIC_REPRESENTATION"
            handler = "QWEN_NARROW_REPAIR"
        elif codes in ({"NUMERIC_ANCHOR_DUPLICATION"}, {"NUMBER_PRESERVATION", "NUMERIC_ANCHOR_DUPLICATION"}) and exact:
            policy = "NUMERIC_ANCHOR_OWNERSHIP_CORRECTION"
            # This is a bounded transition to the existing current-head
            # deterministic correction in run_production_checkpoint.
            handler = "CURRENT_CHECKPOINT_RESUME"
        elif codes == {"URL_PRESERVATION"} and exact:
            policy = "EXACT_SOURCE_URL_RESTORATION"
            handler = "CURRENT_CHECKPOINT_RESUME"
        elif codes == {"DOI_PRESERVATION"} and exact:
            policy = "DOI_PRESERVATION"
            handler = "QWEN_NARROW_REPAIR"
        else:
            return _deny("QA_POLICY_EVIDENCE_NOT_ALLOWLISTED")
        return {"family": "LOCAL_CONTENT_QA_FAILURE", "policy_id": policy, "eligible": True, "handler_kind": handler, "attempt_budget": 2, "repair_contract": exact, "postconditions": ["immutable_raw_sha_unchanged", "final_deterministic_qa_pass", "sealed_checkpoint"], "escalation_code": "AUTO_RECOVERY_EXHAUSTED", "denial_reason": None}
    return _deny("CLASSIFICATION_NOT_ALLOWLISTED")
