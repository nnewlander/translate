# Architecture Explorer v4 Ticket #77 implementation record

## Fixed inputs

- Ticket: #77, `v4-03 Candidate Relationship 逐对象可信审核`
- Parent Spec: #74, `Architecture Explorer v4：规模化架构内容与批量可信审核（proposed Spec）`
- Integration branch: `experiment/matt-skills`
- B0: `09aaf4a033123c7eca8d280fa8982e5b0e711b86`
- Bstart: `09aaf4a033123c7eca8d280fa8982e5b0e711b86`
- Implementation branch: `codex/architecture-explorer-v4-ticket-03`

The remote integration Head was checked live before the Worktree was created. At
that point it was identical to B0. The implementation Worktree was created from
that exact commit.

## Frozen public contract before implementation

The shared #76/#77 consumer handoff sample is
`docs/architecture-explorer/v4-review-projection-handoff.json`. It is a
display-interface fixture, not a Decision, receipt, or trust anchor. #77 emits
`review_projection` schema `architecture-explorer.review-projection.v1` with
canonical object keys, explicit `result` and `state`, and `approved_scope` as a
list. #76 consumes that object; `node_review_projection` remains a historical
Node compatibility input. The shared `review_cli.py` integration hunk belongs
to #77 only for dedicated review subcommands and review-material production;
#76 retains View discovery, View composition, projection consumption, and
generation assembly. Merge of the shared file requires explicit handoff.
For `review_cli.py`, merge #76's repeated `--view-definition` discovery and
composition handling first, then add #77's dedicated relationship review
commands and material projection before `write_site`; a single-path
`args.view_definition.read_bytes()` implementation must not replace the
multi-View input contract.

- The canonical review object key is `(kind, object_id)`. The supported kinds
  are `candidate-node`, `candidate-collaboration`, and
  `candidate-decomposition`.
- Historical Candidate Node keys that carry `node_id` are accepted only through
  a compatibility adapter. Existing Node Decisions, State, Continuity, and
  projections are not rewritten.
- Relationship semantics are `collaboration` or `decomposition`. Semantics and
  direction are independent of provenance, Decision result, Continuity, and
  endpoint review state. No review behavior may be inferred from an ID prefix.
- A relationship Packet binds the exact source and target Node identities and
  responsibilities, direction, semantics, Claims, Evidence records, reviewed
  source snapshot, dependencies, and retained unknowns.
- The read-only review projection reports an explicit per-object `result`
  (`APPROVED`, `REJECTED`, or `Pending`), `state` (`current`, `needs-review`,
  `unknown`, or `not-reviewed`), and approval scope. Missing material is never
  promoted or inferred.
- Endpoint Pending or review-state changes alone do not block relationship
  review and do not propagate trust. An endpoint rejection affects a
  relationship only when a trusted Decision carries an explicit structured
  rejection basis bound by canonical hash to the exact `RESPONSIBILITY` Claim
  on which the relationship relies. Historical rejection without that basis is
  ambiguous and is never guessed from Claim text.

## Exclusive write boundary

Ticket #77 owns Review definitions, Packet construction, trusted-source and
Decision validation, State, Continuity, the read-only relationship review
projection, and their tests. It does not modify the renderer, View Projection,
or View-definition composition owned by #76. It does not implement the batch
workflow owned by #78-#80 and does not publish comments or create Owner
Decisions.

## Pre-agreed TDD seam

Tests exercise the public
`Relationship Review Definition -> Packet -> trusted Decision/source -> State -> review projection`
boundary. This catches identity, endpoint/responsibility, semantics/direction,
Evidence, source-snapshot, trust-anchor, Continuity, and projection failures.
It intentionally does not test private helpers or renderer behavior.

## Implemented behavior

- Added a closed Relationship Review Definition and Packet contract for
  Candidate Collaboration and Candidate Decomposition.
- Added a centralized v3 Collaboration compatibility adapter. It obtains the
  old record's semantic family from the authoritative typed Collaboration
  container and never from the relationship ID.
- Added exact Packet bindings for object identity, endpoint identities and
  responsibilities, normalized semantics, direction, relationship text,
  Evidence records, source snapshot, declared dependencies, and retained
  unknowns.
- Added trusted GitHub Owner/admin source validation against an independently
  supplied host policy, a closed Decision contract, atomically written State,
  monotonic Continuity, and an explicit read-only review projection.
- Deepened the shared Review trust module: historical Node Review and new
  Relationship Review now use one host-owned approval-anchor type, Trust
  Policy, and source-matching rule. Object-specific Claim and material checks
  remain local.
- Extended Node Decision v2 with an optional
  `architecture-explorer.rejection-basis.v1`. Each rejected Claim is bound by
  canonical SHA256, reason code, and explanation, and the basis hash is bound
  into the trusted GitHub comment body. Decisions without it remain valid
  historical assets.
- Relationship review is independent of endpoint review state. A current
  rejected endpoint Decision triggers conservative relationship re-review only
  when its explicit rejection basis binds the exact endpoint `RESPONSIBILITY`
  Claim. Rejection of another Claim leaves the relationship current; missing or
  malformed rejection basis produces `unknown / ambiguous`.
- State update and read-only projection call the same Continuity assessment.
  Projection re-evaluates live material and the latest trusted Node projection,
  so a persisted old `current` State cannot mask invalidated endpoint support.
  Projection never writes State or synthesizes a Decision.
- The actual Review CLI now prepares relationship packets, updates one
  relationship State, reads authoritative relationship material directories,
  and projects Node, Candidate Collaboration, and Candidate Decomposition
  reviews during `build`. Node projection intentionally precedes relationship
  assessment.
- `update-relationship-review-state` and `build` now call the same Node Review
  Definition/material loader and trusted projection seam. An endpoint without a
  Node Definition or Decision remains Pending and does not block relationship
  review; an explicitly supplied incomplete or untrusted material directory
  fails before State is written.
- Formal `main()` constructs Relationship Trust Policy only from the package's
  host-owned production anchor registry. CLI arguments, environment variables,
  review material, and the caller-selected repository cannot add an anchor.
  The registry remains empty until a separate Owner-authorized installation.
- Rejection-basis mutation, replacement with a different Claim, and reuse on a
  different Node are rejected by the combined Claim, trusted comment body,
  Receipt, and exact Anchor bindings.
- Identity, endpoint responsibility, semantics, direction, source snapshot, or
  Evidence changes make the old relationship Decision historical and
  `needs-review`; no new Decision is synthesized.
- Historical Candidate Node keys are normalized without rewriting Node review
  definitions, Decisions, State, Continuity, or projections.

No production trust anchor, real review comment, Owner Decision, batch-review
workflow, renderer change, View Projection change, or View-definition change
was added.

## Verification evidence

All Python commands used the Worktree-local `.venv\\Scripts\\python.exe`
junction to the verified project environment.

- `python -m pip check` -> `No broken requirements found.`
- `python -m compileall -q src\\booktrans\\architecture_explorer` -> PASS.
- Focused v4 seam, including the actual CLI packet/state/build entry and all
  three object kinds:
  `python -m pytest tests\\architecture_explorer\\test_v4_relationship_review.py -q`
  -> 16 passed.
- Round-two affected historical Node material/build regressions, using short
  isolated `--basetemp`: 39 passed. The temporary root was removed afterward.
- Targeted historical Node/CLI/review regressions -> 105 passed, 3 deselected.
  One historical Packet test initially hit the Windows sandbox temp-path limit;
  that exact test passed under short root `D:\\translate\\t77-review-fix`,
  which was then removed.
- Historical non-browser review seams:
  `python -m pytest ... -k 'not real_browser and not real_chrome'`
  -> 120 passed, 20 deselected.
- Historical Chrome review seams, outside the sandbox because Chrome Crashpad
  cannot start inside it -> 20 passed, 68 deselected.
- Complete `tests\\architecture_explorer` run with Chrome -> 651 passed and 2
  failures caused by Windows path length in deeply nested temporary Git
  repositories. Re-running those exact two tests under the short temp root
  `D:\\translate\\t77` -> 2 passed.
- A repository-wide `python -m pytest -q` was attempted once. Collection
  stopped because the isolated Worktree correctly lacks the ignored production
  fixture
  `runs/full-book-20260916T000200Z-decb09/full_book_batch_013_selection.json`
  required by `tests/unit/test_validate_authored_target_map.py`. No production
  `runs/` asset was copied, linked, or fabricated.
- The project environment has no `mypy`, `pyright`, or `ruff` installation;
  type-checker/linter execution was therefore unavailable. Compile validation
  and the typed public-seam tests were run instead.
- The Git blob hashes of
  `architecture-explorer/reviews/supervisor-worker.v1.json` and
  `architecture-explorer/reviews/supervisor-worker.source.v1.json` exactly
  match Bstart. No renderer, View Projection, or View-definition path changed.

## Dirty-worktree review handoff

```text
Base: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
HEAD: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
Branch: codex/architecture-explorer-v4-ticket-03
Worktree: D:\translate\worktrees\architecture-explorer-v4-ticket-03
Scope: GitHub Issues #77 and #74; public-interface coordination with #76
```

The independent reviewer must inspect both the original untracked Ticket #77
implementation and the targeted tracked-file repairs:

- `docs/architecture-explorer-v4-ticket-03.md`: fixed inputs, interface
  contract, implementation record, and verification evidence.
- `src/booktrans/architecture_explorer/relationship_review.py`: complete
  Relationship Review implementation.
- `src/booktrans/architecture_explorer/review_trust.py`: the shared trust root
  and approval-anchor matching rule.
- `tests/architecture_explorer/test_v4_relationship_review.py`: complete
  positive, negative, Continuity, trust-boundary, and compatibility tests.
- `src/booktrans/architecture_explorer/node_review.py` and
  `node_review_definitions.py`: compatibility-preserving shared trust reuse and
  the optional structured rejection basis.
- `src/booktrans/architecture_explorer/review_cli.py`: actual packet, State,
  authoritative material-reading, and build projection entry.

The implementation is intentionally uncommitted and unpushed for independent
`$code-review`.
