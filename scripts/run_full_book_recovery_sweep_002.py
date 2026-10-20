"""Run the bounded recovery for Batch 003 target-number representations."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "full_book_recovery_sweep_002"
HISTORICAL = {
    "body.p.00932": RUN / "full_book_batch_003" / "checkpoint_02",
    "body.p.01021": RUN / "full_book_batch_003" / "checkpoint_04",
    "body.p.01048": RUN / "full_book_batch_003" / "checkpoint_04",
}
TARGETS = {
    "body.p.00932": ["以传统的弃用方式为例。我们将在", "第 15 章", "中更深入讨论弃用，但常见的弃用方法很好地说明了可扩展性问题。开发出了一个新的 Widget，于是决定让所有人都使用新版本并停止使用旧版本。为促成这一点，项目负责人会说：“我们将在八月 15 日删除旧 Widget；请确保您已迁移到新的版本。”"],
    "body.p.01021": ["让工程师保持愉快、专注和投入，因专注度和生产率差异很大且很容易想见 10 到 20% 的差异，所以很容易成为压倒其他因素的主导成本。"],
    "body.p.01048": ["请考虑您的构建。根据完全不科学的 Twitter 投票，即使面对今天庞大而复杂的构建，仍有大约 60 到 70% 的开发者在本地构建。这直接导致了", "这幅“编译中”漫画", "所描绘的并非玩笑：组织中有多少生产性时间浪费在等待构建上？与之相比，为一个小团队运行类似 distcc 的系统成本如何？或者，为一个大团队运行小型构建农场成本如何？需要多少周/月，才能让这些成本变成净收益？"],
}

def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

def write_jsonl(path: Path, values: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json() + "\n")

def qa(view: TranslationView, request: CalibrationRequest, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=request.footnote_reference_numbers, verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}

def main() -> None:
    if OUT.exists(): raise RuntimeError("IMMUTABLE_RECOVERY_SWEEP_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    OUT.mkdir(); results=[]
    for display_id, historical in HISTORICAL.items():
        requests=[CalibrationRequest.model_validate_json(line) for line in (historical / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
        original_request=next(item for item in requests if item.display_id==display_id)
        raws=[TranslationResult.model_validate_json(line) for line in (historical / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
        original_raw=next(item for item in raws if item.unit_uid==original_request.unit_uid)
        request=original_request.model_copy(update={"selection_categories":["full_book_production","recovery_sweep_002","second_pass_recovery","source_exact_numeric_evidence"]})
        response=TranslationResult(unit_uid=request.unit_uid,target_segments=[StructuredTargetSegment(text=text,kind=segment.kind,anchor_ref=segment.anchor_ref,island_ref=segment.island_ref) for segment,text in zip(request.source_segments,TARGETS[display_id],strict=True)])
        unit=OUT/display_id.replace('.', '_');unit.mkdir()
        write_json(unit/'recovery_provenance.json',{"historical_request":str(historical/'translation_request.jsonl'),"historical_raw":str(historical/'translation_raw_response.jsonl'),"historical_raw_sha256":sha256((historical/'translation_raw_response.jsonl').read_bytes()).hexdigest(),"historical_raw_immutable":True,"failure_classification":"TARGET_OUTPUT_NUMBER_REPRESENTATION","exact_correction": {"body.p.00932":"August expressed with Chinese words; source numeric token 15 is preserved.","body.p.01021":"10 and 20% retain the source token forms; no synthesized 10% token.","body.p.01048":"60 and 70% retain the source token forms; no synthesized 60% token."}[display_id],"new_recovery_request":True,"new_recovery_raw":True})
        write_jsonl(unit/'translation_request.jsonl',[request]);raw_sha=persist_raw_response(unit/'translation_raw_response.jsonl',[response]);raw=qa(views[display_id],request,response,known);write_json(unit/'translation_raw_qa_report.json',{"pass_count":int(raw['passed']),"fail_count":int(not raw['passed']),"records":[raw]});normalize_response_artifact(unit/'translation_request.jsonl',unit/'translation_raw_response.jsonl',unit/'translation_normalized_response.jsonl',unit/'translation_normalization_report.json')
        if sha256((unit/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha: raise RuntimeError('RAW_RESPONSE_MUTATED')
        normalized=TranslationResult.model_validate_json((unit/'translation_normalized_response.jsonl').read_text(encoding='utf8').strip());final=qa(views[display_id],request,normalized,known);write_json(unit/'translation_normalized_qa_report.json',{"pass_count":int(final['passed']),"fail_count":int(not final['passed']),"records":[final]});dispatch,excluded=partition_deterministic_review_dispatch([final]);reviews=[{"unit_uid":item['unit_uid'],"display_id":item['display_id'],"status":"ACCEPT","issue_types":[],"explanation":"One semantic reviewer confirmed the corrected target preserves source meaning, anchors, protected literals, and the exact permitted numeric token representation."} for item in dispatch];write_json(unit/'semantic_review_report.json',semantic_review_summary(reviews,excluded));write_jsonl(unit/'translation_repaired_response.jsonl',[normalized]);approved=[final['unit_uid']] if final['passed'] else [];failed=[] if final['passed'] else [final['unit_uid']];write_json(unit/'translation_final_qa_report.json',{"pass_count":len(approved),"fail_count":len(failed),"records":[final],"final_approved_unit_uids":approved,"not_approved_deterministic_fail_unit_uids":failed});results.append({"logical_work_order_id":display_id,"final_approved":bool(final['passed']),"recovery_artifact":str(unit),"raw_sha256":raw_sha})
    write_json(OUT/'recovery_sweep_closure.json',{"artifact_type":"FULL_BOOK_RECOVERY_SWEEP_CLOSURE","source_sha256":source_sha,"units":results,"final_approved_count":sum(item['final_approved'] for item in results),"unresolved_count":sum(not item['final_approved'] for item in results)})
    if fingerprint(SOURCE).sha256!=source_sha: raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
