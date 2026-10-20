# Qwen structured-provider — Phase 2

## Scope and unchanged production boundary

Phase 2 adds untrusted, in-memory candidate providers only.  It does **not**
change the Translation Harness, checkpoint selection, RAW persistence, QA,
D050, control-plane decisions, sealed artifacts, or the Codex worker path.
No provider is wired into continuous production.

`QwenMaxProvider` uses `qwen3.8-max` through the existing OpenAI-compatible
base URL.  Missing `DASHSCOPE_API_KEY` or `BOOKTRANS_QWEN_BASE_URL` raises
`PROVIDER_CONFIGURATION_MISSING` before any request.  The key is used only in
the HTTP Authorization header and is deliberately absent from provenance,
exceptions, fixture data, and ordinary logs.

```powershell
$env:DASHSCOPE_API_KEY = '<DashScope key>'
$env:BOOKTRANS_QWEN_BASE_URL = 'https://<workspace>.<region>.maas.aliyuncs.com/compatible-mode/v1'
```

Phase 2 pins the approved model identities in code (`qwen-mt-plus` and
`qwen3.8-max`) rather than accepting an arbitrary environment override.

## Strict structured contract

The Qwen Max call supplies OpenAI-compatible `response_format` with a Pydantic
JSON Schema and `strict: true`.  It also sends `enable_thinking: false` for
translation, projection, review, and narrow repair calls: these are bounded
mechanical operations, not an invitation to turn on costly reasoning.

The model schema is intentionally minimal:

```json
{
  "schema_version": "booktrans.structured-translation.v1",
  "unit_uid": "<exact frozen unit uid>",
  "source_fingerprint": "<sha256 of frozen CalibrationRequest>",
  "segments": [{"index": 0, "target_text": "..."}]
}
```

The model never supplies anchor, island, or protected identity.  The local
closed-world projector requires an exact UID/fingerprint, exact indices
`0..N-1`, no duplicates/missing/extra entries, and unchanged protected
segments.  Segment mode is classified locally from the frozen source; the
model cannot supply it:

- `TEXT` — ordinary translatable content.  Empty or whitespace-only
  `target_text` remains `PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT`.
- `PRESERVE` — protected segments, and whitespace-only non-protected
  segments such as a single U+0020 run.  The target must copy the frozen
  source text exactly.  An omitted space is still `EMPTY_TARGET_TEXT`; empty
  strings are not globally allowed.
- `INTENTIONAL_EMPTY` — only an unprotected source segment whose frozen text
  is the empty string.  The target must then be empty.

Only then it copies each frozen source segment's `kind`, `anchor_ref`, and
`island_ref` into the existing `TranslationResult`.  It never splits, merges,
pads, guesses segments, or weakens existing Harness validation.  A failure is
`PROVIDER_CONTRACT_GAP`, before RAW is eligible to be written.

## Multi-segment pilot arms

- **A — direct structured:** `qwen3.8-max` receives the whole semantic unit,
  ordered source segments, existing bounded aids, and returns the strict
  target-segment schema.
- **B — holistic then projection:** `qwen-mt-plus` first produces one complete
  semantic-unit candidate.  `qwen3.8-max` then receives that Chinese candidate
  plus the frozen source segment structure under a *projection only; do not
  retranslate or improve* contract.  Its output goes through the exact same
  local projector as arm A.

Both arms subsequently use the existing Harness in an isolated pilot run.
Neither assumes tag/sentinel preservation by Qwen-MT.

## Reviewer and repair boundary

`QwenMaxReviewerProvider` returns only this strict review result:

```json
{
  "schema_version": "booktrans.structured-review.v1",
  "unit_uid": "<exact uid>",
  "verdict": "ACCEPT | REPAIR | HUMAN_REQUIRED",
  "issues": [{"code": "...", "affected_segment_indices": [0], "reason": "...", "repair_instruction": "..."}]
}
```

It does not modify a target candidate.  Its bounded input is one semantic unit,
source/target segments, relevant aids, and deterministic-QA summary.

`QwenMaxRepairProvider` accepts an already-approved
`ApprovedRepairContract`: exact UID/fingerprint, policy ID, exact permitted
segment indices, and repair instruction.  It returns only indexed patches;
the local applier rejects duplicate/out-of-scope patches, protected scope,
empty text, or binding mismatch.  The result stays in memory.  Only existing
Control Plane/Registry policy may approve a contract; a provider cannot decide
scope, overwrite RAW, or persist evidence.

## Isolated 50-unit pilot

The checked-in planner is read-only unless `--out` is passed.  It selects only
request records from sealed checkpoints with passing final QA, verifies the
source SHA, reserves at least 40% multi-segment units, and refuses to write a
plan if any required category is unavailable.

Current isolated plan:

`runs/qwen-structured-pilot-20260917-phase2/pilot_plan.json`

It contains 50 approved semantic units: 30 simple and 20 multi-segment,
including code-adjacent (6), protected (4), number (10), URL (3), DOI (1), and
footnote-related (1) cases.  It contains only references to sealed source
requests; it does not copy or change them.

After setting both credentials, first re-check the plan without contacting a
provider:

```powershell
D:\translate\.venv\Scripts\python.exe scripts\pilot\run_qwen_structured_pilot.py `
  --plan runs\qwen-structured-pilot-20260917-phase2\pilot_plan.json `
  --source 'Software Engineering at Google.docx' `
  --translation-bible runs\full-book-20260916T000200Z-decb09\full_book_batch_022\translation_bible_snapshot.md
```

Then run the opt-in pilot only to a new isolated directory:

```powershell
D:\translate\.venv\Scripts\python.exe scripts\pilot\run_qwen_structured_pilot.py `
  --plan runs\qwen-structured-pilot-20260917-phase2\pilot_plan.json `
  --source 'Software Engineering at Google.docx' `
  --translation-bible runs\full-book-20260916T000200Z-decb09\full_book_batch_022\translation_bible_snapshot.md `
  --out runs\qwen-structured-pilot-live-<timestamp> --execute
```

The output comparison report includes arm-specific Harness pass/fail counts,
failure classes, request attempts, and returned usage metadata.  A single
`ProviderContractGap` is recorded as arm evidence; it does not abort the
pilot.  Each unit×arm is isolated, flushed to `pilot_progress.jsonl`, and
skipped on `--resume` after the identity file checks plan SHA, source SHA,
model IDs, schema versions, and Translation-Bible SHA.  Resume never overwrites
create-once provenance or RAW.

For every candidate which passes the existing deterministic import gate, the
script then dispatches the non-authoritative Qwen Max Reviewer.  It records
ACCEPT/REPAIR/HUMAN_REQUIRED rates and usage in the comparison report, writes
strict review results, and writes a side-by-side source/target report for
manual sampling.  Reviewer output never overrides Harness or Control Plane
policy.

Live runs print compact progress such as `[3/20] simple_qwen_mt PASS …` and
`[multi A 3/20] CONTRACT_GAP …`.  They do not print source text, translations,
or secrets.

Optionally add `--input-price-per-million <USD>` and
`--output-price-per-million <USD>` to calculate a transparent estimate from
returned usage.  Without explicit rates, the report says `RATE_NOT_SUPPLIED`;
it never guesses a price.

To continue an interrupted isolated directory:

```powershell
D:\translate\.venv\Scripts\python.exe scripts\pilot\run_qwen_structured_pilot.py `
  --plan runs\qwen-structured-pilot-20260917-phase2\pilot_plan.json `
  --source 'Software Engineering at Google.docx' `
  --translation-bible runs\full-book-20260916T000200Z-decb09\full_book_batch_022\translation_bible_snapshot.md `
  --out runs\qwen-structured-pilot-live-<timestamp> --execute --resume
```

## Runtime retry boundary

Timeout/connection faults, HTTP 429, and HTTP 5xx use at most three attempts
with 1/3/8-second backoff.  Authentication, other 4xx, malformed structured
content, and all provider-contract gaps fail closed.  A deterministic QA
failure is never a reason to resend a translation request.

Official API basis: [Alibaba structured-output documentation](https://help.aliyun.com/en/model-studio/qwen-structured-output) and [Alibaba deep-thinking documentation](https://help.aliyun.com/en/model-studio/deep-thinking).
