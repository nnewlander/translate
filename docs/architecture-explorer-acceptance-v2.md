---
contract_family: architecture-explorer-acceptance
contract_version: 2
contract_ref: architecture-explorer/v2
status: proposed
runtime_authority: false
authority_issue: https://github.com/nnewlander/translate/issues/51
fixed_starting_baseline: 4c7f8be1f0be48189804a51ed42aaf8bdb64ab94
approved_spec_sha256: b33db66aabf8869028db2c81207a4d65a109c4ea5c72d225dfd4637c11ce842e
inherits: architecture-explorer/v1
activation: candidate-head-acceptance-then-metadata-only-switch
---

# Architecture Explorer v2 proposed acceptance contract

This file records the Owner-approved successor contract from
[GitHub Issue #51](https://github.com/nnewlander/translate/issues/51). It is a
repository preservation copy for implementation and review; it is not runtime
architecture data and does not activate v2.

v1 remains the sole current contract (`architecture-explorer/v1`). This proposed
contract intentionally has no `effective_baseline`. Activation is reserved for
Ticket #54 after the candidate Head H acceptance described in Issue #51.

## Inheritance and Delta

v2 inherits every v1 trust, Evidence, offline, browser, responsive and review
invariant that Issue #51 does not explicitly replace. The approved Delta is:

- navigation is driven by normalized Views and explicit Appearance actions,
  without a fixed maximum depth;
- Node identity is stable across Views and never widens a review decision;
- Architecture Decomposition, Candidate Collaboration, formal Relationship,
  static call and Evidence linkage remain separate semantics;
- T7 remains the single graph renderer and consumes the normalized current
  View;
- BACK, HOME, complete breadcrumb, keyboard activation, Evidence, Source Trail
  and focus restoration apply at recursive View depth;
- invalid references and Architecture Decomposition cycles fail closed, while
  intentional reciprocal View Transitions remain valid and cycle-safe.

## Approved target inventory

- Top-level modules: 7
- Formal Relationships: 1
- Approved formal Evidence records: 13
- Candidate Nodes: 20
- Candidate Collaborations: 9
- Candidate Decomposition: 2
- Views: 9

Ticket #52 does not activate or fully populate this target inventory. It only
introduces the normalized generated View Projection and replays the existing
eight Views with their existing 18 Candidate Nodes and 8 Candidate
Collaborations. Tickets #53 and #54 own the remaining feature and activation
steps.

## Authority and conflict rule

The full approved Spec and Glossary are preserved in this repository and in
Issue #51. Executable Tickets remain authoritative for their own Delta, live
status and later explicit Owner overrides. A material conflict without a later
Owner override blocks implementation or review; it must not be silently merged.

Runtime generation must not read this contract. Existing Module Map, Candidate
Fragment v1, Source Facts, formal Relationship, Node Review, Continuity and
Evidence inputs remain the authorities for their domains. The generated View
Projection is disposable UI Projection only.
