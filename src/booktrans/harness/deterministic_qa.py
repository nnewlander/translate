"""Pre-patch deterministic checks for M4 synthetic candidate text."""

import re
from collections.abc import Sequence
from typing import Any

from booktrans.domain.calibration import (
    ExplicitNumericRepresentationEquivalence,
    VerifiedFootnoteNumericBinding,
)
from booktrans.domain.models import Diagnostic
from booktrans.domain.synthetic import CandidateQAResult, SyntheticCandidate


# Keep a grouped thousands literal whole so ``1,000`` and ``1000`` have the
# same deterministic numeric identity.  Splitting the former into ``1`` and
# ``000`` made an unchanged semantic value look like an unexpected target
# number after normal Chinese punctuation/formatting projection.
# A period cannot be globally excluded as a left boundary: source extraction
# represents inclusive ranges as ``0...32,000``.  The first numeric literal
# still consumes ordinary decimals as one token, while the relaxed boundary
# lets the range endpoint retain its own identity.
# A bare sentence-period numeric suffix is a closed-world D050 marker and
# must remain invisible to generic numeric extraction until exact verified
# evidence admits it.  Keep the range endpoint exception for ``0...32``.
NUMBER = re.compile(r"(?<![A-Za-z0-9_])(?<![^.]\.)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
INLINE_ALPHA_NUMBER = re.compile(r"(?<=[A-Za-z])\d+(?!\d)")
URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>]+", re.IGNORECASE)
ISBN = re.compile(r"\bISBN(?:-1[03])?\s*[:]?\s*[0-9Xx][0-9Xx\- ]{8,}[0-9Xx]\b", re.IGNORECASE)
_CHINESE_INTEGER = re.compile(r"^[零〇一二三四五六七八九十百千万亿两]+$")
_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_CHINESE_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
_ENGLISH_CARDINAL_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_ENGLISH_CARDINAL_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}


class DeterministicCandidateQA:
    def validate(
        self,
        candidate: SyntheticCandidate,
        *,
        known_unit_ids: set[str],
        protected_text: list[str] | None = None,
        footnote_reference_numbers: list[str] | None = None,
        verified_footnote_numeric_bindings: list[VerifiedFootnoteNumericBinding] | None = None,
        explicit_numeric_representation_equivalences: list[ExplicitNumericRepresentationEquivalence] | None = None,
        source_segments: Sequence[Any] | None = None,
        target_segments: Sequence[Any] | None = None,
    ) -> CandidateQAResult:
        diagnostics: list[Diagnostic] = []
        if candidate.unit_uid not in known_unit_ids:
            diagnostics.append(_failure("UNKNOWN_UNIT_ID", "Candidate does not refer to a known source BlockIR unit."))
        if candidate.source_text and not candidate.target_text:
            diagnostics.append(_failure("EMPTY_TARGET", "Source has translatable text but synthetic target is empty."))
        protected_urls = {token for token in protected_text or [] if URL.fullmatch(token)}
        checks = (
            ("NUMBER_PRESERVATION", _numeric_tokens),
            # A protected URL's exact text is authoritative.  Do not create a
            # competing generic token from source punctuation around it.
            ("URL_PRESERVATION", lambda text: [token for token in _url_tokens(text) if token not in protected_urls]),
            ("EMAIL_PRESERVATION", lambda text: EMAIL.findall(text)),
            ("DOI_PRESERVATION", lambda text: DOI.findall(text)),
            ("ISBN_PRESERVATION", lambda text: ISBN.findall(text)),
        )
        for code, tokens in checks:
            source_tokens = tokens(candidate.source_text)
            if code == "URL_PRESERVATION" and protected_urls:
                source_tokens = _protected_aware_source_url_tokens(candidate.source_text, protected_urls)
            # This list is closed-world exporter evidence.  It is intentionally
            # not inferred from raised text, punctuation, or a numeric suffix.
            # A verified source footnote marker remains a required numeric
            # token even when conversion layout prevents the generic extractor
            # from seeing it in normal prose.
            if code == "NUMBER_PRESERVATION":
                source_tokens = _numeric_tokens_with_verified_footnote_boundaries(
                    candidate.source_text,
                    unit_uid=candidate.unit_uid,
                    bindings=verified_footnote_numeric_bindings,
                )
                source_tokens = _apply_explicit_numeric_representation_equivalences(
                    source_tokens,
                    candidate.source_text,
                    candidate.target_text,
                    unit_uid=candidate.unit_uid,
                    equivalences=explicit_numeric_representation_equivalences,
                )
                approved_target_numeric_tokens = _explicit_equivalent_target_numeric_tokens(
                    candidate.source_text,
                    candidate.target_text,
                    unit_uid=candidate.unit_uid,
                    equivalences=explicit_numeric_representation_equivalences,
                )
                for value in footnote_reference_numbers or []:
                    if value not in source_tokens:
                        source_tokens.append(value)
            target_tokens = tokens(candidate.target_text)
            # ``NUMBER`` intentionally normalizes grouped literals (``1,000``
            # -> ``1000``), so only that check compares the normalized token
            # sets.  URL/DOI/protected-text checks retain exact-substring
            # semantics because their extraction normalization is different.
            target_presence = target_tokens if code == "NUMBER_PRESERVATION" else candidate.target_text
            missing = [token for token in source_tokens if token not in target_presence]
            if missing:
                diagnostics.append(_failure(code, f"Required source token(s) are absent from target: {missing[:3]}"))
            if code == "NUMBER_PRESERVATION":
                extra = [token for token in target_tokens if token not in source_tokens and token not in approved_target_numeric_tokens]
                if extra:
                    diagnostics.append(_failure(code, f"Unexpected target numeric token(s): {extra[:3]}"))
        for token in protected_text or []:
            if token and token not in candidate.target_text:
                diagnostics.append(_failure("PROTECTED_TEXT", f"Protected source token is absent from target: {token[:80]}"))
        diagnostics.extend(_numeric_anchor_ownership_diagnostics(source_segments, target_segments))
        return CandidateQAResult(unit_uid=candidate.unit_uid, passed=not diagnostics, diagnostics=diagnostics)


def _failure(code: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="blocker", message=message)


def _numeric_anchor_ownership_diagnostics(
    source_segments: Sequence[Any] | None,
    target_segments: Sequence[Any] | None,
) -> list[Diagnostic]:
    """Reject an extra numeric literal outside its one source anchor.

    This narrow check applies only when the source has one *standalone*,
    anchored numeric owner for a token and the paired target owner retains it
    exactly once.  It deliberately does nothing for repeated source numbers,
    mixed prose/numeric source runs, D050, or D051 representations.
    """
    if not isinstance(source_segments, Sequence) or not isinstance(target_segments, Sequence):
        return []
    if len(source_segments) != len(target_segments):
        return []
    source_tokens = [_numeric_tokens(str(getattr(segment, "text", ""))) for segment in source_segments]
    diagnostics: list[Diagnostic] = []
    for owner_index, tokens in enumerate(source_tokens):
        source = source_segments[owner_index]
        target = target_segments[owner_index]
        if (
            len(tokens) != 1
            or str(getattr(source, "text", "")).strip() != tokens[0]
            or getattr(source, "kind", None) != "anchored"
            or not getattr(source, "anchor_ref", None)
            or getattr(target, "kind", None) != getattr(source, "kind", None)
            or getattr(target, "anchor_ref", None) != getattr(source, "anchor_ref", None)
            or getattr(target, "island_ref", None) != getattr(source, "island_ref", None)
        ):
            continue
        token = tokens[0]
        if sum(token in items for items in source_tokens) != 1:
            continue
        if _numeric_tokens(str(getattr(target, "text", ""))).count(token) != 1:
            continue
        duplicate_indices = [
            index for index, candidate in enumerate(target_segments)
            if index != owner_index and token in _numeric_tokens(str(getattr(candidate, "text", "")))
        ]
        if duplicate_indices:
            diagnostics.append(_failure(
                "NUMERIC_ANCHOR_DUPLICATION",
                f"Numeric token {token!r} is uniquely owned by source anchor index {owner_index} "
                f"but is duplicated in non-owner target segment(s): {duplicate_indices}.",
            ))
    return diagnostics


def _url_tokens(text: str) -> list[str]:
    """Extract generic URLs without absorbing terminal prose punctuation.

    The trim is deliberately terminal-only: dots in hosts, paths and query
    values remain untouched, while a dot/comma/colon/semicolon/exclamation or
    question mark immediately ending a prose URL is not made contractual.
    Protected URLs bypass this generic path entirely.
    """
    # A protected URL may be followed by a Chinese closing parenthesis or
    # sentence stop in its adjacent anchored segment.  Those delimiters are
    # typography, not part of the protected literal.  Keep trimming strictly
    # terminal so URL-path punctuation remains contractual.
    return [match.group(0).rstrip(".,;:!?。！？）】》」』") for match in URL.finditer(text)]


def _protected_aware_source_url_tokens(text: str, protected_urls: set[str]) -> list[str]:
    """Use a protected literal, rather than adjacent source punctuation, as its URL token."""
    tokens: list[str] = []
    terminal_delimiters = ".,;:!?。！？)]}）】》」』"
    for match in URL.finditer(text):
        token = match.group(0)
        protected = next(
            (literal for literal in protected_urls if token.startswith(literal) and set(token[len(literal):]) <= set(terminal_delimiters)),
            None,
        )
        tokens.append(protected or token.rstrip(".,;:!?。！？）】》」』"))
    return tokens


def _numeric_tokens(text: str) -> list[str]:
    """Return ordinary numeric tokens plus attached ASCII reference numbers.

    DOCX conversion can represent a footnote-like marker as ``Ben1``.  It is
    still a source numeric token until the exporter supplies explicit footnote
    evidence; treating it as invisible would allow ``Ben2`` or ``Ben¹`` to
    evade the same preservation contract applied to ordinary text.
    """
    return [
        *(match.group(0).replace(",", "") for match in NUMBER.finditer(text)),
        *INLINE_ALPHA_NUMBER.findall(text),
    ]


def _numeric_tokens_with_verified_footnote_boundaries(
    text: str,
    *,
    unit_uid: str,
    bindings: list[VerifiedFootnoteNumericBinding] | None,
) -> list[str]:
    """Apply D050 only to one exact, evidence-bound flattened marker.

    A matching record removes its complete exact marker before generic token
    extraction and restores only the pre-proven ordinary numeric tokens plus
    the independently preserved footnote number. No punctuation or typography
    is interpreted here.
    """
    remaining = text
    injected: list[str] = []
    for binding in bindings or []:
        marker = binding.exact_source_marker_token
        if binding.unit_uid != unit_uid:
            continue
        if not marker or binding.footnote_number not in marker:
            continue
        if remaining.count(marker) != 1:
            continue
        remaining = remaining.replace(marker, "", 1)
        injected.extend(binding.pre_footnote_numeric_tokens)
        injected.append(binding.footnote_number)
    return [*_numeric_tokens(remaining), *injected]


def _apply_explicit_numeric_representation_equivalences(
    source_tokens: list[str],
    source_text: str,
    target_text: str,
    *,
    unit_uid: str,
    equivalences: list[ExplicitNumericRepresentationEquivalence] | None,
) -> list[str]:
    """Apply D051 only when the complete reviewed equivalence exactly matches.

    There is no arithmetic or numeral parsing here. A matching evidence record
    merely removes the already-enumerated literal numeric token sequence from
    its one exact source span; every number outside that span remains subject
    to ordinary numeric QA.
    """
    remaining_source = source_text
    result = list(source_tokens)
    for equivalence in equivalences or []:
        span = equivalence.exact_source_span
        if equivalence.unit_uid != unit_uid or not span:
            continue
        if not _numeric_equivalence_contract_valid(equivalence):
            continue
        if remaining_source.count(span) != 1:
            continue
        if _numeric_tokens(span) != equivalence.source_numeric_tokens:
            continue
        if equivalence.approved_target_representation not in target_text:
            continue
        source_without_span = remaining_source.replace(span, "", 1)
        if any(token in _numeric_tokens(source_without_span) for token in equivalence.source_numeric_tokens):
            # The primitive has no positional token model. Refuse a duplicate
            # rather than risk suppressing a same-valued number outside the
            # exact approved source span.
            continue
        remaining_source = source_without_span
        for token in equivalence.source_numeric_tokens:
            try:
                result.remove(token)
            except ValueError:
                # A D050 binding may already have removed an overlapping token;
                # do not make this equivalence a fallback parser.
                break
    return result


def _explicit_equivalent_target_numeric_tokens(
    source_text: str,
    target_text: str,
    *,
    unit_uid: str,
    equivalences: list[ExplicitNumericRepresentationEquivalence] | None,
) -> list[str]:
    """Allow only target literals named by a fully exact D051 record."""
    allowed: list[str] = []
    for equivalence in equivalences or []:
        span = equivalence.exact_source_span
        target = equivalence.approved_target_representation
        if (
            equivalence.unit_uid != unit_uid
            or not span
            or not target
            or not _numeric_equivalence_contract_valid(equivalence)
            or source_text.count(span) != 1
            or _numeric_tokens(span) != equivalence.source_numeric_tokens
            or target not in target_text
        ):
            continue
        allowed.extend(_numeric_tokens(target))
    return allowed


def _chinese_integer_value(text: str) -> int | None:
    """Parse only closed-world, unambiguous Chinese integer notation.

    This is used solely to verify a Human-reviewed D051 artifact.  It never
    discovers an equivalence from arbitrary target prose.
    """
    if not _CHINESE_INTEGER.fullmatch(text):
        return None
    total = 0
    section = 0
    digit: int | None = None
    for char in text:
        if char in _CHINESE_DIGITS:
            if digit is not None:
                return None
            digit = _CHINESE_DIGITS[char]
        elif char in _CHINESE_SMALL_UNITS:
            unit = _CHINESE_SMALL_UNITS[char]
            value = 1 if digit is None else digit
            section += value * unit
            digit = None
        elif char in _CHINESE_LARGE_UNITS:
            unit = _CHINESE_LARGE_UNITS[char]
            section += 0 if digit is None else digit
            if section == 0:
                return None
            total += section * unit
            section = 0
            digit = None
        else:  # Defensive: the regex above should make this unreachable.
            return None
    return total + section + (digit or 0)


def _numeric_equivalence_contract_valid(equivalence: ExplicitNumericRepresentationEquivalence) -> bool:
    if equivalence.reviewed_source_semantic_numeric_value != equivalence.reviewed_target_semantic_numeric_value:
        return False
    if equivalence.equivalence_kind == "ENGLISH_CARDINAL_TO_LITERAL":
        source_value = _english_cardinal_value(equivalence.exact_source_span)
        target_values = _numeric_tokens(equivalence.approved_target_representation)
        try:
            reviewed_value = int(equivalence.reviewed_source_semantic_numeric_value)
        except ValueError:
            return False
        # One unqualified source cardinal and one target literal only.  This
        # deliberately excludes vague, range, comparator and approximate forms.
        return (
            source_value == reviewed_value
            and len(target_values) == 1
            and int(target_values[0]) == reviewed_value
            and not any((
                equivalence.source_comparison_operator,
                equivalence.target_comparison_operator,
                equivalence.source_counter_context,
                equivalence.target_counter_context,
            ))
        )
    qualifiers = (
        equivalence.source_comparison_operator,
        equivalence.target_comparison_operator,
        equivalence.source_counter_context,
        equivalence.target_counter_context,
    )
    # Legacy D051 artifacts were already approved under the older exact-span
    # contract.  Do not reinterpret them; new Chinese-integer evidence must
    # provide all four qualifiers.
    if not any(qualifiers):
        return True
    if any(value is None or not value.strip() for value in qualifiers):
        return False
    if equivalence.source_comparison_operator != equivalence.target_comparison_operator:
        return False
    if equivalence.source_counter_context != equivalence.target_counter_context:
        return False
    operator_phrases = {
        "MORE_THAN": ("more than", "超过"),
        "LESS_THAN": ("less than", "少于"),
        "AT_LEAST": ("at least", "至少"),
        "AT_MOST": ("at most", "至多"),
    }
    counter_phrases = {"USAGES": ("usages", "次"), "USERS": ("users", "用户")}
    operator = operator_phrases.get(equivalence.source_comparison_operator)
    counter = counter_phrases.get(equivalence.source_counter_context)
    if operator is None or counter is None:
        return False
    if operator[0] not in equivalence.exact_source_span.casefold() or operator[1] not in equivalence.approved_target_representation:
        return False
    if counter[0] not in equivalence.exact_source_span.casefold() or counter[1] not in equivalence.approved_target_representation:
        return False
    # Closed-world counter labels must not be silently swapped in the target.
    if any(
        target_marker in equivalence.approved_target_representation and context != equivalence.source_counter_context
        for context, (_, target_marker) in counter_phrases.items()
    ):
        return False
    numeral = _target_chinese_integer(equivalence.approved_target_representation)
    if numeral is None:
        return False
    try:
        return int(equivalence.reviewed_source_semantic_numeric_value) == numeral
    except ValueError:
        return False


def _english_cardinal_value(text: str) -> int | None:
    """Parse only an unqualified English cardinal for the opt-in D051 form."""
    words = text.casefold().replace("-", " ").split()
    if not words or any(word not in _ENGLISH_CARDINAL_WORDS and word not in _ENGLISH_CARDINAL_SCALES for word in words):
        return None
    total = 0
    current = 0
    for word in words:
        if word in _ENGLISH_CARDINAL_WORDS:
            value = _ENGLISH_CARDINAL_WORDS[word]
            if value >= 20 and current:
                return None
            current += value
        elif word == "hundred":
            if current != 1:
                return None
            current *= 100
        else:
            scale = _ENGLISH_CARDINAL_SCALES[word]
            if current != 1:
                return None
            total += current * scale
            current = 0
    value = total + current
    return value if value > 0 else None


def _target_chinese_integer(text: str) -> int | None:
    """Return exactly one unambiguous Chinese integer embedded in evidence."""
    matches_with_offsets = list(re.finditer(r"[零〇一二三四五六七八九十百千万亿两]+", text))
    if any(
        match.end() < len(text) and text[match.end()] in {"多", "余", "约", "近", "数", "几", "半"}
        for match in matches_with_offsets
    ):
        return None
    matches = [match.group(0) for match in matches_with_offsets]
    values = [_chinese_integer_value(match) for match in matches]
    parsed = [value for value in values if value is not None]
    return parsed[0] if len(parsed) == 1 and len(parsed) == len(matches) else None
