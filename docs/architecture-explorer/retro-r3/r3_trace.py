"""Temporary Issue #101 trace. Load only in separate diagnostic pytest runs."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
import importlib
import json
import os
import shutil
import sys
from time import perf_counter_ns

import pytest


class Trace:
    def __init__(self, patch):
        self.patch = patch
        self.events = []
        self.active = []
        self.fixture_phase = "initial"
        self.fixture_copy_count = 0

    def wrap(self, module, name, label, describe=None):
        original = getattr(module, name)

        @wraps(original)
        def wrapped(*args, **kwargs):
            detail = describe(*args, **kwargs) if describe else None
            if detail is False:
                return original(*args, **kwargs)
            parent = self.active[-1] if self.active else None
            index = len(self.events)
            event = {
                "id": index,
                "parent": parent,
                "phase": label,
                "detail": detail,
                "outcome": "returned",
            }
            self.events.append(event)
            self.active.append(index)
            event["start_ns"] = perf_counter_ns()
            try:
                return original(*args, **kwargs)
            except BaseException as error:
                event["outcome"] = type(error).__name__
                raise
            finally:
                event["end_ns"] = perf_counter_ns()
                assert self.active.pop() == index

        self.patch.setattr(module, name, wrapped)

    def record_fixture_commit(self, *_args, **_kwargs):
        self.fixture_phase = "committed"
        return None

    def select_fixture_copy(self, _source, destination, **_kwargs):
        if self.fixture_phase != "extra":
            return False
        self.fixture_copy_count += 1
        return Path(destination).name

    def install(self, test):
        prefix = "booktrans.architecture_explorer."
        cli = importlib.import_module(prefix + "review_cli")
        scanner = importlib.import_module(prefix + "source_scanner")
        continuity = importlib.import_module(prefix + "continuity")
        candidate = importlib.import_module(prefix + "candidate_scanner")
        mapping = importlib.import_module(prefix + "review_mapping")
        generator = importlib.import_module(prefix + "generator")
        projection = importlib.import_module(prefix + "view_projection")
        fixture = sys.modules["t5_lite_fixture"]
        published = sys.modules["test_t8_node_review"]

        self.wrap(test, "create_source_repository", "git_fixture.create")
        original_create = test.create_source_repository

        @wraps(original_create)
        def start_extra_copies(*args, **kwargs):
            repository = original_create(*args, **kwargs)
            self.fixture_phase = "extra"
            return repository

        self.patch.setattr(test, "create_source_repository", start_extra_copies)
        self.wrap(test, "commit", "git_fixture.extra_commit", self.record_fixture_commit)
        self.wrap(fixture, "git", "git_fixture.git",
                  lambda _repository, *args: args[0] if args[0] != "-c" else "commit")
        self.wrap(shutil, "copyfile", "git_fixture.extra_copy", self.select_fixture_copy)
        self.wrap(test, "_published_explorer", "import_fixture.explorer")

        for module, name in ((cli, "_main"), (test, "_review_main_with_test_policy")):
            self.wrap(module, name, "cli.command", describe_cli)
        self.wrap(cli, "_import_node_review_from_github", "import_node_review")
        self.wrap(cli, "update_review_state", "relationship_state.update")
        self.wrap(cli, "_update_node_review_state", "node_state.v1_update")
        self.wrap(test, "update_node_review_v2_state", "node_state.v2_update")

        for module in (cli, published):
            self.wrap(module, "publish_reviewed_explorer", "relationship.publish",
                      lambda repository, *_args, **_kwargs: Path(repository).name)
            self.wrap(module, "scan_python_source_facts", "source.scan",
                      lambda repository: Path(repository).name)
            self.wrap(module, "attach_python_source_facts", "source.attach")
            self.wrap(module, "apply_navigation", "navigation.apply")
            self.wrap(module, "apply_candidate_fragments", "candidate_fragments.apply")
        self.wrap(mapping, "_production_continuity", "relationship.file_hashes")
        self.wrap(continuity, "scan_supervisor_worker_candidate", "relationship.scan")
        self.wrap(scanner, "_scan_module", "source.python_module",
                  lambda _repository, relative: relative)
        self.wrap(scanner, "_definition_index", "source.definition_index")
        self.wrap(scanner, "_call_facts", "source.call_facts")
        self.wrap(scanner, "_scan_supervisor", "source.supervisor")
        self.wrap(scanner, "_powershell_ast", "source.powershell_ast")
        self.wrap(candidate, "_powershell_ast", "relationship.powershell_ast")

        for module, label in ((scanner, "source"), (candidate, "relationship_scan"),
                              (mapping, "relationship_publish")):
            self.wrap(module, "_git", label + ".git",
                      lambda _repository, *args: args[0])

        self.wrap(cli, "_project_legacy_node_review_v1", "node_projection.legacy")
        self.wrap(cli, "_project_node_review_inputs", "node_projection.generic")
        self.wrap(projection, "apply_view_projection", "ui_projection")
        self.wrap(cli, "write_site", "site.write",
                  lambda _data, directory, _assets: Path(directory).name)
        self.wrap(generator, "write_json", "site.json")
        self.wrap(cli, "write_json", "cli.json")

    def finish(self):
        assert not self.active
        for event in reversed(self.events):
            event["inclusive_ns"] = event["end_ns"] - event["start_ns"]
            event["exclusive_ns"] = event["inclusive_ns"] - event.pop("children_ns", 0)
            parent = event["parent"]
            if parent is not None:
                ancestor = self.events[parent]
                ancestor["children_ns"] = ancestor.get("children_ns", 0) + event["inclusive_ns"]


def describe_cli(args, **_kwargs):
    label = args[0]
    if "--output-directory" in args:
        value = args[args.index("--output-directory") + 1]
        label += ":" + value
    if "--node-review-state" in args and label == "update-node-review-state":
        label += ":" + Path(args[args.index("--node-review-state") + 1]).name
    return label


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    # Share pytest's fixture so the test's own patches and the probe unwind in LIFO order.
    trace = Trace(item.funcargs["monkeypatch"])
    trace.install(item.module)
    start = perf_counter_ns()
    try:
        return (yield)
    finally:
        end = perf_counter_ns()
        trace.finish()
        Path(os.environ["R3_DIAG_OUT"]).write_text(
            json.dumps({
                "node": item.nodeid,
                "start_ns": start,
                "end_ns": end,
                "events": trace.events,
            }, indent=2),
            encoding="utf-8",
        )

