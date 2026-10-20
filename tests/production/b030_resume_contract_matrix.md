# B030 Resume Contract Matrix

This is L2 coverage mapping for frozen B029 incident snapshots. It introduces
no runtime state, authority, disposition, or transition.

| Scenario | Persisted state / current authority | Completed transitions | Expected next transition | Create-once / commit barrier | Provider calls / replay |
| --- | --- | --- | --- | --- | --- |
| R1 | Consumed recovery handoff; unique current partial | Handoff consumed | Normalize and triage current recovery | Handoff identity is normalized once | 0; stable on repeat |
| R2 | Missing/exact/conflicting Human candidate evidence | Exact current parent/head | Create, reuse, or fail closed | Candidate path is create-once | 0; no overwrite |
| R3 | B029 historical recovery plus current Human CA descendant | Human descendant is current | None; historical recovery is inert | CA head supersedes historical stages | 0; projection is pure |
| R4 | Complete seal generation; successor manifest absent | Seal evidence durable | Commit prepared checkpoint | Manifest commit is idempotent | 0; replay is noop |
| R5 | Historical machine `HUMAN_REQUIRED`; unique current CA has either an eligible corrective transition or a safe exhausted semantic state | Historical terminal only | Eligible current recovery wins; otherwise safe exhaustion yields `DEFERRED_SEMANTIC_DEBT`; ambiguous/unsafe current authority remains `HARD_STOP` | Current CA/effective-QA/disposition binding wins over every historical machine escalation | 0; no Human escalation |
| R6 | Current-head corrective episode: trigger, executed, or validated terminal evidence | Exact current head plus rereview/repair bindings | Existing follow-on transition only while nonterminal; validated terminal yields its canonical disposition | Recovery state is monotonic (`TRIGGER < EXECUTED < TERMINAL`); stale head or wrong episode cannot terminate current recovery | 0 provider calls in replay; terminal never resurrects its trigger |

Acceptance for every row: same persisted evidence yields the same current
authority and existing next transition; immutable overwrite and duplicate
provider dispatch are zero; replay is stable.
