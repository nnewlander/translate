"""Apply the owner-approved B028 release decisions without provider calls.

The decision artifact is immutable.  Every application rebuilds effective
source, review group and Candidate Authority at apply time and rejects a stale
head before it writes any Human adjudication evidence.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.effective_source import resolve_effective_source
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair,
    HumanApprovedPatch,
    apply_human_adjudication_to_production_checkpoint,
    build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _rescue_working_overrides,
    _resolve_checkpoint_authoritative_candidate,
    build_current_review_group,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, load_stage
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_028"
BATCH_DIR = RUN / BATCH.lower()
DECISION_PATH = BATCH_DIR / "b028_human_adjudication_decisions_v1.json"
REPORT_PATH = BATCH_DIR / "b028_human_adjudication_apply_report_v1.json"

DECISIONS = {
    "artifact_type": "B028_HUMAN_ADJUDICATION_DECISIONS",
    "schema_version": 1,
    "batch": BATCH,
    "decision_count": 2,
    "binding_rule": (
        "Each decision is expected-current-head CAS bound. Application derives "
        "effective source, current review group, request binding, deterministic QA "
        "and Candidate Authority from immutable current evidence."
    ),
    "decisions": [
        {
            "work_order_id": "body.p.08713",
            "checkpoint": "checkpoint_02",
            "action": "CORRECT_TARGET",
            "expected_current_head": "e94f00212871368e45006019a3a6845fdd348b33e284d2990cc4c2dd22de83b1",
            "target_segments": ["生产代码变更的类型，", "233", "对其状态的理解，", "410", "-", "412"],
            "reason": (
                "Owner-approved correction: preserve the two independent index "
                "semantic terms without inferring the omitted object after 'of'; "
                "numeric locators and range remain Harness-deterministic."
            ),
        },
        {
            "work_order_id": "body.p.08815",
            "checkpoint": "checkpoint_06",
            "action": "ACCEPT_CURRENT",
            "expected_current_head": "3e5ffcd8d67583a1659c38d4391be93e5b2496bd6a46893d19fadb84839691c1",
            "reason": (
                "Owner-approved current target: do not reassemble numeric or locator "
                "segments from historical Reviewer prose; index numeric locator and "
                "delimiter assembly are Harness-deterministic and review scope is "
                "TERM_AND_QUALIFIER_ONLY."
            ),
        },
    ],
}


def _write_decisions() -> dict:
    if DECISION_PATH.is_file():
        existing = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
        if existing != DECISIONS:
            raise RuntimeError("B028_HUMAN_DECISION_ARTIFACT_CONFLICT")
    else:
        write_json_once(DECISION_PATH, DECISIONS)
    reread = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
    if reread != DECISIONS or reread.get("decision_count") != 2:
        raise RuntimeError("B028_HUMAN_DECISION_ARTIFACT_INCOMPLETE")
    return reread


def _already_applied(directory: Path, display_id: str, expected_head: str) -> bool:
    existing = load_stage(directory, "human_adjudications", display_id)
    if existing is None:
        return False
    if (
        existing.get("artifact_type") != "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION"
        or existing.get("batch_label") != BATCH
        or existing.get("decision") != "HUMAN_APPROVED"
        or existing.get("expected_current_heads", {}).get(display_id) != expected_head
    ):
        raise RuntimeError(f"B028_HUMAN_EXISTING_TERMINAL_CONFLICT:{display_id}")
    return True


def _apply_item(item: dict) -> dict:
    display_id = item["work_order_id"]
    checkpoint = item["checkpoint"]
    directory = BATCH_DIR / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    requests = {request.display_id: request for request in loaded["requests"]}
    request = requests.get(display_id)
    if request is None:
        raise RuntimeError(f"B028_HUMAN_REQUEST_MISSING:{display_id}")
    raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
    raw = raws.get(request.unit_uid)
    if raw is None:
        raise RuntimeError(f"B028_HUMAN_RAW_MISSING:{display_id}")
    head = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    expected_head = item["expected_current_head"]
    if head.candidate_sha256 != expected_head:
        raise RuntimeError(f"B028_HUMAN_CURRENT_HEAD_STALE:{display_id}:{head.candidate_sha256}")
    if _already_applied(directory, display_id, expected_head):
        return {
            "work_order_id": display_id,
            "checkpoint": checkpoint,
            "status": "IDEMPOTENT_ALREADY_APPLIED",
            "authoritative_candidate_sha256": head.candidate_sha256,
        }

    effective = resolve_effective_source(checkpoint_dir=directory, request=request).request
    overrides = _rescue_working_overrides(directory, loaded, raws)
    group = build_current_review_group(
        effective, loaded=loaded, run=RUN, checkpoint=checkpoint,
        existing_raw=raws, candidate_overrides=overrides,
    )
    if group is None or list(group.member_work_order_ids) != [display_id]:
        raise RuntimeError(f"B028_HUMAN_CURRENT_GROUP_UNSAFE:{display_id}")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    provider_request = enrich_request(effective, translation_bible=bible)
    members = [(provider_request, head.candidate)]
    if item["action"] == "CORRECT_TARGET":
        target_texts = item["target_segments"]
    elif item["action"] == "ACCEPT_CURRENT":
        target_texts = [segment.text for segment in head.candidate.target_segments]
    else:
        raise RuntimeError(f"B028_HUMAN_ACTION_INVALID:{display_id}")
    if (
        not isinstance(target_texts, list)
        or len(target_texts) != len(effective.source_segments)
        or any(not isinstance(text, str) or not text.strip() for text in target_texts)
    ):
        raise RuntimeError(f"B028_HUMAN_TARGET_CONTRACT_INVALID:{display_id}")
    allowed = HumanAllowedRepair(
        logical_work_order_id=display_id,
        unit_uid=effective.unit_uid,
        allowed_segment_indices=list(range(len(target_texts))),
        source_fingerprint=provider_request.request_binding_sha256,
    )
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason=item["reason"],
        allowed_repairs=[allowed],
        source_hashes={display_id: provider_request.request_binding_sha256},
        original_candidate_hashes={display_id: candidate_sha(raw)},
        expected_current_heads={display_id: expected_head},
        approved_patches=[
            HumanApprovedPatch(
                logical_work_order_id=display_id, unit_uid=effective.unit_uid,
                index=index, target_text=text,
            )
            for index, text in enumerate(target_texts)
        ],
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"],
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
        adjudication=adjudication, group=group, members=members, translation_bible=bible,
        triage_classification="TRUE_SEMANTIC_HUMAN", batch_label=BATCH, checkpoint=checkpoint,
    )
    refreshed = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    if item["action"] == "CORRECT_TARGET" and refreshed.candidate_sha256 == expected_head:
        raise RuntimeError(f"B028_HUMAN_MUTATION_NOT_AUTHORITATIVE:{display_id}")
    if item["action"] == "ACCEPT_CURRENT" and refreshed.candidate_sha256 != expected_head:
        raise RuntimeError(f"B028_HUMAN_ACCEPT_CURRENT_HEAD_CHANGED:{display_id}")
    if not all(record.get("passed") for record in result["qa"].values()):
        raise RuntimeError(f"B028_HUMAN_QA_NOT_PASS:{display_id}")
    return {
        "work_order_id": display_id,
        "checkpoint": checkpoint,
        "status": "PASS",
        "action": item["action"],
        "parent_candidate_sha256": expected_head,
        "authoritative_candidate_sha256": refreshed.candidate_sha256,
        "core_qa_pass": True,
        "translation_api_calls": result["translation_api_calls"],
        "reviewer_api_calls": result["reviewer_api_calls"],
        "repair_api_calls": result["repair_api_calls"],
    }


def main() -> None:
    artifact = _write_decisions()
    report = {
        "artifact_type": "B028_HUMAN_ADJUDICATION_APPLY_REPORT",
        "schema_version": 1,
        "decision_artifact_path": str(DECISION_PATH),
        "decision_artifact_sha256": sha256(DECISION_PATH.read_bytes()).hexdigest(),
        "results": [_apply_item(item) for item in artifact["decisions"]],
        "translation_api_calls": 0,
        "reviewer_api_calls": 0,
        "repair_api_calls": 0,
    }
    write_json_once(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
