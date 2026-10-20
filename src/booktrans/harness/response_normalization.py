"""Narrow, auditable normalization for observed footnote-reference typography."""

from dataclasses import dataclass
import re

from booktrans.domain.calibration import CalibrationRequest, TranslationResult


_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUPERSCRIPT_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")


@dataclass(frozen=True)
class NormalizationChange:
    unit_uid: str
    segment_index: int
    before: str
    after: str
    character_start: int
    character_end: int
    before_characters: str
    after_characters: str
    source_reference_evidence: str
    rule_id: str = "FOOTNOTE_SUPERSCRIPT_TO_ASCII"


def normalize_footnote_superscripts(
    request: CalibrationRequest,
    response: TranslationResult,
) -> tuple[TranslationResult, list[NormalizationChange]]:
    """Normalize only source-evidenced footnote-reference superscripts.

    This is deliberately not a number formatter.  It never alters protected
    segments, cannot invent a value, and requires the exact ASCII value to be
    present in the source in a footnote-like position.
    """
    source_inline_refs = set(re.findall(r"(?<=[A-Za-z])\d+(?=\s)", request.source_text))
    source_leading_refs = set(re.findall(r"^\s*(\d+)\s", request.source_text))
    source_numbers = set(_NUMBER.findall(request.source_text)) | source_inline_refs | source_leading_refs
    evidenced_footnote_refs = set(request.footnote_reference_numbers)
    # Explicit exporter evidence is the only approved way a reference marker
    # omitted by generic prose-number extraction can enter this closed-world
    # normalizer.  This covers conversion forms such as ``wheels.2`` without
    # treating all raised numeric text as a footnote.
    source_numbers.update(evidenced_footnote_refs)
    changes: list[NormalizationChange] = []
    segments = []
    for index, segment in enumerate(response.target_segments):
        if segment.kind == "protected":
            segments.append(segment)
            continue

        def replace(match: re.Match[str]) -> str:
            value = match.group(0).translate(_SUPERSCRIPT)
            # Closed-world rule: a number merely looking like a footnote is
            # not enough.  The request exporter must supply structural
            # evidence that this exact source number is footnote-like.
            if value not in source_numbers or value not in evidenced_footnote_refs:
                return match.group(0)
            start, end = match.span()
            before = segment.text[:start]
            after = segment.text[end:]
            leading_ref = not before and value in source_leading_refs and (not after or after[0].isspace())
            inline_ref = bool(before) and not before[-1].isdigit() and value in source_inline_refs
            explicit_reference = value in evidenced_footnote_refs
            if not (leading_ref or inline_ref or explicit_reference):
                return match.group(0)
            changes.append(NormalizationChange(
                unit_uid=response.unit_uid,
                segment_index=index,
                before=segment.text,
                after=segment.text[:start] + value + segment.text[end:],
                character_start=start,
                character_end=end,
                before_characters=match.group(0),
                after_characters=value,
                source_reference_evidence=(
                    "explicit_request_footnote_reference" if explicit_reference else (
                        "inline_source_footnote_reference" if inline_ref else "leading_source_footnote_reference"
                    )
                ),
            ))
            return value

        text = _SUPERSCRIPT_RUN.sub(replace, segment.text)
        if text != segment.text:
            segments.append(segment.model_copy(update={"text": text}))
        else:
            segments.append(segment)
    return response.model_copy(update={"target_segments": segments}), changes
