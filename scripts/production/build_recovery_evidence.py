"""Read immutable recovery facts and emit canonical registry evidence only."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "production"))

from booktrans.production_control.recovery_evidence_builder import build_recovery_evidence
from booktrans.production_control.canonical_qa_failure import canonicalize_qa_failure
from triage_final_deterministic_qa import classify_final_deterministic_qa


def _numeric_tokens(text: str) -> set[str]:
    import re
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _exact_index_locator_recovery(checkpoint: Path, failing: list[str]) -> bool:
    """Prove the narrow, already-implemented Index locator transition.

    This is routing evidence only.  The production checkpoint independently
    resolves Candidate Authority, materializes the locator, and reruns Core.
    """
    if len(failing) != 1:
        return False
    display_id = failing[0]
    try:
        payload = _read_json(checkpoint / "reviewer_payloads" / f"{display_id}.json")
        context = payload.get("unit_context")
        source = payload.get("source_segments")
        target = payload.get("target_segments")
        group = payload.get("semantic_review_group")
        if not isinstance(context, dict) or not isinstance(source, list) or not isinstance(target, list) or len(source) != len(target):
            return False
        identity = context.get("source_structural_identity")
        members = group.get("member_work_order_ids") if isinstance(group, dict) else None
        rules = context.get("continuation_review_rules")
        if not isinstance(identity, dict) or identity.get("content_role") != "index_entry":
            return False
        if context.get("continuation_relation") != "INDEX_ENTRY" or members != [display_id]:
            return False
        if not isinstance(rules, dict) or rules.get("numeric_locator_owner") != "HARNESS_DETERMINISTIC":
            return False
        # An existing descendant would be the current authority instead of
        # RAW; do not route based on this compatibility payload in that case.
        if (checkpoint / "accepted_candidate_mutations" / f"{display_id}.json").exists():
            return False
        missing: list[str] = []
        for source_segment, target_segment in zip(source, target):
            if not isinstance(source_segment, dict) or not isinstance(target_segment, dict):
                return False
            if source_segment.get("kind") != "anchored" or target_segment.get("kind") != "anchored":
                return False
            source_numbers = _numeric_tokens(str(source_segment.get("text") or ""))
            target_numbers = _numeric_tokens(str(target_segment.get("text") or ""))
            missing.extend(sorted(source_numbers - target_numbers))
        return bool(missing) and all(
            sum(token in _numeric_tokens(str(segment.get("text") or "")) for segment in source if isinstance(segment, dict)) == 1
            for token in missing
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _source_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def collect_immutable_recovery_facts(
    handoff: dict[str, Any], cursor: dict[str, Any], source: Path
) -> dict[str, Any]:
    """Read only the active checkpoint and its frozen selection."""
    checkpoint = Path(str(handoff.get("partial_checkpoint_path") or ""))
    expected_source = str(cursor.get("source_sha256") or "")
    source_valid = source.is_file() and bool(expected_source) and _source_sha(source) == expected_source
    active_ids: list[str] = []
    try:
        selection = _read_json(Path(str(cursor["active_selection"]["path"])))
        active_ids = [str(value) for value in selection.get("selected_logical_work_order_ids", [])]
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        pass
    failing = [str(value) for value in handoff.get("failing_work_order_ids") or []]
    identity_valid = (
        str(handoff.get("active_batch") or "") == str(cursor.get("active_batch") or "")
        and str(handoff.get("checkpoint") or "") == str(cursor.get("next_checkpoint") or "")
    )
    raw = checkpoint / "translation_raw_response.jsonl"
    expected_raw = str(handoff.get("raw_sha256") or "")
    raw_valid = (not handoff.get("raw_exists")) or (
        raw.is_file() and bool(expected_raw) and sha256(raw.read_bytes()).hexdigest() == expected_raw
    )
    facts: dict[str, Any] = {
        "source_sha_valid": source_valid,
        "identity_valid": identity_valid,
        "partial_path_exists": checkpoint.is_dir(),
        "scope_active_and_unique": bool(failing) and len(failing) == len(set(failing)) and len(failing) <= 5 and all(value in active_ids for value in failing),
        "raw_sha_valid": raw_valid,
        "deterministic_index_locator_recovery": _exact_index_locator_recovery(checkpoint, failing),
    }
    if str(handoff.get("failure_classification") or "") == "EVIDENCE_PROVIDER_UNAVAILABLE":
        # This is a pre-checkpoint infrastructure handoff.  Require an exact
        # local requirement supplied by the worker; no checkpoint/RAW is
        # expected or invented here.
        requirement = handoff.get("evidence_requirement")
        pages = requirement.get("page_numbers") if isinstance(requirement, dict) else None
        pdf_sha = requirement.get("pdf_sha256") if isinstance(requirement, dict) else None
        ids = requirement.get("logical_work_order_ids") if isinstance(requirement, dict) else None
        pdf = source.with_suffix(".pdf")
        actual_pdf_sha = _source_sha(pdf) if pdf.is_file() else ""
        canonical_requirement = {
            "source_pdf_path": str(pdf), "pdf_sha256": pdf_sha,
            "page_numbers": pages, "logical_work_order_ids": ids,
            "evidence_requirement": requirement.get("evidence_requirement") if isinstance(requirement, dict) else None,
        }
        facts["evidence_requirement"] = canonical_requirement
        facts["evidence_requirement_valid"] = (
            facts["identity_valid"] and isinstance(pages, list) and bool(pages)
            and all(isinstance(page, int) and page > 0 for page in pages)
            and isinstance(pdf_sha, str) and len(pdf_sha) == 64 and pdf_sha == actual_pdf_sha
            and isinstance(ids, list) and bool(ids)
            and all(isinstance(value, str) and value in active_ids for value in ids)
            and requirement.get("evidence_requirement") == "D050_PDF_CORROBORATION"
        )
        return facts
    stage = str(handoff.get("stage_reached") or "")
    qa_names = {
        "raw": "translation_raw_qa_report.json",
        "normalized": "translation_normalized_qa_report.json",
        "final": "translation_final_qa_report.json",
    }
    reports: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    for key, name in qa_names.items():
        path = checkpoint / name
        if path.is_file():
            try:
                reports[key] = _read_json(path)
                paths[key] = str(path)
            except (OSError, ValueError, json.JSONDecodeError):
                # The pure canonicalizer receives no invented report facts.
                paths[key] = str(path)
    canonical = None
    # A semantic-terminal handoff can still be caused by the current
    # authoritative candidate's deterministic Core gate.  In that case the
    # immutable RAW report is the current-head QA evidence when no accepted
    # descendant exists yet.  Do not infer from the terminal label: require
    # the same canonical report/scope proof used by deterministic stages.
    canonical_stage = stage
    if stage == "SEMANTIC_TERMINAL":
        canonical_stage = "RAW_DETERMINISTIC_QA"
    if "DETERMINISTIC_QA" in canonical_stage:
        canonical = canonicalize_qa_failure(
            stage=canonical_stage, handoff=handoff, reports=reports, report_paths=paths,
            source_sha_valid=source_valid, raw_required=bool(handoff.get("raw_exists")), raw_sha_valid=raw_valid,
        )
        facts["canonical_qa_failure"] = canonical
    if canonical and canonical["valid"]:
        codes = canonical["qa_codes"]
        sparse = canonical["failure_count"] <= 3 and canonical["density"] is not None and canonical["density"] <= 0.12
        if not sparse:
            facts["final_qa_triage"] = {"eligible": False, "reason": "QA_FAILURE_NOT_SPARSE", "codes": codes,
                                         "failure_count": canonical["failure_count"], "failure_density": canonical["density"]}
        elif codes == ["NUMBER_PRESERVATION"] and facts.get("deterministic_index_locator_recovery"):
            facts["final_qa_triage"] = {
                "eligible": True, "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
                "repair_policy": "INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION", "reason": None,
                "failure_count": canonical["failure_count"], "failure_density": canonical["density"],
                "failing_work_order_ids": failing, "raw_sha256": expected_raw, "codes": codes,
            }
        elif codes == ["NUMBER_PRESERVATION"]:
            facts["final_qa_triage"] = {
                "eligible": True, "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
                "repair_policy": "NUMERIC_REPRESENTATION", "reason": None,
                "failure_count": canonical["failure_count"], "failure_density": canonical["density"],
                "failing_work_order_ids": failing, "raw_sha256": expected_raw, "codes": codes,
            }
        elif set(codes) in ({"NUMERIC_ANCHOR_DUPLICATION"}, {"NUMBER_PRESERVATION", "NUMERIC_ANCHOR_DUPLICATION"}):
            # This exact pair is handled by the already implemented
            # current-checkpoint numeric-anchor ownership correction.  The
            # supervisor must resume the same immutable checkpoint; it must
            # not invoke a provider repair or synthesize a new recovery path.
            facts["final_qa_triage"] = {
                "eligible": True, "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
                "repair_policy": "NUMERIC_ANCHOR_OWNERSHIP_CORRECTION", "reason": None,
                "failure_count": canonical["failure_count"], "failure_density": canonical["density"],
                "failing_work_order_ids": failing, "raw_sha256": expected_raw, "codes": codes,
            }
        elif codes in (["DOI_PRESERVATION"], ["URL_PRESERVATION"]):
            # Exact DOI-boundary proof is shared across deterministic stages.
            facts["final_qa_triage"] = classify_final_deterministic_qa(
                checkpoint=checkpoint, expected_raw_sha256=expected_raw,
                expected_source_sha256=expected_source,
                actual_source_sha256=_source_sha(source) if source.is_file() else "",
                handoff_failing_ids=failing,
            )
        else:
            facts["final_qa_triage"] = {
                "eligible": False, "reason": "QA_FAILURE_CODE_NOT_ALLOWLISTED",
                "codes": codes, "failure_count": canonical["failure_count"],
                "failure_density": canonical["density"],
            }
    elif canonical:
        facts["final_qa_triage"] = {
            "eligible": False, "reason": canonical["denial_reason"],
            "codes": canonical["qa_codes"], "failure_count": canonical["failure_count"],
            "failure_density": canonical["density"],
        }
    if str(handoff.get("failure_classification") or "").startswith("D050_PREFLIGHT_REQUIRED"):
        # The current D050 worker contract is exact only when every bounded
        # failing id remains in the frozen active selection.  The recovery
        # worker must still build and validate unit-uid evidence itself.
        facts["exact_d050_binding"] = bool(facts["scope_active_and_unique"])
        facts["d050_repair_contract"] = {
            "failing_work_order_ids": failing,
            "exact_logical_work_order_and_unit_uid_binding_required": True,
        }
    return facts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    handoff = _read_json(args.handoff) if args.handoff else json.load(sys.stdin)
    if not isinstance(handoff, dict):
        raise ValueError("HANDOFF_OBJECT_REQUIRED")
    cursor = _read_json(args.state)
    facts = collect_immutable_recovery_facts(handoff, cursor, args.source)
    print(json.dumps(build_recovery_evidence(handoff, cursor, facts), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
