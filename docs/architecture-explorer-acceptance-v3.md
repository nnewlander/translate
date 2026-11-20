---
contract_family: architecture-explorer-acceptance
contract_version: 3
contract_ref: architecture-explorer/v3
status: proposed
runtime_authority: false
authority_issue: https://github.com/nnewlander/translate/issues/58
implementation_issue: https://github.com/nnewlander/translate/issues/59
fixed_starting_baseline: 6a239f6919cbb791e69e56383b91dbb634aeaf0f
approved_spec_sha256: 0eb5f5650897fc0ba15b129a2782c09eeceda551e087cb3ff7e51ca5afbf1283
inherits: architecture-explorer/v2
activation: candidate-head-acceptance-then-contract-status-only-switch
content_delta_manifest_schema: architecture-explorer.content-delta-manifest.v1
---

# Architecture Explorer v3 proposed acceptance contract

## Contract status

- current_contract: architecture-explorer/v2
- target_contract: architecture-explorer/v3
- target_status: proposed
- effective_baseline: absent

## Inheritance and Delta

v3 inherits every v2 invariant not explicitly replaced by Issue #58. It
replaces long-lived exact totals for Candidate Nodes, Candidate Collaborations,
Candidate Decomposition Relationships and Views with dynamic inventory derived
from validated authoritative inputs.

Every v3 candidate acceptance validates a Content Delta Manifest v1 against a
fixed full Base commit and an explicitly supplied full Head commit. Both sides
are read from Git commit objects. Canonical object hashes, complete content
digests and the declared add/modify/delete set must match the actual object
delta in both directions.

Seven top-level modules, formal Relationships and formal Evidence are protected
objects. Any change to them fails this Ticket's acceptance and requires
separate Owner authorization.

## Runtime authority

The Manifest is validation and audit input only. Candidate Fragments, View
Definitions, Source Facts, Evidence and review assets remain the authorities
for runtime projection. Removing or replacing a Manifest cannot change a
runtime Projection; it can only make proposed-v3 acceptance fail.
