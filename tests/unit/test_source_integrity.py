from booktrans.harness.source_integrity import assert_unchanged, fingerprint
from booktrans.ooxml.inspector import OOXMLInspector


def test_inspection_keeps_source_hash_unchanged(rich_docx):
    before = fingerprint(rich_docx)
    OOXMLInspector().inspect(rich_docx)
    after = fingerprint(rich_docx)
    assert before.sha256 == after.sha256
    assert before.size_bytes == after.size_bytes
    assert_unchanged(before, after, rich_docx)
