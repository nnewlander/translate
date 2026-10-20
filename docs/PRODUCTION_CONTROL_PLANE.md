# Production Control Plane — Phase 1

## Purpose and scope

This document specifies the control plane for the full-book production
campaign.  It does not change the Translation Harness, source authority,
checkpoint size (25), or batch size (200).  It separates immutable production
facts from resumable control state, so known safe conditions can be recovered
deterministically while unknown or contradictory conditions fail closed.

Phase 1 is an architecture and test-contract document.  It is deliberately
not a rewrite of `production_campaign_supervisor.ps1`.

## 1. Authority hierarchy

| Level | Data | Authority and write rule |
| --- | --- | --- |
| A — source | DOCX, TranslationView, frozen selection, D050 registries | Immutable input authority.  Source SHA must match before every action. |
| B — production evidence | request, RAW, QA reports, repaired response, final QA, checkpoint evidence, batch manifest, progress, closure/reconciliation evidence | Immutable production truth.  A sealed checkpoint is never replayed or modified. |
| C — derived authoritative index | `production_campaign_state.json` | Atomically rebuilt from A/B.  It is never evidence and cannot override A/B. |
| D — operational coordination | writer lock, current handoff, rotated handoffs, supervisor status, metrics, milestone log, stop request | Resumable operational metadata only.  It may trigger validation but never establish or negate a production fact. |
| E — observation | Watcher console, worker JSONL, UI launchers | Read-only presentation or diagnostic evidence.  It cannot influence control decisions. |

`production_campaign_worker.lock` is a lifetime-exclusive ownership lease for
one Supervisor and its child worker/recovery worker.  A handoff binds to a
`worker_id`; it reports control intent, while post-worker immutable evidence
decides the cursor.

## 2. Formal control model

The model has two orthogonal dimensions.  Cursor values are data, not FSM
states; `FULL_BOOK_BATCH_020/checkpoint_03` is therefore never a distinct
control state.

### Operational state

| State | Meaning | Permitted next states |
| --- | --- | --- |
| `STOPPED` | No owned lock or active child worker. | `RUNNING`, `RECONCILING` |
| `RUNNING` | Lock held; a fresh production worker may be started or evaluated. | `TRIAGING`, `RECONCILING`, `STOPPING`, `BLOCKED`, `RUNNING` |
| `TRIAGING` | A current `RECOVERY_REQUIRED` handoff is being classified from immutable evidence. | `AUTO_RECOVERING`, `BLOCKED`, `RUNNING` |
| `AUTO_RECOVERING` | Lock held; one bounded recovery worker is acting under a policy contract. | `RUNNING`, `BLOCKED` |
| `RECONCILING` | Deterministic control-plane repair/rebuild is proving a unique cursor. | `RUNNING`, `BLOCKED`, `STOPPED` |
| `STOPPING` | A stop request was observed at a safe worker boundary. | `STOPPED` |
| `BLOCKED` | No unique safe action is proven.  Lock is released before terminal status is published. | `STOPPED` |

`RECOVERY_REQUIRED` remains a handoff/result classification, not a new
operational state.  It enters `TRIAGING`; an ineligible result ends as
`BLOCKED` (with the published diagnostic explaining whether human recovery is
required).

### Authoritative cursor

The cursor is rebuilt only from immutable evidence:

```
source_sha256
active_batch
active_selection + SHA
active_manifest + SHA
completed_count / target_count
sealed checkpoint prefix
next_checkpoint
next_logical_work_order_id
current partial checkpoint, if uniquely present
campaign_status + recovery_queue
```

A cursor transition is valid only when the selection prefix, manifest,
checkpoint evidence, RAW SHA, Final QA, source SHA, and progress artifact all
agree.  `completed_count` advances only by sealed evidence.

## 3. Event model and decision rule

Every handler uses one form:

```
(operational state, event, validated immutable/operational evidence)
    -> (next operational state, deterministic action or bounded worker contract)
```

| Event | Evidence required | Normal action |
| --- | --- | --- |
| `SUPERVISOR_STARTED` | lock acquired | rebuild cursor, then reconcile before readiness |
| `WORKER_STARTED` / `WORKER_EXITED` | worker ID, lifetime, JSONL, exit code | rebuild before/after cursor; validate handoff against post-worker cursor |
| `CHECKPOINT_SEALED` | valid evidence + Final QA + RAW SHA + manifest prefix | advance cursor; reset progress-based guards |
| `HANDOFF_RECEIVED` | schema, worker ID, time window, post-worker cursor | accept, normalize documented derivable fields, rotate stale metadata, or fail closed |
| `QA_FAILED` | immutable QA reports + RAW/source SHA | triage into a canonical family/policy |
| `STATE_DRIFT_DETECTED` | rebuild comparison | deterministic reconciliation only when a unique result is proven |
| `RECOVERY_REQUIRED` | unique current partial and matching worker handoff | `TRIAGING` |
| `RECOVERY_PASS` / `RECOVERY_FAIL` | rebuilt cursor and policy postconditions | continue, retry within budget, or block |
| `BATCH_COMPLETED` / `ROLLOVER_REQUIRED` | all 8 checkpoints sealed, closure inputs unique | reconcile closure then BATCH_READY |
| `STOP_REQUESTED` | request file at a safe boundary | `STOPPING`; no next worker |
| `RUNTIME_TRANSIENT` | exact recognized runtime signature and no cursor/partial/source change | bounded retry with backoff |

Precedence is fixed: immutable cursor integrity first; validated newly sealed
progress next (even if it arrives at a current partial); a current matching
`BLOCKED` or `RECOVERY_REQUIRED` handoff then owns that partial; a stale
handoff is rotated only after proving its checkpoint is sealed/superseded;
finally operational retry logic.
Authoritative progress supersedes only `CONTINUE` or stale control metadata,
never a valid current blocker.

## 4. Global invariants

These assertions apply to every production and fault-injection test.

1. Source SHA is unchanged throughout a transaction.
2. A sealed checkpoint, immutable RAW, QA report, and checkpoint evidence are never overwritten or replayed.
3. Exactly one writer lock owner exists for an active Supervisor/worker lifetime.
4. Progress derives only from immutable evidence; status, metrics, logs, and handoffs never override it.
5. A partial checkpoint is preserved, uniquely classified, and never silently discarded.
6. Recovery may change only declared failing work orders and only downstream of immutable RAW.
7. Frozen selection identity/order and its source binding remain exact.
8. Batch closure requires the complete sealed prefix; a next batch is runnable only after BATCH_READY.
9. Handoff identity requires the current `worker_id`; checkpoint is a dynamic post-worker position, not launch identity.
10. Unknown, ambiguous, competing, corrupt, or integrity-violating evidence blocks rather than guesses.
11. Every retry/recovery is bounded and resets only on validated cursor progress.
12. A stop request never kills an artifact-writing worker; it prevents the next worker after a safe refresh.

## 5. Failure taxonomy and policy boundary

| Family | Detection source | Recovery mode | Preconditions / budget | Escalation |
| --- | --- | --- | --- | --- |
| `RUNTIME_TRANSIENT` | exact worker-log signature | Supervisor retry | validated unchanged cursor, source, no partial; current model timeout only; 3 attempts | runtime guard |
| `CONTROL_METADATA_DRIFT` | rebuild, stale state/status/manifest binding | deterministic reconciliation | unique immutable successor; one rebuild/retry | competing/invalid evidence |
| `HANDOFF_PROTOCOL_DRIFT` | schema/identity/normalizer | normalize or rotate | valid worker ID, lifetime, unique current partial; only documented derivable fields | identity missing, future/unrelated, contradiction |
| `CHECKPOINT_PARTIAL_RECOVERABLE` | partial artifact chain | policy-directed recovery | unique current partial and immutable-chain validation | incomplete/corrupt chain |
| `LOCAL_CONTENT_QA_FAILURE` | raw + normalized final-QA triage | bounded recovery worker | source/RAW SHA, sparse threshold, known policy, exact scope; 2 attempts | unknown, dense, ambiguous, second failure |
| `EVIDENCE_PREFLIGHT_REQUIRED` | exact D050 preflight evidence | bounded recovery worker | exact work-order/unit UID and chapter-local evidence | ambiguous/missing evidence |
| `EVIDENCE_INFRASTRUCTURE` | source-PDF capability probe | deterministic provider retry | exact PDF SHA/page requirement, approved local provider or verified derived cache; 2 attempts | source/cache SHA drift or all providers unavailable |
| `BATCH_TRANSITION_DRIFT` | closure/readiness validation | deterministic reconciliation | terminal sealed evidence and unique successor artifacts | competing selection/manifest or source drift |
| `IMMUTABLE_INTEGRITY_FAILURE` | SHA/schema/evidence contradiction | none | n/a | block immediately |
| `HUMAN_REQUIRED` | immutable progress or verified classification | none | n/a | block immediately |
| `UNKNOWN` | any unclassified result | none | n/a | block immediately |

The taxonomy is intentionally not a list of historical error strings.  Raw
strings map to a family only through a detector that validates evidence.

## 6. Recovery policy registry

The Supervisor depends on a registry entry, not on content-specific `if NUMBER`,
`if DOI`, or `if D050` branches in its orchestration loop.  Phase 3 makes this
the authoritative policy-decision boundary.  The legacy decision function is a
shadow oracle only in `-WhatIf` / `-ShadowControlPlane` runs.

```text
PolicyEntry {
  policy_id, family, detector, validator, handler_kind,
  allowed_artifact_stage, scope_rule, attempt_budget,
  progress_predicate, postconditions, escalation_code
}
```

Initial verified entries only:

| Policy ID | Family | Handler | Closed-world rule |
| --- | --- | --- | --- |
| `NUMERIC_REPRESENTATION` | `LOCAL_CONTENT_QA_FAILURE` | recovery worker | existing sparse numeric repair contract only |
| `DOI_PRESERVATION` | `LOCAL_CONTENT_QA_FAILURE` | recovery worker | exact unique protected DOI plus verified local punctuation-boundary contract only |
| `D050_EXACT_EVIDENCE` | `EVIDENCE_PREFLIGHT_REQUIRED` | recovery worker | exact logical-work-order + unit-UID chapter-local evidence only |
| `MODEL_REFRESH_TIMEOUT` | `RUNTIME_TRANSIENT` | Supervisor | exact observed CLI signature; no content repair |
| `NO_PROGRESS_CLEAN_EXIT` | `RUNTIME_TRANSIENT` | Supervisor | zero exit, unchanged safe cursor, no partial |
| `PDF_EVIDENCE_PROVIDER_RECOVERY` | `EVIDENCE_INFRASTRUCTURE` | Supervisor | exact source-PDF SHA/page capability probe and only approved local provider/cache |

The registry validator returns a canonical family, a serializable repair
contract, and a denial reason.  The handler receives only that contract,
not permission to browse or widen scope.  A policy cannot write immutable
facts until its validator passes again immediately before and after handling.

## 7. Idempotent transaction rules

| Transaction | Required repeat-safe behavior | Current assessment |
| --- | --- | --- |
| State rebuild | derive A/B to one atomically replaced derived index | implemented |
| Handoff normalization | in-memory derivation only; supplied contradictions reject | implemented |
| Stale handoff rotation | archive operational record, never alter evidence | implemented |
| Checkpoint-state reconciliation | detect sealed-next cursor drift and rebuild from evidence | partially implemented; ordinary and terminal paths are separate branches |
| Batch closure | complete only if terminal sealed evidence and successor are unique | partially implemented through terminal reconciliation and legacy advance policy |
| Successor manifest reconciliation | create/reuse one versioned manifest only with explicit supersession proof | partially implemented; needs a single transaction contract |
| BATCH_READY | read-only repeatable validation | implemented |
| Recovery validation | validate source/RAW/cursor/scope before and after handler | largely implemented; needs registry-owned postcondition schema |

Crash rule: after any crash, the Supervisor starts with rebuild → reconciliation
→ BATCH_READY.  It must detect whether the prior action committed immutable
evidence, did nothing, or left one preserved partial; it must never infer a
fourth outcome.

## 8. Fault-injection matrix

All scenarios use a fake Codex worker and isolated run fixture.  Each asserts
the global invariants, final cursor, writer-lock release, operational state,
and no unintended artifact write.

| Injected condition | Expected state/action |
| --- | --- |
| exit after request | `TRIAGING` then current partial recovery classification; never retry as clean |
| exit after RAW | `TRIAGING`; RAW SHA retained; only downstream policy allowed |
| exit after QA | `TRIAGING`; QA evidence governs policy/blocked result |
| exit after Final QA before seal | `RECONCILING` or narrow seal completion; never regenerate RAW |
| exit after seal before rebuild | `RECONCILING` → `RUNNING` with advanced cursor |
| one worker seals multiple checkpoints | post-worker cursor accepted; launch checkpoint is not current identity |
| derivable handoff field absent | normalize → `TRIAGING` |
| worker ID/timestamp/non-derivable identity absent | `BLOCKED` |
| stale handoff for sealed checkpoint | rotate → `RUNNING` |
| Supervisor crash after worker exit | restart rebuild/reconcile; no duplicate worker |
| Supervisor crash during closure | restart `RECONCILING`; unique committed state resumes |
| terminal cp08 sealed, successor selection missing | `BLOCKED` unless unique existing immutable authority proves a deterministic creation path |
| unique successor selection, manifest missing | `RECONCILING` → `RUNNING` after manifest recovery and BATCH_READY |
| progress written, state stale | rebuild → `RUNNING` |
| state manifest SHA stale, unique reconciled manifest | rebuild once → BATCH_READY → `RUNNING` |
| recovery attempt 1 fails, 2 succeeds | `AUTO_RECOVERING` → `RUNNING` |
| recovery attempts exhausted | `BLOCKED`, lock released |
| exact model refresh timeout | bounded `RUNTIME_TRANSIENT` retry |
| clean no-progress exit | bounded retry only after safe unchanged-cursor proof |
| source SHA or RAW SHA drift | `BLOCKED` |
| competing immutable manifest/selection | `BLOCKED` |
| unknown QA code or dense QA failures | `BLOCKED` |

## 9. Historical incident replay matrix

Each real incident becomes a named fixture in the future fault-injection
suite, with immutable-like fixture artifacts and expected decision traces.

| Replay fixture | Canonical family / expected result |
| --- | --- |
| `model_refresh_timeout` | `RUNTIME_TRANSIENT`; bounded retry/exhaustion |
| `no_progress_false_guard` | progress resets guard; no false `RUNTIME_GUARD` |
| `partial_deterministic_qa` | current partial → triage, no RAW replay |
| `d050_missing_preflight` | exact evidence policy or fail closed |
| `target_segment_count_mismatch` | pre-checkpoint local staging correction; no checkpoint directory |
| `multi_checkpoint_handoff_identity` | dynamic cursor accepted with fixed worker ID |
| `batch_manifest_identity_mismatch` | derived drift rebuild only if immutable pair is unique |
| `stale_blocked_handoff_precedence` | current blocker honored; sealed historical blocker rotated |
| `terminal_cp08_closure_interruption` | closure reconciliation under unique terminal evidence |
| `recovery_handoff_missing_fields` | documented field normalization only |
| `number_preservation_local` | sparse policy-directed recovery |
| `doi_preservation_local` | sparse unique DOI boundary policy-directed recovery |

## 10. Telemetry contract

Metrics remain derived metadata and must add no content-path scan beyond the
existing manifest/evidence reads.  Distinguish:

* **Campaign cumulative:** units/hour, productive/recovery/blocked ratios,
  worker startup and handoff/control overhead, human escalation rate, mean
  uninterrupted run duration.
* **Session:** session productive, recovery, blocked, and retry durations;
  worker count and exit reasons.  A restart must not reset campaign history.
* **Checkpoint:** request/start, seal time, duration, units, final result.
* **Recovery:** family/policy, attempt count, duration, success/failure,
  escalation reason.

During Phase 2 shadow mode, every adapter comparison logs `latency_ms` as a
derived milestone.  Phase 3 may persist aggregate adapter latency only after
the registry becomes part of the production decision path.

Throughput analysis may later change granularity only after these metrics show
that checkpoint or handoff overhead, rather than model work, is the limiting
factor.

## 11. Migration plan

1. **Phase 1 (this document):** freeze vocabulary, invariants, registry
   contract, and fault-injection/replay matrix.  Do not change production flow.
2. **Phase 2:** extract pure Python/PowerShell decision adapters for cursor,
   handoff, and policy registry; retain current Supervisor as orchestrator.
3. **Phase 3:** make the registry the authoritative policy-decision boundary;
   dispatch existing handlers through its serialized contracts; prove
   equivalence with historical replay fixtures.
4. **Phase 4:** implement crash-point fault injection and append-only derived
   event telemetry; require invariants for every scenario.
5. **Phase 5:** simplify Supervisor branches only after all replay fixtures
   and full pytest pass.  Preserve its writer lock, exit-code handling,
   graceful stop, UTF-8 logging, BATCH_READY, and fail-closed boundaries.

### Do not rewrite during consolidation

Do not rewrite the Translation Harness, immutable artifact formats, selection
policy, checkpoint sealing, source/D050 contracts, launcher behavior, or the
existing state rebuild/readiness validators.  In particular, no registry may
weaken deterministic QA or turn an unrecognized failure string into a retry.

## 12. Phase 2 implementation status

Phase 2 adds three pure adapters under `src/booktrans/production_control/`:

| Adapter | Interface | Output vocabulary |
| --- | --- | --- |
| `cursor_decision.decide_cursor(before, after, evidence)` | serializable cursor facts | `PROGRESS`, `NO_PROGRESS`, `CURRENT_PARTIAL`, `SEALED_STATE_DRIFT`, `BATCH_COMPLETE`, `INTEGRITY_FAILURE` |
| `handoff_decision.decide_handoff(worker_id, handoff, post_cursor, facts)` | worker/handoff/post-cursor facts | `ACCEPT_CONTINUE`, `NORMALIZE`, `STALE_ROTATE`, `TRIAGE`, `BLOCK` |
| `recovery_policy_registry.decide_recovery_policy(evidence)` | canonical validated failure evidence | family, policy ID, eligibility, handler kind, budget, contract/denial |

`scripts/production/shadow_control_plane_decision.py` is a stdin/stdout JSON
wrapper around those modules.  The Supervisor compares its existing decision
to adapter output only in `-WhatIf` or explicit `-ShadowControlPlane` mode;
a difference raises `CONTROL_PLANE_SHADOW_DIFF`.  Default production mode
does not yet call the adapters, so Phase 2 changes neither worker launch nor
handler behavior.  The historical replay fixture is
  `tests/production/fixtures/control_plane_historical_replays.json`.

## 13. Phase 3 implementation status

`scripts/production/decide_recovery_policy.py` is the low-frequency JSON
boundary for the registry.  It is called only after a runtime or recovery
failure has already been validated; normal worker startup does not launch an
extra Python process.  The authoritative `PolicyDecision` contains `family`,
`policy_id`, `eligible`, `handler_kind`, `attempt_budget`,
`repair_contract`, `postconditions`, `escalation_code`, and a denial reason.

The first closed-world entries are `NUMERIC_REPRESENTATION`,
`DOI_PRESERVATION`, `D050_EXACT_EVIDENCE`, `MODEL_REFRESH_TIMEOUT`, and
`NO_PROGRESS_CLEAN_EXIT`.  `RECOVERY_WORKER` entries receive only the existing
bounded repair contract; `SUPERVISOR_RETRY` entries use the contract's bounded
backoff.  Unknown evidence, source/RAW SHA drift, ambiguous scope, and an
invalid contract return an ineligible decision and remain fail-closed.

The Supervisor's main loop now dispatches by `handler_kind`; it contains no
NUMBER/DOI/D050-specific recovery branch.  During `-WhatIf` or
`-ShadowControlPlane`, eligibility and handler-kind equivalence with the
legacy oracle are enforced.  The process boundary is intentionally low
frequency; the pure in-process adapter remains the fixture-test path and its
decision latency is asserted below 50 ms per replay fixture.

## 14. Phase 4 implementation status

`src/booktrans/production_control/fault_injection.py` is a test-only,
disk-backed fake-run builder.  It writes minimal source bindings, frozen
selection, manifest/state, checkpoint chain, handoff/recovery facts, and
successor artifacts only under pytest's isolated temporary directory.
`restart_from_disk()` is read-only and is invoked twice per crash scenario;
the result must be identical and the immutable-like snapshot must be
byte-identical.  It covers fourteen named crash points from request write
through successor progress, separately from the twelve historical incident
replays.

`production_campaign_events.jsonl` is append-only derived operational
telemetry.  An event has a timestamp, event type, operational state, cursor
summary, worker/policy/attempt identifiers where available, duration/result,
and reason.  Append failures are counted and reported to the console but do
not alter state, locks, worker exit handling, or recovery decisions.  The
read-only `derive_production_campaign_event_metrics.py` derives productive,
recovery, and blocked durations; units/hour; worker startup/mean
uninterrupted-run timing; and recovery success rate from events alone.  It
tolerates one interrupted final JSONL line.

Phase 4 also distinguishes a missing `worker_id` from a mismatched one:
the former is non-derivable identity evidence and blocks; the latter remains
a stale operational handoff eligible for rotation.  The legacy policy oracle
and shadow comparisons remain in place for Phase 5.

## 15. Phase 5 implementation status

`scripts/production/control_plane_decision_adapters.ps1` is the production
hot-path, side-effect-free port of the cursor and handoff adapters.  It is
dot-sourced once by the Supervisor, so normal cursor/handoff decisions do not
start the Phase-2 Python shadow process.  The Supervisor collects facts and
dispatches outcomes; it does not own the pure cursor/handoff rules.

The production cursor adapter now decides `PROGRESS`, `NO_PROGRESS`,
`CURRENT_PARTIAL`, `SEALED_STATE_DRIFT`, `BATCH_COMPLETE`, and
`INTEGRITY_FAILURE`.  The production handoff adapter now decides
`ACCEPT_CONTINUE`, `NORMALIZE`, `STALE_ROTATE`, `TRIAGE`, and `BLOCK`; the
Supervisor maps only those decisions to existing lifecycle operations such as
normalization, rotation, recovery triage, and terminal reporting.

The previous cursor calculation and `Get-LegacyCurrentWorkerHandoff` remain
as `-WhatIf` / `-ShadowControlPlane` oracles.  A mismatch with the production
adapter raises `CONTROL_PLANE_DECISION_DIFF`; the existing Python shadow also
guards the PowerShell/Python port equivalence.  The policy registry remains
the only content-policy boundary.  Metrics record cursor, handoff, policy,
and event-append decision overhead without influencing any authoritative
decision.
