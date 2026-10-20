# Qwen-MT provider — Phase 1

## Boundary

`QwenMTProvider` is a direct, single-turn candidate generator. It receives one
complete `CalibrationRequest` semantic unit and returns only an untrusted
`TranslationCandidate`. It does not select work, create checkpoints, write RAW,
route D050 evidence, run QA, seal artifacts, or make control-plane decisions.

The OpenAI-compatible request contains exactly one `user` message whose content
is the complete `source_text`. It has no system message. The body contains
`model: qwen-mt-plus` and `translation_options` with `source_lang: English`,
`target_lang: Chinese`, bounded `terms`, bounded `tm_list`, and an English
software-engineering `domains` description.

## Configuration

Both variables are required only when an actual isolated pilot is executed:

```powershell
$env:DASHSCOPE_API_KEY = '<DashScope key>'
$env:BOOKTRANS_QWEN_BASE_URL = 'https://<workspace>.<region>.maas.aliyuncs.com/compatible-mode/v1'
```

The provider never writes either value to a request, provenance, exception, or
log. Missing configuration raises `PROVIDER_CONFIGURATION_MISSING` before a
network call.

## Bounded aids

The active Translation Bible is parsed as a small term table. Only terms that
occur in the current semantic unit are emitted, capped at eight. Translation
memory is supplied by the caller as previously approved bilingual examples;
only lexical matches are used, capped at three. The provider never scans broad
historical artifacts to assemble either list.

## RAW and projection gate

Existing `TranslationResult` RAW JSONL remains unchanged. Provider provenance is
a separate create-once JSONL sidecar containing provider/model, request and
options hashes, source/unit identity, response text, timestamps, response id,
and non-secret usage fields.

Phase 1 accepts a plain Qwen response into the existing RAW contract only for a
single non-protected source segment. The adapter copies that segment's exact
kind/anchor/island identity. For multi-segment or protected-only units it raises
`PROVIDER_CONTRACT_GAP`; it never guesses boundaries, splits, merges, or pads.
This is intentionally a gate, not a temporary fallback.

## Retry policy

Timeout/connection faults, HTTP 429, and HTTP 5xx use at most three attempts
with 1/3/8 second backoff. Authentication, malformed responses, and other 4xx
responses fail closed. Deterministic QA is not a provider retry trigger.

## Isolated pilot

`scripts/pilot/run_qwen_mt_offline_pilot.py` is opt-in. Its dry-run mode reads
only supplied sealed/release-approved request artifacts and reports projection
compatibility without credentials or disk writes. Execution requires a new
output directory and both configuration variables; it writes only an isolated
pilot request, provenance sidecar, RAW candidate, and Harness report.

Before any actual pilot, the selected 25–50 units must include the agreed
representative categories and every selected unit must clear the projection gate.
An unproven multi-segment mapping blocks the pilot rather than weakening the
existing tagged projection contract.
