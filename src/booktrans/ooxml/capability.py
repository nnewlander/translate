"""Conservative M4 audit for paragraph-level non-identity projection."""

from collections import Counter, defaultdict
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ir import BlockIR, DocumentIR, SourceLocator
from booktrans.domain.synthetic import CapabilityRecord, CapabilityReport
from booktrans.ooxml.locator import LocatorResolutionError, RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part


class NonIdentityCapabilityAudit:
    """Accept only one contiguous, translatable OOXML text region per block."""

    def audit(self, source_path: Path, document: DocumentIR) -> CapabilityReport:
        records: list[CapabilityRecord] = []
        with zipfile.ZipFile(source_path, "r") as package:
            roots: dict[str, etree._Element] = {}
            indexes: dict[str, RootLocatorIndex] = {}
            for block in document.blocks:
                if not _is_translatable_block(block):
                    continue
                reasons = self._reasons(package, roots, indexes, block)
                records.append(CapabilityRecord(
                    unit_uid=block.unit_uid,
                    display_id=block.display_id,
                    part_name=block.part_name,
                    locator=block.locator,
                    status="COMPLEX_UNSUPPORTED" if reasons else "SAFE_SIMPLE",
                    reasons=reasons,
                ))
        reason_counts = Counter(reason for record in records for reason in record.reasons)
        samples: dict[str, list[CapabilityRecord]] = defaultdict(list)
        for record in records:
            for reason in record.reasons:
                if len(samples[reason]) < 5:
                    samples[reason].append(record)
        safe = sum(record.status == "SAFE_SIMPLE" for record in records)
        total = len(records)
        return CapabilityReport(
            total_translatable_blocks=total,
            safe_simple_blocks=safe,
            complex_unsupported_blocks=total - safe,
            safe_simple_percentage=round((safe / total * 100) if total else 0.0, 2),
            reason_counts=dict(sorted(reason_counts.items())),
            reason_samples=dict(sorted(samples.items())),
            records=records,
        )

    def _reasons(
        self,
        package: zipfile.ZipFile,
        roots: dict[str, etree._Element],
        indexes: dict[str, RootLocatorIndex],
        block: BlockIR,
    ) -> list[str]:
        reasons: set[str] = set()
        text_spans = [span for span in block.spans if span.kind == "text" and span.text]
        all_text_spans = [span for span in block.spans if span.kind == "text"]
        if not text_spans:
            reasons.add("NO_EDITABLE_TEXT")
        if any(span.classification != CoverageClassification.TRANSLATABLE for span in all_text_spans):
            reasons.add("PROTECTED_CONTENT")
        if any(span.kind == "field_boundary" for span in block.spans) or any(
            "field_instruction" in span.container_context for span in text_spans
        ):
            reasons.add("FIELD_CONTEXT")
        if len(text_spans) != 1:
            reasons.add("MULTI_FORMAT_CONTEXT")
        if any("hyperlink_rid" in span.container_context for span in text_spans):
            reasons.add("HYPERLINK_BOUNDARY")
        for span in text_spans:
            for locator in [span.locator, *span.source_run_locators]:
                try:
                    root = roots.get(locator.part_name)
                    if root is None:
                        root = read_xml_part(package, locator.part_name)
                        if root is None:
                            raise LocatorResolutionError("LOCATOR_PART_NOT_XML")
                        roots[locator.part_name] = root
                        indexes[locator.part_name] = RootLocatorIndex(root)
                    node = resolve_locator_in_root(root, locator, index=indexes[locator.part_name])
                except (KeyError, LocatorResolutionError, etree.XMLSyntaxError):
                    reasons.add("LOCATOR_UNRESOLVABLE")
                    continue
                alternate = _ancestor(node, qn("mc", "AlternateContent"))
                if alternate is not None:
                    branches = [*alternate.findall("mc:Choice", namespaces=NS), *alternate.findall("mc:Fallback", namespaces=NS)]
                    if any(len(branch.xpath(".//w:t", namespaces=NS)) != 1 for branch in branches):
                        reasons.add("ALTERNATE_MULTI_NODE")
        return sorted(reasons)


def _is_translatable_block(block: BlockIR) -> bool:
    return any(
        span.kind == "text" and span.text and span.classification == CoverageClassification.TRANSLATABLE
        for span in block.spans
    )


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None
