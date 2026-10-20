"""Probe a single required source-PDF page through approved local providers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.production_control.pdf_evidence_provider import (  # noqa: E402
    EvidenceIntegrityError,
    EvidenceProviderUnavailable,
    PdfEvidenceRequirement,
    get_pdf_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pdf-sha256", required=True)
    parser.add_argument("--page", type=int, action="append", required=True)
    parser.add_argument("--requirement", default="D050_PDF_CORROBORATION")
    parser.add_argument("--cache-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = [get_pdf_evidence(args.pdf, PdfEvidenceRequirement(args.pdf_sha256.lower(), page, args.requirement), cache_root=args.cache_root) for page in args.page]
    except EvidenceIntegrityError as exc:
        print(json.dumps({"status": "BLOCKED", "family": "IMMUTABLE_INTEGRITY_FAILURE", "reason": str(exc)}))
        return 2
    except EvidenceProviderUnavailable as exc:
        print(json.dumps({"status": "BLOCKED", "family": "EVIDENCE_PROVIDER_UNAVAILABLE", "reason": str(exc)}))
        return 3
    print(json.dumps({"status": "PASS", "evidence": [item.__dict__ for item in evidence]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
