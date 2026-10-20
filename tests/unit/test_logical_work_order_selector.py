from booktrans.harness.logical_work_order_selector import select_final_logical_work_orders


def test_final_scope_selector_keeps_standalones_and_counts_composite_once():
    selected = select_final_logical_work_orders(
        ["body.one", "body.composite", "body.two"],
        [
            {"display_id": "footer.one"},
            {"display_id": "footer.two"},
            {"display_id": "footer.three"},
            {"display_id": "footer.composite"},
        ],
        [{"group_id": "modules_dependencies", "members": ["footer.composite", "body.composite"]}],
    )

    assert [item.work_order_id for item in selected] == [
        "body.one",
        "composite.modules_dependencies",
        "body.two",
        "footer.one",
        "footer.two",
        "footer.three",
    ]
    assert selected[1].member_display_ids == ("footer.composite", "body.composite")
    assert len(selected) == 6  # seven physical blocks, one two-member composite
