from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.production_worker import worker
from booktrans.production_worker.selection import FrozenSelectionError


def _request(*, island: str) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-1",
        display_id="body.p.00001",
        story="body",
        content_role="paragraph",
        source_text="source",
        source_segments=[CalibrationSourceSegment(kind="plain", text="source", island_ref=island)],
    )


def _inputs(frozen_view):
    return {
        "source_sha256": "a" * 64,
        "selection_sha256": "b" * 64,
        "order": ["body.p.00001"],
        "views": {"body.p.00001": frozen_view},
        "blocks": {"body.p.00001": SimpleNamespace(story="body", part_name="word/document.xml")},
    }


def _wire(monkeypatch, tmp_path: Path, *, frozen_request, effective_request, rebuilt_request):
    checkpoint = tmp_path / "checkpoint_01"
    checkpoint.mkdir()
    (tmp_path / "batch_manifest.json").write_text("{}", encoding="utf-8")
    frozen_view, rebuilt_view = object(), object()
    monkeypatch.setattr(worker, "frozen_batch_checkpoint_inputs", lambda *args, **kwargs: _inputs(frozen_view))
    monkeypatch.setattr(worker, "production_checkpoint_path", lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(worker, "resolve_effective_translation_request", lambda **kwargs: (checkpoint / "translation_request.jsonl", [effective_request]))
    monkeypatch.setattr(worker, "fingerprint", lambda path: SimpleNamespace(
        sha256="a" * 64, size_bytes=1, mtime_utc=datetime.now(timezone.utc),
    ))
    monkeypatch.setattr(worker, "DocumentIR", lambda **kwargs: kwargs)
    monkeypatch.setattr(worker, "DocumentStoryIR", lambda **kwargs: kwargs)
    monkeypatch.setattr(worker.TranslationViewBuilder, "build", lambda *args, **kwargs: [SimpleNamespace(display_id="body.p.00001", marker="rebuilt")])
    import booktrans.production_worker.selection as selection

    def build(view, block, *, source_sha256):
        if view is frozen_view:
            return frozen_request
        if getattr(view, "marker", None) == "rebuilt":
            return rebuilt_request
        raise AssertionError("unexpected view")

    monkeypatch.setattr(selection, "build_calibration_request", build)
    return frozen_view


def test_current_view_boundary_reuses_matching_frozen_view(monkeypatch, tmp_path):
    request = _request(island="island.001")
    frozen_view = _wire(monkeypatch, tmp_path, frozen_request=request, effective_request=request, rebuilt_request=request)

    loaded = worker.current_batch_checkpoint_inputs(tmp_path, batch_label="FULL_BOOK_BATCH_037", checkpoint="checkpoint_01")

    assert loaded["views"]["body.p.00001"] is frozen_view
    assert loaded["current_view_rebuilt_count"] == 0
    assert loaded["current_view_frozen_reuse_count"] == 1


def test_current_view_boundary_rebuilds_only_when_effective_request_differs(monkeypatch, tmp_path):
    frozen, effective = _request(island="island.unsupported"), _request(island="island.001")
    _wire(monkeypatch, tmp_path, frozen_request=frozen, effective_request=effective, rebuilt_request=effective)

    loaded = worker.current_batch_checkpoint_inputs(tmp_path, batch_label="FULL_BOOK_BATCH_037", checkpoint="checkpoint_01")

    assert getattr(loaded["views"]["body.p.00001"], "marker") == "rebuilt"
    assert loaded["current_view_rebuilt_count"] == 1
    assert loaded["current_view_frozen_reuse_count"] == 0


def test_current_view_boundary_fails_closed_when_rebuild_cannot_bind_effective_request(monkeypatch, tmp_path):
    frozen, effective = _request(island="island.unsupported"), _request(island="island.001")
    _wire(monkeypatch, tmp_path, frozen_request=frozen, effective_request=effective, rebuilt_request=frozen)

    with pytest.raises(FrozenSelectionError, match="CURRENT_VIEW_EFFECTIVE_REQUEST_MISMATCH"):
        worker.current_batch_checkpoint_inputs(tmp_path, batch_label="FULL_BOOK_BATCH_037", checkpoint="checkpoint_01")


def test_fresh_request_birth_uses_current_rebuilt_view(monkeypatch, tmp_path):
    frozen_view = object()
    current_view = SimpleNamespace(display_id="body.p.00001", marker="current")
    inputs = _inputs(frozen_view)
    inputs.update({"selection": {"selected_logical_work_order_ids": ["body.p.00001"]}})
    request = _request(island="island.001")
    monkeypatch.setattr(worker, "fingerprint", lambda path: SimpleNamespace(
        sha256="a" * 64, size_bytes=1, mtime_utc=datetime.now(timezone.utc),
    ))
    monkeypatch.setattr(worker, "DocumentIR", lambda **kwargs: kwargs)
    monkeypatch.setattr(worker, "DocumentStoryIR", lambda **kwargs: kwargs)
    monkeypatch.setattr(worker.TranslationViewBuilder, "build", lambda *args, **kwargs: [current_view])
    monkeypatch.setattr(worker, "d050_paths_for_checkpoint", lambda *args, **kwargs: ())
    captured = {}

    def load_requests(**kwargs):
        captured.update(kwargs)
        return ["body.p.00001"], [request]

    monkeypatch.setattr(worker, "load_checkpoint_requests", load_requests)

    views, requests = worker.fresh_checkpoint_requests_from_current_view(
        tmp_path, batch_label="FULL_BOOK_BATCH_037", checkpoint="checkpoint_01", frozen_inputs=inputs,
    )

    assert views == {"body.p.00001": current_view}
    assert requests == [request]
    assert captured["views"] == views
    assert captured["selection_categories"] == ["full_book_production", "full_book_batch_037", "checkpoint_01"]
