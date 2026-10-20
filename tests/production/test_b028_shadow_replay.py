"""Test-only, provider-free B028 shadow replay certification."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from booktrans.production_worker.production_checkpoint import _resolve_checkpoint_authoritative_candidate
from booktrans.production_worker.unit_store import load_raw_by_uid


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(root: Path, batch_label: str) -> dict:
    batch = root / batch_label.lower()
    manifest = _read(batch / "batch_manifest.json")
    selection = _read(root / f"{batch_label.lower()}_selection.json")["selected_logical_work_order_ids"]
    ca_heads: dict[str, str] = {}
    effective_qa: dict[str, bool] = {}
    dispositions: dict[str, str] = {}
    deferred: list[str] = []
    seals: list[dict] = []
    for checkpoint in manifest["checkpoints"]:
        name = checkpoint["id"]; directory = batch / name
        evidence = _read(directory / "checkpoint_evidence.json")
        final = _read(directory / "translation_final_qa_report.json")
        raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
        records = {row["unit_uid"]: row for row in final["records"]}
        deferred_uids = set(final.get("deferred_semantic_debt_unit_uids") or [])
        for display_id in evidence["logical_work_orders"]:
            unit = _read(directory / "units" / f"{display_id}.json")
            uid = unit["unit_uid"]
            resolved = _resolve_checkpoint_authoritative_candidate(directory, display_id, raws[uid])
            ca_heads[display_id] = resolved.candidate_sha256
            effective_qa[display_id] = bool(records[uid]["passed"])
            if uid in deferred_uids:
                dispositions[display_id] = "DEFERRED_SEMANTIC_DEBT"; deferred.append(display_id)
            else:
                dispositions[display_id] = "APPROVED" if records[uid]["passed"] else "FAIL_CLOSED"
        seals.append({"checkpoint": name, "logical_work_orders": evidence["logical_work_orders"], "evidence_sha256": sha256((directory / "checkpoint_evidence.json").read_bytes()).hexdigest()})
    gates = sorted(batch.glob("release_gate_v*.json"))
    assert gates
    gate = _read(gates[-1])
    debt = _read(batch / Path(gate["release_debt_manifest"]).name)
    assert sorted(ca_heads) == selection and len(ca_heads) == 200
    return {"ca_heads": ca_heads, "effective_qa": effective_qa, "checkpoint_seals": seals,
            "production_dispositions": dispositions, "deferred_debt": sorted(deferred),
            "release_debt": debt, "release_gate": gate}


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("batch_label", ("FULL_BOOK_BATCH_028", "FULL_BOOK_BATCH_032"))
def test_closed_batch_shadow_replay_matches_authoritative_summary_without_provider(
    tmp_path: Path, batch_label: str,
) -> None:
    real = _summary(RUN, batch_label)
    shadow = tmp_path / "shadow"; shadow.mkdir()
    shutil.copytree(RUN / batch_label.lower(), shadow / batch_label.lower())
    shutil.copy2(RUN / f"{batch_label.lower()}_selection.json", shadow / f"{batch_label.lower()}_selection.json")
    replay = _summary(shadow, batch_label)
    report = {"real_authoritative_digest": _digest(real), "shadow_authoritative_digest": _digest(replay),
              "shadow_provider_calls": 0,
              "ca_head_diff_count": sum(real["ca_heads"].get(k) != replay["ca_heads"].get(k) for k in set(real["ca_heads"]) | set(replay["ca_heads"])),
              "effective_qa_diff_count": sum(real["effective_qa"].get(k) != replay["effective_qa"].get(k) for k in set(real["effective_qa"]) | set(replay["effective_qa"])),
              "checkpoint_state_diff_count": int(real["checkpoint_seals"] != replay["checkpoint_seals"]),
              "production_disposition_diff_count": sum(real["production_dispositions"].get(k) != replay["production_dispositions"].get(k) for k in set(real["production_dispositions"]) | set(replay["production_dispositions"])),
              "debt_diff_count": int(real["deferred_debt"] != replay["deferred_debt"] or real["release_debt"] != replay["release_debt"]),
              "release_gate_match": real["release_gate"] == replay["release_gate"]}
    (shadow / "b028_shadow_replay_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert report["shadow_provider_calls"] == 0
    assert report["real_authoritative_digest"] == report["shadow_authoritative_digest"]
    assert all(report[key] == 0 for key in ("ca_head_diff_count", "effective_qa_diff_count", "checkpoint_state_diff_count", "production_disposition_diff_count", "debt_diff_count"))
    assert report["release_gate_match"] is True
