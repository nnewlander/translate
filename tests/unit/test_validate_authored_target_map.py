from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from booktrans.domain.translation_view import TranslationView
from production.validate_authored_target_map import TargetMapValidationError, validate_authored_target_map


RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SELECTION = json.loads((RUN / "full_book_batch_013_selection.json").read_text(encoding="utf-8"))
VIEWS = {
    view.display_id: view
    for view in (
        TranslationView.model_validate_json(line)
        for line in (RUN.parent / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )
}
ORDER = SELECTION["selected_logical_work_order_ids"][75:100]


def valid_map() -> dict:
    return {
        "artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP",
        "logical_work_orders": list(ORDER),
        "targets": {item: [segment.text for segment in VIEWS[item].segments] for item in ORDER},
    }


def assert_fails(artifact: dict, match: str) -> None:
    with pytest.raises(TargetMapValidationError, match=match):
        validate_authored_target_map(
            selection=SELECTION, checkpoint="checkpoint_04", target_artifact=artifact, views=VIEWS
        )


def test_exact_25_and_exact_segment_counts_pass() -> None:
    assert validate_authored_target_map(
        selection=SELECTION, checkpoint="checkpoint_04", target_artifact=valid_map(), views=VIEWS
    ) == ORDER


def test_fewer_or_extra_target_segments_fail_with_exact_count_diagnostic() -> None:
    fewer = valid_map()
    fewer["targets"]["body.p.04169"] = fewer["targets"]["body.p.04169"][:-1]
    assert_fails(fewer, r"TARGET_SEGMENT_COUNT_MISMATCH: body\.p\.04169 expected=21 actual=20")
    extra = valid_map()
    extra["targets"]["body.p.04169"].append("extra")
    assert_fails(extra, r"TARGET_SEGMENT_COUNT_MISMATCH: body\.p\.04169 expected=21 actual=22")


def test_missing_extra_and_wrong_order_fail_closed_world_contract() -> None:
    missing = valid_map()
    del missing["targets"][ORDER[-1]]
    assert_fails(missing, "TARGET_MAP_CLOSED_WORLD_MISMATCH")
    extra = valid_map()
    extra["targets"]["not-in-selection"] = ["x"]
    assert_fails(extra, "TARGET_MAP_CLOSED_WORLD_MISMATCH")
    wrong_order = valid_map()
    wrong_order["logical_work_orders"] = list(reversed(ORDER))
    assert_fails(wrong_order, "AUTHORITATIVE_SELECTION_MISMATCH")


def test_non_string_target_fails() -> None:
    artifact = valid_map()
    artifact["targets"]["body.p.04169"][0] = 123
    assert_fails(artifact, r"TARGET_SEGMENT_NOT_STRING: body\.p\.04169 index=0")


def test_preflight_is_read_only_and_cannot_create_checkpoint_directory(tmp_path: Path) -> None:
    artifact = valid_map()
    artifact["targets"]["body.p.04169"] = artifact["targets"]["body.p.04169"][:-1]
    checkpoint = tmp_path / "checkpoint_04"
    assert not checkpoint.exists()
    assert_fails(artifact, "TARGET_SEGMENT_COUNT_MISMATCH")
    assert not checkpoint.exists()


def test_runner_keeps_the_preflight_hook_before_output_creation() -> None:
    """The generic runner calls the PASS/fail helper before ``output.mkdir``."""
    source = (ROOT / "scripts" / "run_full_book_batch006_checkpoint.py").read_text(encoding="utf-8")
    assert "validate_authored_target_map(" in source
    assert source.index("validate_authored_target_map(") < source.index("output.mkdir()")
