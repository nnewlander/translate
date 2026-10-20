"""Closed-world local providers for read-only PDF evidence.

The provider returns a rendered source-PDF page only after verifying the PDF
SHA and page identity.  It does not interpret D050 markers or create D050
records; that remains the exact-evidence validator's responsibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable, Protocol
from uuid import uuid4


class EvidenceProviderUnavailable(RuntimeError):
    """No approved local provider could produce the required evidence."""


class EvidenceIntegrityError(RuntimeError):
    """The PDF or a derived cache item does not match its binding."""


@dataclass(frozen=True)
class PdfEvidenceRequirement:
    pdf_sha256: str
    page_number: int
    evidence_requirement: str = "PDF_CORROBORATION"
    dpi: int = 150


@dataclass(frozen=True)
class PdfEvidence:
    provider_id: str
    provider_version: str
    pdf_sha256: str
    page_number: int
    evidence_requirement: str
    representation_path: str
    representation_sha256: str
    cached: bool


class PdfEvidenceProvider(Protocol):
    provider_id: str

    def render(self, pdf: Path, requirement: PdfEvidenceRequirement, output_prefix: Path) -> Path: ...

    def version(self) -> str: ...


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _poppler_candidates() -> Iterable[Path]:
    configured = os.environ.get("BOOKTRANS_PDFTOPPM")
    if configured:
        yield Path(configured)
    resolved = shutil.which("pdftoppm")
    if resolved:
        yield Path(resolved)
    # Codex desktop ships Poppler independently of a worker's inherited PATH.
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    yield bundled


class PopplerProvider:
    provider_id = "POPPLER_PDFTOPPM"

    def __init__(self, executable: Path | None = None) -> None:
        candidates = [executable] if executable else list(_poppler_candidates())
        self.executable = next((path for path in candidates if path and path.is_file()), None)

    def version(self) -> str:
        if not self.executable:
            return "unavailable"
        try:
            result = subprocess.run([str(self.executable), "-v"], text=True, capture_output=True, timeout=10, check=False)
        except OSError:
            return "unavailable"
        return (result.stderr or result.stdout or "unknown").strip().splitlines()[0][:160]

    def render(self, pdf: Path, requirement: PdfEvidenceRequirement, output_prefix: Path) -> Path:
        if not self.executable:
            raise EvidenceProviderUnavailable("POPPLER_PDFTOPPM_UNAVAILABLE")
        command = [str(self.executable), "-f", str(requirement.page_number), "-l", str(requirement.page_number), "-singlefile", "-png", "-r", str(requirement.dpi), str(pdf), str(output_prefix)]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EvidenceProviderUnavailable(f"POPPLER_PDFTOPPM_EXECUTION_FAILED:{type(exc).__name__}") from exc
        output = output_prefix.with_suffix(".png")
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "unknown").strip().replace("\n", " ")[:240]
            raise EvidenceProviderUnavailable(f"POPPLER_PDFTOPPM_RENDER_FAILED:{detail}")
        return output


def default_providers() -> tuple[PopplerProvider, ...]:
    """Return each distinct approved Poppler location as a separate fallback."""
    result: list[PopplerProvider] = []
    seen: set[Path] = set()
    for candidate in _poppler_candidates():
        try:
            canonical = candidate.resolve()
        except OSError:
            canonical = candidate
        if canonical in seen or not canonical.is_file():
            continue
        seen.add(canonical)
        result.append(PopplerProvider(canonical))
    return tuple(result)


def _cache_paths(cache_root: Path, requirement: PdfEvidenceRequirement) -> tuple[Path, Path]:
    stem = f"{requirement.pdf_sha256}-p{requirement.page_number}-{requirement.dpi}dpi"
    return cache_root / f"{stem}.png", cache_root / f"{stem}.json"


def _load_cache(cache_root: Path, requirement: PdfEvidenceRequirement) -> PdfEvidence | None:
    image, metadata = _cache_paths(cache_root, requirement)
    if not image.is_file() or not metadata.is_file():
        return None
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        if value.get("pdf_sha256") != requirement.pdf_sha256 or value.get("page_number") != requirement.page_number or value.get("evidence_requirement") != requirement.evidence_requirement:
            return None
        digest = _sha256(image)
        if value.get("representation_sha256") != digest:
            return None
        return PdfEvidence(**{**value, "representation_path": str(image), "cached": True})
    except (OSError, ValueError, TypeError):
        return None


def get_pdf_evidence(
    pdf: Path,
    requirement: PdfEvidenceRequirement,
    *,
    cache_root: Path | None = None,
    providers: Iterable[PdfEvidenceProvider] | None = None,
) -> PdfEvidence:
    """Return verified local PDF page evidence, or fail closed.

    A cache is derived operational material: it is accepted only when both its
    metadata and image SHA bind it to the exact immutable source-PDF SHA/page.
    """
    if not pdf.is_file():
        raise EvidenceProviderUnavailable("PDF_SOURCE_MISSING")
    actual_sha = _sha256(pdf)
    if actual_sha != requirement.pdf_sha256:
        raise EvidenceIntegrityError("PDF_SOURCE_SHA_MISMATCH")
    if requirement.page_number < 1:
        raise EvidenceIntegrityError("PDF_PAGE_INVALID")
    if cache_root:
        cached = _load_cache(cache_root, requirement)
        if cached:
            return cached
    attempts: list[str] = []
    for provider in providers or default_providers() or (PopplerProvider(),):
        if cache_root:
            staging = cache_root / f".pdf-evidence-{uuid4().hex}"
        else:
            # Never create derived data next to an immutable source PDF.
            staging = Path(tempfile.gettempdir()) / f"booktrans-pdf-evidence-{uuid4().hex}"
        try:
            staging.parent.mkdir(parents=True, exist_ok=True)
            image = provider.render(pdf, requirement, staging)
            digest = _sha256(image)
            evidence = PdfEvidence(provider.provider_id, provider.version(), actual_sha, requirement.page_number, requirement.evidence_requirement, str(image), digest, False)
            if cache_root:
                target, metadata = _cache_paths(cache_root, requirement)
                target_tmp = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
                metadata_tmp = metadata.with_name(f"{metadata.name}.{uuid4().hex}.tmp")
                shutil.move(str(image), target_tmp)
                os.replace(target_tmp, target)
                record = asdict(PdfEvidence(provider.provider_id, provider.version(), actual_sha, requirement.page_number, requirement.evidence_requirement, str(target), digest, False))
                metadata_tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
                os.replace(metadata_tmp, metadata)
                return PdfEvidence(**{**record, "cached": False})
            return evidence
        except EvidenceProviderUnavailable as exc:
            attempts.append(str(exc))
        finally:
            for item in (staging, staging.with_suffix(".png")):
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    pass
    raise EvidenceProviderUnavailable("EVIDENCE_PROVIDER_UNAVAILABLE:" + "|".join(attempts or ["NO_APPROVED_PROVIDER"]))
