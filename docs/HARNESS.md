# Harness：当前保护与未来预留

## 当前已实现（M1–M5.1d）

### Source integrity

`inspect`、`extract` 和 `identity` 均在读取 input 前记录 SHA-256、size 与 mtime（mtime 只作诊断），完成后重新计算 SHA-256。两个 hash 不同会以 `SOURCE_MUTATED` BLOCKER 失败。OOXML source package 始终由 `zipfile.ZipFile(..., mode="r")` 打开；运行 JSON 和 SQLite 只写入项目的 `runs/` 与 `data/`。

### DOCX/package validation

输入必须存在、可读、是 ZIP，并至少具有 `[Content_Types].xml`、`_rels/.rels` 与 `word/document.xml`；所需 XML 必须可解析。失败时使用 `INPUT_NOT_FOUND`、`INVALID_ZIP` 或 `INVALID_DOCX`，给出路径/缺失 part 等定位信息。

### 显式风险报告

检查器为已发现的 fields、tracked changes、content controls、text boxes、equations、SmartArt、OLE 和 embedded objects 产生 warning。可疑但未分类的 `word/` part 不会静默视作已支持。置信度与边界写入 JSON 的 `special_constructs.detection_confidence`。

### M2 Coverage Harness

`extract` 建立可序列化的最小 Document IR，并对每个 extracted visible text span 进行 scope accounting。`coverage_complete` 的严格必要条件为：source integrity PASS、`unclassified_visible_text == 0`、`unknown_potential_visible_content == 0`。不确定性不会被混入普通 coverage：冲突的 AlternateContent、未知 field result、AltChunk/Office Math/OLE/subdocument、无法确认的 DrawingML/VML shape、段落外文字都会创建可定位的 `CoverageEntry`，并令结果 incomplete。comments 与 deleted revision text 按显式 `EXCLUDED_BY_POLICY` 记账。

### M3 Identity round-trip and preservation Harness

`identity` 只能在 `runs/<run_id>/candidate/identity.docx` 生成 candidate。它将 M2 的 `TRANSLATABLE` text-node locator 转成 identity patch plan，并执行 XML text assignment，即便 target 等于现有字串也记录 attempted/verified write。`patch_report.json` 会列出 unresolved locator、write counts、跳过的 protected/excluded span 和 touched OOXML parts；任何 resolution failure 都是 blocker。

candidate 必须可作为 DOCX 打开且 required XML 可解析。所有未触碰 ZIP entry 的解压 payload 必须逐 bytes 相同；media entry 的路径/count/解压 SHA-256 必须相同；全部 `.rels` payload 必须相同。touch XML 以 C14N 检查 identity structure/text 等价。最后 M2 re-extract candidate，比较 story/part/locator/block/text/span/coverage accounting；不以含 document SHA 的 `unit_uid` 做跨包 equality。两边 coverage complete、逻辑 mismatch=0 和 preservation pass 才是 M3 PASS。

### M4 capability audit, deterministic QA and synthetic round-trip

`synthetic` 先为每个 translatable block 输出 `SAFE_SIMPLE` 或 `COMPLEX_UNSUPPORTED` capability record 和 reason code。Complex block 永不作为 identity fallback 被计作 synthetic success。安全 block 才会获得 deterministic Unicode synthetic target，并在 patch 前运行 QA：空 target、数字、URL、email、DOI、ISBN、protected text 和 unknown unit-id 均是 blocker。

candidate 使用 M3 同一 package validation、source integrity、untouched payload、media 与 relationship preservation 检查。M4 不错误复用 M3 的 source/candidate character-count equality：non-identity target 可以合法改变可见/可翻译字符数。验收改为按 stable locator 比较：approved block 必须等于 target，未 patch complex/protected block 必须仍等于 source；candidate coverage 仍必须 complete。

### M4.5 Format & Translation-Unit evidence audit

`format-audit` 只读 source package，并在开始/结束复验 source SHA-256。它输出 `format_unit_audit.json`，拆分 raw run fingerprint 差异中的 translation-relevant direct format、hyperlink/container、language/metadata-only 与其他 direct properties；未知 direct property 保守保留为 non-collapsible。该报告不写 candidate、不修改 Patcher，也不把 short block 自动合并；fragment sample 仅供后续设计审查。

### M4.6 Format semantics & fragment root-cause evidence audit

`format-fragment-audit` 同样只读 source，并输出 `format_fragment_decision_audit.json`。它将 M4.5 的 primary `OTHER_DIRECT_FORMAT_DIFFERENCE` 回溯到具体 `w:rPr` QName，记录 block count、共现格式差异与 representative sample；logical region 仅为统计模拟。对 fragment candidates，它保留 evidence bucket 和 OOXML/container root cause，uncertain 不被强制分类。该命令绝不生成 candidate 或写入 OOXML。

### M4.7 tagged format-anchor projection

`tagged-synthetic` 在 source SHA-256 前后复验下，从 M2 `BlockIR`/`SpanIR` 建立封闭的 `TranslationView`。它只将已观察到的 semantic anchors（bold、italic、underline、visible color/highlight、font family/size、character style、hyperlink 与 protected reference）暴露给 structured synthetic target；`w:spacing` 与 `w:w` 留在 source run template，属于 patcher-owned layout metadata。

写前 Harness 会阻断 unknown/missing/duplicated anchor、changed protected text、空 target、数字/URL/email/DOI/ISBN 丢失及未知 unit id。只对 `TAGGED_SUPPORTED` paragraph 投影；field/composite、AlternateContent、未知 container 或含非 text run structure 的 block 明确拒绝。候选包必须继续通过 package/media/relationship preservation、coverage complete 和 re-extraction expected-text 比较；anchor QA 还比较输出 run 的 `rPr` template，并验证 hyperlink 的 `r:id` 未改变。M4.7 是合成验证，非真实翻译或 render/release gate。

### M4.8 structural text-island projection and CJK policy

`structural-tagged-synthetic` 写出 `structural_capability_report.json`，按实际 XML 子类型列出 M4.7 generic paragraph-structure blocker、样例和是否可由 island strategy 安全支持。`EditableTextIsland` 只含可替换的 text run/hyperlink children；`StructuralAnchor` 保留在原位置，并以 kind、fingerprint 和 relative order 验证。bookmark、tab、break、drawing 与 VML pict 可在不改 anchor 的前提下支持；mixed text/structural run、field composite、AlternateContent 与未知 child 明确拒绝。

写后 QA 比较每个已 patch paragraph 的 structural-anchor count/type/fingerprint/order。任何 missing、duplicate 或 movement 都是 blocker。drawing/VML fingerprint 排除内部 `w:txbxContent` 的独立文本 payload，以允许其 BlockIR 自身合法更新，同时仍验证外围结构。对于新 CJK synthetic text，QA 阻断 `w:spacing`/`w:w` 的盲目继承；unchanged protected text 保留原 micro-layout。该 Harness 不替代 M7 的视觉 render/layout release gate。

### M5.0 real translation calibration contract

`calibration-export` 只读 source，并以 deterministic strata 导出 30–50 条 `TAGGED_SUPPORTED` request。`calibration-import` 只读取外部 JSONL response；没有 provider、API、retry 或并发框架。response schema 为 strict Pydantic；unknown/duplicate unit、unknown/missing anchor、protected mutation、空 target、URL/email/DOI/ISBN/数字丢失或 island contract 变化都会产生 actionable diagnostic，失败 unit 不得写入 candidate。

通过单位才沿用 tagged projector，之后必须通过 source integrity、candidate re-extraction expected-text、structural-anchor、coverage、media/relationship 和 untouched payload checks。visual review manifest 不声称 visual PASS：它仅记录 selected unit 与 drawing/textbox、island、continuation 风险，供外部 smoke review。M7 前不存在 full render/release gate。

### M5.1b conservative review gate

`calibration-import` 在 deterministic QA 之前检查 `translator_notes`。非空 note 的 response 明确标为 `REVIEW_REQUIRED`，写入 `review_required.json`（unit UID、display ID、note、source text），不 patch，也不被伪装成 QA PASS 或 FAIL。无 note response 才进入既有 schema、closed-world anchor/island、protected literal、number、URL/email/DOI/ISBN 与 structural contract QA，并按 `PASS`/`FAIL` 写入 `calibration_qa_report.json`；只有 PASS 投影。

candidate round-trip 对未 patch 的 FAIL/REVIEW_REQUIRED block 仍要求 source text 原样存在。被拒 response 的 pre-patch diagnostics 保留在 import artifact，但不会让一个仅含 PASS patch 的 candidate 错误地失败 package/round-trip proof。`calibration_import_report.json` 同时报告 response 状态分布、patch 数及 source SHA before/after。导入后的 visual manifest 带实际 patch status 和 high-priority visual smoke 标签，但不等同于 render/release QA。

### M5.1c URL and context contract hardening

protected anchor text is authoritative: generic URL extraction excludes a URL already represented by `protected_text`. Generic URLs trim only terminal prose punctuation (`.`, `,`, `;`, `:`, `!`, `?`); dots in a hostname, path or query value remain required. URL QA therefore still rejects a genuinely changed protected URL through exact `PROTECTED_TEXT` comparison. Unit tests cover terminal punctuation, path/query dots and protected URL mutation.

Future request export supplies locally available previous/next context for normal body prose independently of `continuation_hint`; target schema remains current-block-only. Import additionally emits `calibration_review_checklist.md`, a human navigation artifact for PASS-patched text. It contains no automated semantic judgment and does not alter candidate OOXML.

### M5.1d semantic calibration review and repair gate

`semantic_review_report.json` is a 40-unit, auditable language review artifact. Every record has one of `ACCEPT`, `REPAIR_REQUIRED`, or `HUMAN_REQUIRED`, identifies issue types, gives a concise reason, preserves the original target, and carries a repaired target only when the reviewer can determine it reliably. `HUMAN_REQUIRED` is reserved for unrecoverable source ambiguity, not ordinary translation preference.

`calibration_repaired_response.jsonl` remains untrusted input. It goes through the unchanged M5.0/M5.1 deterministic schema, closed-world anchor/island, protected literal, number/URL/email/DOI/ISBN and structural-anchor checks before projection. Only resulting `PASS` records enter the candidate; repair never grants a bypass. The visual smoke artifact is diagnostic only and never substitutes for M7 release validation.

## M5.2 contiguous pilot gate

The Chapter 1 pilot keeps both first-pass and repaired response files. Draft QA records schema, anchor, protected, island, numeric and URL checks before review. The final importer patches only deterministic `PASS` units; a `HUMAN_REQUIRED` unit retains source text in candidate. Re-extraction compares every patched unit with its approved Chinese target while source integrity, coverage, structural anchor, media, relationship and untouched-payload checks remain mandatory. `VISUAL_REVIEW_NOT_AVAILABLE` is explicit when no reliable read-only renderer exists; it is never a visual PASS.

## M5.2a audit and visual-smoke hardening

The normalizer records its input/output segment changes and runs only after raw response persistence. It may normalize only source-evidenced footnote superscript typography; deterministic QA remains responsible for changed, missing and extra numeric values. Semantic-review artifacts must reject no-op repairs by comparing text, segment kind/order, anchor reference and island reference. Bible snapshots are hash-verified during a run. Word COM detection and any Word PDF export are read-only smoke evidence only; failure to create a reliable rendering route is an explicit unavailable state, not a visual success.

## M5.2b immutable translation-artifact Harness

`persist_raw_response` creates `translation_raw_response.jsonl` once and refuses a second write. The normalizer reads it without write access, records SHA-256 before and after, and aborts with `RAW_RESPONSE_MUTATED` if the values differ. It writes a separate `translation_normalized_response.jsonl` and a `translation_normalization_report.json`; path collision or overwrite is rejected. The report records every changed unit, exact segment character span, before/after values, source footnote evidence, semantic-value proof, anchor/island/protected invariants, counts and rule distribution. No-change runs remain auditable through both artifacts and `input_equals_output=true`.

`FOOTNOTE_SUPERSCRIPT_TO_ASCII` is the only allowed contract normalizer. It needs an exact source number *and* explicit exporter footnote-reference evidence. It never guesses/moves/adds/removes numbers, rewrites prose dates or eras, or touches protected text. The test lifecycle covers safe conversion, a missing number, `1990s` paraphrase, `mid-2000s` approximation and protected numeric text. Review repair remains downstream and must pass the material structured-target comparison.

## M5.2c CompositeFragmentGroup Harness

A composite is writable only when its classification is `PROVEN_COMPOSITE_PROSE`. The patcher rejects possible/unknown groups and paragraphs with unknown or multi-island structure. It resolves every approved member locator, changes the designated primary island, and clears only the secondary island text nodes; it never deletes a textbox/drawing/footer node. Candidate verification re-extracts every member: primary must equal the approved composite target and every secondary must be empty. Existing source-integrity, coverage, media, relationship and untouched-payload checks remain mandatory.

## M5.2d / M5.2e CJK line-break evidence Harness

`CJKLineBreakPolicy` accepts only an explicit audited list of translated `body_paragraph` targets. Before creating a candidate it rejects a target that is not CJK prose, is outside normal-flow body, contains a URL/protected literal, is in hyperlink/field context, or has tab/break/drawing/VML non-text structure. A rejection leaves no usable partial candidate. The sole approved production mutation is paragraph-level `w:kinsoku="1"`; the policy neither writes text nor document-level settings. M5.2d's historical `w:wordWrap="0"` experiment was structurally safe but visually ineffective for the observed mixed-script token splits and is not retained as an approved mutation.

The output is compared with its M5.2c candidate through a full re-extract keyed by part and stable locator: every extracted plain-text block must be identical, there may be no extra block, coverage must remain complete, and unclassified/unknown counts must remain zero. It also compares protected structural-node type/identity ordering and invokes existing candidate validation, media hashes, relationship payload and untouched-entry preservation checks. `cjk_linebreak_audit.json` records the OOXML evidence, scope, observed PDF cases, properties, expected side effects and the distinction between a verified page mapping and an unresolved external-PDF mapping. M5.2e additionally proves probe text identity across A/B/C variants, template immutability, and that no probe variant is promoted without new visual evidence; it does not claim a visual solution.

### M5.2f positive-control contract

M5.2e's generic probe was visually `INCONCLUSIVE`: its baseline did not reproduce the real failure, so it does not establish any effect of explicit script language metadata. M5.2f uses the complete offending candidate paragraph as the payload. The manifest records the source paragraph SHA, exact pPr/run/property diff, token, width condition and payload SHA for every A/B/C/D fixture. A/B/C/D must have identical visible text at a given case and width. The external visual handoff accepts only `WHOLE_TOKEN_SAME_LINE`, `WHOLE_TOKEN_MOVED_TO_NEXT_LINE`, `TOKEN_INTERNAL_SPLIT`, or `OVERFLOW_OR_CLIPPING`; without an A/B reproduction, no candidate strategy can be approved.

The final Word→PDF review recorded zero A/B control reproductions in all 60
fixtures. Its decision is `PROBE_NOT_DIAGNOSTIC`, not a visual policy result:
experimental explicit-language and historical word-wrap mutations may not enter
a production candidate. `MIXED_SCRIPT_TOKEN_WRAP` is deferred to M7.

## M5.3a2 story-spanning scope ledger

`chapter_scope_closure_report.json` retains the original body baseline and
records every audited non-body candidate as included chapter content, excluded
running decoration, or review-required. It rejects duplicate evidence and a
body member presented as an added non-body unit. `REVIEW_REQUIRED` is a scope
blocker, not a silent omission. A scope-contract change never mutates an
existing raw response: its SHA remains checked and its provenance records
`RAW_REUSABLE` or `SCOPE_SUPERSEDED_RAW`.

The final closure gate passes only when `REVIEW_REQUIRED=0`; it reports both
physical included members and logical translation work orders. Each approved
two-member composite produces one logical translator/reviewer target while
retaining both physical members for patch/preservation verification.

### M5.3 composite contract parity

A flat composite target is allowed only for a single editable island with no
movable semantic anchor or protected literal. It still runs number, URL, email,
DOI/ISBN and footnote-evidence checks; being a composite never weakens the
ordinary deterministic safety contract. An unevidenced superscript digit is a
blocker, not an invitation to mutate immutable RAW.

The parity preflight serializes the aggregated member evidence and the required
response form for every approved group. Flat responses use the ordinary
`DeterministicCandidateQA`; structured responses reuse anchor/island checks.
Letter-attached source digits remain numeric-preservation evidence unless the
exporter explicitly labels the exact number a footnote. Historical incomplete
contract RAW stays byte-preserved as `CONTRACT_SUPERSEDED_RAW` and cannot count
as current RAW.

### Original PDF reference

`source_reference_manifest.json` binds the immutable original PDF and DOCX by
path, SHA-256 and book-identity evidence. PDF inspection is read-only and may
support an auditable reading-order or source-divergence finding. It does not
replace source integrity, locator, anchor/island, deterministic QA or package
preservation checks on the DOCX mutation path.

## 未来预留（均未实现）

- Translation deterministic QA：约束占位符、编号、链接和结构不变量。
- Semantic QA：检查翻译含义、术语和可读性。
- Document Release Gate：汇总所有 Harness，阻止不满足质量门槛的输出发布。
