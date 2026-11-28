"""Summarize the Issue #101 JSONL and diagnostic traces with the standard library."""
from collections import Counter, defaultdict
from pathlib import Path
import json
import re
from statistics import median, stdev
import sys


SITE_FILES = {
    "explorer-data.json", "explorer-data.js", "index.html", "t4-state.js",
    "app.js", "styles.css",
}
APPROVED_ONLY_SITES = {"site-missing-node-state", "generic-v2-site"}
COMMON_SITES = {"site", "generic-v1-site", "material-input-site", "historical-site"}


DURATION = re.compile(r"^([0-9.]+)s\s+(setup|call|teardown)\s+(.*)$", re.MULTILINE)
CATEGORIES = {
    "git_fixture": "Git fixture",
    "source": "Broad Source Facts",
    "relationship_scan": "Narrow Continuity scan",
    "relationship_publish": "T3 relationship publication (remainder)",
    "relationship.publish": "T3 relationship publication (remainder)",
    "relationship.file_hashes": "T3 relationship publication (remainder)",
    "relationship.scan": "Narrow Continuity scan",
    "relationship.powershell_ast": "Narrow Continuity scan",
    "navigation": "Projection assembly",
    "candidate_fragments": "Projection assembly",
    "node_projection": "Projection assembly",
    "ui_projection": "Projection assembly",
    "site": "Site serialization and writes",
    "relationship_state": "Review State, import and output",
    "node_state": "Review State, import and output",
    "import_node_review": "Review State, import and output",
    "cli.json": "Review State, import and output",
}


def stats(values):
    return {
        "samples": values,
        "n": len(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
        "span": max(values) - min(values),
        "stdev_sample": stdev(values) if len(values) > 1 else None,
    }


def load_batch(directory, site_inventory):
    items = [
        json.loads(line)
        for line in (directory / "runs.jsonl").read_text(encoding="utf-8-sig").splitlines()
    ]
    assert items and all(item["exitCode"] == 0 for item in items)
    assert {item["stdout"] for item in items} == set(site_inventory)
    for item in items:
        stdout = (directory / item["stdout"]).read_text(encoding="utf-8-sig")
        stderr = (directory / item["stderr"]).read_text(encoding="utf-8-sig")
        assert "1 passed" in stdout and not stderr
        stages = {
            stage: float(value)
            for value, stage, node in DURATION.findall(stdout)
            if node.rstrip() == item["node"]
        }
        assert set(stages) == {"setup", "call", "teardown"}, (item, stdout)
        item["pytestDurationsRounded"] = stages
        expected = COMMON_SITES | (
            APPROVED_ONLY_SITES if item["variant"] == "approved" else set()
        )
        sites = site_inventory[item["stdout"]]
        assert set(sites) == expected, (item, sites, expected)
        for sizes in sites.values():
            assert set(sizes) == SITE_FILES and all(size > 0 for size in sizes.values())
        item["generatedSiteFileSizes"] = sites
    return items

def classify(phase):
    return CATEGORIES.get(phase, CATEGORIES.get(phase.split(".", 1)[0],
                                                "Other test and CLI work"))


def trace_result(path):
    trace = json.loads(path.read_text(encoding="utf-8"))
    events = trace["events"]
    assert len(events) > 0
    direct_children = Counter()
    phase_totals = defaultdict(Counter)
    category_totals = Counter()
    cli = []
    for event in events:
        if event["parent"] is not None:
            parent = events[event["parent"]]
            assert parent["start_ns"] <= event["start_ns"] <= event["end_ns"] <= parent["end_ns"]
            direct_children[parent["id"]] += event["inclusive_ns"]
        assert event["exclusive_ns"] >= 0
        phase = event["phase"]
        phase_totals[phase].update(count=1, inclusive_ns=event["inclusive_ns"],
                                   exclusive_ns=event["exclusive_ns"])
        category_totals[classify(phase)] += event["exclusive_ns"]
        if phase == "cli.command":
            cli.append({key: event[key] for key in ("id", "detail", "outcome", "inclusive_ns", "exclusive_ns")})
    for event in events:
        assert event["inclusive_ns"] - event["exclusive_ns"] == direct_children[event["id"]]
    duration = trace["end_ns"] - trace["start_ns"]
    gap = duration - sum(category_totals.values())
    assert gap >= 0
    category_totals["Other test and CLI work"] += gap
    broad_worktree = [event for event in events if event["phase"] == "source.scan"
                      and event["detail"] != "source"]
    broad_fixture = [event for event in events if event["phase"] == "source.scan"
                     and event["detail"] == "source"]
    assert len(broad_worktree) == 1
    git_labels = {"git_fixture.git", "source.git", "relationship_scan.git", "relationship_publish.git"}
    powershell_labels = {"source.powershell_ast", "relationship.powershell_ast"}
    subsets = {
        "broad_scan_worktree": broad_worktree,
        "broad_scan_fixture": broad_fixture,
        "git_commands_all": [event for event in events if event["phase"] in git_labels],
        "powershell_ast_all": [event for event in events if event["phase"] in powershell_labels],
    }
    subgroup_totals = {
        name: {"count": len(group), "inclusive_ns": sum(event["inclusive_ns"] for event in group)}
        for name, group in subsets.items()
    }
    projection = sum(event["inclusive_ns"] for event in events if event["phase"] == "ui_projection")
    site = phase_totals["site.write"]["inclusive_ns"]
    assert site - projection == category_totals["Site serialization and writes"]
    return {
        "call_ns": duration, "trace_residual_outside_wrappers_ns": gap,
        "phases": dict(phase_totals), "categories_exclusive_ns": dict(category_totals),
        "cli": cli,
        "subgroups": subgroup_totals,
    }


def main():
    root = Path(__file__).resolve().parent
    baseline_path = root / "baseline-b01"
    diag_path = root / "diagnostic-d01"
    evidence = json.loads((root / "site-files.json").read_text(encoding="utf-8"))
    inventory = evidence["batches"]
    baseline = load_batch(baseline_path, inventory[baseline_path.name])
    diagnostic = load_batch(diag_path, inventory[diag_path.name])
    assert [item["order"] for item in baseline if item["pytestDurationsRounded"]["setup"] == 0.01] == [6, 7]
    assert all(
        item["pytestDurationsRounded"]["setup"] == (0.01 if item["order"] in {6, 7} else 0)
        and item["pytestDurationsRounded"]["teardown"] == 0
        and (item["order"] not in {6, 7} or item["variant"] == "rejected")
        for item in baseline
    )
    for item in diagnostic:
        path = diag_path / item["stdout"].replace("-stdout.txt", "-trace.json")
        item["traceSummary"] = trace_result(path)
        phases = item["traceSummary"]["phases"]
        approved = item["variant"] == "approved"
        for label, count in {
            "git_fixture.git": 5,
            "git_fixture.extra_copy": 18,
            "source.git": 44 if approved else 36,
            "source.powershell_ast": 11 if approved else 9,
            "relationship.powershell_ast": 10 if approved else 8,
            "site.json": 6 if approved else 4,
            "source.scan": 11 if approved else 9,
            "relationship.scan": 10 if approved else 8,
            "relationship.publish": 9 if approved else 7,
            "ui_projection": 6 if approved else 4,
            "site.write": 6 if approved else 4,
        }.items():
            assert phases[label]["count"] == count, (label, phases[label]["count"], count)
        assert sum(event["outcome"] == "ExplorerInputError" for event in item["traceSummary"]["cli"]) == (11 if approved else 4)
    all_samples = baseline + diagnostic
    assert len({item["basetemp"] for item in all_samples}) == len(all_samples)
    assert {item["commit"] for item in all_samples} == {evidence["baseline"]}
    assert {item["mode"] for item in baseline} == {"baseline"}
    assert {item["mode"] for item in diagnostic} == {"diagnostic"}
    variant_order = ["approved", "rejected", "rejected", "approved"]
    assert [item["variant"] for item in baseline] == (variant_order * 3)[:10]
    paired = defaultdict(float)
    for item in baseline:
        paired[item["group"]] += item["seconds"]
    def selected(items, variant):
        return [item for item in items if item["variant"] == variant]

    baseline_stats = {}
    diag_stats = {}
    for variant in ("approved", "rejected"):
        formal = selected(baseline, variant)
        assert len(formal) == 5
        traces = selected(diagnostic, variant)
        assert len(traces) == 3
        baseline_stats[variant] = {
            "wall_seconds": stats([item["seconds"] for item in formal]),
            "stages_seconds": {
                phase: stats([item["pytestDurationsRounded"][phase] for item in formal])
                for phase in ("setup", "call", "teardown")
            },
        }
        phase_names = sorted({name for item in traces for name in item["traceSummary"]["phases"]})
        category_names = sorted({name for item in traces for name in item["traceSummary"]["categories_exclusive_ns"]})
        diag_stats[variant] = {
            "wall_seconds": stats([item["seconds"] for item in traces]),
            "traced_call_seconds": stats([item["traceSummary"]["call_ns"] / 1e9 for item in traces]),
            "phases": {
                name: {
                    field: stats([item["traceSummary"]["phases"].get(name, {}).get(field, 0) / factor
                                  for item in traces])
                    for field, factor in (("count", 1), ("inclusive_ns", 1e9), ("exclusive_ns", 1e9))
                }
                for name in phase_names
            },
            "subgroups": {
                name: {
                    "count": stats([item["traceSummary"]["subgroups"][name]["count"] for item in traces]),
                    "seconds": stats([item["traceSummary"]["subgroups"][name]["inclusive_ns"] / 1e9
                                      for item in traces]),
                }
                for name in traces[0]["traceSummary"]["subgroups"]
            },
            "categories_exclusive_seconds": {
                name: stats([item["traceSummary"]["categories_exclusive_ns"].get(name, 0) / 1e9
                             for item in traces])
                for name in category_names
            },
        }
    data = {
        "baseline_samples": baseline,
        "baseline": baseline_stats,
        "paired_wall_seconds": stats(list(paired.values())),
        "diagnostic_samples": diagnostic,
        "diagnostic": diag_stats,
        "diagnostic_phase_units": "Counts are calls per test. Inclusive and exclusive fields are converted from ns to seconds.",
    }
    result = json.dumps(data, indent=2) + "\n"
    if len(sys.argv) > 2:
        raise SystemExit("usage: recompute.py [output.json]")
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()

