from lxml import etree
import pytest
from zipfile import ZipFile

from booktrans.domain.ir import SourceLocator
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.ooxml.locator import RootLocatorIndex
from booktrans.ooxml.namespaces import NS
from booktrans.ooxml.structural_islands import equivalent_alternate_paragraphs, inspect_paragraph
from booktrans.ooxml.tagged_projection import TaggedProjection, TaggedProjectionError, _project_one
from booktrans.ooxml.translation_view import TranslationViewBuilder
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.domain.models import SourceSnapshot
from booktrans.harness.source_integrity import fingerprint


def _root(inner: str):
    return etree.fromstring(
        f'<w:document xmlns:w="{NS["w"]}"><w:body><w:p>{inner}</w:p></w:body></w:document>'.encode()
    )


def _layout(inner: str):
    root = _root(inner)
    paragraph = root.xpath('.//w:p', namespaces=NS)[0]
    return root, paragraph, inspect_paragraph(paragraph, 'unit.hyperlink-tab')


def test_hyperlink_text_tab_text_has_coherent_ordered_islands():
    _, paragraph, layout = _layout(
        '<w:hyperlink w:anchor="toc"><w:r><w:t>Before</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>After</w:t></w:r></w:hyperlink>'
    )
    assert layout.features == {'hyperlink_with_tab'}
    assert layout.unsupported_subtypes == set()
    assert [island.island_id for island in layout.islands] == ['island.001', 'island.002']
    assert [node.text for island in layout.islands for node in island.text_nodes] == ['Before', 'After']
    assert [layout.node_path_to_island[node.getroottree().getpath(node)] for node in paragraph.xpath('.//w:t', namespaces=NS)] == [
        'island.001', 'island.002'
    ]
    assert [anchor.kind for anchor in layout.anchors] == ['tab']
    assert len(paragraph.xpath('.//w:tab', namespaces=NS)) == 1
    assert paragraph.xpath('./w:hyperlink/@w:anchor', namespaces=NS) == ['toc']


def test_hyperlink_multiple_tabs_and_text_groups_remain_ordered():
    _, _, layout = _layout(
        '<w:hyperlink w:anchor="toc"><w:r><w:t>A</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>B</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>C</w:t></w:r></w:hyperlink>'
    )
    assert [island.island_id for island in layout.islands] == ['island.001', 'island.002', 'island.003']
    assert [node.text for island in layout.islands for node in island.text_nodes] == ['A', 'B', 'C']
    assert [anchor.kind for anchor in layout.anchors] == ['tab', 'tab']


def test_simple_hyperlink_and_non_hyperlink_layouts_are_unchanged():
    _, _, hyperlink = _layout('<w:hyperlink w:anchor="toc"><w:r><w:t>Only text</w:t></w:r></w:hyperlink>')
    assert hyperlink.features == set()
    assert hyperlink.unsupported_subtypes == set()
    assert [island.island_id for island in hyperlink.islands] == ['island.001']
    _, _, plain = _layout('<w:r><w:t>Before</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>After</w:t></w:r>')
    assert plain.features == set()
    assert [island.island_id for island in plain.islands] == ['island.001', 'island.002']


def test_other_hyperlink_structure_remains_fail_closed():
    _, _, layout = _layout('<w:hyperlink w:anchor="toc"><w:r><w:t>Before</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>After</w:t></w:r></w:hyperlink>')
    assert layout.features == set()
    assert layout.unsupported_subtypes == {'hyperlink_nontext_structure'}


def test_tagged_projection_preserves_hyperlink_and_tab_while_rewriting_text():
    root, _, layout = _layout(
        '<w:hyperlink w:anchor="toc"><w:r><w:t>Before</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>After</w:t></w:r></w:hyperlink>'
    )
    locator = SourceLocator(part_name='word/document.xml', story='body', xml_path='/word/document.xml/paragraph[1]/text[1]', container_path='/word/document.xml/paragraph[1]', paragraph_ordinal=1)
    view = TranslationView(unit_uid='unit.hyperlink-tab', display_id='body.p.test', source_text='BeforeAfter', base_template_locator=locator, structural_anchors=layout.anchors)
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=[StructuredTargetSegment(text='前', kind='plain', island_ref='island.001'), StructuredTargetSegment(text='后', kind='plain', island_ref='island.002')], target_text='前后')
    attempted, verified = _project_one(root, RootLocatorIndex(root), TaggedProjection(view, candidate))
    assert (attempted, verified) == (2, 2)
    assert layout.features == {'hyperlink_with_tab'}
    assert root.xpath('count(.//w:hyperlink)', namespaces=NS) == 1.0
    assert root.xpath('.//w:hyperlink/@w:anchor', namespaces=NS) == ['toc']
    assert len(root.xpath('.//w:hyperlink/w:r/w:tab', namespaces=NS)) == 1
    assert [node.text for node in root.xpath('.//w:hyperlink//w:t', namespaces=NS)] == ['前', '后']


def test_translation_view_uses_the_same_hyperlink_tab_mapping(tmp_path):
    source = tmp_path / 'hyperlink-tab.docx'
    with ZipFile(source, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', etree.tostring(_root(
            '<w:hyperlink w:anchor="toc"><w:r><w:t>Before</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>After</w:t></w:r></w:hyperlink>'
        )))
    snap = fingerprint(source)
    document = DocumentExtractor().extract(source, SourceSnapshot(path=str(source), sha256=snap.sha256, size_bytes=snap.size_bytes, mtime_utc=snap.mtime_utc))[0]
    view = TranslationViewBuilder().build(source, document)[0]
    assert [segment.island_ref for segment in view.segments] == ['island.001', 'island.002']
    assert [island.island_id for island in view.text_islands] == ['island.001', 'island.002']
    assert all(segment.island_ref != 'island.unsupported' for segment in view.segments)


def test_equivalent_alternate_content_branches_require_matching_text_and_structure():
    root = etree.fromstring(f'''<w:document xmlns:w="{NS['w']}" xmlns:mc="{NS['mc']}"><w:body>
      <mc:AlternateContent><mc:Choice Requires="w14"><w:txbxContent><w:p><w:r><w:t>Choice</w:t></w:r></w:p></w:txbxContent></mc:Choice>
      <mc:Fallback><w:txbxContent><w:p><w:r><w:t>Choice</w:t></w:r></w:p></w:txbxContent></mc:Fallback></mc:AlternateContent>
    </w:body></w:document>'''.encode())
    paragraph = root.xpath('.//mc:Choice//w:p', namespaces=NS)[0]
    paired = equivalent_alternate_paragraphs(paragraph)
    assert paired is not None
    assert len(paired) == 2
    root.xpath('.//mc:Fallback//w:t', namespaces=NS)[0].text = 'Different'
    assert equivalent_alternate_paragraphs(paragraph) is None
