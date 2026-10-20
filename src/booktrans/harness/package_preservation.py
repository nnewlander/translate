"""Small package-level preservation checks shared by M3 and M4."""

from hashlib import sha256
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.models import Diagnostic
from booktrans.domain.roundtrip import PreservationReport
from booktrans.ooxml.package import PackageValidationError, open_docx_package


def verify_package_preservation(
    source_path: Path,
    candidate_path: Path,
    touched_parts: list[str],
    *,
    require_touched_xml_equivalence: bool,
) -> PreservationReport:
    """Validate the candidate and protect every payload M4 is not allowed to change."""
    report = PreservationReport()
    touched = set(touched_parts)
    try:
        with open_docx_package(candidate_path):
            report.candidate_valid = True
    except PackageValidationError as exc:
        report.unchanged_payload_mismatches.append(Diagnostic(
            code="CANDIDATE_INVALID_DOCX", severity="blocker", message="Candidate is not a valid DOCX/OOXML package.", detail=str(exc),
        ))
        return report
    with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(candidate_path, "r") as candidate:
        source_names = set(source.namelist())
        candidate_names = set(candidate.namelist())
        for name in sorted(source_names | candidate_names):
            if name not in source_names or name not in candidate_names:
                report.unchanged_payload_mismatches.append(Diagnostic(
                    code="PACKAGE_ENTRY_SET_CHANGED", severity="blocker", message="Candidate package entry set differs from source.", part=name,
                ))
        for name in sorted(source_names & candidate_names):
            source_payload = source.read(name)
            candidate_payload = candidate.read(name)
            if name not in touched:
                report.untouched_entries_checked += 1
                if source_payload != candidate_payload:
                    report.unchanged_payload_mismatches.append(Diagnostic(
                        code="UNTOUCHED_PAYLOAD_CHANGED", severity="blocker", message="Untouched package payload differs.", part=name,
                    ))
            if name.endswith(".rels"):
                report.relationship_entries_checked += 1
                if source_payload != candidate_payload:
                    report.relationship_payload_mismatches.append(Diagnostic(
                        code="RELATIONSHIP_PAYLOAD_CHANGED", severity="blocker", message="M3/M4 must not change relationship payloads.", part=name,
                    ))
            if name.startswith("word/media/"):
                report.media_entries_checked += 1
                if sha256(source_payload).hexdigest() != sha256(candidate_payload).hexdigest():
                    report.media_hash_mismatches.append(Diagnostic(
                        code="MEDIA_BINARY_CHANGED", severity="blocker", message="Media binary SHA-256 differs in candidate.", part=name,
                    ))
            if require_touched_xml_equivalence and name in touched:
                try:
                    if etree.tostring(etree.fromstring(source_payload), method="c14n") != etree.tostring(etree.fromstring(candidate_payload), method="c14n"):
                        report.touched_xml_structure_mismatches.append(Diagnostic(
                            code="TOUCHED_XML_CHANGED", severity="blocker", message="Identity-patched XML is not canonically equivalent to source.", part=name,
                        ))
                except etree.XMLSyntaxError as exc:
                    report.touched_xml_structure_mismatches.append(Diagnostic(
                        code="TOUCHED_XML_UNPARSEABLE", severity="blocker", message="Could not compare touched XML canonical structure.", part=name, detail=str(exc),
                    ))
    return report
