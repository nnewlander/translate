from __future__ import annotations

import json

from booktrans.production_worker.production_checkpoint import _blocked


def test_blocked_handoff_uses_explicit_active_batch_not_legacy_default(tmp_path) -> None:
    handoff = tmp_path / "handoff.json"
    assert _blocked(
        handoff, "worker-b036", checkpoint="checkpoint_12",
        classification="CHECKPOINT_SELECTION_INVALID", active_batch="FULL_BOOK_BATCH_036",
    ) == 2
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["active_batch"] == "FULL_BOOK_BATCH_036"
    assert payload["failure_classification"] == "CHECKPOINT_SELECTION_INVALID"
    assert "FULL_BOOK_BATCH_022" not in handoff.read_text(encoding="utf-8")
