"""Allowlisted recovery execution.  Codex is not on this path."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping
import re

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.harness.deterministic_qa import NUMBER, URL, _numeric_tokens, _url_tokens
from booktrans.production_worker.numeric_recovery import failing_number_segment_indices
from booktrans.production_worker.pipeline import repair_with_contract_boundary
from booktrans.production_worker.qa import QaFn
from booktrans.production_worker.selection import overlay_verified_d050_bindings
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.translation_provider import TranslationProviderRequest


HANDLER_QWEN_NARROW_REPAIR = "QWEN_NARROW_REPAIR"
HANDLER_DETERMINISTIC_EVIDENCE = "DETERMINISTIC_EVIDENCE"
HANDLER_SUPERVISOR_RETRY = "SUPERVISOR_RETRY"
ALLOWLISTED_HANDLERS = frozenset({HANDLER_QWEN_NARROW_REPAIR, HANDLER_DETERMINISTIC_EVIDENCE})
ALLOWLISTED_POLICIES = frozenset({"NUMERIC_REPRESENTATION", "DOI_PRESERVATION", "D050_EXACT_EVIDENCE"})
QWEN_REPAIR_POLICIES = frozenset({"NUMERIC_REPRESENTATION", "DOI_PRESERVATION"})


# Source-owned Index locators are structural data, never translated content.
_INDEX_LOCATOR_SUFFIX = re.compile(
    r"(?P<delimiter>\s*,\s*)(?P<locator>\d+(?:\s*[-–]\s*\d+)?(?:\s+\d+(?:\s*[-–]\s*\d+)?)*)\s*$"
)
_INDEX_LOCATOR_ONLY = re.compile(
    r"^\s*(?P<locator>\d+(?:\s*[-–]\s*\d+)?(?:\s+\d+(?:\s*[-–]\s*\d+)?)*)\s*$"
)
_TERMINAL_INDEX_DELIMITER = re.compile(r"\s*[,，]\s*$")
_NUMERIC_RANGE_DELIMITER = re.compile(r"^\s*[-–]\s*$")


def restore_exact_source_urls(request: CalibrationRequest, candidate: TranslationResult) -> TranslationResult | None:
    """Restore one source-owned URL only when its segment ownership is exact.

    This is deliberately a structural projection, not URL parsing or text
    normalization.  Every source URL must occur once, occupy whole source
    segments, and have a one-to-one target segment binding.  ``None`` is the
    fail-closed result for ambiguity or any unrelated URL shape.
    """
    if len(request.source_segments) != len(candidate.target_segments):
        return None
    source_text = "".join(segment.text for segment in request.source_segments)
    if source_text != request.source_text:
        return None
    urls = _url_tokens(source_text)
    if not urls or len(set(urls)) != len(urls):
        return None
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for segment in request.source_segments:
        offsets.append((cursor, cursor + len(segment.text)))
        cursor += len(segment.text)
    replacements: dict[int, str] = {}
    target_text = "".join(segment.text for segment in candidate.target_segments)
    for url in urls:
        if source_text.count(url) != 1:
            return None
        # Do not publish an authority descendant for an already-correct head.
        if target_text.count(url) == 1:
            continue
        match = next((item for item in URL.finditer(source_text) if item.group(0).rstrip(".,;:!?。！？）】》」』") == url), None)
        if match is None:
            return None
        covered = [index for index, (start, end) in enumerate(offsets) if start >= match.start() and end <= match.end()]
        if not covered or offsets[covered[0]][0] != match.start() or offsets[covered[-1]][1] != match.end():
            return None
        for index in covered:
            source, target = request.source_segments[index], candidate.target_segments[index]
            if (
                source.kind != target.kind
                or source.anchor_ref != target.anchor_ref
                or source.island_ref != target.island_ref
            ):
                return None
            replacements[index] = source.text
    if not replacements:
        return candidate
    repaired = candidate.model_copy(update={
        "target_segments": [
            segment if index not in replacements else StructuredTargetSegment(
                text=replacements[index], kind=segment.kind,
                anchor_ref=segment.anchor_ref, island_ref=segment.island_ref,
            )
            for index, segment in enumerate(candidate.target_segments)
        ]
    })
    repaired_text = "".join(segment.text for segment in repaired.target_segments)
    if any(repaired_text.count(url) != 1 for url in urls):
        return None
    return repaired


def materialize_index_locator(request: CalibrationRequest, candidate: TranslationResult) -> TranslationResult | None:
    """Restore exact terminal Index locators from source-owned slots.

    ``None`` means a conflicting numeric form was found and callers must
    fail closed.  An unchanged result means there is no independent locator
    to materialize, or it is already exact.
    """
    if len(request.source_segments) != len(candidate.target_segments):
        return None
    replacements: dict[int, str] = {}
    for index, (source, target) in enumerate(zip(request.source_segments, candidate.target_segments, strict=True)):
        if (
            source.kind != "anchored" or not source.anchor_ref
            or target.kind != source.kind or target.anchor_ref != source.anchor_ref
            or target.island_ref != source.island_ref
        ):
            return None
        match = _INDEX_LOCATOR_SUFFIX.search(source.text)
        only_locator = _INDEX_LOCATOR_ONLY.match(source.text)
        if match is None and only_locator is None:
            continue
        locator = (match or only_locator).group("locator")
        delimiter = "" if match is None else match.group("delimiter")
        source_prefix = "" if match is None else source.text[:match.start("delimiter")]
        target_suffix = _INDEX_LOCATOR_SUFFIX.search(target.text)
        if target_suffix is not None and target_suffix.group("locator") == locator:
            if target_suffix.group("delimiter") != delimiter:
                return None
            continue
        # A source-owned locator can retain its terminal entry delimiter in
        # its own anchored slot.  The numeric literal is already exact, so it
        # is not conflicting evidence that should prevent materializing a
        # later missing locator in the same Index entry.
        if only_locator is not None and target.text.strip().rstrip(",，").strip() == locator:
            continue
        if _numeric_tokens(target.text):
            return None
        if source_prefix.strip():
            semantic = _TERMINAL_INDEX_DELIMITER.sub("", target.text)
            replacements[index] = semantic.rstrip() + delimiter + locator
        else:
            replacements[index] = locator
    if not replacements:
        return candidate
    return candidate.model_copy(update={
        "target_segments": [
            segment if index not in replacements else StructuredTargetSegment(
                text=replacements[index], kind=segment.kind,
                anchor_ref=segment.anchor_ref, island_ref=segment.island_ref,
            )
            for index, segment in enumerate(candidate.target_segments)
        ]
    })


def correct_numeric_anchor_duplication(request: CalibrationRequest, candidate: TranslationResult) -> TranslationResult | None:
    """Remove one uniquely proven duplicate numeric representation.

    The ordinary form removes one numeric token from one non-owner segment.
    A stricter extension handles one contiguous terminal range representation
    (for example ``321-322``) only when every literal already has its own
    exact anchored source-owned slot.  Any other multi-number form remains
    intentionally fail-closed.
    """
    if len(request.source_segments) != len(candidate.target_segments):
        return None
    source_tokens = [_numeric_tokens(segment.text) for segment in request.source_segments]
    replacements: dict[int, str] = {}
    for owner, (source, target, tokens) in enumerate(zip(request.source_segments, candidate.target_segments, source_tokens, strict=True)):
        if not (
            len(tokens) == 1 and source.text.strip() == tokens[0]
            and source.kind == "anchored" and source.anchor_ref
            and target.kind == source.kind and target.anchor_ref == source.anchor_ref and target.island_ref == source.island_ref
            and _numeric_tokens(target.text).count(tokens[0]) == 1
            and sum(tokens[0] in row for row in source_tokens) == 1
        ):
            continue
        token = tokens[0]
        duplicates = [index for index, item in enumerate(candidate.target_segments) if index != owner and _numeric_tokens(item.text).count(token) == 1]
        if len(duplicates) != 1 or source_tokens[duplicates[0]]:
            continue
        duplicate = duplicates[0]
        matches = list(NUMBER.finditer(candidate.target_segments[duplicate].text))
        if len(matches) != 1 or matches[0].group(0).replace(",", "") != token:
            continue
        text = candidate.target_segments[duplicate].text
        replacements[duplicate] = text[:matches[0].start()] + text[matches[0].end():]
    if len(replacements) != 1:
        return _correct_contiguous_numeric_range_duplication(request, candidate, source_tokens)
    corrected = [
        segment if index not in replacements else StructuredTargetSegment(text=replacements[index], kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
        for index, segment in enumerate(candidate.target_segments)
    ]
    return candidate.model_copy(update={"target_segments": corrected})


def _exact_numeric_owner(source, target, token: str, source_tokens: list[list[str]]) -> bool:
    return bool(
        source.text.strip() == token
        and source.kind == "anchored"
        and source.anchor_ref
        and target.kind == source.kind
        and target.anchor_ref == source.anchor_ref
        and target.island_ref == source.island_ref
        and _numeric_tokens(target.text) == [token]
        and sum(token in row for row in source_tokens) == 1
    )


def _correct_contiguous_numeric_range_duplication(
    request: CalibrationRequest,
    candidate: TranslationResult,
    source_tokens: list[list[str]],
) -> TranslationResult | None:
    """Remove one exact duplicated terminal numeric range, or return ``None``.

    This deliberately models only a contiguous sequence of canonical owner
    slots: number, range delimiter, number (optionally repeated).  The whole
    duplicate must occur once in a *single* non-owner semantic slot and that
    slot may contain no other numeric literals.  These guards prevent this
    deterministic path from becoming arbitrary multi-number deletion.
    """
    segments = request.source_segments
    targets = candidate.target_segments
    candidates: list[tuple[int, str]] = []
    for start in range(len(segments)):
        first_tokens = source_tokens[start]
        if len(first_tokens) != 1 or not _exact_numeric_owner(segments[start], targets[start], first_tokens[0], source_tokens):
            continue
        owner_indices = [start]
        owner_tokens = [first_tokens[0]]
        cursor = start
        while cursor + 2 < len(segments):
            delimiter_source, delimiter_target = segments[cursor + 1], targets[cursor + 1]
            next_tokens = source_tokens[cursor + 2]
            if not (
                not _numeric_tokens(delimiter_source.text)
                and _NUMERIC_RANGE_DELIMITER.fullmatch(delimiter_source.text)
                and delimiter_target.kind == delimiter_source.kind
                and delimiter_target.anchor_ref == delimiter_source.anchor_ref
                and delimiter_target.island_ref == delimiter_source.island_ref
                and delimiter_target.text == delimiter_source.text
                and len(next_tokens) == 1
                and _exact_numeric_owner(segments[cursor + 2], targets[cursor + 2], next_tokens[0], source_tokens)
            ):
                break
            owner_indices.extend((cursor + 1, cursor + 2))
            owner_tokens.append(next_tokens[0])
            cursor += 2
        if len(owner_tokens) < 2:
            continue
        representation = "".join(targets[index].text for index in owner_indices)
        source_representation = "".join(segments[index].text for index in owner_indices)
        if representation != source_representation or not request.source_text.rstrip().endswith(source_representation.rstrip()):
            continue
        for duplicate, (source, target) in enumerate(zip(segments, targets, strict=True)):
            if duplicate in owner_indices or source_tokens[duplicate]:
                continue
            if not (
                target.kind == source.kind
                and target.anchor_ref == source.anchor_ref
                and target.island_ref == source.island_ref
                and _numeric_tokens(target.text) == owner_tokens
                and target.text.count(representation) == 1
            ):
                continue
            replacement = target.text.replace(representation, "", 1)
            # The duplicate slot is semantic, not a second locator owner.
            # It must retain content after removal.
            if not replacement.strip():
                continue
            candidates.append((duplicate, replacement))
    if len(candidates) != 1:
        return None
    duplicate, replacement = candidates[0]
    corrected = [
        segment if index != duplicate else StructuredTargetSegment(
            text=replacement, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref,
        )
        for index, segment in enumerate(targets)
    ]
    return candidate.model_copy(update={"target_segments": corrected})


class CodexMustNotRun:
    """Test double / production guard.  Allowlisted recovery never calls it."""

    def __init__(self) -> None:
        self.exec_count = 0

    def exec(self, *args, **kwargs):
        self.exec_count += 1
        raise RuntimeError("CODEX_EXEC_FORBIDDEN_ON_ALLOWLISTED_RECOVERY")


def permitted_segment_indices(contract: Mapping[str, Any] | None) -> tuple[int, ...]:
    """Read explicit repair indices only.  Ambiguous shapes return empty."""
    if not isinstance(contract, Mapping):
        return ()
    for key in ("permitted_segment_indices", "target_segment_indices"):
        values = contract.get(key)
        if isinstance(values, list) and values:
            try:
                return tuple(int(item) for item in values)
            except (TypeError, ValueError):
                return ()
    repairs = contract.get("repairs")
    if isinstance(repairs, list):
        indices: list[int] = []
        for item in repairs:
            if not isinstance(item, Mapping) or "target_segment_index" not in item:
                return ()
            try:
                indices.append(int(item["target_segment_index"]))
            except (TypeError, ValueError):
                return ()
        return tuple(indices)
    if "target_segment_index" in contract:
        try:
            return (int(contract["target_segment_index"]),)
        except (TypeError, ValueError):
            return ()
    return ()


def execute_recovery_decision(
    decision: Mapping[str, Any],
    *,
    request: CalibrationRequest,
    raw: TranslationResult,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    repair_provider=None,
    d050_registry: Mapping | None = None,
    source_sha_valid: bool = True,
    raw_sha_valid: bool = True,
    raw_bytes: bytes | None = None,
    expected_raw_sha256: str | None = None,
    codex: CodexMustNotRun | None = None,
) -> dict[str, Any]:
    """Supervisor-shaped entry: dispatch only by handler_kind / policy_id / contract."""
    probe = codex or CodexMustNotRun()
    if not decision.get("eligible"):
        reason = str(decision.get("denial_reason") or "CLASSIFICATION_NOT_ALLOWLISTED")
        status = "BLOCKED" if str(decision.get("escalation_code") or "BLOCK") == "BLOCK" else "HUMAN_REQUIRED"
        return {
            "status": status,
            "reason": reason,
            "handler_kind": decision.get("handler_kind"),
            "policy_id": decision.get("policy_id"),
            "codex_exec_count": probe.exec_count,
            "translation_api_calls": 0,
            "raw_mutated": False,
        }
    kind = str(decision.get("handler_kind") or "")
    if kind == HANDLER_SUPERVISOR_RETRY:
        return {
            "status": "SUPERVISOR_RETRY",
            "reason": None,
            "handler_kind": kind,
            "policy_id": decision.get("policy_id"),
            "codex_exec_count": probe.exec_count,
            "translation_api_calls": 0,
            "raw_mutated": False,
        }
    return dispatch_allowlisted_recovery(
        handler_kind=kind,
        policy_id=str(decision.get("policy_id") or ""),
        repair_contract=decision.get("repair_contract") if isinstance(decision.get("repair_contract"), Mapping) else {},
        request=request,
        raw=raw,
        view=view,
        known_unit_ids=known_unit_ids,
        qa_fn=qa_fn,
        repair_provider=repair_provider,
        d050_registry=d050_registry,
        source_sha_valid=source_sha_valid,
        raw_sha_valid=raw_sha_valid,
        raw_bytes=raw_bytes,
        expected_raw_sha256=expected_raw_sha256,
        codex=probe,
    )


def dispatch_allowlisted_recovery(
    *,
    handler_kind: str,
    policy_id: str,
    repair_contract: Mapping[str, Any] | None,
    request: CalibrationRequest,
    raw: TranslationResult,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    repair_provider=None,
    d050_registry: Mapping | None = None,
    source_sha_valid: bool = True,
    raw_sha_valid: bool = True,
    raw_bytes: bytes | None = None,
    expected_raw_sha256: str | None = None,
    codex: CodexMustNotRun | None = None,
) -> dict[str, Any]:
    """Execute one allowlisted recovery.  Never translates.  Never calls Codex."""
    probe = codex or CodexMustNotRun()
    report: dict[str, Any] = {
        "handler_kind": handler_kind,
        "policy_id": policy_id,
        "codex_exec_count": 0,
        "translation_api_calls": 0,
        "status": "PENDING",
        "raw_mutated": False,
    }
    try:
        if not source_sha_valid:
            report.update(status="BLOCKED", reason="SOURCE_SHA_MISMATCH")
            return report
        if not raw_sha_valid:
            report.update(status="BLOCKED", reason="IMMUTABLE_RAW_SHA_MISMATCH")
            return report
        if expected_raw_sha256 and raw_bytes is not None and sha256(raw_bytes).hexdigest() != expected_raw_sha256:
            report.update(status="BLOCKED", reason="IMMUTABLE_RAW_SHA_MISMATCH")
            return report
        if handler_kind not in ALLOWLISTED_HANDLERS or policy_id not in ALLOWLISTED_POLICIES:
            report.update(status="HUMAN_REQUIRED", reason="HANDLER_NOT_ALLOWLISTED")
            return report
        if handler_kind == HANDLER_DETERMINISTIC_EVIDENCE:
            if policy_id != "D050_EXACT_EVIDENCE":
                report.update(status="HUMAN_REQUIRED", reason="POLICY_HANDLER_MISMATCH")
                return report
            report.update(
                _d050_exact_evidence(
                    request,
                    raw,
                    view,
                    known_unit_ids,
                    qa_fn,
                    repair_contract or {},
                    d050_registry or {},
                )
            )
            return report
        if policy_id not in QWEN_REPAIR_POLICIES or handler_kind != HANDLER_QWEN_NARROW_REPAIR:
            report.update(status="HUMAN_REQUIRED", reason="POLICY_HANDLER_MISMATCH")
            return report
        if repair_provider is None:
            report.update(status="BLOCKED", reason="QWEN_REPAIR_PROVIDER_MISSING")
            return report
        report.update(
            _qwen_narrow_repair(
                request,
                raw,
                view,
                known_unit_ids,
                qa_fn,
                repair_provider,
                policy_id,
                repair_contract or {},
            )
        )
        return report
    finally:
        report["codex_exec_count"] = probe.exec_count
        report["translation_api_calls"] = 0


def _qwen_narrow_repair(
    request: CalibrationRequest,
    raw: TranslationResult,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    repair_provider,
    policy_id: str,
    repair_contract: Mapping[str, Any],
) -> dict[str, Any]:
    before = raw.model_dump()
    indices = permitted_segment_indices(repair_contract)
    if not indices and policy_id == "NUMERIC_REPRESENTATION":
        inferred = failing_number_segment_indices(request, raw)
        indices = tuple(inferred) if inferred else ()
    if not indices:
        return {"status": "HUMAN_REQUIRED", "reason": "REPAIR_SCOPE_AMBIGUOUS", "repaired": False}
    provider_request = TranslationProviderRequest(request=request)
    qa_codes = ["DOI_PRESERVATION" if policy_id == "DOI_PRESERVATION" else "NUMBER_PRESERVATION"]
    evidence = {
        "source_sha_valid": True,
        "raw_required": True,
        "raw_sha_valid": True,
        "sparse_local": True,
        "failure_classification": "DOI_PRESERVATION" if policy_id == "DOI_PRESERVATION" else "FINAL_DETERMINISTIC_QA_FAILED",
        "qa_codes": qa_codes,
        "repair_contract": {**dict(repair_contract), "permitted_segment_indices": list(indices)},
        "eligible": True,
        "policy_id": policy_id,
    }
    gate = decide_approved_repair_contract(
        provider_request,
        raw,
        qa_codes=qa_codes,
        qa_segment_indices=indices,
        registry_evidence=evidence,
        allow_semantic_narrow=False,
    )
    if gate.action != "REPAIR" or gate.contract is None:
        return {"status": "HUMAN_REQUIRED", "reason": gate.reason, "repaired": False}
    if gate.contract.unit_uid != request.unit_uid:
        return {"status": "HUMAN_REQUIRED", "reason": "REPAIR_UNIT_MISMATCH", "repaired": False}
    if gate.policy_id != policy_id:
        return {"status": "HUMAN_REQUIRED", "reason": "REPAIR_POLICY_MISMATCH", "repaired": False}
    repaired = repair_with_contract_boundary(repair_provider, provider_request, raw, gate.contract).repaired
    if raw.model_dump() != before:
        return {"status": "BLOCKED", "reason": "RAW_MUTATED", "raw_mutated": True, "repaired": False}
    qa = qa_fn(view, request, repaired, known_unit_ids)
    if not qa.get("passed"):
        return {
            "status": "HUMAN_REQUIRED",
            "reason": "REPAIR_QA_FAILED",
            "repaired": True,
            "qa": qa,
            "repair_policy_id": gate.policy_id,
        }
    return {
        "status": "PASS",
        "reason": None,
        "repaired": True,
        "qa": qa,
        "repair_policy_id": gate.policy_id,
        "permitted_segment_indices": list(gate.contract.permitted_segment_indices),
    }


def _d050_exact_evidence(
    request: CalibrationRequest,
    raw: TranslationResult,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    repair_contract: Mapping[str, Any],
    registry: Mapping,
) -> dict[str, Any]:
    logical_id = str(repair_contract.get("logical_work_order_id") or "")
    unit_uid = str(repair_contract.get("unit_uid") or "")
    if not logical_id or not unit_uid:
        return {"status": "BLOCKED", "reason": "D050_BINDING_AMBIGUOUS"}
    if logical_id != request.display_id or unit_uid != request.unit_uid:
        return {"status": "BLOCKED", "reason": "D050_BINDING_MISMATCH"}
    if (logical_id, unit_uid) not in registry:
        return {"status": "BLOCKED", "reason": "D050_EVIDENCE_NOT_EXACT"}
    overlaid = overlay_verified_d050_bindings(request, registry)
    qa = qa_fn(view, overlaid, raw, known_unit_ids)
    if not qa.get("passed"):
        return {"status": "HUMAN_REQUIRED", "reason": "D050_QA_FAILED", "qa": qa, "overlay_applied": True}
    return {"status": "PASS", "reason": None, "qa": qa, "overlay_applied": True}
