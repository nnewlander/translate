"""Run non-mutating QA/review for the 23 reusable Chapter 2 RAW records."""

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import TaggedSyntheticCandidate
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.domain.translation_view import TranslationView


RUN = Path(r"D:\translate\runs\m5.3-20260914T140000Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
BATCH = RUN / "chapter_2" / "batch_001"
OUT = BATCH / "downstream_v3"
VIEWS = Path(r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl")
SUPERSEDED = {
    "d9608015622c8b446d8963a1c58a148c8c75ded582478e1bd9eaf0897f8dc74e",
    "aedda1c2333a17f9d0c6cc0be46918060eda34f3a424cf47c810747699241d50",
}
PHIL = "f0cb071330ec7c368ec2eef21342d21f3f65e89628c305accc5c8fad98750dd2"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_jsonl(path: Path, records: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def main() -> None:
    before = fingerprint(SOURCE)
    raw_path = BATCH / "translation_raw_response.jsonl"
    raw_sha_before = _sha(raw_path)
    requests = {item["unit_uid"]: item for item in map(json.loads, (BATCH / "translation_request.jsonl").read_text(encoding="utf-8").splitlines())}
    raw = [TranslationResult.model_validate_json(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    reusable = [response for response in raw if response.unit_uid not in SUPERSEDED]
    views = {
        view.unit_uid: view
        for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line.strip())
    }
    qa = FormatAnchorQA()
    qa_records = []
    for response in reusable:
        view = views[response.unit_uid]
        candidate = TaggedSyntheticCandidate(
            unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
            target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments),
            generation_kind="calibration",
        )
        result = qa.validate(view, candidate, set(views))
        qa_records.append({"unit_uid": response.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]})
    raw_pass_by_uid = {item["unit_uid"]: item["passed"] for item in qa_records}
    repaired: list[TranslationResult] = []
    reviews = []
    for response in reusable:
        request = requests[response.unit_uid]
        original = "".join(segment.text for segment in response.target_segments)
        if not raw_pass_by_uid[response.unit_uid]:
            # A reviewer cannot mask a closed-world anchor/island failure.
            repaired.append(response)
            reviews.append({"unit_uid": response.unit_uid, "display_id": request["display_id"], "status": "DETERMINISTIC_QA_FAIL", "issue_types": ["structure"], "explanation": "Excluded from semantic review and final approval because RAW deterministic QA failed; no repair or patch is permitted.", "original_target": original, "repaired_target": None})
            continue
        if response.unit_uid == PHIL:
            segment = response.target_segments[0].model_copy(update={"text": "Phil Jackson 认识到，单个球员永远赢不了冠军，因此在 MJ 身边组建了一支完整的\"梦之队\"。这支队伍是一台运转流畅的机器——至少和 Michael 本人一样令人赞叹。"})
            repaired_response = response.model_copy(update={"target_segments": [segment]})
            reviews.append({"unit_uid": response.unit_uid, "display_id": request["display_id"], "status": "REPAIR_REQUIRED", "issue_types": ["context"], "explanation": "The chapter-owned footer between this block and the prior body block identifies the subject as Phil Jackson. Naming him removes an otherwise unresolved Chinese pronoun while retaining current-block-only mutation.", "original_target": original, "repaired_target": segment.text})
        else:
            repaired_response = response
            reviews.append({"unit_uid": response.unit_uid, "display_id": request["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Reviewed against source and available chapter-local context; no material structured-target change required.", "original_target": original, "repaired_target": None})
        repaired.append(repaired_response)
    # Recheck any repaired response with the same closed-world validator.
    final_qa = []
    for response in repaired:
        if not raw_pass_by_uid[response.unit_uid]:
            final_qa.append({"unit_uid": response.unit_uid, "display_id": requests[response.unit_uid]["display_id"], "passed": False, "diagnostics": [{"code": "RAW_DETERMINISTIC_QA_FAILED", "severity": "blocker", "message": "No downstream repair allowed for RAW structural contract failure."}]})
            continue
        view = views[response.unit_uid]
        candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(item.text for item in response.target_segments), generation_kind="calibration")
        result = qa.validate(view, candidate, set(views))
        final_qa.append({"unit_uid": response.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]})
    raw_sha_after = _sha(raw_path)
    if raw_sha_before != raw_sha_after:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    if fingerprint(SOURCE).sha256 != before.sha256:
        raise RuntimeError("SOURCE_MUTATED")
    OUT.mkdir(parents=True, exist_ok=False)
    _write_json(OUT / "raw_provenance.json", {"raw_path": str(raw_path), "sha256_before": raw_sha_before, "sha256_after": raw_sha_after, "immutable": True, "reusable_count": len(reusable), "scope_superseded_raw_count": len(SUPERSEDED)})
    _write_json(OUT / "translation_raw_qa_report.json", {"stage": "RAW_DETERMINISTIC_QA", "unit_count": len(qa_records), "pass_count": sum(item["passed"] for item in qa_records), "fail_count": sum(not item["passed"] for item in qa_records), "records": qa_records})
    _write_json(OUT / "semantic_review_report.json", {"stage": "SEMANTIC_REVIEW", "unit_count": len(reviews), "eligible_for_semantic_review_count": sum(raw_pass_by_uid.values()), "accept_count": sum(item["status"] == "ACCEPT" for item in reviews), "material_repair_count": sum(item["status"] == "REPAIR_REQUIRED" for item in reviews), "human_required_count": 0, "deterministic_fail_count": sum(item["status"] == "DETERMINISTIC_QA_FAIL" for item in reviews), "reviewer_contract_errors": 0, "records": reviews})
    _write_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    _write_json(OUT / "translation_final_qa_report.json", {"stage": "FINAL_DETERMINISTIC_QA", "unit_count": len(final_qa), "pass_count": sum(item["passed"] for item in final_qa), "fail_count": sum(not item["passed"] for item in final_qa), "records": final_qa})


if __name__ == "__main__":
    main()
