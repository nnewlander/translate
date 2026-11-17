from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)


PROJECT = Path(__file__).resolve().parents[2]
CALL_PATHS = [
    "src/booktrans/production_worker/pipeline.py",
    "src/booktrans/providers/router.py",
    "src/booktrans/providers/qwen_mt.py",
    "src/booktrans/providers/qwen_max.py",
]


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repository(tmp_path: Path, pipeline: str, router: str = "def route(value):\n    return value\n") -> Path:
    repository = tmp_path / "source"
    sources = {
        CALL_PATHS[0]: pipeline,
        CALL_PATHS[1]: router,
        CALL_PATHS[2]: "def mt_adapter():\n    return None\n",
        CALL_PATHS[3]: "def max_adapter():\n    return None\n",
        "src/booktrans/providers/fifth.py": "def should_not_be_scanned():\n    hidden_call()\n",
    }
    for relative, source in sources.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
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


def _calls(result: dict, path: str = CALL_PATHS[0]) -> list[dict]:
    file = next(item for item in result["python_call_facts"]["files"] if item["path"] == path)
    return file["calls"]


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _rehash(result: dict) -> None:
    extension = result["python_call_facts"]
    extension["deterministic_facts_sha256"] = _digest(
        {key: value for key, value in extension.items() if key != "deterministic_facts_sha256"}
    )
    result["deterministic_facts_sha256"] = _digest(
        {key: value for key, value in result.items() if key != "deterministic_facts_sha256"}
    )


def test_call_facts_are_bounded_and_resolve_unique_local_and_import_calls(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "from booktrans.providers.router import route\n\n"
        "def enrich(value):\n"
        "    return value\n\n"
        "def process_unit(request, providers):\n"
        "    decision = route(request)\n"
        "    enriched = enrich(request)\n"
        "    providers.mt.translate(enriched)\n"
        "    return decision\n",
    )

    result = scan_python_source_facts(repository)

    extension = result["python_call_facts"]
    assert extension["schema"] == "architecture-explorer.python-call-facts.v1"
    assert extension["scope"] == {
        "selection": "The closed T10 translation-execution call-facts allowlist",
        "paths": CALL_PATHS,
        "files_scanned": 4,
    }
    assert [item["path"] for item in extension["files"]] == CALL_PATHS
    assert all(item["path"] != "src/booktrans/providers/fifth.py" for item in extension["files"])
    assert all(type(item["has_star_import"]) is bool for item in extension["files"])
    assert extension["files"][0]["has_star_import"] is False
    calls = {item["callee"]: item for item in _calls(result)}
    assert calls["route"]["resolution"] == "exact-import"
    assert calls["enrich"]["resolution"] == "exact-local"
    assert calls["providers.mt.translate"]["resolution"] == "unresolved-dispatch"
    assert "target_definition_evidence_id" in calls["route"]
    assert "target_definition_evidence_id" in calls["enrich"]
    assert "target_definition_evidence_id" not in calls["providers.mt.translate"]
    definition_ids = {
        definition["evidence"]["evidence_id"]
        for file in result["python_definition_index"]["files"]
        for definition in file["definitions"]
    }
    assert calls["route"]["target_definition_evidence_id"] in definition_ids
    assert calls["enrich"]["target_definition_evidence_id"] in definition_ids
    assert all(item["enclosing_definition_evidence_id"] in definition_ids for item in calls.values())
    for call in calls.values():
        assert call["occurrence"] >= 1
        assert "-of-" in call["occurrence_discriminator"]
        assert call["resolution_reason"]
        evidence = call["evidence"]
        assert evidence["evidence_kind"] == "python_call"
        assert evidence["source_role"] == "automatic_source_fact"
        assert evidence["path"] == CALL_PATHS[0]
        assert evidence["symbol"] == call["callee"]
        assert evidence["structural_anchor"].startswith("python:call:")
        assert evidence["syntax_position"]
        assert evidence["snippet"]
        assert evidence["normalized_snippet_sha256"]
        assert evidence["file_sha256"]


@pytest.mark.parametrize(
    ("pipeline", "callee", "expected"),
    [
        (
            "def helper():\n    return None\n\ndef process_unit(helper):\n    helper()\n",
            "helper",
            "dynamic-or-ambiguous",
        ),
        (
            "from booktrans.providers.router import route\n\ndef process_unit(value):\n    route = value\n    route()\n",
            "route",
            "dynamic-or-ambiguous",
        ),
        (
            "from booktrans.providers.router import *\n\ndef process_unit(value):\n    route(value)\n",
            "route",
            "dynamic-or-ambiguous",
        ),
        (
            "import importlib\n\ndef process_unit():\n    importlib.import_module('booktrans.providers.router')\n",
            "importlib.import_module",
            "unresolved-dispatch",
        ),
        (
            "def process_unit(provider):\n    getattr(provider, 'translate')()\n",
            "getattr(provider, 'translate')",
            "dynamic-or-ambiguous",
        ),
        (
            "def process_unit(callback):\n    callback()\n",
            "callback",
            "dynamic-or-ambiguous",
        ),
        (
            "def process_unit(factory):\n    factory()()\n",
            "factory()",
            "dynamic-or-ambiguous",
        ),
        (
            "def process_unit(provider):\n    provider['translate']()\n",
            "provider['translate']",
            "dynamic-or-ambiguous",
        ),
        (
            "def process_unit(provider):\n    provider.translate()\n",
            "provider.translate",
            "unresolved-dispatch",
        ),
        (
            "def helper():\n    return 1\ndef process_unit():\n    global helper\n    helper()\n",
            "helper",
            "dynamic-or-ambiguous",
        ),
        (
            "def helper():\n    return 1\ndef helper():\n    return 2\ndef process_unit():\n    helper()\n",
            "helper",
            "dynamic-or-ambiguous",
        ),
    ],
)
def test_call_facts_fail_closed_for_dynamic_shadowed_and_dispatch_shapes(
    tmp_path: Path,
    pipeline: str,
    callee: str,
    expected: str,
) -> None:
    calls = _calls(scan_python_source_facts(_repository(tmp_path, pipeline)))
    call = next(item for item in calls if item["callee"] == callee)
    assert call["resolution"] == expected
    assert call["resolution_reason"]
    assert "target_definition_evidence_id" not in call


def test_call_facts_fail_closed_when_an_outer_closure_rebinds_the_callee(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'module'\n\n"
        "def process_unit(value):\n"
        "    helper = value\n"
        "    def invoke():\n"
        "        return helper()\n"
        "    return invoke()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "name-is-parameter-or-rebound-in-enclosing-scope"
    assert "target_definition_evidence_id" not in call


def test_call_facts_fail_closed_when_a_plain_import_alias_shadows_the_callee(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'local'\n\n"
        "import booktrans.providers.router as helper\n\n"
        "def process_unit():\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "local-definition-binding-is-not-unique"
    assert "target_definition_evidence_id" not in call


def test_external_from_import_shadows_a_same_named_module_function(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'local'\n\n"
        "from external_package import helper\n\n"
        "def process_unit():\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "local-definition-binding-is-not-unique"
    assert "target_definition_evidence_id" not in call


def test_conditional_star_import_makes_module_name_resolution_ambiguous(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'local'\n\n"
        "if enable_external_helpers:\n"
        "    from external_package import *\n\n"
        "def process_unit():\n"
        "    return helper()\n",
    )

    result = scan_python_source_facts(repository)
    call = next(item for item in _calls(result) if item["callee"] == "helper")
    call_file = next(
        file
        for file in result["python_call_facts"]["files"]
        if file["path"] == CALL_PATHS[0]
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "star-import-makes-name-resolution-ambiguous"
    assert "target_definition_evidence_id" not in call
    assert call_file["has_star_import"] is True


def test_call_facts_use_python_class_name_lookup_rules(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'module'\n\n"
        "class Handler:\n"
        "    helper = object()\n"
        "    direct = helper()\n\n"
        "    def process_unit(self):\n"
        "        return helper()\n",
    )

    helper_calls = [
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    ]

    assert [item["resolution"] for item in helper_calls] == [
        "dynamic-or-ambiguous",
        "exact-local",
    ]
    assert "target_definition_evidence_id" not in helper_calls[0]
    assert "target_definition_evidence_id" in helper_calls[1]


def test_method_default_expression_uses_the_class_body_binding_scope(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'module'\n\n"
        "class Handler:\n"
        "    helper = lambda: 'class'\n\n"
        "    def process_unit(value=helper()):\n"
        "        return value\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "name-is-parameter-or-rebound-in-enclosing-scope"
    assert "target_definition_evidence_id" not in call


def test_exception_alias_shadows_a_same_named_module_function(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'module'\n\n"
        "def process_unit():\n"
        "    try:\n"
        "        raise RuntimeError('failure')\n"
        "    except RuntimeError as helper:\n"
        "        return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "name-is-parameter-or-rebound-in-enclosing-scope"
    assert "target_definition_evidence_id" not in call


def test_module_match_as_capture_shadows_a_same_named_function(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'definition'\n\n"
        "match subject:\n"
        "    case [helper]:\n"
        "        pass\n\n"
        "def process_unit():\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert "target_definition_evidence_id" not in call


def test_function_match_star_capture_shadows_a_same_named_function(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'definition'\n\n"
        "def process_unit(value):\n"
        "    match value:\n"
        "        case [head, *helper]:\n"
        "            pass\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "name-is-parameter-or-rebound-in-enclosing-scope"
    assert "target_definition_evidence_id" not in call


def test_nested_match_mapping_rest_capture_shadows_a_same_named_function(
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'definition'\n\n"
        "def process_unit(value):\n"
        "    match value:\n"
        "        case {'payload': [first], **helper}:\n"
        "            pass\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "name-is-parameter-or-rebound-in-enclosing-scope"
    assert "target_definition_evidence_id" not in call


@pytest.mark.parametrize(
    "pattern",
    [
        "_",
        "[*_]",
        "{'payload': _}",
    ],
)
def test_patterns_without_capture_names_preserve_exact_local_resolution(
    tmp_path: Path,
    pattern: str,
) -> None:
    repository = _repository(
        tmp_path,
        "def helper():\n"
        "    return 'definition'\n\n"
        "def process_unit(value):\n"
        "    match value:\n"
        f"        case {pattern}:\n"
        "            pass\n"
        "    return helper()\n",
    )

    call = next(
        item
        for item in _calls(scan_python_source_facts(repository))
        if item["callee"] == "helper"
    )

    assert call["resolution"] == "exact-local"
    assert "target_definition_evidence_id" in call


@pytest.mark.parametrize(
    ("pipeline", "context_owner", "expected_reason"),
    [
        (
            "def helper():\n"
            "    return 'module'\n\n"
            "def process_unit(value):\n"
            "    helper = value\n"
            "    def invoke():\n"
            "        return helper()\n"
            "    return invoke()\n",
            "call",
            "name-is-parameter-or-rebound-in-enclosing-scope",
        ),
        (
            "def helper():\n"
            "    return 'local'\n\n"
            "import booktrans.providers.router as helper\n\n"
            "def process_unit():\n"
            "    return helper()\n",
            "file",
            "local-definition-binding-is-not-unique",
        ),
        (
            "def helper():\n"
            "    return 'local'\n\n"
            "from external_package import helper\n\n"
            "def process_unit():\n"
            "    return helper()\n",
            "file",
            "local-definition-binding-is-not-unique",
        ),
        (
            "def helper():\n"
            "    return 'definition'\n\n"
            "def process_unit(value):\n"
            "    match value:\n"
            "        case [head, *helper]:\n"
            "            pass\n"
            "    return helper()\n",
            "call",
            "name-is-parameter-or-rebound-in-enclosing-scope",
        ),
    ],
    ids=[
        "outer-closure-rebind",
        "plain-import-alias",
        "external-from-import",
        "function-pattern-capture",
    ],
)
def test_binding_context_matrix_persists_and_rejects_rehashed_false_exact(
    tmp_path: Path,
    pipeline: str,
    context_owner: str,
    expected_reason: str,
) -> None:
    result = scan_python_source_facts(_repository(tmp_path, pipeline))
    call = next(item for item in _calls(result) if item["callee"] == "helper")
    call_file = next(
        file for file in result["python_call_facts"]["files"] if file["path"] == CALL_PATHS[0]
    )

    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == expected_reason
    assert "target_definition_evidence_id" not in call
    persisted_bindings = (
        call["enclosing_bindings"] if context_owner == "call" else call_file["module_bindings"]
    )
    assert "helper" in persisted_bindings

    malformed = deepcopy(result)
    forged_call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    target = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".helper")
    )
    forged_call["resolution"] = "exact-local"
    forged_call["resolution_reason"] = "unique-unshadowed-same-module-definition"
    forged_call["target_definition_evidence_id"] = target["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_call_identity_ignores_blank_lines_and_remains_deterministic(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "def helper(value):\n"
        "    return value\n\n"
        "def process_unit(value):\n"
        "    first = helper(value)\n"
        "    return helper(value)\n",
    )
    first = scan_python_source_facts(repository)
    repeated = scan_python_source_facts(repository)
    path = repository / CALL_PATHS[0]
    path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    moved = scan_python_source_facts(repository)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "return helper(value)", "return helper(value + 1)"
        ),
        encoding="utf-8",
    )
    changed = scan_python_source_facts(repository)

    first_calls = [item for item in _calls(first) if item["callee"] == "helper"]
    moved_calls = [item for item in _calls(moved) if item["callee"] == "helper"]
    changed_calls = [item for item in _calls(changed) if item["callee"] == "helper"]
    assert first["python_call_facts"] == repeated["python_call_facts"]
    assert len(first_calls) == 2
    assert len({item["evidence"]["evidence_id"] for item in first_calls}) == 2
    assert [item["evidence"]["evidence_id"] for item in first_calls] == [
        item["evidence"]["evidence_id"] for item in moved_calls
    ]
    assert first_calls[0]["evidence"]["current_location"] != moved_calls[0]["evidence"]["current_location"]
    assert first_calls[0]["evidence"]["file_sha256"] != moved_calls[0]["evidence"]["file_sha256"]
    assert {
        item["evidence"]["evidence_id"] for item in moved_calls
    }.isdisjoint(
        item["evidence"]["evidence_id"] for item in changed_calls
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-parent",
        "missing-enclosing-bindings",
        "missing-module-bindings",
        "unsorted-module-bindings",
        "duplicate-module-bindings",
        "invalid-module-binding",
        "missing-star-import-flag",
        "integer-star-import-flag",
        "string-star-import-flag",
        "exact-without-target",
        "unresolved-with-target",
        "unknown-resolution",
    ],
)
def test_attach_rejects_rehashed_inconsistent_call_facts(tmp_path: Path, mutation: str) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper(value):\n    return value\n\ndef process_unit(value):\n    return helper(value)\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    call_file = next(
        file for file in malformed["python_call_facts"]["files"] if file["path"] == CALL_PATHS[0]
    )
    if mutation == "missing-parent":
        call["enclosing_definition_evidence_id"] = "evidence:sha256:" + "0" * 64
    elif mutation == "missing-enclosing-bindings":
        del call["enclosing_bindings"]
    elif mutation == "missing-module-bindings":
        del call_file["module_bindings"]
    elif mutation == "unsorted-module-bindings":
        call_file["module_bindings"] = ["zeta", "alpha"]
    elif mutation == "duplicate-module-bindings":
        call_file["module_bindings"] = ["helper", "helper"]
    elif mutation == "invalid-module-binding":
        call_file["module_bindings"] = [1]
    elif mutation == "missing-star-import-flag":
        del call_file["has_star_import"]
    elif mutation == "integer-star-import-flag":
        call_file["has_star_import"] = 0
    elif mutation == "string-star-import-flag":
        call_file["has_star_import"] = "false"
    elif mutation == "exact-without-target":
        del call["target_definition_evidence_id"]
    elif mutation == "unresolved-with-target":
        call["resolution"] = "unresolved-dispatch"
    else:
        call["resolution"] = "guessed"
        del call["target_definition_evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_attach_rejects_rehashed_exact_call_pointing_at_a_wrong_existing_target(
    tmp_path: Path,
) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper(value):\n"
            "    return value\n\n"
            "def decoy(value):\n"
            "    return value\n\n"
            "def process_unit(value):\n"
            "    return helper(value)\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    decoy = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".decoy")
    )
    call["target_definition_evidence_id"] = decoy["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_attach_rejects_rehashed_shadowed_call_forged_as_exact(
    tmp_path: Path,
) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper():\n"
            "    return 'module'\n\n"
            "def process_unit():\n"
            "    try:\n"
            "        raise RuntimeError('failure')\n"
            "    except RuntimeError as helper:\n"
            "        return helper()\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    assert "helper" in call["enclosing_bindings"]
    target = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".helper")
    )
    call["resolution"] = "exact-local"
    call["resolution_reason"] = "unique-unshadowed-same-module-definition"
    call["target_definition_evidence_id"] = target["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_attach_rejects_rehashed_module_assignment_shadow_forged_as_exact(
    tmp_path: Path,
) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper():\n"
            "    return 'definition'\n\n"
            "helper = object()\n\n"
            "def process_unit():\n"
            "    return helper()\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    call_file = next(
        file for file in malformed["python_call_facts"]["files"] if file["path"] == CALL_PATHS[0]
    )
    assert call["resolution"] == "dynamic-or-ambiguous"
    assert "target_definition_evidence_id" not in call
    assert "helper" in call_file["module_bindings"]
    assert call_file["module_bindings"] == sorted(set(call_file["module_bindings"]))
    assert "module_bindings" not in call
    target = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".helper")
    )
    call["resolution"] = "exact-local"
    call["resolution_reason"] = "unique-unshadowed-same-module-definition"
    call["target_definition_evidence_id"] = target["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_attach_rejects_rehashed_match_capture_forged_as_exact(
    tmp_path: Path,
) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper():\n"
            "    return 'definition'\n\n"
            "match subject:\n"
            "    case [helper]:\n"
            "        pass\n\n"
            "def process_unit():\n"
            "    return helper()\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    call_file = next(
        file for file in malformed["python_call_facts"]["files"] if file["path"] == CALL_PATHS[0]
    )
    assert call["resolution"] == "dynamic-or-ambiguous"
    assert "helper" in call_file["module_bindings"]
    target = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".helper")
    )
    call["resolution"] = "exact-local"
    call["resolution_reason"] = "unique-unshadowed-same-module-definition"
    call["target_definition_evidence_id"] = target["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_attach_rejects_rehashed_conditional_star_import_forged_as_exact(
    tmp_path: Path,
) -> None:
    result = scan_python_source_facts(
        _repository(
            tmp_path,
            "def helper():\n"
            "    return 'definition'\n\n"
            "if enable_external_helpers:\n"
            "    from external_package import *\n\n"
            "def process_unit():\n"
            "    return helper()\n",
        )
    )
    malformed = deepcopy(result)
    call = next(item for item in _calls(malformed) if item["callee"] == "helper")
    call_file = next(
        file for file in malformed["python_call_facts"]["files"] if file["path"] == CALL_PATHS[0]
    )
    assert call["resolution"] == "dynamic-or-ambiguous"
    assert call["resolution_reason"] == "star-import-makes-name-resolution-ambiguous"
    assert "target_definition_evidence_id" not in call
    assert call_file["has_star_import"] is True
    target = next(
        definition
        for file in malformed["python_definition_index"]["files"]
        if file["path"] == CALL_PATHS[0]
        for definition in file["definitions"]
        if definition["qualified_symbol"].endswith(".helper")
    )
    call["resolution"] = "exact-local"
    call["resolution_reason"] = "unique-unshadowed-same-module-definition"
    call["target_definition_evidence_id"] = target["evidence"]["evidence_id"]
    _rehash(malformed)

    with pytest.raises(ExplorerInputError, match="invalid Python call facts exact target"):
        attach_python_source_facts({"deterministic_content_sha256": "0" * 64}, malformed)


def test_fixed_baseline_process_unit_has_exact_helpers_and_unresolved_provider_dispatch() -> None:
    result = scan_python_source_facts(PROJECT)
    definitions = {
        definition["evidence"]["evidence_id"]: definition
        for file in result["python_definition_index"]["files"]
        for definition in file["definitions"]
    }
    process_unit_id = next(
        evidence_id
        for evidence_id, definition in definitions.items()
        if definition["qualified_symbol"] == "booktrans.production_worker.pipeline.process_unit"
    )
    calls = [
        item
        for item in _calls(result)
        if item["enclosing_definition_evidence_id"] == process_unit_id
    ]
    by_callee = {item["callee"]: item for item in calls}
    assert by_callee["route_semantic_unit"]["resolution"] == "exact-import"
    assert by_callee["enrich_request"]["resolution"] == "exact-local"
    assert by_callee["translate_unit"]["resolution"] == "exact-local"
    provider_call = next(
        item
        for item in _calls(result)
        if item["callee"] == "providers.mt.translate"
    )
    assert provider_call["resolution"] == "unresolved-dispatch"
    assert "target_definition_evidence_id" not in provider_call
