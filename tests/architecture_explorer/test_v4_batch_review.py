from copy import deepcopy
import hashlib
import json

import pytest

from booktrans.architecture_explorer.batch_review import (
    confirm_batch_review,
    decided_objects,
    prepare_batch_review,
    render_batch_checklist,
    render_final_decision_list,
    validate_batch_confirmation,
)
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.batch_review_cli import main as batch_main
from test_v4_relationship_review import _definition, _explorer, _node_definition


def _batch():
    analysis = {
        "candidate-node::worker-entry": {
            "topic": "受控交接",
            "display_name": "Worker 入口",
            "source_check": "源码片段与职责一致；运行时路径未验证。",
            "assessment": "职责范围清晰。",
            "concerns": ["运行时路径未知"],
            "risk": "低",
            "suggestion": "APPROVED",
            "suggestion_reason": "静态职责证据足够。",
        },
        "candidate-collaboration::relationship-under-review": {
            "topic": "受控交接",
            "display_name": "Supervisor 到 Worker 的交接",
            "source_check": "源码可见调用；运行时可达性未验证。",
            "assessment": "方向与端点职责一致。",
            "concerns": ["运行时可达性未知"],
            "risk": "中",
            "suggestion": "DEFERRED",
            "suggestion_reason": "关系解释仍需核对。",
        },
    }
    return prepare_batch_review(_explorer(), _node_definition(), _definition(), analysis)


def _freeze_fixture(batch, decisions):
    return confirm_batch_review(batch, decisions, final_decision_list=render_final_decision_list(batch, decisions))


def test_final_decision_list_names_every_object_and_full_approval_scope():
    batch = _batch()
    decisions = {
        "candidate-node::worker-entry": {"result": "APPROVED", "reason": "认可完整 Packet 的静态范围", "scope": "FULL_PACKET"},
        "candidate-collaboration::relationship-under-review": {"result": "PENDING", "reason": "Owner 未作决定"},
    }
    final_list = render_final_decision_list(batch, decisions)
    assert "Worker 入口" in final_list
    assert "candidate-node::worker-entry" in final_list
    assert "认可完整 Packet 的静态范围" in final_list
    assert "完整 Packet 的全部 Claims" in final_list
    assert "RESPONSIBILITY" in final_list
    assert "Runtime reachability remains unknown." in final_list
    assert "Supervisor 到 Worker 的交接" in final_list
    assert "Owner 未作决定" in final_list
    assert "不批准任何 Claims" in final_list
    record = confirm_batch_review(batch, decisions, final_decision_list=final_list)
    assert validate_batch_confirmation(record, batch) == decisions
    with pytest.raises(ExplorerInputError):
        confirm_batch_review(batch, decisions, final_decision_list=final_list.replace("APPROVED", "REJECTED"))


def test_partial_or_unspecified_approval_scope_cannot_be_frozen():
    batch = _batch()
    decisions = {key: {"result": "PENDING", "reason": "Owner 未作决定"} for key in batch["objects"]}
    key = "candidate-node::worker-entry"
    decisions[key] = {"result": "APPROVED", "reason": "只批准职责"}
    with pytest.raises(ExplorerInputError):
        render_final_decision_list(batch, decisions)
    decisions[key]["scope"] = "RESPONSIBILITY_ONLY"
    with pytest.raises(ExplorerInputError):
        render_final_decision_list(batch, decisions)


def test_checklist_contains_complete_review_appendix_without_raw_json_requirement():
    checklist = render_batch_checklist(_batch())
    appendix = checklist.split("## 审核附录", 1)[1]
    assert "IDENTITY" in appendix and "RESPONSIBILITY" in appendix
    assert "SOURCE_ASSIGNMENT" in appendix and "DEPENDENCIES_AND_LIMITS" in appendix
    assert "09aaf4a033123c7eca8d280fa8982e5b0e711b86" in appendix
    assert "evidence:sha256:" in appendix
    assert "完整 Packet 的全部 Claims" in appendix
    assert "零正式架构依赖" in appendix


def test_batch_owner_can_confirm_explicit_per_object_decisions_and_pending():
    batch = _batch()
    assert len(batch["objects"]) == 2
    assert batch["objects"]["candidate-node::worker-entry"]["packet"]["claims"]
    decisions = {
        "candidate-node::worker-entry": {"result": "REJECTED", "reason": "证据未覆盖职责", "rejection_basis": {"claim_kinds": ["RESPONSIBILITY"]}},
        "candidate-collaboration::relationship-under-review": {"result": "DEFERRED", "reason": "待核对运行时"},
    }
    record = _freeze_fixture(batch, decisions)
    assert validate_batch_confirmation(record, batch) == decisions
    assert record["decisions"]["candidate-collaboration::relationship-under-review"]["result"] == "DEFERRED"
    assert list(decided_objects(record, batch)) == ["candidate-node::worker-entry"]
    checklist = render_batch_checklist(batch)
    assert "AI 建议" in checklist and "Owner 决定：Pending" in checklist
    assert "审核主题" in checklist and "证据 1" in checklist and "运行时路径未知" in checklist
    main_reading = checklist.split("## 审核附录")[0]
    assert "candidate-node::worker-entry" not in main_reading
    assert batch["batch_sha256"] not in main_reading
    assert "The source supports this bounded responsibility." in main_reading
    assert main_reading.index("### 1. Worker 入口") < main_reading.index("Supervisor 到 Worker 的交接")
    assert '"kind":' not in main_reading
    assert record["checklist_sha256"]
    with pytest.raises(ExplorerInputError):
        confirm_batch_review(batch, decisions, final_decision_list="AI 建议全部批准")
    with pytest.raises(ExplorerInputError):
        _freeze_fixture(batch, {"candidate-node::worker-entry": decisions["candidate-node::worker-entry"]})


def test_confirmation_rejects_changed_packet_claims_evidence_snapshot_or_decision():
    batch = _batch()
    decisions = {key: {"result": "DEFERRED", "reason": "待审"} for key in batch["objects"]}
    record = _freeze_fixture(batch, decisions)
    for field, value in (("object_key", "changed"), ("claims", "changed"), ("candidate_evidence", "changed"), ("reviewed_source_snapshot", "changed")):
        altered = deepcopy(batch)
        altered["objects"]["candidate-node::worker-entry"]["packet"][field] = value
        with pytest.raises(ExplorerInputError):
            validate_batch_confirmation(record, altered)
    altered_record = deepcopy(record)
    altered_record["decisions"]["candidate-node::worker-entry"]["result"] = "APPROVED"
    with pytest.raises(ExplorerInputError):
        validate_batch_confirmation(altered_record, batch)
    altered_record = deepcopy(record)
    altered_record["checklist_sha256"] = "0" * 64
    with pytest.raises(ExplorerInputError):
        validate_batch_confirmation(altered_record, batch)
    altered = deepcopy(batch)
    altered["objects"]["candidate-node::worker-entry"]["analysis"]["suggestion"] = "REJECTED"
    with pytest.raises(ExplorerInputError):
        validate_batch_confirmation(record, altered)
    with pytest.raises(ExplorerInputError):
        prepare_batch_review(_explorer(), _node_definition(), _definition(), {})


def test_decomposition_can_be_reviewed_while_endpoint_nodes_are_pending():
    key = "candidate-decomposition::relationship-under-review"
    batch = prepare_batch_review(
        _explorer(semantic_family="candidate-decomposition"),
        None,
        _definition(kind="candidate-decomposition"),
        {key: {"topic": "受控交接", "display_name": "Worker 分解", "source_check": "静态源码已核对", "assessment": "运行时未知", "concerns": ["运行时未知"], "risk": "中", "suggestion": "REJECTED", "suggestion_reason": "解释与证据不符。"}},
    )
    assert batch["objects"][key]["packet"]["endpoint_bindings"]["source"]["node_id"] == "supervisor-boundary"
    record = _freeze_fixture(batch, {key: {"result": "APPROVED", "reason": "Owner 独立判断", "scope": "FULL_PACKET"}})
    assert decided_objects(record, batch)[key]["result"] == "APPROVED"


def test_offline_public_workflow_writes_checklist_then_requires_explicit_confirmation(tmp_path):
    inputs = {
        "explorer.json": _explorer(),
        "node.json": _node_definition(),
        "relationship.json": _definition(),
        "analysis.json": {key: item["analysis"] for key, item in _batch()["objects"].items()},
    }
    for name, value in inputs.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "review"
    assert batch_main([
        "prepare", "--explorer-data", str(tmp_path / "explorer.json"),
        "--node-definitions", str(tmp_path / "node.json"),
        "--relationship-definitions", str(tmp_path / "relationship.json"),
        "--ai-analysis", str(tmp_path / "analysis.json"),
        "--output-directory", str(output),
    ]) == 0
    batch = json.loads((output / "batch-review.json").read_text(encoding="utf-8"))
    assert "Owner 决定：Pending" in (output / "checklist.md").read_text(encoding="utf-8")
    decisions = {key: {"result": "DEFERRED", "reason": "等待核对"} for key in batch["objects"]}
    (tmp_path / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    final_list_path = output / "final-decisions.md"
    assert batch_main(["prepare-decisions", "--batch", str(output / "batch-review.json"), "--decisions", str(tmp_path / "decisions.json"), "--output", str(final_list_path)]) == 0
    assert "Worker 入口" in final_list_path.read_text(encoding="utf-8")
    args = ["confirm", "--batch", str(output / "batch-review.json"), "--decisions", str(tmp_path / "decisions.json"), "--final-list", str(final_list_path), "--output", str(output / "confirmation.json")]
    checklist_path = output / "checklist.md"
    original = checklist_path.read_text(encoding="utf-8")
    checklist_path.write_text(original + "\n伪造的额外说明\n", encoding="utf-8")
    with pytest.raises(ExplorerInputError):
        batch_main(args)
    checklist_path.write_bytes(original.encode("utf-8"))
    changed = deepcopy(decisions)
    changed["candidate-node::worker-entry"] = {"result": "APPROVED", "reason": "新增批准", "scope": "FULL_PACKET"}
    (tmp_path / "decisions.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ExplorerInputError):
        batch_main(args)
    confused = {"candidate-node::wrong-object": decisions["candidate-node::worker-entry"],
                "candidate-collaboration::relationship-under-review": decisions["candidate-collaboration::relationship-under-review"]}
    (tmp_path / "decisions.json").write_text(json.dumps(confused), encoding="utf-8")
    with pytest.raises(ExplorerInputError):
        batch_main(args)
    (tmp_path / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    assert batch_main(args) == 0
    record = json.loads((output / "confirmation.json").read_text(encoding="utf-8"))
    assert record["checklist_sha256"] == hashlib.sha256(checklist_path.read_bytes()).hexdigest()
    assert decided_objects(record, batch) == {}
