"""Persist the Chapter 11 closed-world footnote evidence prepared before translation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
BLOCKS = ROOT / "runs/extract-20260914T050300Z-decb09-094777/blocks.jsonl"
OUT = RUN / "chapter_11_verified_footnote_evidence.json"


def main() -> None:
    if OUT.exists():
        raise SystemExit("Chapter 11 evidence sidecar exists; refusing to overwrite it.")
    blocks = {row["display_id"]: row for row in (json.loads(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    records = [
        ("body.p.03624", "cases.1", "/word/document.xml/paragraph[3948]/text[68]", "1", "footer.162.p.0001", 235, "1 See “Defect Prevention: Reducing Costs and Enhancing Quality.”"),
        ("body.p.03643", "life.2", "/word/document.xml/paragraph[3967]/text[38]", "2", "footer.164.p.0001", 237, "2 See “Failure at Dhahran.”"),
        ("body.p.03685", "systems.3", "/word/document.xml/paragraph[4009]/text[32]", "3", "body.p.03697", 241, "3 Getting the behavior right across different browsers and languages is a different story! But, ideally, the end-user experience should be the same for everyone."),
        ("body.p.03718", "11-2.4", "/word/document.xml/paragraph[4042]/text[209]", "4", "footer.169.p.0001 + footer.169.p.0002", 243, "4 Technically, we have four sizes of test at Google: small, medium, large, and enormous. The internal difference between large and enormous is actually subtle and historical; so, in this book, most descriptions of large actually apply to our notion of enormous."),
        ("body.p.03727", "operations,5", "/word/document.xml/paragraph[4052]/text[33]", "5", "body.p.03733", 244, "5 There is a little wiggle room in this policy. Tests are allowed to access a filesystem if they use a hermetic, in-memory implementation."),
        ("body.p.03765", "pyramid;6", "/word/document.xml/paragraph[4096]/text[15]", "6", "body.p.03772", 248, "6 Mike Cohn, Succeeding with Agile: Software Development Using Scrum (New York: Addison-Wesley Professional, 2009)."),
        ("body.p.03791", "coverage.7", "/word/document.xml/paragraph[4125]/text[63]", "7", "body.p.03797", 250, "7 Keep in mind that there are different kinds of coverage (line, path, branch, etc.), and each says something different about which code has been tested."),
        ("body.p.03830", "things.8", "/word/document.xml/paragraph[4164]/text[201]", "8", "footer.178.p.0001", 253, "8 Each supported language at Google has one standard test framework and one standard mocking/stubbing library. One set of infrastructure runs most tests in all languages across the entire codebase."),
        ("body.p.03841", "testing.9", "/word/document.xml/paragraph[4175]/text[53]", "9", "body.p.03846", 254, "9 This class was so successful that an updated version is still taught today. In fact, it is one of the longest-running orientation classes in the company’s history."),
    ]
    verified = []
    for display_id, marker, locator, number, footnote_id, page, footnote_text in records:
        block = blocks[display_id]
        if marker not in block["source_plain_text"]:
            raise RuntimeError(f"STALE_OR_INCORRECT_MARKER: {display_id} {marker!r}")
        verified.append({
            "display_id": display_id,
            "unit_uid": block["unit_uid"],
            "exact_source_marker_token": marker,
            "source_run_locator": locator,
            "footnote_number": number,
            "w_position_evidence": "w:position=6",
            "matching_converted_footnote_display_id": footnote_id,
            "original_pdf_page": page,
            "pdf_footnote_text": footnote_text,
            "verification_status": "VERIFIED",
        })
    payload = {
        "schema_version": "m5.3-verified-footnote-evidence-v1",
        "scope": "Chapter 11 exact markers verified using DOCX/OOXML plus read-only original-PDF page evidence; not a generic footnote detector.",
        "source_docx_sha256": "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba",
        "original_pdf_sha256": "4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e",
        "verified_markers": verified,
        "unverified_marker_count": 0,
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"verified markers: {len(verified)}")
    print(f"sidecar sha256: {sha256(OUT.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
