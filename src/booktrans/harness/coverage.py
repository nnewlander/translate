"""Deterministic accounting for M2 extracted visible text and known uncertainty."""

from collections import defaultdict

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ir import CoverageEntry, CoverageGroup, CoverageLedger, DocumentIR
from booktrans.domain.models import Diagnostic


def _group_name(block) -> str:
    if block.block_type == "textbox_paragraph":
        return "textboxes"
    if block.block_type == "table_cell_paragraph":
        return "tables"
    return block.story


def build_coverage_ledger(
    document: DocumentIR,
    uncertainty: list[CoverageEntry],
    source_integrity_pass: bool,
) -> CoverageLedger:
    groups: dict[str, CoverageGroup] = defaultdict(lambda: CoverageGroup(name=""))
    # Keep absent stories visible in the ledger; zero is different from silently
    # omitting a story category from an audit report.
    for name in ("body", "tables", "headers", "footers", "footnotes", "endnotes", "textboxes", "comments"):
        groups[name].name = name
    entries = list(uncertainty)
    warnings: list[Diagnostic] = list(document.diagnostics)
    for block in document.blocks:
        group_name = _group_name(block)
        group = groups[group_name]
        group.name = group_name
        group.units += 1
        group.visible_blocks += int(bool(block.source_plain_text))
        group.visible_characters += len(block.source_plain_text)
        warnings.extend(block.diagnostics)
        classifications_in_block: set[CoverageClassification] = set()
        for span in block.spans:
            warnings.extend(span.diagnostics)
            if span.classification is None or span.kind != "text":
                continue
            classifications_in_block.add(span.classification)
            group.visible_text_nodes += 1 + len(span.source_run_locators)
            characters = len(span.text)
            if span.classification == CoverageClassification.TRANSLATABLE:
                group.translatable_characters += characters
            elif span.classification == CoverageClassification.PROTECTED:
                group.protected_characters += characters
            elif span.classification == CoverageClassification.EXCLUDED_BY_POLICY:
                group.excluded_characters += characters
            elif span.classification == CoverageClassification.UNCLASSIFIED:
                group.unclassified_visible_text += 1
                entries.append(CoverageEntry(
                    classification=CoverageClassification.UNCLASSIFIED,
                    locator=span.locator,
                    code="UNCLASSIFIED_VISIBLE_TEXT",
                    message="Visible text span has no permitted translation-scope classification.",
                    text_snippet=span.text[:160],
                    character_count=characters,
                ))
            for diagnostic in span.diagnostics:
                if diagnostic.code == "UNKNOWN_FIELD_RESULT":
                    entries.append(CoverageEntry(
                        classification=CoverageClassification.UNKNOWN_POTENTIAL_VISIBLE_CONTENT,
                        locator=span.locator,
                        code="UNKNOWN_FIELD_RESULT",
                        message="Displayed field result is protected, but its translation-scope policy is not yet proven.",
                        text_snippet=span.text[:160],
                        character_count=characters,
                    ))
        if CoverageClassification.TRANSLATABLE in classifications_in_block:
            group.translatable_blocks += 1
        if CoverageClassification.PROTECTED in classifications_in_block:
            group.protected_blocks += 1
        if CoverageClassification.EXCLUDED_BY_POLICY in classifications_in_block:
            group.excluded_blocks += 1
        if CoverageClassification.UNCLASSIFIED in classifications_in_block:
            group.unclassified_blocks += 1
    for entry in entries:
        if entry.classification != CoverageClassification.UNKNOWN_POTENTIAL_VISIBLE_CONTENT:
            continue
        group = groups[entry.locator.story]
        group.name = entry.locator.story
        group.unknown_potential_visible_content += 1
        group.unknown_potential_characters += entry.character_count
    result_groups = sorted(groups.values(), key=lambda item: item.name)
    blockers: list[Diagnostic] = []
    unclassified = sum(group.unclassified_visible_text for group in result_groups)
    unknown = sum(group.unknown_potential_visible_content for group in result_groups)
    if unclassified:
        blockers.append(Diagnostic(code="UNCLASSIFIED_VISIBLE_TEXT", severity="blocker", message=f"{unclassified} visible text span(s) are unclassified."))
    if unknown:
        blockers.append(Diagnostic(code="UNKNOWN_POTENTIAL_VISIBLE_CONTENT", severity="blocker", message=f"{unknown} potentially visible OOXML construct(s) are not proven covered."))
    if not source_integrity_pass:
        blockers.append(Diagnostic(code="SOURCE_MUTATED", severity="blocker", message="Source integrity check failed during extraction."))
    return CoverageLedger(
        document_sha256=document.document_sha256,
        source_integrity_pass=source_integrity_pass,
        groups=result_groups,
        entries=entries,
        warnings=warnings,
        blockers=blockers,
        coverage_complete=not blockers,
        visible_text_nodes=sum(group.visible_text_nodes for group in result_groups),
        visible_blocks=sum(group.visible_blocks for group in result_groups),
        visible_characters=sum(group.visible_characters for group in result_groups),
        translatable_characters=sum(group.translatable_characters for group in result_groups),
        translatable_blocks=sum(group.translatable_blocks for group in result_groups),
        protected_characters=sum(group.protected_characters for group in result_groups),
        protected_blocks=sum(group.protected_blocks for group in result_groups),
        excluded_characters=sum(group.excluded_characters for group in result_groups),
        excluded_blocks=sum(group.excluded_blocks for group in result_groups),
        unclassified_visible_text=unclassified,
        unclassified_blocks=sum(group.unclassified_blocks for group in result_groups),
        unknown_potential_visible_content=unknown,
        unknown_potential_characters=sum(group.unknown_potential_characters for group in result_groups),
    )
