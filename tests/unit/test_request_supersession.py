from hashlib import sha256
import json

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker.artifacts import write_jsonl_once
from booktrans.production_worker.unit_store import (
    RequestSupersessionError,
    create_preseal_request_supersession,
    resolve_effective_translation_request,
    persist_request_bound_provider_reacquisition,
    resolve_effective_provider_acquisition,
)


CONTEXT = dict(source_sha256="a" * 64, batch_id="FULL_BOOK_BATCH_037", checkpoint_id="checkpoint_01",
               selection_sha256="b" * 64, manifest_sha256="c" * 64)


def _rows():
    return [CalibrationRequest(source_sha256=CONTEXT["source_sha256"], unit_uid=f"u{n}", display_id=f"body.p.{n:05d}",
                               story="body", content_role="paragraph", source_text=f"text {n}",
                               source_segments=[CalibrationSourceSegment(kind="plain", text=f"text {n}", island_ref="island.unsupported")]) for n in range(2)]


def _setup(tmp_path):
    original = tmp_path / "translation_request.jsonl"
    write_jsonl_once(original, _rows())
    return original


def _corrected():
    return [row.model_copy(update={"source_segments": [row.source_segments[0].model_copy(update={"island_ref": "island.001"})]}) for row in _rows()]


def _resolve(path):
    return resolve_effective_translation_request(checkpoint_dir=path, expected_order=[row.display_id for row in _rows()], **CONTEXT)


def test_preseal_request_supersession_resolves_child_and_preserves_original(tmp_path):
    original = _setup(tmp_path)
    original_bytes = original.read_bytes()
    child = create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=_corrected(),
                                                 affected_unit_uids=["u0", "u1"], code_evidence={"test": "harness"}, **CONTEXT)
    effective_path, rows = _resolve(tmp_path)
    assert effective_path == child
    assert [row.source_segments[0].island_ref for row in rows] == ["island.001", "island.001"]
    assert original.read_bytes() == original_bytes
    assert _resolve(tmp_path)[0] == child  # deterministic replay


@pytest.mark.parametrize("mutation, error", [
    ("fork", "FORK"), ("source", "STALE_BINDING"), ("seal", "AFTER_SEAL"),
])
def test_invalid_request_supersession_fails_closed(tmp_path, mutation, error):
    original = _setup(tmp_path)
    child = create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=_corrected(),
                                                 affected_unit_uids=["u0", "u1"], **CONTEXT)
    record_path = next((tmp_path / "translation_request_supersessions").glob("*.json"))
    if mutation == "fork":
        record = json.loads(record_path.read_text(encoding="utf-8")); record["child_request_artifact"] = child.name
        record["child_request_sha256"] = sha256(child.read_bytes()).hexdigest()
        (tmp_path / "translation_request_supersessions" / ("d" * 64 + ".json")).write_text(json.dumps(record), encoding="utf-8")
    elif mutation == "source":
        record = json.loads(record_path.read_text(encoding="utf-8")); record["source_sha256"] = "z" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
    else:
        (tmp_path / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RequestSupersessionError, match=error):
        _resolve(tmp_path)


def test_request_supersession_rejects_source_identity_change(tmp_path):
    original = _setup(tmp_path)
    bad = _corrected(); bad[0] = bad[0].model_copy(update={"source_text": "changed"})
    with pytest.raises(RequestSupersessionError, match="SOURCE_IDENTITY_CHANGED"):
        create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=bad,
                                             affected_unit_uids=["u0", "u1"], **CONTEXT)


def test_request_bound_reacquisition_supersedes_only_unbound_base_raw(tmp_path):
    original = _setup(tmp_path)
    child = create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=_corrected(),
                                                 affected_unit_uids=["u0", "u1"], **CONTEXT)
    rows = _corrected(); bindings = {row.unit_uid: f"{n}" * 64 for n, row in enumerate(rows, start=1)}
    old = __import__("booktrans.domain.calibration", fromlist=["TranslationResult"]).TranslationResult
    base = [old(unit_uid=row.unit_uid, target_segments=[StructuredTargetSegment(text="旧", kind="plain", island_ref="island.001")]) for row in rows]
    write_jsonl_once(tmp_path / "translation_raw_response.jsonl", base)
    provenance = [{"unit_uid": row.unit_uid, "request_binding_sha256": "x" * 64} for row in rows]
    (tmp_path / "translation_provider_provenance.jsonl").write_text("".join(json.dumps(x) + "\n" for x in provenance), encoding="utf-8")
    current, historical = resolve_effective_provider_acquisition(checkpoint_dir=tmp_path, requests=rows, request_binding_by_uid=bindings,
        effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), **CONTEXT)
    assert not current and historical == {"u0", "u1"}
    fresh = old(unit_uid="u0", target_segments=[StructuredTargetSegment(text="新", kind="plain", island_ref="island.001")])
    persist_request_bound_provider_reacquisition(checkpoint_dir=tmp_path, request=rows[0], request_binding_sha256=bindings["u0"],
        effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), raw=fresh,
        provenance={"request_binding_sha256": bindings["u0"], "provider": "test", "model": "test"}, **CONTEXT)
    current, historical = resolve_effective_provider_acquisition(checkpoint_dir=tmp_path, requests=rows, request_binding_by_uid=bindings,
        effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), **CONTEXT)
    assert current["u0"].target_segments[0].text == "新" and "u0" in historical


def test_sealed_preseal_reacquisition_is_read_only_projection(tmp_path):
    original = _setup(tmp_path)
    child = create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=_corrected(),
                                                 affected_unit_uids=["u0", "u1"], **CONTEXT)
    rows = _corrected(); bindings = {row.unit_uid: f"{n}" * 64 for n, row in enumerate(rows, start=1)}
    result = __import__("booktrans.domain.calibration", fromlist=["TranslationResult"]).TranslationResult
    write_jsonl_once(tmp_path / "translation_raw_response.jsonl", [
        result(unit_uid=row.unit_uid, target_segments=[StructuredTargetSegment(text="旧", kind="plain", island_ref="island.001")]) for row in rows
    ])
    (tmp_path / "translation_provider_provenance.jsonl").write_text("".join(json.dumps({"unit_uid": row.unit_uid, "request_binding_sha256": "x" * 64}) + "\n" for row in rows), encoding="utf-8")
    for row in rows:
        raw = result(unit_uid=row.unit_uid, target_segments=[StructuredTargetSegment(text="新", kind="plain", island_ref="island.001")])
        persist_request_bound_provider_reacquisition(checkpoint_dir=tmp_path, request=row, request_binding_sha256=bindings[row.unit_uid],
            effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), raw=raw,
            provenance={"request_binding_sha256": bindings[row.unit_uid], "source_sha256": CONTEXT["source_sha256"], "unit_uid": row.unit_uid}, **CONTEXT)
    (tmp_path / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    current, _ = resolve_effective_provider_acquisition(checkpoint_dir=tmp_path, requests=rows, request_binding_by_uid=bindings,
        effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), **CONTEXT)
    assert set(current) == {"u0", "u1"}
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before  # sealed projection performs neither provider calls nor writes


def test_sealed_ambiguous_reacquisition_binding_fails_closed(tmp_path):
    original = _setup(tmp_path)
    child = create_preseal_request_supersession(checkpoint_dir=tmp_path, parent_path=original, corrected_requests=_corrected(),
                                                 affected_unit_uids=["u0", "u1"], **CONTEXT)
    rows = _corrected(); bindings = {row.unit_uid: f"{n}" * 64 for n, row in enumerate(rows, start=1)}
    result = __import__("booktrans.domain.calibration", fromlist=["TranslationResult"]).TranslationResult
    write_jsonl_once(tmp_path / "translation_raw_response.jsonl", [])
    raw = result(unit_uid="u0", target_segments=[StructuredTargetSegment(text="新", kind="plain", island_ref="island.001")])
    persist_request_bound_provider_reacquisition(checkpoint_dir=tmp_path, request=rows[0], request_binding_sha256=bindings["u0"],
        effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), raw=raw,
        provenance={"request_binding_sha256": bindings["u0"], "source_sha256": "wrong", "unit_uid": "u0"}, **CONTEXT)
    (tmp_path / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RequestSupersessionError, match="PROVIDER_REACQUISITION_SEALED_BINDING_INVALID"):
        resolve_effective_provider_acquisition(checkpoint_dir=tmp_path, requests=rows, request_binding_by_uid=bindings,
            effective_request_artifact_sha256=sha256(child.read_bytes()).hexdigest(), **CONTEXT)


def test_sealed_postseal_reacquisition_still_fails_closed(tmp_path):
    (tmp_path / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
    folder = tmp_path / "provider_reacquisitions"; folder.mkdir()
    path = folder / "new.json"; path.write_text(json.dumps({"artifact_type": "REQUEST_BOUND_PROVIDER_REACQUISITION"}), encoding="utf-8")
    # Make the fixture unambiguously newer than the seal on file systems with
    # coarse timestamp granularity.
    import os
    seal_ns = (tmp_path / "checkpoint_evidence.json").stat().st_mtime_ns
    os.utime(path, ns=(seal_ns + 1_000_000_000, seal_ns + 1_000_000_000))
    with pytest.raises(RequestSupersessionError, match="PROVIDER_REACQUISITION_AFTER_SEAL"):
        resolve_effective_provider_acquisition(checkpoint_dir=tmp_path, requests=_rows(), request_binding_by_uid={},
            effective_request_artifact_sha256="d" * 64, **CONTEXT)
