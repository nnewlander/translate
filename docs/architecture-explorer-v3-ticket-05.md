# Architecture Explorer v3 Ticket 05 delivery evidence

## Scope and status

- Ticket: GitHub Issue #63, `Architecture Explorer v3-T5: semantic review and constrained repair internal View`
- Parent Spec: GitHub Issue #58, approved Spec SHA-256 `0eb5f5650897fc0ba15b129a2782c09eeceda551e087cb3ff7e51ca5afbf1283`
- Worktree Base: `66585816244dbd58570d1b9363ab8585abb89b09`
- Contract posture: v2 remains current; v3 remains proposed.
- Review posture: implementation evidence only. No Standards/Spec review was run or claimed.
- Git posture: no commit, push, PR, merge, GitHub write, or v3 activation was performed.

## Content contribution

The isolated Candidate Fragment contributes exactly three Candidate Nodes,
three Candidate Decompositions, and two Candidate Collaborations under
`quality-semantic-review-and-repair`. The isolated View Definition contribution
adds `quality-semantic-review-and-repair-internal` and the entry action from the
existing `quality-and-repair` View Appearance.

The baseline shared `architecture-explorer/views/recursive-views.v1.json`
remains byte-for-byte unchanged. Ticket 05 keeps its View and entry action in a
separate auditable contribution so concurrent content tickets do not overwrite
one another. Ticket 06 may combine the contributions without introducing a new
runtime configuration system.

Canonical object hashes and the exact 11-object contribution are recorded in
`docs/architecture-explorer-v3-ticket-05-content-delta.json`. This is a Ticket
contribution record, not the final batch Content Delta Manifest; Ticket 06 must
reconcile it with the other content tickets against its frozen candidate Head.

## Evidence and retained unknowns

| Candidate responsibility | Exact Source Facts / Evidence | Retained unknowns |
| --- | --- | --- |
| `semantic-review-contract-boundary` | `process_unit → reviewer_dispatch_reason` (`evidence:sha256:2ad2ef43dd06310aac8f94beb75ced83a771a10e09787afbffa312c8df52b9ab`), `process_unit → review_with_contract_boundary` (`evidence:sha256:21bdd7d118512a9003b58a70c49bed4c10f42a27df0ef5a88614b4f5e363b8e5`), plus both definition facts | Concrete `reviewer` receiver dispatch and runtime reachability remain unknown. |
| `repair-authorization-gate` | semantic `process_unit → decide_approved_repair_contract` call (`evidence:sha256:cb24e0b86ebcd884747d463bafdd332faf46a4ec9bc6c8614cfdbac3278d9579`) and definition (`evidence:sha256:c4b4ffa893088a9463f196276d23df2e240683e0f2899783b2f248c5e6552dfd`) | The gate checks an existing approved contract scope; it is not an automatic approver and does not prove a repair will run. |
| `constrained-repair-call-boundary` | semantic `process_unit → repair_with_contract_boundary` call (`evidence:sha256:e1a90c2d85a20605e101b13d2b236cbc0bcbd76522a1c402fdc0c76846aded77`) and definition (`evidence:sha256:60c20454d9012e1006dc2a6f10060f74e4a8113a506b0426d5f6bafedf2bfceb`) | Concrete `repair` receiver dispatch and runtime reachability remain unknown. |

Every Candidate Decomposition and Collaboration states that its static Evidence
does not prove actual runtime ordering or reachability. All new Nodes and
Relationships remain `candidate`; the UI projects them as Pending Review even
when a controlled test marks the parent Node `Current / Approved`.

The relationship binding audit is exact:

| ID | Source | Target | Label |
| --- | --- | --- | --- |
| `candidate-decomposition:quality-semantic-review-and-repair-to-semantic-review-contract-boundary` | `quality-semantic-review-and-repair` | `semantic-review-contract-boundary` | 语义审核与受限修复分解为审核合同边界 |
| `candidate-decomposition:quality-semantic-review-and-repair-to-repair-authorization-gate` | `quality-semantic-review-and-repair` | `repair-authorization-gate` | 语义审核与受限修复分解为修复授权门禁 |
| `candidate-decomposition:quality-semantic-review-and-repair-to-constrained-repair-call-boundary` | `quality-semantic-review-and-repair` | `constrained-repair-call-boundary` | 语义审核与受限修复分解为受限修复调用边界 |
| `candidate-collaboration:semantic-review-contract-boundary-to-repair-authorization-gate` | `semantic-review-contract-boundary` | `repair-authorization-gate` | 审核合同信息衔接修复授权范围判断 |
| `candidate-collaboration:repair-authorization-gate-to-constrained-repair-call-boundary` | `repair-authorization-gate` | `constrained-repair-call-boundary` | 批准合同衔接受限修复调用 |

## RED/GREEN record

1. RED: the Candidate seam failed because the Ticket Fragment did not exist.
   GREEN: the exact 3/3/2 Candidate content projected with valid Evidence and
   retained unknowns.
2. RED: the View seam failed because
   `quality-semantic-review-and-repair-internal` did not exist. GREEN: the
   Appearance entry action, four Appearances, five Relationships, and trust
   isolation projected successfully.
3. Visual follow-up RED: real Chrome at 1280 CSS pixels rendered the technical
   View suffix as the main title, had no independent View status or parent
   Scope identity, and drew five straight relationships with overlapping or
   obstructed labels. GREEN: the main title now uses the clicked Scope Node's
   display name, `Candidate · Pending Review` is independent, the stable parent
   is labelled `当前范围 · 父节点`, and generic routing/label placement clears
   unrelated cards and other labels.
4. Responsive RED: at 480 CSS pixels, long decomposition edges crossed earlier
   nodes in the single-column stack. GREEN: a generic candidate-graph side
   corridor and obstruction-aware polyline routing cleared the stack; the same
   assertions passed at 1440, 1280, 960, and 480 CSS pixels.
5. GREEN navigation: real Chrome completed pointer/keyboard entry, breadcrumb,
   Source Trail, BACK/HOME, focus restoration, exact relationship binding, and
   responsive overflow checks at all four widths. The Worker mixed-relation
   View passed the same endpoint/card/label geometry checks.
6. Earlier full-suite feedback exposed a shared View Definition collision. The direct
   shared-file edit was removed; the isolated contribution passed the same
   focused tests and the complete suite.
7. Final visual RED: at 1280 CSS pixels the third decomposition routed below
   its target and back upward; at 480 CSS pixels the third decomposition label
   was crossed by the second decomposition path. GREEN: decomposition-scope
   Views keep three ordered children on one row from 761 CSS pixels upward,
   obstruction-aware label placement rejects every candidate crossed by another
   relationship, and downward decompositions may not route below their target.
8. Final visual GREEN: decomposition and collaboration edges now carry distinct
   visual classes and dash patterns. Ticket 05 collaborations remain short,
   direct links between adjacent responsibilities; their labels remain on their
   own edge and retain Candidate/Pending Review wording rather than implying a
   confirmed runtime sequence.

## Validation evidence

- Project interpreter prefix, `booktrans` import, and `python -m pip check`:
  PASS (`No broken requirements found`).
- Ticket focused suite: `9 passed in 23.64s`; real Chrome covered 1440, 1280,
  960, and 480 CSS pixels with no skip.
- Shared T7 renderer and recursive View regression: `64 passed in 163.01s`,
  including same-page resize and the existing Worker mixed-relation View.
- Complete authoritative Architecture Explorer suite:
  `536 passed in 1344.28s (0:22:24)`, exit code 0.
- `git diff --check`: PASS.
- Shared baseline View Definition: no diff.
- No navigation core, quality pipeline production code, formal review asset,
  or shared dependency was modified.

## Shared renderer delta and integration overlap

The renderer change is generic and data-driven: a View whose Scope Node is also
an Appearance and whose remaining Appearances are decomposition targets gets a
Scope-over-children grid even when candidate collaborations are also present.
Edges retain direct paths when clear, otherwise use card-aware orthogonal
routing; labels search path-adjacent free space and then free surface space,
rejecting positions crossed by any other visible relationship. From 761 CSS
pixels upward, decomposition-scope Views use three ordered child columns so a
three-step collaboration chain stays adjacent. Candidate decompositions and
collaborations use distinct generic visual classes and dash patterns. No Ticket
ID appears in renderer or CSS logic.

This overlaps T3's mixed-relation layout work in Scope labelling, candidate
View status, obstruction-aware paths, and label collision avoidance. It overlaps
T4 only in the parent-over-children visual idea; T4's pure-decomposition tree
criterion is intentionally not copied because Ticket 05 is a mixed
decomposition-and-collaboration View. Ticket 06 must reconcile these shared
renderer hunks rather than merge either content branch wholesale. Existing T7
DOM helpers now read the last SVG path point as the target endpoint so both
straight and polyline paths are tested correctly.

The Candidate Fragment, View Definition, and entry action did not change during
the visual follow-up. Therefore the 11 canonical hashes and exact Content Delta
contribution remain unchanged.

## Offline demonstration

- Entry page:
  `output/architecture-explorer/v3-ticket-05/index.html`
- Projection evidence:
  `output/architecture-explorer/v3-ticket-05/explorer-data.json`
- Generation check: `OFFLINE_DEMO_PROJECTION_PASS` with exactly the Ticket 05
  four Appearances and five visible Relationships.
- Offline artifact SHA-256: `index.html`
  `3e4c6710fa076afcd5e9ce74badcd41fe5b6ed801155b2d3ae930ae883a85433`,
  `app.js` `cb1c2f92b0c387a97e2e125c223d1c9ac6c00f7400bfa05d55042a0c305ac341`,
  `styles.css` `ff6776e3a1e999a8aad8b959e41f4de8f82cf38f3bcf05b286a67ebdb7e9a2bb`,
  and `explorer-data.json`
  `a43cca412209c05203d717af482af055d7b5bb60e7888f60efb99045635c62ee`.
- Key viewport screenshots:
  `screenshots/quality-semantic-review-and-repair-1440.png`,
  `screenshots/quality-semantic-review-and-repair-1280.png`,
  `screenshots/quality-semantic-review-and-repair-960.png`, and
  `screenshots/quality-semantic-review-and-repair-480.png`.

## Remaining integration risk

The final batch Manifest and candidate Head do not exist in this uncommitted
Ticket worktree. Ticket 06 must combine the independent content contributions,
recompute the complete Base/Head content digests, validate exact bidirectional
Manifest equality from immutable Git commits, and rerun its own candidate-Head
acceptance. A fresh independent `$code-review` remains an Owner-scheduled next
step and was intentionally not started here.
