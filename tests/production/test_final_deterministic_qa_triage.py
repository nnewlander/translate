from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "production" / "triage_final_deterministic_qa.py"
SPEC = importlib.util.spec_from_file_location("final_qa_triage", SCRIPT)
assert SPEC and SPEC.loader
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)


def _checkpoint(tmp_path: Path, *, codes: list[str], raw: bytes = b"immutable raw\n") -> tuple[Path, str, list[str]]:
    checkpoint = tmp_path / "checkpoint_03"
    checkpoint.mkdir(parents=True)
    (checkpoint / "translation_request.jsonl").write_text("{}\n", encoding="utf-8")
    (checkpoint / "translation_raw_response.jsonl").write_bytes(raw)
    ids = [f"body.p.{56000 + index:05d}" for index in range(len(codes))]
    records = [
        {
            "display_id": work_order,
            "passed": False,
            "diagnostics": [{"code": code, "severity": "blocker"}],
        }
        for work_order, code in zip(ids, codes, strict=True)
    ]
    records.extend({"display_id": f"pass.{index}", "passed": True, "diagnostics": []} for index in range(25 - len(records)))
    report = {"pass_count": 25 - len(codes), "fail_count": len(codes), "records": records}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json"):
        (checkpoint / name).write_text(json.dumps(report), encoding="utf-8")
    from hashlib import sha256
    return checkpoint, sha256(raw).hexdigest(), ids


def _classify(checkpoint: Path, raw_sha: str, ids: list[str], *, source: str = "source") -> dict:
    return triage.classify_final_deterministic_qa(
        checkpoint=checkpoint,
        expected_raw_sha256=raw_sha,
        expected_source_sha256="source",
        actual_source_sha256=source,
        handoff_failing_ids=ids,
    )


def test_known_sparse_numeric_failure_is_canonical_local_recovery(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _checkpoint(tmp_path, codes=["NUMBER_PRESERVATION"] * 3)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is True
    assert result["canonical_class"] == "LOCAL_DETERMINISTIC_QA_FAILURE"
    assert result["repair_policy"] == "NUMERIC_REPRESENTATION"
    assert result["failure_density"] == 0.12


def test_unknown_final_qa_code_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _checkpoint(tmp_path, codes=["UNKNOWN_QA_CODE"])
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "QA_FAILURE_CODE_NOT_ALLOWLISTED"


def test_high_density_known_failure_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _checkpoint(tmp_path, codes=["NUMBER_PRESERVATION"] * 4)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "QA_FAILURE_NOT_SPARSE"


def test_raw_sha_drift_fails_closed(tmp_path: Path) -> None:
    checkpoint, _, ids = _checkpoint(tmp_path, codes=["NUMBER_PRESERVATION"])
    result = _classify(checkpoint, "0" * 64, ids)
    assert result["eligible"] is False
    assert result["reason"] == "IMMUTABLE_RAW_SHA_MISMATCH"


def test_source_sha_drift_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _checkpoint(tmp_path, codes=["NUMBER_PRESERVATION"])
    result = _classify(checkpoint, raw_sha, ids, source="different")
    assert result["eligible"] is False
    assert result["reason"] == "SOURCE_SHA_MISMATCH"


def _doi_checkpoint(tmp_path: Path, *, target_suffix: str = "。", duplicate_source_doi: bool = False) -> tuple[Path, str, list[str]]:
    checkpoint = tmp_path / "checkpoint_02"
    checkpoint.mkdir()
    doi = "https://doi.org/10.1145/2786805.2786855"
    source_segments = [
        {"text": "Study ", "kind": "anchored", "anchor_ref": "a0"},
        {"text": doi, "kind": "protected", "island_ref": "p1"},
        {"text": ".", "kind": "anchored", "anchor_ref": "a1"},
    ]
    if duplicate_source_doi:
        source_segments.extend([
            {"text": doi, "kind": "protected", "island_ref": "p2"},
            {"text": ".", "kind": "anchored", "anchor_ref": "a2"},
        ])
    request = {"display_id": "body.p.05951", "source_text": f"Study {doi}.", "source_segments": source_segments}
    target_segments = [dict(segment) for segment in source_segments]
    target_segments[2]["text"] = target_suffix
    response = {"unit_uid": "unit-1", "target_segments": target_segments}
    (checkpoint / "translation_request.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    raw_bytes = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
    for name in ("translation_raw_response.jsonl", "translation_normalized_response.jsonl"):
        (checkpoint / name).write_bytes(raw_bytes)
    report = {"pass_count": 24, "fail_count": 1, "records": [
        {"display_id": "body.p.05951", "passed": False, "diagnostics": [{"code": "DOI_PRESERVATION", "severity": "blocker"}]},
        *({"display_id": f"pass.{index}", "passed": True, "diagnostics": []} for index in range(24)),
    ]}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json"):
        (checkpoint / name).write_text(json.dumps(report), encoding="utf-8")
    from hashlib import sha256
    return checkpoint, sha256(raw_bytes).hexdigest(), ["body.p.05951"]


def test_sparse_exact_doi_punctuation_boundary_is_eligible(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _doi_checkpoint(tmp_path)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is True
    assert result["repair_policy"] == "DOI_PRESERVATION"
    assert result["doi"]["repairs"][0]["target_after"] == "."


def test_doi_nonunique_boundary_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _doi_checkpoint(tmp_path, target_suffix=",")
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "DOI_PUNCTUATION_BOUNDARY_NOT_UNIQUE"


def test_multiple_source_doi_candidates_fail_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _doi_checkpoint(tmp_path, duplicate_source_doi=True)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "DOI_SOURCE_NOT_UNIQUE"


def test_source_without_doi_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _doi_checkpoint(tmp_path)
    request_path = checkpoint / "translation_request.jsonl"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["source_text"] = "Study without a DOI."
    request["source_segments"][1]["text"] = "not-a-doi"
    request_path.write_text(json.dumps(request) + "\n", encoding="utf-8")
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "DOI_SOURCE_NOT_UNIQUE"


def test_high_density_doi_failure_fails_closed_before_repair_inference(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _checkpoint(tmp_path, codes=["DOI_PRESERVATION"] * 4)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "QA_FAILURE_NOT_SPARSE"


def _url_checkpoint(tmp_path: Path, *, duplicated_prefix: bool = False, ambiguous_source: bool = False) -> tuple[Path, str, list[str]]:
    checkpoint = tmp_path / "checkpoint_04"
    checkpoint.mkdir(parents=True)
    url = "https://ieeexplore.ieee.org/abstract/document/8443579"
    source_url = url + (" " + url if ambiguous_source else "")
    source_segments = [
        {"text": "5 See ", "kind": "anchored", "anchor_ref": "a0", "island_ref": "i"},
        {"text": "https://ieeexplore.ieee.org/", "kind": "protected", "anchor_ref": "a1", "island_ref": "i"},
        {"text": "abstract/document/8443579", "kind": "anchored", "anchor_ref": "a2", "island_ref": "i"},
        {"text": ".", "kind": "anchored", "anchor_ref": "a3", "island_ref": "i"},
    ]
    if ambiguous_source:
        source_segments.extend([
            {"text": " ", "kind": "anchored", "anchor_ref": "a4", "island_ref": "i"},
            {"text": url, "kind": "anchored", "anchor_ref": "a5", "island_ref": "i"},
        ])
    request = {"schema_version": "m5.0", "source_sha256": "a" * 64, "unit_uid": "url-unit", "display_id": "footer.354.p.0001", "story": "footers", "content_role": "story_paragraph", "source_text": "5 See " + source_url + ".", "source_segments": source_segments, "footnote_reference_numbers": [], "verified_footnote_numeric_bindings": [], "explicit_numeric_representation_equivalences": []}
    target = [dict(segment) for segment in source_segments]
    target[2]["text"] = "https://ieeexplore.ieee.org/" if duplicated_prefix else "摘要/文档/8443579"
    target[3]["text"] = "摘要/文档/8443579" if duplicated_prefix else "."
    response = {"schema_version": "m5.0", "unit_uid": "url-unit", "target_segments": target}
    raw = (json.dumps(response, ensure_ascii=False) + "\n").encode()
    (checkpoint / "translation_request.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    (checkpoint / "translation_raw_response.jsonl").write_bytes(raw)
    report = {"pass_count": 24, "fail_count": 1, "records": [{"display_id": "footer.354.p.0001", "passed": False, "diagnostics": [{"code": "URL_PRESERVATION", "severity": "blocker"}]}, *({"display_id": f"pass.{index}", "passed": True, "diagnostics": []} for index in range(24))]}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json"):
        (checkpoint / name).write_text(json.dumps(report), encoding="utf-8")
    from hashlib import sha256
    return checkpoint, sha256(raw).hexdigest(), ["footer.354.p.0001"]


def test_split_or_duplicated_url_is_exact_deterministic_recovery(tmp_path: Path) -> None:
    for duplicated in (False, True):
        checkpoint, raw_sha, ids = _url_checkpoint(tmp_path / str(duplicated), duplicated_prefix=duplicated)
        result = _classify(checkpoint, raw_sha, ids)
        assert result["eligible"] is True
        assert result["repair_policy"] == "EXACT_SOURCE_URL_RESTORATION"


def test_url_source_ambiguity_fails_closed(tmp_path: Path) -> None:
    checkpoint, raw_sha, ids = _url_checkpoint(tmp_path, ambiguous_source=True)
    result = _classify(checkpoint, raw_sha, ids)
    assert result["eligible"] is False
    assert result["reason"] == "URL_SOURCE_OR_BINDING_AMBIGUOUS"
