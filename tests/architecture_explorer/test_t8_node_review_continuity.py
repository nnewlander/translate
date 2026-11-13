from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.generator import write_site
from booktrans.architecture_explorer.node_review_continuity import (
    _evaluate_node_review_state,
    _update_node_review_state,
)
from booktrans.architecture_explorer.node_review import _project_node_review
from booktrans.architecture_explorer.source_scanner import scan_python_source_facts
from test_t8_node_review import (
    _fixture_materials,
    _fixture_policy,
    _published_explorer,
    _target,
)
from test_t7_top_level_graph import (
    WEB_ASSETS,
    _assert_browser_dom,
    _desktop_browser,
    _dump_dom,
)


def test_explicit_update_persists_independent_current_node_review_state(
    tmp_path: Path,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "node-review-state.json"

    state = _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-13T08:00:00Z",
        trust_policy=_fixture_policy(),
    )

    assert json.loads(state_path.read_text(encoding="utf-8")) == state
    assert state == {
        "schema": "architecture-explorer.node-review-state.v1",
        "object_key": {
            "kind": "candidate-node",
            "parent_module_id": "production-control",
            "node_id": "worker-launch-boundary",
        },
        "decision_id": "github-comment:999999002",
        "state": "current",
        "continuity": "exact-match",
        "evaluated_source_snapshot": {
            "revision": explorer["source_facts"]["source_snapshot"]["revision"],
            "worktree_state": explorer["source_facts"]["source_snapshot"]["worktree"]["state"],
            "source_facts_sha256": explorer["source_facts"]["deterministic_facts_sha256"],
        },
        "assessed_at": "2026-11-13T08:00:00Z",
        "reasons": [],
        "current_evidence_locations": [
            {
                "evidence_id": evidence_id,
                "path": record["path"],
                "line_start": record["current_location"]["line_start"],
                "line_end": record["current_location"]["line_end"],
            }
            for evidence_id in _target(explorer)["source_assignment"]["evidence_ids"]
            for record in _matching_evidence(explorer, evidence_id)
        ],
    }


def _matching_evidence(explorer: dict, evidence_id: str) -> list[dict]:
    matches: list[dict] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            evidence = value.get("evidence")
            if isinstance(evidence, dict) and evidence.get("evidence_id") == evidence_id:
                matches.append(evidence)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(explorer["source_facts"])
    assert len(matches) == 1
    return matches


def _source_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "source-repository"
    for relative_path in (
        "scripts/production/production_campaign_supervisor.ps1",
        "scripts/production/run_qwen_deterministic_worker.py",
    ):
        source = Path(__file__).resolve().parents[2] / relative_path
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    unrelated = repository / "src/booktrans/unrelated_fixture.py"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("VALUE = 1\n", encoding="utf-8")
    for arguments in (
        ("init",),
        ("config", "user.name", "Architecture Explorer Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("add", "."),
        ("commit", "-m", "fixture baseline"),
    ):
        subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    return repository


def test_evidence_content_change_records_material_invalidation(tmp_path: Path) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result="APPROVED")
    repository = _source_fixture(tmp_path)
    supervisor = repository / "scripts/production/production_campaign_supervisor.ps1"
    text = supervisor.read_text(encoding="utf-8-sig")
    supervisor.write_text(
        text.replace(
            '"scripts\\production\\run_qwen_deterministic_worker.py"',
            '"scripts\\production\\run_qwen_deterministic_worker_v2.py"',
            1,
        ),
        encoding="utf-8",
    )
    explorer["source_facts"] = scan_python_source_facts(repository)

    state = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:01:00Z",
        trust_policy=_fixture_policy(),
    )

    assert state["state"] == "needs-review"
    assert state["continuity"] == "material-change"
    assert state["reasons"] == ["candidate-evidence:material-change"]


def test_recorded_node_invalidation_survives_source_restoration(tmp_path: Path) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result="APPROVED")
    state_path = tmp_path / "node-review-state.json"
    changed = _published_explorer()
    changed["generation_inputs"]["navigation"]["sha256"] = "0" * 64

    invalid = _update_node_review_state(
        changed,
        decision,
        source,
        state_path,
        assessed_at="2026-11-13T08:02:00Z",
        trust_policy=_fixture_policy(),
    )
    restored = _update_node_review_state(
        explorer,
        decision,
        source,
        state_path,
        assessed_at="2026-11-13T08:03:00Z",
        trust_policy=_fixture_policy(),
    )

    assert invalid["state"] == "needs-review"
    assert restored["state"] == "needs-review"
    assert restored["continuity"] == "material-change"
    assert restored["reasons"] == ["versioned-input:navigation_sha256"]


@pytest.mark.parametrize(
    ("result", "projection"),
    (("APPROVED", "Historical · Approved"), ("REJECTED", "Historical · Rejected")),
    ids=("approved-history", "rejected-history"),
)
def test_invalidated_decisions_project_as_distinct_history(
    tmp_path: Path,
    result: str,
    projection: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result=result)
    changed = _published_explorer()
    changed["generation_inputs"]["candidate_fragments"][-2]["sha256"] = "0" * 64
    state_path = tmp_path / "node-review-state.json"
    _update_node_review_state(
        changed,
        decision,
        source,
        state_path,
        assessed_at="2026-11-13T08:04:00Z",
        trust_policy=_fixture_policy(),
    )

    state = _evaluate_node_review_state(decision, state_path)
    projected = _project_node_review(
        explorer,
        decision=decision,
        review_source=source,
        node_review_state=state,
    )

    assert _target(projected)["node_review"]["projection"] == projection
    assert _target(projected)["node_review"]["state"]["status"] == "needs-review"


@pytest.mark.parametrize("change", ("line-relocation", "unrelated-edit"))
def test_non_material_source_changes_keep_current_review(
    tmp_path: Path,
    change: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    repository = _source_fixture(tmp_path)
    if change == "line-relocation":
        for relative_path in (
            "scripts/production/production_campaign_supervisor.ps1",
            "scripts/production/run_qwen_deterministic_worker.py",
        ):
            path = repository / relative_path
            path.write_text("\n\n" + path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    else:
        unrelated = repository / "src/booktrans/unrelated_fixture.py"
        unrelated.write_text("VALUE = 2\n", encoding="utf-8")
    explorer["source_facts"] = scan_python_source_facts(repository)

    state = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:05:00Z",
        trust_policy=_fixture_policy(),
    )

    assert state["state"] == "current"
    assert state["continuity"] == "exact-match"
    assert state["reasons"] == []


@pytest.mark.parametrize("state_kind", ("missing", "corrupt", "inconsistent"))
def test_unusable_state_projects_unknown_not_pending(
    tmp_path: Path,
    state_kind: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    state_path = tmp_path / "node-review-state.json"
    if state_kind == "corrupt":
        state_path.write_text("{broken", encoding="utf-8")
    elif state_kind == "inconsistent":
        state_path.write_text(json.dumps({"schema": "nearby"}), encoding="utf-8")

    state = _evaluate_node_review_state(decision, state_path)
    projected = _project_node_review(
        explorer,
        decision=decision,
        review_source=source,
        node_review_state=state,
    )

    assert _target(projected)["node_review"]["projection"] == "Unknown"
    assert state["reasons"] == [f"node-review-state:{state_kind}"]


@pytest.mark.parametrize("state_kind", ("corrupt", "inconsistent"))
def test_explicit_update_never_overwrites_unusable_state(
    tmp_path: Path,
    state_kind: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    state_path = tmp_path / "node-review-state.json"
    content = b"{broken" if state_kind == "corrupt" else b'{"schema":"nearby"}'
    state_path.write_bytes(content)

    with pytest.raises(ExplorerInputError, match="refusing overwrite"):
        _update_node_review_state(
            explorer,
            decision,
            source,
            state_path,
            assessed_at="2026-11-13T08:06:00Z",
            trust_policy=_fixture_policy(),
        )

    assert state_path.read_bytes() == content


@pytest.mark.parametrize(
    ("failure", "expected_state", "expected_continuity", "expected_reason"),
    (
        ("missing", "needs-review", "missing", "candidate-evidence:missing"),
        ("ambiguous", "unknown", "ambiguous", "candidate-evidence:ambiguous"),
        ("parse-failure", "unknown", "parse-failure", "source-scan:parse-failure"),
    ),
)
def test_missing_ambiguous_and_parse_failure_degrade_conservatively(
    tmp_path: Path,
    failure: str,
    expected_state: str,
    expected_continuity: str,
    expected_reason: str,
) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    evidence_id = _target(explorer)["source_assignment"]["evidence_ids"][-1]
    if failure == "missing":
        explorer["source_facts"]["modules"] = [
            module
            for module in explorer["source_facts"]["modules"]
            if module.get("path") != "scripts/production/run_qwen_deterministic_worker.py"
        ]
    elif failure == "ambiguous":
        duplicate = deepcopy(_matching_evidence(explorer, evidence_id)[0])
        explorer["source_facts"]["modules"][0]["entries"].append(
            {"kind": "isolated-ambiguous-fixture", "evidence": duplicate}
        )
    else:
        explorer["_node_review_scan_error"] = "isolated parser failure"

    state = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:07:00Z",
        trust_policy=_fixture_policy(),
    )

    assert state["state"] == expected_state
    assert state["continuity"] == expected_continuity
    assert state["reasons"] == [expected_reason]


def test_node_review_requires_the_t3_relationship_own_current_gate(tmp_path: Path) -> None:
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    explorer["architecture"]["relationships"][0]["review_status"] = "needs-review"

    state = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:08:00Z",
        trust_policy=_fixture_policy(),
    )

    assert state["state"] == "needs-review"
    assert state["continuity"] == "missing"
    assert state["reasons"] == ["formal-dependency:missing"]


@pytest.mark.parametrize(
    ("result", "state_status", "continuity", "projection", "label", "reason"),
    (
        (
            "APPROVED",
            "needs-review",
            "material-change",
            "Historical · Approved",
            "曾批准 · 需复核",
            "candidate-evidence:material-change",
        ),
        (
            "REJECTED",
            "needs-review",
            "missing",
            "Historical · Rejected",
            "曾拒绝 · 需复核",
            "candidate-evidence:missing",
        ),
        (
            "APPROVED",
            "unknown",
            "ambiguous",
            "Unknown",
            "审核有效性未知",
            "candidate-evidence:ambiguous",
        ),
    ),
    ids=("historical-approved", "historical-rejected", "unknown"),
)
@pytest.mark.parametrize("viewport_width", (1440, 1280, 960), ids=("1440", "1280", "960"))
def test_real_browser_renders_historical_and_unknown_node_review_states(
    tmp_path: Path,
    viewport_width: int,
    result: str,
    state_status: str,
    continuity: str,
    projection: str,
    label: str,
    reason: str,
) -> None:
    browser_name, browser = _desktop_browser()
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer, result=result)
    current = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:09:00Z",
        trust_policy=_fixture_policy(),
    )
    projected = _project_node_review(
        explorer,
        decision=decision,
        review_source=source,
        node_review_state={
            "status": state_status,
            "continuity": continuity,
            "decision_id": decision["decision_id"],
            "reasons": [reason],
            "evaluated_source_snapshot": current["evaluated_source_snapshot"],
            "current_evidence_locations": current["current_evidence_locations"],
        },
    )
    site = tmp_path / "site"
    write_site(projected, site, WEB_ASSETS)
    shutil.copyfile(
        Path(__file__).resolve().parent / "t8_node_review_dom.js",
        site / "t8-node-review-dom.js",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            "<script>"
            f"window.T8_EXPECTED_PROJECTION={json.dumps(projection)};"
            f"window.T8_EXPECTED_REVIEW_LABEL={json.dumps(label)};"
            f"window.T8_EXPECTED_DECISION_RESULT={json.dumps(result)};"
            f"window.T8_EXPECTED_STATE={json.dumps(f'{state_status} · {continuity}')};"
            f"window.T8_EXPECTED_REASON={json.dumps(reason)};"
            "window.T8_EXPECTS_CURRENT_LOCATORS=true;"
            "</script><script src=\"t8-node-review-dom.js\"></script></body>",
        ),
        encoding="utf-8",
    )

    completed = _dump_dom(
        browser,
        page,
        tmp_path / "browser-profile",
        virtual_time_budget=4000,
        viewport_width=viewport_width,
        viewport_height=900,
    )
    _assert_browser_dom(
        completed,
        browser_name=browser_name,
        pass_marker='data-t8-node-review="pass"',
    )


def test_real_chrome_preserves_historical_node_review_during_resize_round_trip(
    tmp_path: Path,
) -> None:
    browser_name, browser = _desktop_browser()
    if browser_name != "chrome":
        pytest.skip("same-page node-review resize is a required Chrome acceptance test")
    explorer = _published_explorer()
    source, decision = _fixture_materials(explorer)
    current = _update_node_review_state(
        explorer,
        decision,
        source,
        tmp_path / "node-review-state.json",
        assessed_at="2026-11-13T08:10:00Z",
        trust_policy=_fixture_policy(),
    )
    projected = _project_node_review(
        explorer,
        decision=decision,
        review_source=source,
        node_review_state={
            "status": "needs-review",
            "continuity": "material-change",
            "decision_id": decision["decision_id"],
            "reasons": ["candidate-evidence:material-change"],
            "evaluated_source_snapshot": current["evaluated_source_snapshot"],
            "current_evidence_locations": current["current_evidence_locations"],
        },
    )
    site = tmp_path / "site"
    write_site(projected, site, WEB_ASSETS)
    evidence_path = Path(
        os.environ.get(
            "ARCHITECTURE_EXPLORER_VIEWPORT_EVIDENCE",
            str(tmp_path / "chrome-viewport-evidence.json"),
        )
    )
    completed = subprocess.run(
        (
            "node",
            str(Path(__file__).resolve().parent / "t7_continuous_resize.cjs"),
            str(browser),
            (site / "index.html").resolve().as_uri(),
            str(tmp_path / "continuous-resize-profile"),
            str(evidence_path),
        ),
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "1440x900 -> 1280x900 -> 960x900 -> 1440x900" in completed.stdout
    assert _target(projected)["node_review"]["projection"] == "Historical · Approved"
