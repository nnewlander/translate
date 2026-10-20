"""Closed-world local PDF provider tests; no real production run is touched."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from booktrans.production_control.pdf_evidence_provider import (
    EvidenceIntegrityError,
    EvidenceProviderUnavailable,
    PdfEvidenceRequirement,
    get_pdf_evidence,
)


class FakeProvider:
    provider_id = "FAKE"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def version(self) -> str:
        return "test-v1"

    def render(self, pdf: Path, requirement: PdfEvidenceRequirement, output_prefix: Path) -> Path:
        self.calls += 1
        if self.fail:
            raise EvidenceProviderUnavailable("FAKE_UNAVAILABLE")
        image = output_prefix.with_suffix(".png")
        image.write_bytes(f"pdf-page-{requirement.page_number}".encode())
        return image


def _requirement(pdf: Path, page: int = 1) -> PdfEvidenceRequirement:
    return PdfEvidenceRequirement(sha256(pdf.read_bytes()).hexdigest(), page, "D050_PDF_CORROBORATION")


def test_primary_provider_renders_and_valid_cache_reuses(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"; pdf.write_bytes(b"immutable-pdf")
    cache = tmp_path / "cache"; provider = FakeProvider()
    first = get_pdf_evidence(pdf, _requirement(pdf), cache_root=cache, providers=[provider])
    second = get_pdf_evidence(pdf, _requirement(pdf), cache_root=cache, providers=[provider])
    assert first.pdf_sha256 == _requirement(pdf).pdf_sha256
    assert second.cached is True and provider.calls == 1


def test_provider_fallback_and_tampered_cache_are_closed_world(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"; pdf.write_bytes(b"immutable-pdf")
    cache = tmp_path / "cache"; failed, healthy = FakeProvider(fail=True), FakeProvider()
    result = get_pdf_evidence(pdf, _requirement(pdf), cache_root=cache, providers=[failed, healthy])
    assert result.provider_id == "FAKE" and failed.calls == healthy.calls == 1
    image = next(cache.glob("*.png")); image.write_bytes(b"tampered")
    get_pdf_evidence(pdf, _requirement(pdf), cache_root=cache, providers=[healthy])
    assert healthy.calls == 2


def test_pdf_sha_and_all_provider_failure_block(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"; pdf.write_bytes(b"immutable-pdf")
    with pytest.raises(EvidenceIntegrityError):
        get_pdf_evidence(pdf, PdfEvidenceRequirement("0" * 64, 1), cache_root=tmp_path / "cache", providers=[FakeProvider()])
    with pytest.raises(EvidenceProviderUnavailable):
        get_pdf_evidence(pdf, _requirement(pdf), cache_root=tmp_path / "cache", providers=[FakeProvider(fail=True)])
