from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import zipfile


SCRIPT = Path(r"D:\translate\scripts\diagnostics\probe_libreoffice_renderer.py")
SPEC = importlib.util.spec_from_file_location("probe_libreoffice_renderer", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_child_environment_sanitizes_only_child_python_paths(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "parent-home")
    monkeypatch.setenv("PYTHONPATH", "parent-path")
    unset = probe.safe_child_environment(None)
    sentinel = probe.safe_child_environment("hello")
    assert "PYTHONHOME" not in unset
    assert "PYTHONPATH" not in unset
    assert sentinel["PYTHONHOME"] == "hello"
    assert "PYTHONPATH" not in sentinel
    assert os.environ["PYTHONHOME"] == "parent-home"
    assert os.environ["PYTHONPATH"] == "parent-path"


def test_minimal_control_is_a_valid_docx_and_profile_uri_is_absolute(tmp_path: Path) -> None:
    document = tmp_path / "minimal.docx"
    probe.write_minimal_docx(document)
    assert zipfile.is_zipfile(document)
    with zipfile.ZipFile(document) as archive:
        assert "word/document.xml" in archive.namelist()
    assert probe.file_uri(tmp_path / "lo-profile").startswith("file:///")


def test_renderer_readiness_requires_successful_nonempty_hashed_pdf() -> None:
    complete = {
        "exit_code": 0,
        "timed_out": False,
        "pdf_exists": True,
        "pdf_size_bytes": 42,
        "pdf_sha256": "a" * 64,
    }
    assert probe.conversion_ready(complete)

    # An executable can exist and even return zero while producing no output:
    # it is still not a usable renderer.
    assert not probe.conversion_ready({**complete, "pdf_exists": False, "pdf_size_bytes": None, "pdf_sha256": None})
    assert not probe.conversion_ready({**complete, "timed_out": True})
    assert not probe.conversion_ready({**complete, "pdf_size_bytes": 0})


def test_process_snapshot_failure_is_safe_and_never_claims_a_process(monkeypatch) -> None:
    # The runner's safe default protects against using a failed process query
    # as permission to terminate unrelated LibreOffice sessions.
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("powershell unavailable")

    monkeypatch.setattr(probe.subprocess, "run", unavailable)
    snapshot = probe.process_snapshot()
    assert "error" in snapshot
    assert snapshot["processes"] == []


def test_timeout_is_hang_only_with_observed_idle_cpu_memory_and_files() -> None:
    result = {"exit_code": 1, "timed_out": True, "pdf_exists": False}
    base = {"process_alive": True, "cpu_delta_seconds": 0.0, "working_set_delta_bytes": 0, "activity": {"fingerprint": "same"}}
    idle = [{**base, "elapsed_seconds": value} for value in (0, 60, 120)]
    assert probe.classify_runtime_outcome(result=result, telemetry=idle, no_progress_window_seconds=120) == "HANG_NO_PROGRESS"

    active = [{**base, "elapsed_seconds": 0}, {**base, "elapsed_seconds": 60, "cpu_delta_seconds": 2.0}, {**base, "elapsed_seconds": 120, "cpu_delta_seconds": 2.0}]
    assert probe.classify_runtime_outcome(result=result, telemetry=active, no_progress_window_seconds=120) == "TIMEOUT_WITH_ACTIVITY"


def test_basic_readiness_does_not_imply_representative_readiness() -> None:
    passed = {"exit_code": 0, "timed_out": False, "pdf_exists": True, "pdf_size_bytes": 1, "pdf_sha256": "a" * 64}
    failed = {"exit_code": 0, "timed_out": False, "pdf_exists": False, "pdf_size_bytes": None, "pdf_sha256": None}
    levels = probe.renderer_readiness_levels([
        {**passed, "name": "minimal-pythonhome-unset"},
        {**failed, "name": "real-pythonhome-unset"},
    ])
    assert levels == {"basic_renderer_ready": True, "representative_renderer_ready": False}
