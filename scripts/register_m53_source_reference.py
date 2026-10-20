"""Register the read-only original PDF alongside the immutable DOCX source."""

from hashlib import sha256
import json
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
PDF = ROOT / "Software Engineering at Google.pdf"
DOCX = ROOT / "Software Engineering at Google.docx"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not PDF.is_file() or PDF.suffix.lower() != ".pdf":
        raise ValueError("ORIGINAL_PDF_NOT_FOUND")
    reader = PdfReader(str(PDF))
    evidence = []
    expected = {
        "Software Engineering at Google": "title",
        "978-1-492-08279-8": "isbn",
        "How to Work Well on Teams": "chapter_2_title",
        "Testing Overview": "chapter_11_title",
        "Build Systems and Build Philosophy": "chapter_18_title",
    }
    for page_number, page in enumerate(reader.pages[:30], start=1):
        text = page.extract_text() or ""
        for token, kind in expected.items():
            if token in text and not any(item["kind"] == kind for item in evidence):
                evidence.append({"kind": kind, "page": page_number, "matched_text": token})
    required = {"title", "isbn", "chapter_2_title", "chapter_11_title", "chapter_18_title"}
    if {item["kind"] for item in evidence} != required:
        raise ValueError("ORIGINAL_PDF_IDENTITY_NOT_PROVEN")
    path = RUN / "source_reference_manifest.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump({
            "stage": "M5.3B_SOURCE_REFERENCE_REGISTRATION",
            "identity_verdict": "CONFIRMED_ORIGINAL_ENGLISH_EDITION",
            "original_pdf": {"path": str(PDF), "sha256": digest(PDF), "read_only": True, "pages": len(reader.pages)},
            "source_docx": {"path": str(DOCX), "sha256": digest(DOCX), "read_only": True},
            "identity_evidence": evidence,
            "authority_split": {
                "pdf": ["semantic", "reading_order", "visual", "page_association", "footnote_placement", "conversion_fragment_reconstruction"],
                "docx": ["mutation", "unit_uid", "ooxml_locator", "anchor_island", "patch_writeback", "package_preservation"],
                "prohibition": "PDF is never an OOXML mutation target and never bypasses deterministic response validation.",
            },
        }, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
