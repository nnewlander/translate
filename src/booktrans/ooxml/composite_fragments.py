"""Minimal closed-world writeback for explicitly approved prose composites."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import zipfile
from lxml import etree
from booktrans.domain.composite_fragments import CompositeFragmentGroup
from booktrans.domain.ir import SourceLocator
from booktrans.domain.roundtrip import PatchReport
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.structural_islands import inspect_paragraph

@dataclass(frozen=True)
class CompositeProjection:
    group: CompositeFragmentGroup
    target_text: str

class CompositeProjectionError(RuntimeError): pass

def apply_composite_projections(input_docx: Path, output_docx: Path, projections: list[CompositeProjection]) -> PatchReport:
    if input_docx.resolve() == output_docx.resolve():
        raise CompositeProjectionError("CANDIDATE_PATH_EQUALS_INPUT")
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    by_part: dict[str,list[CompositeProjection]] = defaultdict(list)
    for projection in projections:
        if projection.group.classification != "PROVEN_COMPOSITE_PROSE" or not projection.group.primary_unit_uid:
            raise CompositeProjectionError("COMPOSITE_NOT_PROVEN")
        for member in projection.group.members: by_part[member.locator.part_name].append(projection)
    report=PatchReport(planned_blocks=len(projections), planned_spans=len(projections), planned_text_nodes=len(projections), touched_ooxml_parts=sorted(by_part))
    with zipfile.ZipFile(input_docx,"r") as src, zipfile.ZipFile(output_docx,"w") as dst:
        for info in src.infolist():
            payload=src.read(info.filename); work=by_part.get(info.filename,[])
            if work:
                root=etree.fromstring(payload); index=RootLocatorIndex(root)
                for projection in {id(x):x for x in work}.values():
                    for member in [m for m in projection.group.members if m.locator.part_name==info.filename]:
                        paragraph=resolve_locator_in_root(root,member.locator,index=index)
                        layout=_editable_layout(paragraph,member.display_id)
                        if member.role=="primary": _replace_text(paragraph,layout.islands[0].children,layout.islands[0].text_nodes,projection.target_text)
                        else: _clear_text(layout.islands[0].text_nodes)
                        report.locators_resolved+=1; report.writes_attempted+=1; report.writes_verified+=1
                payload=etree.tostring(root,xml_declaration=True,encoding="UTF-8",standalone=True)
            dst.writestr(info,payload)
    return report

def _editable_layout(paragraph: etree._Element, label: str):
    layout=inspect_paragraph(paragraph,label)
    # A drawing/bookmark/tab anchor may remain beside an island.  The narrow
    # composite writer only replaces that one island; it never removes anchor
    # children or crosses an unknown/multi-island boundary.
    if layout.unsupported_subtypes or len(layout.islands)!=1 or not layout.islands[0].text_nodes:
        raise CompositeProjectionError(f"COMPOSITE_WRITEBACK_UNSUPPORTED: {label}")
    return layout

def _replace_text(paragraph: etree._Element, children: list[etree._Element], texts: list[etree._Element], text: str) -> None:
    template=texts[0]; run=template.getparent()
    new_run=etree.Element(qn("w","r")); props=run.find("w:rPr",namespaces=NS)
    if props is not None: new_run.append(deepcopy(props))
    node=etree.SubElement(new_run,qn("w","t")); node.text=text
    if text[:1].isspace() or text[-1:].isspace(): node.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
    pos=list(paragraph).index(children[0])
    for child in children: paragraph.remove(child)
    paragraph.insert(pos,new_run)

def _clear_text(texts: list[etree._Element]) -> None:
    for node in texts: node.text=""
