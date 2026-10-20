from datetime import UTC, datetime

from booktrans.domain.ir import BlockIR, DocumentIR, DocumentStoryIR, SourceLocator
from booktrans.domain.models import SourceSnapshot
from booktrans.ooxml.chapter_pilot import _chapter_bounds


def _block(text: str, ordinal: int) -> BlockIR:
    locator = SourceLocator(part_name="word/document.xml", story="body", xml_path=f"/word/document.xml/paragraph[{ordinal}]", paragraph_ordinal=ordinal)
    return BlockIR(
        unit_uid=f"unit-{ordinal}", display_id=f"body.p.{ordinal:05d}", story="body", part_name="word/document.xml",
        block_type="body_paragraph", content_role="paragraph", locator=locator,
        source_plain_text=text, source_hash="hash", structure_fingerprint="structure",
    )


def test_chapter_one_boundary_excludes_part_two_and_verifies_chapter_two() -> None:
    blocks = [_block("What Is Software Engineering?", 10), _block("chapter one", 11), _block("PART II", 12), _block("Culture", 13), _block("How to Work Well on Teams", 14)]
    document = DocumentIR(
        document_sha256="sha", source=SourceSnapshot(path="source.docx", sha256="sha", size_bytes=1), extracted_at_utc=datetime.now(UTC),
        stories=[DocumentStoryIR(story="body", part_name="word/document.xml", blocks=blocks)],
    )
    start, end, next_chapter = _chapter_bounds(document)
    assert (start.locator.paragraph_ordinal, end.locator.paragraph_ordinal, next_chapter.locator.paragraph_ordinal) == (10, 12, 14)
