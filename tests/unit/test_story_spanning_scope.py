import pytest

from booktrans.ooxml.story_spanning_scope import StoryScopeEvidence, close_story_spanning_scope


def _e(uid: str, disposition: str) -> StoryScopeEvidence:
    return StoryScopeEvidence("chapter", uid, uid, "word/footer1.xml", disposition, "fixture evidence")


def test_content_footer_and_running_decoration_are_explicitly_accounted_for() -> None:
    report = close_story_spanning_scope({"body"}, [_e("footnote", "INCLUDE_CHAPTER_CONTENT"), _e("header", "EXCLUDE_RUNNING_DECORATION")])
    assert report["final_scope_count"] == 2
    assert report["excluded_running_decoration_count"] == 1


def test_ambiguous_footer_stays_review_required_and_cannot_silently_enter_scope() -> None:
    report = close_story_spanning_scope({"body"}, [_e("ambiguous", "REVIEW_REQUIRED")])
    assert report["final_scope_count"] == 1
    assert report["review_required_count"] == 1


def test_duplicate_evidence_or_an_ordinary_body_member_is_a_blocker() -> None:
    with pytest.raises(ValueError, match="BODY_DUPLICATE"):
        close_story_spanning_scope({"body"}, [_e("body", "INCLUDE_CHAPTER_CONTENT")])
    with pytest.raises(ValueError, match="DUPLICATE_EVIDENCE"):
        close_story_spanning_scope(set(), [_e("footer", "INCLUDE_CHAPTER_CONTENT"), _e("footer", "REVIEW_REQUIRED")])
