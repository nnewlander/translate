from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)


PROJECT = Path(__file__).resolve().parents[2]
FIXED_BASELINE = "14073edcea76a2b200bf15aaa4231c13f20e5ec9"
FIXED_BASELINE_MANAGED_PYTHON_SHA256 = (
    "e26712537ba6648237b39161328d62a385d13808f6977ca9bc434946b0c7efb2"
)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repository(tmp_path: Path, source: str) -> Path:
    repository = tmp_path / "source"
    module = repository / "src/booktrans/sample.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    _git(repository, "init", "--quiet")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Architecture Explorer Test",
        "-c",
        "user.email=explorer-test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=NUL",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return repository


def test_definition_index_describes_lexical_python_definitions(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "@registered\n"
        "class Outer:\n"
        "    async def run(self):\n"
        "        def nested():\n"
        "            return '雪'\n"
        "        return nested()\n\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            return None\n\n"
        "def top():\n"
        "    return 1\n\n"
        "def top():\n"
        "    return 2\n\n"
        "async def async_top():\n"
        "    return 3\n",
    )

    result = scan_python_source_facts(repository)

    index = result["python_definition_index"]
    assert index["schema"] == "architecture-explorer.python-definition-index.v1"
    assert index["summary"] == {
        "classes": 2,
        "functions_and_methods": 6,
        "definitions": 8,
    }
    assert index["scope"]["files_scanned"] == 1
    definitions = index["files"][0]["definitions"]
    assert definitions[0]["evidence"]["snippet"].startswith("@registered")
    assert "class Outer" in definitions[0]["evidence"]["snippet"]
    assert definitions[0]["evidence"]["current_location"]["line_start"] == 1
    assert [item["kind"] for item in definitions] == [
        "class",
        "method",
        "nested-function",
        "class",
        "method",
        "function",
        "function",
        "function",
    ]
    assert [item["qualified_symbol"] for item in definitions] == [
        "booktrans.sample.Outer",
        "booktrans.sample.Outer.run",
        "booktrans.sample.Outer.run.nested",
        "booktrans.sample.Outer.Inner",
        "booktrans.sample.Outer.Inner.method",
        "booktrans.sample.top",
        "booktrans.sample.top",
        "booktrans.sample.async_top",
    ]
    assert [item["occurrence"] for item in definitions if item["qualified_symbol"].endswith(".top")] == [1, 2]
    nested = definitions[2]
    assert nested["parent_symbol"] == "booktrans.sample.Outer.run"
    assert nested["evidence"]["snippet"].startswith("def nested()")
    assert "雪" in definitions[1]["evidence"]["snippet"]
    assert nested["evidence"]["current_location"] == {
        "line_start": 4,
        "column_start": 8,
        "line_end": 5,
        "column_end": 24,
    }
    assert nested["evidence"]["evidence_id"].startswith("evidence:sha256:")
    assert nested["evidence"]["normalized_snippet_sha256"]
    assert nested["evidence"]["file_sha256"] == index["files"][0]["file_sha256"]


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _rehash_definition_projection(source_facts: dict) -> None:
    index = source_facts["python_definition_index"]
    definitions = [
        definition
        for file in index["files"]
        for definition in file.get("definitions", [])
    ]
    classes = sum(item.get("kind") == "class" for item in definitions)
    index["scope"]["files_scanned"] = len(index["files"])
    index["summary"] = {
        "classes": classes,
        "functions_and_methods": len(definitions) - classes,
        "definitions": len(definitions),
    }
    index["deterministic_facts_sha256"] = _digest(
        {key: value for key, value in index.items() if key != "deterministic_facts_sha256"}
    )
    source_facts["deterministic_facts_sha256"] = _digest(
        {
            key: value
            for key, value in source_facts.items()
            if key != "deterministic_facts_sha256"
        }
    )


def test_definition_identity_ignores_lines_but_detects_material_source(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "def work():\n    return 1\n")
    first = scan_python_source_facts(repository)
    path = repository / "src/booktrans/sample.py"
    path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    moved = scan_python_source_facts(repository)
    path.write_text("\n\ndef work():\n    return 2\n", encoding="utf-8")
    changed = scan_python_source_facts(repository)

    first_definition = first["python_definition_index"]["files"][0]["definitions"][0]
    moved_definition = moved["python_definition_index"]["files"][0]["definitions"][0]
    changed_definition = changed["python_definition_index"]["files"][0]["definitions"][0]
    assert first_definition["evidence"]["evidence_id"] == moved_definition["evidence"]["evidence_id"]
    assert first_definition["evidence"]["file_sha256"] != moved_definition["evidence"]["file_sha256"]
    assert first_definition["evidence"]["current_location"] != moved_definition["evidence"]["current_location"]
    assert moved_definition["evidence"]["evidence_id"] != changed_definition["evidence"]["evidence_id"]


@pytest.mark.parametrize(
    "changed_source",
    [
        "def renamed():\n    return 1\n",
        "class Container:\n    def work(self):\n        return 1\n",
        "class work:\n    pass\n",
        "def work():\n    return 1\n\ndef work():\n    return 1\n",
    ],
)
def test_definition_identity_detects_name_parent_kind_and_duplicate_ambiguity(
    tmp_path: Path,
    changed_source: str,
) -> None:
    repository = _repository(tmp_path, "def work():\n    return 1\n")
    before = scan_python_source_facts(repository)
    path = repository / "src/booktrans/sample.py"
    path.write_text(changed_source, encoding="utf-8")
    after = scan_python_source_facts(repository)

    before_id = before["python_definition_index"]["files"][0]["definitions"][0][
        "evidence"
    ]["evidence_id"]
    after_ids = {
        item["evidence"]["evidence_id"]
        for item in after["python_definition_index"]["files"][0]["definitions"]
    }
    assert before_id not in after_ids


def test_nested_definition_identity_includes_full_lexical_occurrence_path(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def outer():\n"
        "    def child():\n"
        "        return 1\n"
        "    return child()\n\n"
        "def outer():\n"
        "    def child():\n"
        "        return 1\n"
        "    return child()\n\n"
        "class Box:\n"
        "    def child(self):\n"
        "        return 1\n\n"
        "class Box:\n"
        "    def child(self):\n"
        "        return 1\n",
    )

    definitions = scan_python_source_facts(repository)["python_definition_index"][
        "files"
    ][0]["definitions"]
    children = [
        item
        for item in definitions
        if item["qualified_symbol"]
        in {"booktrans.sample.outer.child", "booktrans.sample.Box.child"}
    ]
    assert len(children) == 4
    assert len({item["evidence"]["evidence_id"] for item in children}) == 4
    assert [item["lexical_occurrence_path"] for item in children] == [
        ["1-of-2", "1-of-1"],
        ["2-of-2", "1-of-1"],
        ["1-of-2", "1-of-1"],
        ["2-of-2", "1-of-1"],
    ]


def test_definition_index_fails_closed_for_invalid_utf8(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "def work():\n    return 1\n")
    (repository / "src/booktrans/sample.py").write_bytes(b"def work():\n    return '\xff'\n")

    with pytest.raises(ExplorerInputError, match="not UTF-8"):
        scan_python_source_facts(repository)


def test_attach_rejects_rehashed_incomplete_definition_fact(tmp_path: Path) -> None:
    source_facts = scan_python_source_facts(
        _repository(tmp_path, "def work():\n    return 1\n")
    )
    malformed = deepcopy(source_facts)
    del malformed["python_definition_index"]["files"][0]["definitions"][0][
        "parent_symbol"
    ]
    index_without_digest = {
        key: value
        for key, value in malformed["python_definition_index"].items()
        if key != "deterministic_facts_sha256"
    }
    malformed["python_definition_index"]["deterministic_facts_sha256"] = _digest(
        index_without_digest
    )
    facts_without_digest = {
        key: value
        for key, value in malformed.items()
        if key != "deterministic_facts_sha256"
    }
    malformed["deterministic_facts_sha256"] = _digest(facts_without_digest)
    explorer = {"deterministic_content_sha256": "0" * 64}

    with pytest.raises(ExplorerInputError, match="invalid Python definition index"):
        attach_python_source_facts(explorer, malformed)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "unexpected"])
def test_attach_requires_exact_rehashed_definition_file_inventory(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository = _repository(tmp_path, "def first():\n    return 1\n")
    second = repository / "src/booktrans/second.py"
    second.write_text("def second():\n    return 2\n", encoding="utf-8")
    _git(repository, "add", "src/booktrans/second.py")
    malformed = scan_python_source_facts(repository)
    files = malformed["python_definition_index"]["files"]
    assert len(files) == 2
    if mutation == "duplicate":
        files[1] = deepcopy(files[0])
    elif mutation == "missing":
        files.pop()
    else:
        files[1]["path"] = "src/booktrans/unmanaged.py"
        files[1]["module_id"] = "booktrans.unmanaged"
        for definition in files[1]["definitions"]:
            definition["evidence"]["path"] = files[1]["path"]
    _rehash_definition_projection(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python definition index file"):
        attach_python_source_facts(
            {"deterministic_content_sha256": "0" * 64},
            malformed,
        )


def test_definition_index_reports_managed_content_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "def work():\n    return 1\n")
    first = scan_python_source_facts(repository)
    path = repository / "src/booktrans/sample.py"
    path.write_text("def work():\n    return 2\n", encoding="utf-8")
    second = scan_python_source_facts(repository)

    first_identity = first["python_definition_index"]["scope"][
        "managed_files_sha256"
    ]
    second_identity = second["python_definition_index"]["scope"][
        "managed_files_sha256"
    ]
    assert len(first_identity) == 64
    assert first_identity != second_identity


def test_fixed_baseline_definition_inventory_is_explicitly_content_guarded() -> None:
    result = scan_python_source_facts(PROJECT)
    index = result["python_definition_index"]
    summary = index["summary"]

    assert summary["definitions"] == summary["classes"] + summary["functions_and_methods"]
    revision = result["source_snapshot"]["revision"]
    content_identity = index["scope"]["managed_files_sha256"]
    if revision == FIXED_BASELINE:
        assert content_identity == FIXED_BASELINE_MANAGED_PYTHON_SHA256
    if content_identity == FIXED_BASELINE_MANAGED_PYTHON_SHA256:
        assert result["scope"]["files_scanned"] == 320
        assert summary == {
            "classes": 199,
            "functions_and_methods": 1538,
            "definitions": 1737,
        }


def test_nonbaseline_managed_content_uses_current_counts(tmp_path: Path) -> None:
    result = scan_python_source_facts(
        _repository(tmp_path, "def current():\n    return 1\n")
    )

    assert result["python_definition_index"]["scope"]["managed_files_sha256"] != (
        FIXED_BASELINE_MANAGED_PYTHON_SHA256
    )
    assert result["python_definition_index"]["summary"] == {
        "classes": 0,
        "functions_and_methods": 1,
        "definitions": 1,
    }


def test_attach_rejects_rehashed_forged_managed_content_identity(
    tmp_path: Path,
) -> None:
    source_facts = scan_python_source_facts(
        _repository(tmp_path, "def current():\n    return 1\n")
    )
    source_facts["python_definition_index"]["scope"]["managed_files_sha256"] = (
        FIXED_BASELINE_MANAGED_PYTHON_SHA256
    )
    _rehash_definition_projection(source_facts)

    with pytest.raises(
        ExplorerInputError,
        match="invalid Python definition index managed file identity",
    ):
        attach_python_source_facts(
            {"deterministic_content_sha256": "0" * 64},
            source_facts,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "normalized-snippet",
        "evidence-id",
        "symbol",
        "source-role",
        "structural-anchor",
        "syntax-position",
        "lexical-occurrence-path",
    ],
)
def test_attach_rejects_rehashed_internally_inconsistent_definition_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    source_facts = scan_python_source_facts(
        _repository(tmp_path, "def current():\n    return 1\n")
    )
    definition = source_facts["python_definition_index"]["files"][0][
        "definitions"
    ][0]
    evidence = definition["evidence"]
    if mutation == "normalized-snippet":
        evidence["normalized_snippet_sha256"] = "0" * 64
    elif mutation == "evidence-id":
        evidence["evidence_id"] = "evidence:sha256:" + "0" * 64
    elif mutation == "symbol":
        evidence["symbol"] = "booktrans.sample.forged"
    elif mutation == "source-role":
        evidence["source_role"] = "production"
    elif mutation == "structural-anchor":
        evidence["structural_anchor"] = "python:definition:function:forged"
    elif mutation == "syntax-position":
        evidence["syntax_position"] = "forged/function:current"
    else:
        definition["lexical_occurrence_path"] = ["2-of-1"]
    _rehash_definition_projection(source_facts)

    with pytest.raises(
        ExplorerInputError,
        match="invalid Python definition index evidence|lexical occurrence path",
    ):
        attach_python_source_facts(
            {"deterministic_content_sha256": "0" * 64},
            source_facts,
        )
