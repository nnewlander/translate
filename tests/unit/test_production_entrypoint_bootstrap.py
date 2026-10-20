from __future__ import annotations

import importlib.util
from pathlib import Path

from booktrans.production_worker.selection import EXPECTED_SOURCE_SHA
from booktrans.production_worker.worker import DEFAULT_RUN, QwenMTProvider, build_parser, frozen_b022_checkpoint03_inputs


def test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight() -> None:
    path = Path(r"D:\translate\scripts\production\run_qwen_deterministic_worker.py")
    spec = importlib.util.spec_from_file_location("run_qwen_deterministic_worker_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert QwenMTProvider.__name__ == "QwenMTProvider"
    args = build_parser().parse_args(
        [
            "--mode",
            "production",
            "--worker-id",
            "bootstrap-preflight",
            "--run-path",
            str(DEFAULT_RUN),
            "--batch-label",
            "FULL_BOOK_BATCH_025",
            "--checkpoint",
            "checkpoint_07",
        ]
    )
    assert args.mode == "production"
    assert args.authorize_production_checkpoint_write is False
    assert args.batch_label == "FULL_BOOK_BATCH_025"
    assert args.checkpoint == "checkpoint_07"
    payload = frozen_b022_checkpoint03_inputs(DEFAULT_RUN)
    assert payload["source_sha256"] == EXPECTED_SOURCE_SHA
    assert payload["order"][0] == "body.p.06939"
    assert len(payload["order"]) == 25
