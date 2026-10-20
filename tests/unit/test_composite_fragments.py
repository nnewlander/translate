from pathlib import Path
from zipfile import ZipFile
from lxml import etree
import pytest
from booktrans.domain.composite_fragments import CompositeFragmentGroup, CompositeFragmentMember
from booktrans.domain.ir import SourceLocator
from booktrans.ooxml.composite_fragments import CompositeProjection, CompositeProjectionError, apply_composite_projections
from booktrans.ooxml.namespaces import NS

def _docx(path: Path):
    doc='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello </w:t></w:r></w:p><w:p><w:r><w:t>world.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'''
    with ZipFile(path,'w') as z: z.writestr('word/document.xml',doc)
def _member(uid,id,ordinal,role): return CompositeFragmentMember(unit_uid=uid,display_id=id,locator=SourceLocator(part_name='word/document.xml',story='body',xml_path=f'/word/document.xml/paragraph[{ordinal}]',paragraph_ordinal=ordinal),source_text='',role=role,container_kind='body')
def _group(): return CompositeFragmentGroup(group_id='g',classification='PROVEN_COMPOSITE_PROSE',primary_unit_uid='a',members=[_member('a','body.p.1',1,'primary'),_member('b','textbox.1',2,'secondary')],source_combined_text='Hello world.',confidence_reason='fixture')
def test_proven_composite_replaces_primary_and_clears_secondary_preserving_paragraphs(tmp_path):
    source=tmp_path/'s.docx'; out=tmp_path/'o.docx'; _docx(source); before=source.read_bytes(); apply_composite_projections(source,out,[CompositeProjection(_group(),'你好，世界。')])
    with ZipFile(out) as z: root=etree.fromstring(z.read('word/document.xml'))
    assert source.read_bytes()==before
    assert root.xpath('.//w:p[1]//w:t/text()',namespaces=NS)==['你好，世界。']; assert root.xpath('.//w:p[2]//w:t/text()',namespaces=NS)==[]; assert len(root.xpath('.//w:p',namespaces=NS))==2
def test_unknown_group_and_unrelated_secondary_are_rejected(tmp_path):
    source=tmp_path/'s.docx'; out=tmp_path/'o.docx'; _docx(source); g=_group().model_copy(update={'classification':'UNKNOWN'})
    with pytest.raises(CompositeProjectionError,match='COMPOSITE_NOT_PROVEN'): apply_composite_projections(source,out,[CompositeProjection(g,'x')])

def test_adjacent_paragraphs_are_not_automatically_a_composite(tmp_path):
    source=tmp_path/'s.docx'; out=tmp_path/'o.docx'; _docx(source)
    group=CompositeFragmentGroup(group_id='adjacent-only',classification='POSSIBLE_COMPOSITE_PROSE',primary_unit_uid='a',members=_group().members,source_combined_text='Hello world.',confidence_reason='adjacency alone')
    with pytest.raises(CompositeProjectionError,match='COMPOSITE_NOT_PROVEN'):
        apply_composite_projections(source,out,[CompositeProjection(group,'不应写入')])
