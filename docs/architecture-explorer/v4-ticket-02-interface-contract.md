# Architecture Explorer v4 Ticket 02 interface contract

This note records the shared interface and exclusive write boundary before
implementation starts. It is an implementation handoff for Issues #76 and #77,
not a Review authority or a change to the current v3 contract.

## Bases

- Integration branch: `experiment/matt-skills`
- Bstart: `09aaf4a033123c7eca8d280fa8982e5b0e711b86`
- Fixed design and final content-delta base (B0):
  `09aaf4a033123c7eca8d280fa8982e5b0e711b86`

## Unified Review Projection v1

- The only shared top-level field is `review_projection`; its schema is
  `architecture-explorer.review-projection.v1` and its `objects` member is an
  object keyed by `<kind>::<object_id>`.
- Every entry repeats the canonical `object_key` object `{kind, object_id}`.
  Its value must exactly match the map key. `kind` is one of
  `candidate-node`, `candidate-collaboration`, or
  `candidate-decomposition`; Ticket #76 currently consumes Relationship
  entries and keeps the Node compatibility adapter separate.
- A legacy Node `node_id` retains its historical meaning and is converted to
  `object_id` only by the compatibility adapter. Historical authoritative
  assets are not rewritten.
- A Relationship entry has an explicit `semantics` object with `family`
  (`collaboration` or `decomposition`) and `direction` containing exact
  string `source_node_id` and `target_node_id` fields. Ticket #76 validates
  them against the independently projected Relationship and never derives
  trust from them.
- `result` is exactly `APPROVED`, `REJECTED`, or `Pending`; `state` is exactly
  `current`, `needs-review`, `unknown`, or `not-reviewed`; `reasons` is a list
  of strings; `approved_scope` is a list of objects.
- Each approved-scope object has exactly `object_key`,
  `reviewed_source_snapshot`, `claims_sha256`, `evidence_ids`, and
  `decision_id`. The snapshot has exact `revision` and `worktree_state`
  strings; the object key matches its parent; hashes, Evidence IDs and
  Decision ID are non-empty strings.
- `APPROVED` requires a non-empty approved scope. `REJECTED` and `Pending`
  require an empty scope. `Pending` is valid only with `not-reviewed`, while a
  decided result may not use `not-reviewed`.
- `retained_unknowns` is a JSON array of non-empty strings on a decided
  Relationship review. The Relationship Review producer emits it after
  verifying the Decision and its source under Trust Policy. The only
  authority is the Decision's `DEPENDENCIES_AND_LIMITS.value.retained_unknowns`
  Claim. The producer preserves the strings and their order.
- The View Projection consumer validates the array and its association with
  a decided review and trusted source, then carries it unchanged to the
  renderer. An explicit empty array means the trusted Decision lists no
  retained unknowns. An omitted field is accepted for older projections and
  means the projection did not supply that information. The renderer preserves
  this distinction. A Pending review carries neither this field nor a trusted
  source, even when an endpoint has an approved Decision.
- The producer and consumer ship together. The formal build acceptance
  exercises each decided Relationship from the trusted batch through
  `review_projection` and View Projection to the browser. Compatibility inputs
  with an omitted field remain readable under the same v1 schema.
- For a decided Relationship, the producer sets `source.status` to
  `trusted-owner-admin`, copies `repository`, `author`, `author_association`,
  `permission` and `comment_url` from the verified source, and emits the
  Receipt's canonical `sha256`. It sets `source.decision_id` from the verified Decision.
  View Projection carries the source metadata to the detail renderer even
  when the approved scope is empty. Older projections may lack source detail.
  The renderer displays available verified metadata and a comment link
  without fetching GitHub or deriving trust from the link.
- Missing Relationship review data projects as `Pending` / `not-reviewed`, an
  empty approved scope, and reason `trusted-review-decision:missing`. Invalid,
  duplicate, conflicting, semantically mismatched, or unknown-object entries
  fail generation; they never silently degrade to Pending.
- The directly transferable non-authoritative sample is
  `docs/architecture-explorer/v4-review-projection-handoff.json`. It is a
  contract fixture only and MUST NOT be installed or displayed as a trusted
  Review record.

## View Projection and View definitions

- A projected Relationship exposes its semantic independently as
  `collaboration`, `decomposition`, or `formal`. Semantic, direction, endpoint
  identity, provenance, and explicit Review are orthogonal fields.
- View Projection is derived, read-only display data. It contains complete
  displayable Node and Relationship records plus View-local Appearance data;
  one validated Evidence index and one Source-file catalog. The renderer does
  not resolve authoritative container locations.
- The authoritative View-definition namespace is every `.json` file below
  `architecture-explorer/views/`, recursively and in sorted repository-relative
  path order. Normal CLI builds and Content Delta use the same discovery
  function. Repeated `--view-definition` remains the explicit compatibility
  override. Documents compose before projection; duplicate IDs, unresolved
  cross-file references, duplicate paths and decomposition cycles fail closed.

## Exclusive write boundary

Ticket #76 owns input compatibility adapters, View Projection, composable View
definition input, Explorer generation assembly where minimally necessary, the
single renderer, and tests of those surfaces.

Ticket #77 owns Review definitions, Packets, trusted validation, Decisions,
State, Continuity, review projection authority, and tests of those surfaces.
Ticket #76 consumes only an explicit review projection and uses controlled test
data for the shared interface. Such test data is never a trusted Review record.

`review_cli.py` is shared integration surface, not a second contract owner:

1. Ticket #76 supplies the base build flow, View discovery/composition,
   repeated `--view-definition` compatibility and `generation_inputs` records.
2. Ticket #77 adds only its Relationship Review commands/material loading and
   attaches the above `review_projection` before `write_site`.
3. Integration must retain both blocks. In particular it must not replace the
   #76 discovery list with one `args.view_definition.read_bytes()` call and
   must not rename `review_projection`, `objects`, or `approved_scope`.
4. Producer changes to this schema are coordinated here first; #76 and #77 do
   not create parallel field names or independently modify each other's
   exclusive implementation.

Read-only coordination evidence on 2026-11-25: the #77 producer's actual
`project_relationship_reviews` output and the shared handoff JSON were both
accepted by the #76 consumer; the #77 focused producer suite reported 16
passing tests. This records compatibility, not trust installation.

Any required change to this shared interface or any write-scope collision must
be reported before implementation crosses this boundary.
