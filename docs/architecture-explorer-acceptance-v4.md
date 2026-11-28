---
contract_family: architecture-explorer-acceptance
contract_version: 4
contract_ref: architecture-explorer/v4
status: current
effective_baseline: a46aad8827f8ef04ca6df280c0f15db3dfb4b55a
runtime_authority: false
authority_issue: https://github.com/nnewlander/translate/issues/74
implementation_issue: https://github.com/nnewlander/translate/issues/85
fixed_starting_baseline: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
proposed_spec_sha256: c1ddcc9bca93b6584936da14e08eb88efabf212a379c66acfbdf00033804efec
inherits: architecture-explorer/v3
activation: candidate-head-acceptance-then-contract-status-only-switch
content_delta_manifest_schema: architecture-explorer.content-delta-manifest.v1
---

# Architecture Explorer v4 current acceptance contract

## Contract status

- current_contract: architecture-explorer/v4
- target_contract: architecture-explorer/v4
- target_status: current
- effective_baseline: a46aad8827f8ef04ca6df280c0f15db3dfb4b55a

## Inheritance and Delta

The proposed v4 contract inherits v3 invariants and uses the fixed Git object
above as its comparison Base. The Content Delta Manifest v1 declares the
canonical delta of the v3 inventory between that Base and the explicit full
candidate Head. The accompanying comparison accounts for Appearances, Evidence,
Perspectives, actual paths and review coverage. The seven top-level modules,
formal Relationship and formal Evidence require separate Owner authorization
for changes. Review Decisions, Receipts and approved scopes retain their
original bindings. The Manifest serves acceptance and audit only.

## Acceptance gates

1. **Content breadth and depth.** Provide the seven-module audit and findings
   for the five priority areas. Compare Nodes, Relationships by semantics and
   review coverage, Views, Appearances, Evidence, Perspectives and actual
   navigation paths from Git-backed authoritative inputs. Identify material
   increases and remaining coverage limits.
2. **Trusted review and Owner effort.** Verify the real mixed Node,
   Collaboration and Decomposition review, both exact Owner confirmations,
   individual comments and Receipts, independent Approval Anchors and atomic
   import with safe retry. Verify per-object scope and the observed reduction
   in manual work. Exercise legitimate rejection and Pending separately when
   the real batch does not contain them.
3. **Architecture interfaces and compatibility.** Verify independent
   Relationship semantics and trust, storage-independent View Projection,
   the shared renderer, historical assets, formal Relationship and Evidence,
   recursive navigation without a fixed depth limit, Source Trail and
   offline read-only operation.
4. **Semantic, visual and integration acceptance.** Retain the semantic and
   manual desktop and narrow-screen acceptance for every new View. Assess
   Claims, Evidence, Continuity and scope on their actual source revisions.
   Preserve separate evidence for Windows/Chrome CI and real host trust.
   Require complete Explorer validation, the candidate contract and Manifest
   checks, independent Standards and Spec review, CI and Owner acceptance
   before authorizing later contract activation.
