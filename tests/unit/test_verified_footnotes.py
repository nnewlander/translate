import json

from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    footnote_numbers_for_unit,
    load_exact_verified_evidence_registry,
)


def test_exact_verified_marker_reaches_request_evidence() -> None:
    records = [{
        "verification_status": "VERIFIED", "unit_uid": "unit-a",
        "exact_source_marker_token": "wheels.2", "footnote_number": "2",
    }]
    assert footnote_numbers_for_unit(records, unit_uid="unit-a", source_text="You risk reinventing wheels.2") == ["2"]


def test_same_raised_style_or_stale_marker_cannot_supply_evidence() -> None:
    records = [{
        "verification_status": "VERIFIED", "unit_uid": "unit-a",
        "exact_source_marker_token": "wheels.2", "footnote_number": "2",
    }]
    assert footnote_numbers_for_unit(records, unit_uid="unit-b", source_text="You risk reinventing wheels.2") == []
    assert footnote_numbers_for_unit(records, unit_uid="unit-a", source_text="You risk reinventing wheels.") == []
    unverified = [{**records[0], "verification_status": "PENDING"}]
    assert footnote_numbers_for_unit(unverified, unit_uid="unit-a", source_text="You risk reinventing wheels.2") == []


def test_registry_routes_only_exact_work_order_and_unit_across_chapters(tmp_path) -> None:
    def artifact(path, record) -> None:
        path.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    chapter_3 = tmp_path / "chapter_3.json"
    chapter_4 = tmp_path / "chapter_4.json"
    artifact(chapter_3, {"verification_status": "VERIFIED", "logical_work_order_id": "body.p.01717", "unit_uid": "chapter-3", "exact_source_marker_token": "text.5", "footnote_number": "5"})
    artifact(chapter_4, {"verification_status": "VERIFIED", "logical_work_order_id": "body.p.01861", "unit_uid": "chapter-4", "exact_source_marker_token": "database.5", "footnote_number": "5"})
    registry = load_exact_verified_evidence_registry([chapter_3, chapter_4])
    assert exact_evidence_for_work_order(registry, logical_work_order_id="body.p.01717", unit_uid="chapter-3")[0]["exact_source_marker_token"] == "text.5"
    assert exact_evidence_for_work_order(registry, logical_work_order_id="body.p.01861", unit_uid="chapter-4")[0]["exact_source_marker_token"] == "database.5"
    assert exact_evidence_for_work_order(registry, logical_work_order_id="body.p.01861", unit_uid="chapter-3") == ()
    assert exact_evidence_for_work_order(registry, logical_work_order_id="body.p.01931", unit_uid="none") == ()
