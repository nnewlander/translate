# Architecture Explorer T10: Python Definition Index and Source Trail

- **Status:** Historical approved parent contract for completed T10-1/T10-2
- **Project:** `nnewlander/translate`
- **Fixed integration baseline:** `14073edcea76a2b200bf15aaa4231c13f20e5ec9`
- **Current product contract:** `architecture-explorer/v1`
- **Authority:** This versioned document preserves the Owner-accepted T10 parent contract named during ticket acceptance. GitHub Issues #40 and #41 are authoritative for their ticket-specific deltas, execution status, dependencies, and later explicit Owner overrides. This historical contract does not authorize new implementation or changes to completed product behavior.
- **Preservation record:** The contract body was copied from the accepted local formal Spec (`SHA-256 cf93bfc83045c8bc8850e70cb08efd1b700c0866a8c75a31df99e05f98daa280`). Only this status, authority, and preservation metadata was added; the Issue contracts were not merged back into this document.

## Problem Statement

Architecture Explorer currently gives the Owner a trustworthy top-level architecture view, seven candidate drilldowns, Source Facts for direct imports and selected entry points, Candidate Evidence, reviewed relationship evidence, and node-review projections. Those capabilities establish provenance and trust boundaries, but they stop before the source structures that an engineer normally needs when investigating how a responsibility is implemented.

At the fixed baseline, the Source Facts scanner parses 320 managed first-party Python files and records 2,445 top-level direct-import facts and 214 selected entry facts. It does not index ordinary classes, functions, methods, or nested functions. It also does not expose a bounded call trail from a candidate node through its evidence-bearing file to a concrete definition and the calls inside that definition. The user can therefore see that a candidate explanation has source evidence without being able to continue to `process_unit()` and understand which calls are statically exact and which remain unresolved.

This missing depth must not be solved by overstating static analysis. A definition is not proof of runtime execution. An Evidence-bearing file is not thereby approved as belonging to an architecture module. A method with a compatible name is not proof that a dynamic attribute call dispatches to that method. Existing candidate, reviewed, continuity, and coverage states must remain authoritative and distinct.

## Solution

T10 will extend the existing Source Facts pipeline with two additive, independently versioned projections:

1. A Python Definition Index covering all managed Python files and recording static facts for classes, functions, methods, and nested functions.
2. A bounded Python Call Facts projection covering the four translation-execution source files accepted by the Owner and classifying each collected call as `exact-local`, `exact-import`, `unresolved-dispatch`, or `dynamic-or-ambiguous`.

The existing Candidate Evidence file reference will become the entry point to a Source Trail in the existing right-click details surface:

`Candidate Node -> Evidence-bearing File -> Definition -> Call Site -> Confirmed Target or Unresolved Target`

The Source Trail will reuse the current Source Facts data, Candidate Evidence lookup, details dialog, Evidence renderer, navigation state, and security model. T7 remains the only graph renderer. T10 will not add a third graph level, a new page, an approval workflow, or a second evidence authority.

The principal end-to-end testing seam is:

`scan repository -> attach Source Facts -> apply Candidate/Review projections -> write site -> use Chrome to open a translation-execution candidate and navigate to source definitions and call facts`

Focused scanner tests may exercise definition identity and conservative call classification below this seam, but the product is accepted at the existing offline build and real-browser seam.

## User Stories

1. As an Architecture Explorer user, I want every managed Python file to expose its static definitions, so that I can move beyond import-level evidence without leaving the product.
2. As an Architecture Explorer user, I want classes, functions, methods, and nested functions to be distinguished, so that I understand the lexical role of each definition.
3. As an Architecture Explorer user, I want every definition to have a stable qualified symbol, so that I can identify it independently of its current line number.
4. As an Architecture Explorer user, I want every nested definition and method to identify its parent symbol, so that I can understand its lexical context.
5. As an Architecture Explorer user, I want every definition to retain its file, source span, source snippet, and Evidence ID, so that I can inspect and cite the exact static fact.
6. As an Architecture Explorer user, I want blank-line and line-number-only movement to preserve definition identity, so that harmless relocation does not create false evidence churn.
7. As an Architecture Explorer user, I want material changes to a definition's normalized source to be visible as changed evidence, so that continuity does not conceal source changes.
8. As an Architecture Explorer user, I want the definition browser to start from an existing Candidate Evidence file, so that the macro-to-micro trail has an explicit provenance link.
9. As an Architecture Explorer user, I want to see all definitions in an Evidence-bearing file, so that I can find the implementation point relevant to a candidate explanation.
10. As an Architecture Explorer user, I want to filter definitions by name and kind, so that large provider modules remain navigable.
11. As an Architecture Explorer user, I want to open `process_unit()` from the translation-execution candidate, so that I can inspect the real orchestration source behind the candidate explanation.
12. As an Architecture Explorer user, I want calls inside `process_unit()` to show their exact source location and expression, so that the explanation remains tied to real code.
13. As an Architecture Explorer user, I want unique same-module calls to resolve to their local definitions, so that deterministic local flow is directly navigable.
14. As an Architecture Explorer user, I want uniquely bound first-party imports to resolve to their scanned definitions, so that deterministic cross-file flow is directly navigable.
15. As an Architecture Explorer user, I want dynamic Provider attribute calls to remain unresolved, so that the UI does not invent a dispatch target.
16. As an Architecture Explorer user, I want unresolved calls to explain why they were not resolved, so that conservative analysis is useful rather than opaque.
17. As an Architecture Explorer user, I want an exact call target to open its definition in the same details experience, so that I can continue the Source Trail without changing navigation models.
18. As an Architecture Explorer user, I want breadcrumbs and a back action within the Source Trail, so that I can return from a target definition to its call site and evidence file.
19. As a keyboard user, I want file, definition, call, target, back, and close actions to be focusable and ordered, so that the Source Trail is fully usable without a pointer.
20. As a user of the macro architecture graph, I want T7 graph layout, edge semantics, resize behavior, and navigation to remain unchanged, so that source depth does not destabilize the architecture view.
21. As an architecture reviewer, I want Definition and Call Facts to be labelled as static Source Facts, so that they are never mistaken for runtime observations.
22. As an architecture reviewer, I want Candidate Node and Candidate Collaboration status to remain unchanged when source definitions are added, so that scanning depth cannot perform an approval.
23. As an architecture reviewer, I want top-level `source_coverage` to remain `unverified`, so that indexing files does not claim reviewed module coverage.
24. As an architecture reviewer, I want existing reviewed relationship and node-review projections to remain independent of the Source Trail, so that T3, T8, and T9 trust boundaries remain intact.
25. As an architecture reviewer, I want confirmed call targets and unresolved calls to use distinct text labels in addition to styling, so that trust is not communicated by colour alone.
26. As a maintainer, I want the T10 Definition Index and Call Facts versions to be independent of the T8-bound legacy scanner version, so that adding T10 data does not invalidate existing review inputs.
27. As a maintainer, I want existing direct-import, entry, PowerShell, Candidate, and formal Evidence IDs to remain unchanged, so that the T10 extension does not rewrite history.
28. As a maintainer, I want scans with syntax errors, unstable files, linked paths, or invalid UTF-8 to retain the existing fail-closed behavior, so that broader extraction does not weaken input safety.
29. As a maintainer, I want deterministic output for identical repository content, so that generated Explorer data remains auditable.
30. As a maintainer, I want fixed-baseline count assertions to run only against the fixed baseline or its explicit fixture, so that ordinary future source growth does not become a false regression.
31. As a maintainer, I want rebinding, star imports, dynamic imports, `getattr`, callbacks, and polymorphic attribute calls to fail closed, so that the scanner cannot produce a false exact edge.
32. As a product Owner, I want T10 split into independently demoable vertical slices, so that definition breadth can land before bounded call depth without leaving a horizontal-only intermediate state.

## Implementation Decisions

1. **Contract shape.** `architecture-explorer/v1` remains the active product contract. T10 is an explicit additive delta and does not create `architecture-explorer/v2`.

2. **Reuse of Source Facts.** Definition and call data live inside the existing Source Facts object and participate in its deterministic content hash. They do not create a parallel source database, service, page, or authority.

3. **Independent extension versions.** The existing top-level Source Facts scanner version remains the T8-bound compatibility value for the legacy direct-import, entry, and PowerShell facts. T10 adds `architecture-explorer.python-definition-index.v1` and `architecture-explorer.python-call-facts.v1` as separate nested schema/version extensions. A T10 implementation must not update T8's pinned scanner input merely to advertise the new extension.

4. **Legacy evidence isolation.** Existing direct-import, entry, PowerShell, formal relationship, Candidate Evidence, Candidate Fact, Node Review, and T9 review material remain byte-for-byte or semantically unchanged according to their existing continuity contracts. New definition and call Evidence IDs occupy new evidence kinds and cannot collide with existing IDs.

5. **Managed Python scope.** The Definition Index uses exactly the existing managed Python selection policy: Git-tracked first-party Python files under the declared roots, excluding Architecture Explorer's own scanner and generation code. T10 does not widen to tests, third-party packages, generated output, or arbitrary repository files.

6. **Definition kinds.** The closed definition-kind set is `class`, `function`, `method`, and `nested-function`. `AsyncFunctionDef` follows the corresponding function, method, or nested-function kind. A nested class remains kind `class` and records its lexical parent.

7. **Qualified symbols.** A qualified symbol is derived from the existing canonical module identity plus the lexical definition chain. Methods include their enclosing class; nested functions include their enclosing function or method. Duplicate definitions with the same lexical name are retained as separate facts using a deterministic same-parent occurrence discriminator rather than silently merged.

8. **Parent symbols.** Every definition records the canonical module identity as its parent when it is module-level, or the enclosing definition's qualified symbol when it is nested. Parent relationships describe lexical containment only; they do not describe runtime ownership or architecture assignment.

9. **Definition evidence contract.** Each definition records definition kind, qualified symbol, parent symbol, occurrence discriminator, structural anchor, syntax position, current line-and-column span, exact AST-located source snippet, normalized snippet hash, file hash, and Evidence ID. The snippet is the complete source extent returned for that definition by the Python AST locator; consumers must not reconstruct source from line numbers.

10. **Stable identity.** Definition Evidence IDs reuse the existing canonical evidence builder principles and are derived from stable semantic fields, not line numbers or file timestamps. Blank-line insertion and line-only relocation preserve identity when the path, definition kind, qualified symbol, parent, occurrence, structural anchor, syntax position, and normalized snippet remain equivalent. A material source, name, parent, kind, or ambiguity change may create a new identity.

11. **Definition ordering.** Files and definitions use deterministic source order with qualified symbol as a stable tie-breaker. UI filtering must not change the canonical serialized order.

12. **Bounded call scope.** Call Facts are collected only for the Owner-approved four-file translation-execution source set. The scope is an explicit closed allowlist recorded in generated data. Merely adding a new Python file or Candidate Evidence does not silently expand call scanning.

13. **Call fact contract.** Each call records its enclosing definition Evidence ID, callee syntax, resolution class, resolution reason, source span, exact call-expression snippet, structural anchor, occurrence discriminator, file hash, and call Evidence ID. `target_definition_evidence_id` is present only for exact resolutions.

14. **`exact-local`.** A call may be `exact-local` only when its callee is a statically named symbol that uniquely resolves to a scanned definition in the same module and no relevant parameter, assignment, import, nested definition, `global`/`nonlocal` declaration, or other binding can shadow or replace it. Uniqueness is required; best-effort guessing is forbidden.

15. **`exact-import`.** A call may be `exact-import` only when a unique explicit first-party import binding resolves directly to one unique scanned definition and the imported name is not shadowed or rebound. T10 need only support explicit direct imports required by the accepted Source Trail. Star imports and runtime import mechanisms never qualify.

16. **`unresolved-dispatch`.** A call through an object, parameter, protocol field, chained attribute, or other runtime receiver is `unresolved-dispatch` unless the T10 closed rules prove one unique target. Provider calls through the Worker Providers object are explicitly unresolved even when compatible Provider methods exist elsewhere in the scan.

17. **`dynamic-or-ambiguous`.** Calls involving star-import ambiguity, dynamic imports, `getattr`, subscripts, call-returned callables, callbacks, higher-order values, ambiguous duplicate bindings, unsupported binding shapes, or parse uncertainty are `dynamic-or-ambiguous`. This result has no target definition.

18. **Exhaustive classification.** Every collected call in the four-file scope receives exactly one of the four resolution classes. Unknown shapes are not dropped and do not default to an exact class.

19. **Known Source Trail.** At minimum, the fixed-baseline `process_unit()` trail resolves the direct route-selection call as `exact-import` and the direct request-enrichment and translation-dispatch helper calls as `exact-local`. Provider calls made through Worker Provider attributes remain `unresolved-dispatch` and expose no confirmed implementation link.

20. **Candidate-to-file projection.** The file list for a Candidate Node is derived from that node's existing `source_assignment.evidence_ids` and the existing Source Evidence lookup. T10 does not write file membership back into Candidate Facts or top-level modules.

21. **Cross-file continuation.** An exact call target may continue the trail into a file that was not directly referenced by the starting Candidate Node. The UI labels that transition as an exact static call target, not as additional Candidate Evidence or approved module ownership.

22. **Unresolved target behavior.** An unresolved call displays callee syntax, location, classification, and reason. It must not offer a confirmed target link or list name-compatible methods as inferred targets.

23. **Existing details surface.** Candidate Evidence cards gain a file-definition action. File definitions, definition source, call facts, and exact targets render within the existing right-click details dialog using the existing Evidence presentation primitives. No separate source page or editor is introduced.

24. **Source Trail navigation.** The details dialog maintains an internal breadcrumb/back stack for Candidate Node, Evidence-bearing File, Definition, Call Site, and exact target. Closing the dialog returns focus according to the existing context-menu behavior and does not alter macro navigation.

25. **Definition browser.** The file view shows its path, static-fact warning, definition count, name filter, kind filter, and deterministic definition list. Empty results and files with no definitions are explicit states rather than errors.

26. **Trust presentation.** Every Source Trail level continues to show that facts are static. Candidate status, source-assignment status, reviewed projection, call resolution, and unresolved reason remain separate textual concepts. Exact call resolution does not change Candidate or review status.

27. **T7 preservation.** T7 graph nodes, edges, labels, layout, relationship geometry, resize behavior, workflow highlighting, and navigation state are unchanged. Source Trail actions do not add graph nodes or edges and do not invoke a new graph renderer.

28. **T3/T8/T9 preservation.** T10 does not modify T3 formal Relationship decisions or Evidence, T8 Candidate Fact/Decision/State/Projection semantics, or T9 production review materials and trust gates. The build must demonstrate that adding T10 facts alone does not downgrade, approve, or otherwise rewrite those projections.

29. **Failure behavior.** Existing stable-read, path-containment, link/reparse-point, UTF-8, syntax-error, and PowerShell fail-closed behavior remains authoritative. Definition or call extraction failure cannot be treated as an empty successful scan.

30. **No production coupling.** The scanner parses source without importing or executing scanned production modules. T10 makes no change to real translation routing, Provider configuration, production Worker behavior, Supervisor behavior, or production artifacts.

31. **Baseline-bound metrics.** The counts of 320 files, 199 classes, 1,538 functions/methods, and 1,737 definitions are acceptance facts only when the evaluated source revision is the fixed baseline or an explicit immutable fixture representing it. On later revisions, the scanner reports current counts and tests verify invariants rather than requiring obsolete totals.

32. **Ticket dependency.** T10-1 establishes definition identity, file browsing, and the reusable browser state. T10-2 references those definition identities for exact targets and extends the same browser state with calls. T10-2 is therefore blocked by T10-1; no separate prefactor ticket is necessary.

## Acceptance Criteria

- [ ] When evaluated against the fixed baseline, the Definition Index parses all 320 managed Python files and reports exactly 199 classes, 1,538 functions/methods, and 1,737 total definitions.
- [ ] The fixed counts are guarded by an exact baseline revision check or immutable baseline fixture; a later valid source revision is not failed merely because its counts differ.
- [ ] Every definition has a valid kind, qualified symbol, parent symbol, occurrence discriminator, structural anchor, source span, exact AST-located snippet, normalized snippet hash, file hash, and stable Evidence ID.
- [ ] The Call Facts projection is limited to the four approved translation-execution files and records that closed scope in generated data.
- [ ] Every collected call has exactly one resolution class: `exact-local`, `exact-import`, `unresolved-dispatch`, or `dynamic-or-ambiguous`.
- [ ] At the fixed baseline, `process_unit()` resolves route selection as `exact-import` and its deterministic request-enrichment and translation-helper calls as `exact-local`.
- [ ] Calls through Worker Provider attributes remain `unresolved-dispatch`, expose a reason, and do not expose a confirmed Qwen implementation target.
- [ ] Blank-line or line-number-only movement updates locators and file hashes without changing stable definition or call Evidence IDs when all identity fields remain equivalent.
- [ ] Rebinding, star import, dynamic import, `getattr`, callbacks/higher-order values, and polymorphic attribute dispatch never produce false exact targets.
- [ ] A user can navigate in the existing details dialog from the translation-execution candidate to its Evidence-bearing file, `process_unit()`, its source, its calls, and any confirmed target, with keyboard-accessible back and close behavior.
- [ ] Existing Candidate Facts, Candidate Collaborations, the T3 formal Relationship and Evidence, T8 projections and continuity, T9 review materials, and all seven top-level `source_coverage=unverified` values remain unchanged.
- [ ] Existing direct-import, entry, PowerShell, Candidate, and formal Evidence IDs remain unchanged on the fixed baseline.
- [ ] T7 graph topology, renderer, layout, edge semantics, navigation, and viewport/resize behavior remain unchanged.
- [ ] The authoritative Architecture Explorer validation collects and executes the complete suite with no deselection and no skipped required Chrome tests.

## Testing Decisions

1. **Highest seam.** The primary test builds an Explorer payload from a repository scan, attaches Source Facts, applies the existing Candidate and review projections, writes the real static site, and uses the required Chrome harness to navigate from a translation-execution Candidate Node to an Evidence-bearing file, `process_unit()`, its call facts, and an exact target. This is the acceptance seam for both data and UI behavior.

2. **Good-test rule.** Tests assert schemas, stable identities, conservative classifications, unchanged trust projections, and user-observable navigation. They do not assert private helper call order, implementation-specific AST visitor structure, or pixel snapshots.

3. **Fixed-baseline inventory.** Against the fixed baseline, the scan must parse all 320 managed Python files and produce exactly 199 class facts, 1,538 function/method facts, and 1,737 total definitions. The test must first assert the baseline revision or use an immutable baseline fixture so the numbers cannot fail on normal future source changes.

4. **Four-file scope.** A contract test asserts that Call Facts contain exactly the four approved paths, record the scope explicitly, and do not collect calls from a fifth file merely because it is scanned or referenced elsewhere.

5. **Definition contract.** Fixtures cover module functions, classes, methods, nested functions, nested classes, async definitions, decorators, duplicate same-parent names, Unicode source, and files with no definitions. Every fact must have a complete locator, snippet, hashes, qualified symbol, parent, occurrence, structural anchor, and valid Evidence ID.

6. **Stable identity.** Adding blank lines before or between definitions and relocating an unchanged definition must update current locations and file hashes without changing the definition Evidence ID. Renaming, reparenting, changing kind, changing normalized source, or creating an ambiguous duplicate must produce the specified material change or ambiguity behavior.

7. **Determinism.** Two scans of identical content produce deeply equal Definition Index and Call Facts projections and equal deterministic hashes.

8. **Exact local calls.** Positive fixtures prove unique unshadowed same-module calls resolve to their target definition. Negative variants cover parameters, assignments, nested bindings, imports, duplicate definitions, and relevant `global`/`nonlocal` shapes.

9. **Exact import calls.** Positive fixtures prove a unique explicit first-party import resolves to one scanned definition. Aliases are tested only if implemented by the closed T10 contract. Missing targets, multiple targets, external modules, and rebound imported names cannot become exact.

10. **Required negative matrix.** Star imports, dynamic imports, `getattr`, callbacks/higher-order values, call-returned callables, and polymorphic or protocol-backed attribute calls must produce `dynamic-or-ambiguous` or `unresolved-dispatch`, never an exact target.

11. **Fixed Source Trail calls.** At the fixed baseline, `process_unit()` must expose the deterministic route-selection, request-enrichment, and translation-helper calls with the classifications defined above. Provider attribute calls must be `unresolved-dispatch` even though compatible Provider methods exist in the indexed source.

12. **No inferred Candidate Evidence.** Opening an exact target outside the starting node's directly referenced files must not append Evidence IDs to the Candidate Fact, Candidate Collaboration, or source assignment.

13. **Trust-regression matrix.** Existing T3 relationship review state, T8 node review projection and continuity, T9 review materials, all 18 Candidate Nodes, all 8 Candidate Collaborations, and all seven top-level `source_coverage=unverified` states remain unchanged when T10 extensions are present.

14. **Legacy identity regression.** Existing direct-import, entry, PowerShell, Candidate, and formal Evidence IDs remain unchanged on the fixed baseline. The T10 extension version cannot replace the T8-bound legacy scanner version.

15. **Browser behavior.** Real Chrome tests cover pointer and keyboard entry, file browsing, filters, definition source, call classification labels, exact-target continuation, unresolved target behavior, breadcrumbs, back navigation, close/focus restoration, and reopening without stale trail state.

16. **T7 graph regression.** Existing desktop viewport and same-page resize round-trip tests continue to verify unchanged graph topology, edge endpoints, labels, candidate/review styling, and navigation. Source Trail actions must not create graph elements.

17. **Failure cases.** Existing syntax-error, unstable-read, invalid UTF-8, linked-path, malformed-data, and missing-Evidence tests remain fail closed. New malformed definition/call records are rejected rather than partially rendered.

18. **Complete validation.** The authoritative Architecture Explorer validation command must collect and execute the entire Explorer suite with no deselection and no skipped required Chrome tests. T10 tests are additive to the collected suite rather than a replacement for existing coverage.

## Out of Scope

- A full Python call graph or whole-repository Call Facts projection.
- Runtime tracing, coverage collection, profiling, production telemetry, or proof that a call executed.
- Guessing Provider implementations from method names, Protocols, annotations, compatible signatures, or nearby Candidate Evidence.
- Interprocedural value flow, points-to analysis, type inference, data-flow analysis, callback resolution, decorator expansion, or control-flow reachability.
- General multi-language scanning or a reusable language-plugin framework.
- Expanding PowerShell scanning beyond its existing bounded Supervisor contract.
- Scanning tests, third-party packages, generated output, arbitrary local files, or Architecture Explorer's own scanner code.
- Approving Candidate Nodes, Candidate Collaborations, source assignments, module coverage, or new formal Relationships.
- Changing the seven top-level module definitions or promoting any `source_coverage` value from `unverified`.
- Rebuilding or replacing the T7 graph renderer, adding a third graph level, or introducing a source-code graph.
- Adding a new page, source editor, database, network service, approval system, or GitHub dependency to ordinary build/browse behavior.
- Redesigning or rewriting T3, T8, or T9 decisions, continuity rules, review materials, or UI semantics.
- Modifying real translation, Provider, Worker, Supervisor, QA, repair, checkpoint, candidate, or release behavior.
- Creating T10 GitHub issues, tickets, commits, branches, pushes, pull requests, or merges under this Spec-only authorization.

## Further Notes

### Fixed Call-Facts Source Set

The closed T10 Call Facts allowlist is:

1. `src/booktrans/production_worker/pipeline.py`
2. `src/booktrans/providers/router.py`
3. `src/booktrans/providers/qwen_mt.py`
4. `src/booktrans/providers/qwen_max.py`

At the fixed baseline these files collectively contain 111 indexed definitions. This number is diagnostic context, not an additional permanent product invariant.

### Existing Baseline Facts Preserved

The fixed baseline currently yields 2,445 top-level direct-import facts, 214 selected entry facts, eight known Supervisor function entries, fifteen Supervisor script bindings, and one known dot-source. T10 extends these facts; it does not replace them.

### T9 Integration Note

The fixed baseline contains no versioned path explicitly named as a T9 artifact. T10 therefore has no direct T9 data dependency at this baseline. When T10 is integrated with the later T9 state, the implementation must locate the actual T9 production review inputs, treat them as immutable, and run the trust-regression assertions against them. This is an integration verification prerequisite, not a reason to redesign T9 or a blocker to drafting T10.

### Recommended Ticket Split

1. **T10-1 — Python Definition Index and file-level definition browsing**
   - **Blocked by:** None.
   - **Vertical result:** The real scanner emits stable definition facts for all managed Python files; the offline build carries them; an existing Candidate Evidence file opens a filterable definition list and definition source in the existing dialog; fixed-baseline, continuity, trust, and Chrome tests pass.

2. **T10-2 — Bounded Call Facts and function-level Source Trail**
   - **Blocked by:** T10-1.
   - **Vertical result:** The four-file call scope emits conservative call classifications referencing T10-1 definition identities; `process_unit()` exposes exact local/import calls and unresolved Provider dispatch; exact targets and unresolved reasons are navigable in the existing dialog; trust and T7 regressions pass.

This split follows the actual dependency direction and keeps both tickets demoable. No preliminary wide refactor or separate schema-only ticket is justified.

### Readiness

There is no known product-design blocker to implementation. Owner acceptance of this Spec is sufficient to proceed to `$to-tickets`. Publishing tickets, setting tracker dependencies, and applying `ready-for-agent` remain separately authorized actions and are not performed by this draft.
