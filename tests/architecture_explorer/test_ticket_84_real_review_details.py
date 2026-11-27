"""Real Ticket 84 host acceptance. Set ARCHITECTURE_EXPLORER_T84_REQUIRED=1 when running it explicitly."""

import json
import os
import shutil
from pathlib import Path

import pytest

from booktrans.architecture_explorer.batch_import import read_published_review_batch
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.view_projection import apply_view_projection
from test_t7_top_level_graph import _desktop_browser, _dump_dom


@pytest.fixture(scope="module")
def t84_host_inputs() -> tuple[Path, Path]:
    preview = os.environ.get("ARCHITECTURE_EXPLORER_T84_PREVIEW")
    batch = os.environ.get("ARCHITECTURE_EXPLORER_T84_BATCH")
    if not preview or not batch:
        message = "T84 host acceptance requires ARCHITECTURE_EXPLORER_T84_PREVIEW and ARCHITECTURE_EXPLORER_T84_BATCH"
        if preview or batch or os.environ.get("ARCHITECTURE_EXPLORER_T84_REQUIRED") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    batch_path = Path(batch).resolve()
    records, _ = read_published_review_batch(batch_path)
    assert len(records) == 3, "T84 host acceptance requires the three trusted reviews"
    return Path(preview).resolve(), batch_path


def test_view_projection_distinguishes_unknowns_from_an_older_omission(
    t84_host_inputs: tuple[Path, Path],
) -> None:
    preview, _ = t84_host_inputs
    data = json.loads((preview / "explorer-data.json").read_text(encoding="utf-8"))
    reviews = data["review_projection"]["objects"]
    for review in reviews.values():
        object_id = review["object_key"]["object_id"]
        shown = data["view_projection"]["relationship_index"][object_id]["review"]
        assert shown["retained_unknowns"] == review["retained_unknowns"]
    key = next(iter(reviews))
    review = reviews[key]
    object_id = review["object_key"]["object_id"]
    review["retained_unknowns"] = []
    assert apply_view_projection(data)["view_projection"]["relationship_index"][object_id]["review"]["retained_unknowns"] == []
    del review["retained_unknowns"]
    assert "retained_unknowns" not in apply_view_projection(data)["view_projection"]["relationship_index"][object_id]["review"]
    review.update(result="Pending", state="not-reviewed", approved_scope=[], retained_unknowns=[])
    with pytest.raises(ExplorerInputError):
        apply_view_projection(data)


def test_real_chrome_shows_independent_review_limits(
    tmp_path: Path, t84_host_inputs: tuple[Path, Path],
) -> None:
    preview, batch = t84_host_inputs
    browser_name, browser = _desktop_browser()
    assert browser_name == "chrome"
    site = tmp_path / "site"
    shutil.copytree(preview, site)
    decisions = {}
    for path in batch.glob("objects/*/decision.json"):
        decision = json.loads(path.read_text(encoding="utf-8"))
        source = json.loads(path.with_name("source.json").read_text(encoding="utf-8"))
        key = decision["object_key"]
        limits = next(c["value"] for c in decision["claims"] if c["kind"] == "DEPENDENCIES_AND_LIMITS")
        decisions[key.get("node_id", key.get("object_id"))] = {
            "unknowns": limits["retained_unknowns"],
            "claims": [claim["kind"] for claim in decision["claims"]],
            "decision_id": decision["decision_id"],
            "receipt": decision["source_receipt_sha256"],
            "author": source["comment"]["user"]["login"],
            "association": source["comment"]["author_association"],
            "permission": source["permission"]["permission"],
            "comment_url": source["comment"]["html_url"],
        }
    assert sorted(len(item["unknowns"]) for item in decisions.values()) == [2, 2, 8]
    script = """
(() => {
  const expect = (value, message) => { if (!value) throw new Error(message); };
  const card = id => document.querySelector(`[data-module-id="${CSS.escape(id)}"]`);
  const detail = document.querySelector("#detail-content");
  const close = () => document.querySelector(".dialog-close").click();
  const node = "worker-launch-boundary";
  const collab = "candidate-collaboration:supervisor-to-worker-wrapper";
  const decomp = "candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction";
  const pending = "candidate-decomposition:worker-launch-boundary-to-worker-production-entry-guard";
  const names = {
    [collab]: "启动边界指向 Worker 包装入口",
    [decomp]: "启动边界分解为进程启动构造",
    [pending]: "启动边界分解为生产入口守卫",
  };
  const same = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
  const fact = label => [...detail.querySelectorAll("dt")]
    .find(term => term.textContent === label)?.nextElementSibling;
  const checkSource = id => {
    const source = expected[id];
    expect(fact("可信来源")?.textContent === [source.author, source.association, source.permission].join(" · "), `${id}: wrong verified author or permission`);
    expect(fact("Decision ID")?.textContent === source.decision_id, `${id}: wrong Decision ID`);
    expect(fact("来源收据")?.textContent === source.receipt, `${id}: wrong Receipt`);
    expect(fact("GitHub 评论")?.querySelector("a")?.href === source.comment_url, `${id}: wrong GitHub comment link`);
  };
  const unknowns = () => {
    const heading = [...detail.querySelectorAll("h3, h4")].find(h => h.textContent === "仍然未知的事项");
    return [...(heading?.nextElementSibling?.querySelectorAll("li") || [])].map(li => li.textContent);
  };
  const openRelationship = id => {
    const buttons = [...document.querySelectorAll("#candidate-collaboration-list button")];
    const button = buttons.find(item => item.querySelector("h4")?.textContent === names[id]);
    expect(button, `missing relationship ${id}: ${buttons.map(item => item.querySelector("h4")?.textContent)}`);
    button.click();
    expect(detail.dataset.collaborationId === id, `opened wrong relationship ${id}`);
  };
  const checkApproved = id => {
    openRelationship(id);
    expect(detail.textContent.includes("已批准 · 当前有效"), `${id} is not current-approved`);
    expect(same(unknowns(), expected[id].unknowns), `wrong retained unknowns for ${id}: ${JSON.stringify(unknowns())}`);
    checkSource(id);
    expect(detail.textContent.includes(`批准范围：${id === collab ? 2 : 1} 条 Evidence · ${expected[id].decision_id}`), `${id}: wrong approved scope`);
    close();
  };
  try {
    card("production-control").click();
    card(node).dispatchEvent(new MouseEvent("contextmenu", {bubbles: true, cancelable: true, clientX: 40, clientY: 40}));
    document.querySelector("#context-evidence").click();
    expect(detail.querySelector("#detail-title")?.textContent === "Worker 启动边界", "wrong Node detail");
    const original = [...detail.querySelectorAll(".node-review-claim pre")]
      .map(pre => JSON.parse(pre.textContent)).find(value => value?.retained_unknowns);
    expect(same(original?.retained_unknowns, expected[node].unknowns), "the eight existing Node unknowns changed");
    expect(same([...detail.querySelectorAll(".node-review-claim h4:first-child")].map(h => h.textContent), expected[node].claims), "Node claims changed");
    close();
    checkApproved(collab);
    card(node).click();
    checkApproved(decomp);
    openRelationship(pending);
    expect(detail.textContent.includes("分解状态：未审核"), "an unreviewed relationship gained trust");
    expect(unknowns().length === 0, "an unreviewed relationship gained trusted unknowns");
    expect(!fact("可信来源") && !fact("Decision ID") && !fact("来源收据") && !fact("GitHub 评论"), "an unreviewed relationship gained a trusted source");
    close();
    document.querySelector("#back-button").click();
    card(node).dispatchEvent(new MouseEvent("contextmenu", {bubbles: true, cancelable: true, clientX: 40, clientY: 40}));
    document.querySelector("#context-evidence").click();
    checkSource(node);
    const allowedComments = Object.values(expected).map(item => item.comment_url);
    expect([...document.querySelectorAll("a[href]")].every(link =>
      link.protocol === "file:" || allowedComments.includes(link.href)),
      "unexpected or unsafe link in offline review detail");
    expect(performance.getEntriesByType("resource").every(entry => new URL(entry.name).protocol === "file:"),
      "offline review opened a remote resource");
    document.body.dataset.t84Review = "pass";
  } catch (error) {
    document.body.dataset.t84Review = error.message;
  }
})();
"""
    (site / "t84-review-check.js").write_text(
        "const expected = " + json.dumps(decisions) + ";\n" + script, encoding="utf-8",
    )
    page = site / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>", '<script src="t84-review-check.js"></script></body>',
        ),
        encoding="utf-8",
    )
    result = _dump_dom(browser, page, tmp_path / "profile", virtual_time_budget=4000)
    assert result.returncode == 0, result.stderr
    marker = next((line.strip() for line in result.stdout.splitlines() if "data-t84-review=" in line), "")
    assert 'data-t84-review="pass"' in marker, marker or "Chrome did not run the acceptance check"
