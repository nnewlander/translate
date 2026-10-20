"""M3's deliberately narrow OOXML text-node writer.

It starts with the original package, copies every entry, and serializes only
parts containing an explicitly planned M2 text-node locator.  It is not a
translator or a general-purpose DOCX editor.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.ir import SourceLocator
from booktrans.domain.models import Diagnostic
from booktrans.domain.roundtrip import PatchReport
from booktrans.ooxml.locator import LocatorResolutionError, RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn


@dataclass(frozen=True)
class PatchOperation:
    """One physical source text-node write requested by the identity plan."""

    locator: SourceLocator
    block_uid: str
    span_uid: str
    target_text: str | None = None


class PatchWriteError(RuntimeError):
    """A locator or a physical AlternateContent mirror could not be written safely."""


def write_text_at_locator(source_docx: Path, candidate_docx: Path, locator: SourceLocator, target_text: str) -> PatchReport:
    """Fixture-scale writer primitive used to prove an actual non-identity write.

    The caller supplies an output path.  The input package is always opened
    read-only and is never overwritten.
    """
    operation = PatchOperation(locator=locator, block_uid="primitive", span_uid="primitive", target_text=target_text)
    return apply_patch_operations(source_docx, candidate_docx, [operation], planned_blocks=1, planned_spans=1)


def apply_patch_operations(
    source_docx: Path,
    candidate_docx: Path,
    operations: list[PatchOperation],
    *,
    planned_blocks: int,
    planned_spans: int,
    skipped_protected: int = 0,
    skipped_excluded: int = 0,
) -> PatchReport:
    """Copy a package and execute every requested real XML text-node assignment."""
    if candidate_docx.resolve() == source_docx.resolve():
        raise PatchWriteError("CANDIDATE_PATH_EQUALS_SOURCE: refusing to overwrite immutable source DOCX.")
    candidate_docx.parent.mkdir(parents=True, exist_ok=True)
    by_part: dict[str, list[PatchOperation]] = defaultdict(list)
    for operation in operations:
        by_part[operation.locator.part_name].append(operation)
    report = PatchReport(
        planned_blocks=planned_blocks,
        planned_spans=planned_spans,
        planned_text_nodes=len(operations),
        skipped_protected=skipped_protected,
        skipped_excluded=skipped_excluded,
        touched_ooxml_parts=sorted(by_part),
    )

    with zipfile.ZipFile(source_docx, "r") as source, zipfile.ZipFile(candidate_docx, "w") as candidate:
        names = set(source.namelist())
        missing_parts = sorted(set(by_part) - names)
        for part in missing_parts:
            for operation in by_part[part]:
                report.resolution_failures.append(_diagnostic(
                    "LOCATOR_PART_MISSING", "blocker", "Patch locator refers to a missing OOXML part.", part, operation.locator.xml_path,
                ))
        for info in source.infolist():
            payload = source.read(info.filename)
            part_operations = by_part.get(info.filename)
            if part_operations:
                root = etree.fromstring(payload)
                index = RootLocatorIndex(root)
                for operation in part_operations:
                    try:
                        node = resolve_locator_in_root(root, operation.locator, index=index)
                        report.locators_resolved += 1
                        target = node.text or "" if operation.target_text is None else operation.target_text
                        attempted, verified, mirrored = _write_node(node, target)
                        report.writes_attempted += attempted
                        report.writes_verified += verified
                        report.mirrored_alternate_nodes += mirrored
                    except (LocatorResolutionError, PatchWriteError) as exc:
                        report.resolution_failures.append(_diagnostic(
                            "LOCATOR_WRITE_FAILED", "blocker", "Could not resolve and write a planned OOXML text node.", info.filename,
                            f"locator={operation.locator.xml_path}; detail={exc}",
                        ))
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            candidate.writestr(info, payload)
    return report


def _write_node(node: etree._Element, target: str) -> tuple[int, int, int]:
    """Assign text even for identity values; never optimize away the write path."""
    alternate = _ancestor(node, qn("mc", "AlternateContent"))
    if alternate is None:
        node.text = target
        return 1, int((node.text or "") == target), 0
    branches = [*alternate.findall("mc:Choice", namespaces=NS), *alternate.findall("mc:Fallback", namespaces=NS)]
    text_nodes = [branch.xpath(".//w:t", namespaces=NS) for branch in branches]
    current = node.text or ""
    if target != current and any(len(nodes) != 1 for nodes in text_nodes):
        raise PatchWriteError(
            "ALTERNATE_MIRROR_AMBIGUOUS: non-identity target cannot be safely distributed across multi-node branches."
        )
    attempted = verified = 0
    for nodes in text_nodes:
        for branch_node in nodes:
            branch_target = target if target != current else (branch_node.text or "")
            branch_node.text = branch_target
            attempted += 1
            verified += int((branch_node.text or "") == branch_target)
    return attempted, verified, max(0, attempted - 1)


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _diagnostic(code: str, severity: str, message: str, part: str | None, detail: str) -> Diagnostic:
    return Diagnostic(code=code, severity=severity, message=message, part=part, detail=detail)
