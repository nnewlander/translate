"""Build canonical, read-only recovery evidence for the policy registry.

This module deliberately does *not* select a policy or write an artifact.  It
turns a production-shaped recovery handoff plus already-collected immutable
facts into the one serializable input accepted by the registry.
"""

from __future__ import annotations

from typing import Any, Mapping


def build_recovery_evidence(
    handoff: Mapping[str, Any], cursor: Mapping[str, Any], facts: Mapping[str, Any]
) -> dict[str, Any]:
    """Canonicalize immutable recovery facts without side effects.

    ``facts`` is collected separately so this function remains deterministic,
    fixture-friendly, and independent of production disk layout.
    """
    classification = str(handoff.get("failure_classification") or "")
    failing = [str(value) for value in (handoff.get("failing_work_order_ids") or [])]
    evidence: dict[str, Any] = {
        "failure_family_candidate": "UNKNOWN",
        "failure_classification": classification,
        "source_sha_valid": bool(facts.get("source_sha_valid")),
        "raw_required": bool(handoff.get("raw_exists")),
        "raw_sha_valid": bool(facts.get("raw_sha_valid", not handoff.get("raw_exists"))),
        "sparse_local": False,
        "qa_codes": [],
        "failing_work_order_ids": failing,
        "failure_count": len(failing),
        "failure_density": None,
        "artifact_stage": str(handoff.get("stage_reached") or ""),
        "repair_contract": None,
        "exact_d050_binding": False,
        "validation_denial": None,
        "triage_denial": None,
        "canonical_qa_failure": facts.get("canonical_qa_failure"),
        "deterministic_index_locator_recovery": bool(facts.get("deterministic_index_locator_recovery", False)),
    }
    if not evidence["source_sha_valid"]:
        evidence["validation_denial"] = "SOURCE_SHA_MISMATCH"
        return evidence
    # A provider failure occurs before a checkpoint/request/RAW exists.  It is
    # recoverable only from a bounded, exact evidence requirement; it must not
    # borrow the partial-checkpoint recovery path.
    if classification == "EVIDENCE_PROVIDER_UNAVAILABLE":
        evidence["failure_family_candidate"] = "EVIDENCE_INFRASTRUCTURE"
        requirement = facts.get("evidence_requirement")
        if isinstance(requirement, Mapping) and facts.get("evidence_requirement_valid", False):
            evidence["evidence_requirement_valid"] = True
            evidence["repair_contract"] = dict(requirement)
        else:
            evidence["validation_denial"] = "EVIDENCE_REQUIREMENT_INVALID"
        return evidence
    if not facts.get("identity_valid", False):
        evidence["validation_denial"] = "RECOVERY_IDENTITY_CHANGED"
        return evidence
    if not facts.get("partial_path_exists", False):
        evidence["validation_denial"] = "PARTIAL_PATH_MISSING"
        return evidence
    if not facts.get("scope_active_and_unique", False):
        evidence["validation_denial"] = "FAILURE_SCOPE_NOT_SPARSE_OR_ACTIVE"
        return evidence
    if evidence["raw_required"] and not evidence["raw_sha_valid"]:
        return evidence

    triage = facts.get("final_qa_triage")
    if isinstance(triage, Mapping):
        codes = triage.get("codes") or []
        evidence["qa_codes"] = sorted({str(code) for code in codes})
        evidence["failure_count"] = triage.get("failure_count", len(failing))
        evidence["failure_density"] = triage.get("failure_density")
        if triage.get("eligible") is True:
            evidence["failure_family_candidate"] = "LOCAL_CONTENT_QA_FAILURE"
            # This is a canonical description of validated QA evidence, not a
            # repair-policy decision.  The registry still chooses the policy.
            evidence["failure_classification"] = str(
                triage.get("canonical_class") or "LOCAL_DETERMINISTIC_QA_FAILURE"
            )
            evidence["sparse_local"] = True
            evidence["repair_contract"] = dict(triage)
        else:
            evidence["failure_family_candidate"] = "LOCAL_CONTENT_QA_FAILURE"
            evidence["triage_denial"] = str(triage.get("reason") or "FINAL_QA_TRIAGE_NOT_ELIGIBLE")
        return evidence

    if classification.startswith("D050_PREFLIGHT_REQUIRED"):
        evidence["failure_family_candidate"] = "EVIDENCE_PREFLIGHT_REQUIRED"
        if facts.get("exact_d050_binding", False):
            evidence["exact_d050_binding"] = True
            evidence["repair_contract"] = facts.get("d050_repair_contract")
        else:
            evidence["validation_denial"] = "D050_EXACT_EVIDENCE_REQUIRED"
        return evidence

    if classification in {"CODEX_MODEL_REFRESH_TIMEOUT", "NO_PROGRESS_CLEAN_EXIT"}:
        evidence["failure_family_candidate"] = "RUNTIME_TRANSIENT"
        evidence["safe_unchanged_cursor"] = bool(facts.get("safe_unchanged_cursor", False))
        return evidence

    # A final-QA stage without readable triage evidence is deliberately denied
    # rather than inferred from the worker's label.
    if evidence["artifact_stage"] == "FINAL_DETERMINISTIC_QA":
        evidence["failure_family_candidate"] = "LOCAL_CONTENT_QA_FAILURE"
        evidence["triage_denial"] = "FINAL_QA_TRIAGE_EVIDENCE_MISSING"
    return evidence
