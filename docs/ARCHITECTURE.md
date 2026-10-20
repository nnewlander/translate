# 架构：M0–M5.1d 基础层

## 项目目标

`booktrans` 的长期目标是把英文书籍 DOCX 翻译为自然、准确的中文 DOCX，同时最大程度保留其 OOXML 结构与视觉语义。原始 DOCX 始终 immutable，且是唯一 Source of Truth。

当前交付为 M0–M5.1d：只读 inspection、source integrity、最小 Document IR、Coverage Ledger、Identity Patcher、synthetic 验证、text-island projection、真实翻译的校准文件合同、保守的 response import review gate、Harness hardening，以及一次小范围 AI semantic calibration review 与 targeted repair。它不包含外部模型调用、整书翻译或 production Reviewer 系统。

## 长期数据流（尚未实现的阶段以虚线表示）

```text
immutable Source DOCX
        │  read-only ZIP / OOXML inspection  (M1)
        ▼
  structured inspection report ──► SQLite audit history (M1)
        │
        ▼
  Minimal Document IR + Coverage Ledger (M2)
        │
        ▼
  Identity round-trip candidate (M3)
        │
        ▼
  Synthetic target + deterministic QA (M4) ──► Real translation / QA
        │
        └────► Format & Translation-Unit Audit (M4.5, read-only evidence)
                  │
                  └────► Format Semantics & Fragment Root-Cause Audit (M4.6, read-only evidence)
                              │
                              └────► Minimal Translation View + Tagged Format Projection (M4.7, synthetic only)
                                          │
                                          └────► Structural Text Islands + CJK Micro-layout Policy (M4.8, synthetic only)
                                                      │
                                                      └────► Real Translation Calibration Contract (M5.0, 30–50 external JSONL responses)
                                                                  │
                                                                  └────► Conservative Review Gate + PASS-only calibration candidate (M5.1b)
                                                                              │
                                                                              └────► URL/context contract hardening + human review checklist (M5.1c)
```

未来 patcher 必须对原 package 做受控的最小变更；绝不通过重新生成整个 DOCX 来替代它。重新生成会丢失未被高层库完全建模的关系、绘图、域、批注、样式和兼容性信息。

## 边界

- `domain/` 是 Pydantic 结构化模型，只保存字符串、数字、日期和嵌套模型；不持有 `lxml Element`、`python-docx` 对象或 ZIP handle。
- `ooxml/` 负责只读 ZIP、relationships 与 namespace-aware XML 检查。
- `harness/` 负责可验证不变量：source SHA-256 前后对比、M2 coverage complete 条件及 M3 package preservation/round-trip comparison。
- `storage/` 记录 document identity、inspection/extraction run、稳定 units；coverage 的细节保留为 run artifact，不为当前问题拆分额外表。
- CLI 的 `extract` 写出轻量 `document.json`、逐行的 `blocks.jsonl`、`coverage.json` 与 `extract_report.json` 到 `runs/<run_id>/`，不会写入 input。

## M2 Minimal Document IR 与 Span

一个 `BlockIR` 对应一个安全识别的 paragraph-like 单元：正文、标题、表格单元格、页眉/页脚、脚注/尾注、文本框或评论段落。它有稳定 `unit_uid`、人类可读 `display_id`、`SourceLocator`、纯文本、文本/结构 hash、span 及诊断。`unit_uid` 是 source SHA-256、part、稳定 XML path 与文本 hash 的确定性 hash；它不是每次运行生成的 UUID。

`SpanIR` 是 **source mapping，不是 translation granularity**。它只保留可见 `text` span 与 `field_boundary`：text span 带 scope classification、原始 text-node locators、轻量 direct-format fingerprint 和 hyperlink/content-control/field context；field instruction 不作为普通 text span。Translation Unit 未来仍以 paragraph-like `BlockIR` 为候选，M3 不为每个 span 建 prompt、unit 或 semantic segmentation。IR 不复制 XML tree，也不实现 style inheritance 或完整 Atom framework。

## M2 Translation Scope 与 Coverage

每个可见 text span 被显式归为 `TRANSLATABLE`、`PROTECTED` 或 `EXCLUDED_BY_POLICY`。`UNCLASSIFIED` 是已识别 text span 的 Harness 失败；`UNKNOWN_POTENTIAL_VISIBLE_CONTENT` 是 container/diagnostic 风险，不能伪造为不存在的 text unit。comments 明确按 `EXCLUDED_BY_POLICY` 入账；tracked deletions 也按该 policy 入账并附诊断，tracked insertions 作为当前可见内容提取并由 M1/M2 风险诊断标记。

Coverage Ledger 按 body、tables、headers、footers、footnotes、endnotes、textboxes、comments 分组，记录 units、可见文本 node/字符、各 scope 字符数、unclassified 和未知潜在内容。仅当 source integrity PASS、unclassified 为 0 且 unknown potential 为 0 时才会报告 `coverage_complete=true`。`extract` 在 incomplete 时仍写出完整审计产物，但以退出码 4 表示 coverage 不 complete。

## M2 特殊策略

- `mc:AlternateContent`：相同 Choice/Fallback 文本只提取一次；只有一个有文字的分支也可提取一次；分支文字冲突时提取 Fallback baseline 并创建 uncertainty，不会报告 complete coverage。
- fields：`w:instrText` 永不作为普通翻译文本。已知 PAGE/NUMPAGES/REF/PAGEREF/TOC 的 result 归 Protected；未知 field 的 displayed result 也暂为 Protected，但会产生定位明确的 coverage uncertainty，阻止 complete coverage。
- `w:sdt`：继续下钻其 Word content，保留 semantic boundary；不当作黑盒。
- `w:txbxContent`/VML textbox：其中段落进入 IR。无法证明是普通图片或 Word textbox 的 DrawingML/VML shape 记为 unknown potential。

## M3 Identity Round-trip Patcher

M3 从 immutable source package 复制出 `runs/<run_id>/candidate/identity.docx`，并仅按 M2 `TRANSLATABLE` span locator 在 candidate XML 上执行真实的 text-node assignment。即使 identity target 与原字串相同，writer 也必须赋值并验证；这避免“只复制文件就宣称通过”。Protected、excluded 和 field instruction 都不写。

每次 run 写出 `patch_report.json`（计划 block/span/node、locator resolution、attempted/verified writes、跳过项、touch part 与诊断）和 `roundtrip_report.json`。candidate 随后必须通过 DOCX validation；所有未触碰 ZIP payload 完全一致，media path/count/SHA 相同，所有 relationship payload 相同。被触碰 XML 以 canonical XML 比较 identity 等价性。

最后用 M2 extractor 重提 candidate，并忽略因 candidate SHA 改变而不同的 `unit_uid`，改按 story、part、locator、block type、plain text 和 span text/classification 比较。两侧 coverage 必须 complete，visible/translatable/protected accounting、block/story breakdown、文本和 locator 集合必须相同。

对逻辑上等价的 `mc:AlternateContent`，coverage 虽只入账一次，M3 writer 仍需同步其 Choice/Fallback 物理 branch。当前最小 non-identity primitive 只对每支一个 text node 的镜像安全支持；多 node branch 的非 identity distribution 明确拒绝，而不是猜测重分配。这不是完整 Office compatibility engine。

## M4 Synthetic Non-Identity Target + Deterministic QA

M4 的 synthetic target 是可重复的测试字符串（`【测试译文】` 加 source text），不是翻译。它先对每个含 translatable text 的 BlockIR 做 capability audit：只有单一可编辑 text span、没有 protected/field/hyperlink 混入、没有多 format/container 分配、locator 可解析，且 AlternateContent branch 可安全镜像时，才标记 `SAFE_SIMPLE`。所有其他 block 明确记为 `COMPLEX_UNSUPPORTED` 并附 reason code，不写回。

被审计为安全的 block 才生成 `SyntheticCandidate`，在进入 DOCX 前必须通过 EMPTY_TARGET、number、URL、email、DOI、ISBN、protected text 和 known unit-id 的 deterministic QA。投影策略只将完整 target 放入简单区域的首个 source text node，并清空同一已证明简单 span 的其余节点；不重建 paragraph/run，不改 relationship、media、field 或未知结构。

M4 re-extract candidate 后，已 patch block 的 plain text 必须等于 approved synthetic target，complex/protected block 必须仍等于 source plain text。coverage 仍须 complete；但 target 长度可变化，因此 M4 **不**要求 source/candidate visible 或 translatable character count 相同。package preservation 延续 M3，唯 touched XML 的 text 不再要求 canonical identity。

## M4.5 Format & Translation-Unit Audit

M4 的真实数据表明绝大多数 block 因 `MULTI_FORMAT_CONTEXT` 被保守拒绝，但该标签本身不足以证明必须建立完整 semantic-format framework。M4.5 只读分析 source OOXML 的 `w:rPr`，将 bold、italic、underline、font family/size、color/highlight、character style、hyperlink/container 和其他 direct-format 差异同 language/metadata-only 差异分开。只有 translation-relevant signature 和容器都等价、且没有其他 M4 blocker 的 block 才被计入“理论可折叠”的 normalized-safe estimate；本阶段不改变 Patcher。

同一审计还统计 BlockIR 的字符数、word count、短标题、标点/页码样 block 和带邻居文本的 suspicious prose fragments。它们仅是 evidence，不自动合并或改变 Translation Unit。BlockIR 因而仍是当前 paragraph-like candidate，而不再未经实证地等同于最终 Translation Unit。

## M4.6 Format Semantics & Fragment Root-Cause Audit

M4.6 把 M4.5 的 `OTHER_DIRECT_FORMAT_DIFFERENCE` 回溯到实际 `w:rPr` property QName，并按 CLEARLY_VISUAL_OR_SEMANTIC、LIKELY_TECHNICAL_OR_NON_SEMANTIC 与 UNKNOWN 记录 evidence。它还以连续的 translation-relevant format signature 与 container context 模拟 logical region count，不创建 FormatRegion domain model 或修改 write path。

M4.5 的 fragment candidates 在 M4.6 中只按结构、相邻 block 的断句/大小写、列表/页码/联系信息样模式分为 likely true prose fragment、likely legitimate standalone 与 uncertain；不会自动 grouping。根因只按实际 OOXML ancestor/container 统计，普通 body paragraph 分离也会明确报告为该事实，而不会臆测为特定 Word feature。

## M4.7 Minimal Translation View + Tagged Format Projection

M4.7 不改变 `BlockIR`/`SpanIR` 的 source-mapping 职责，而是在其上建立每个 paragraph-like block 的轻量 `TranslationView`。它的有序 segment 只暴露 plain text、语义 format/container anchor 与 protected reference；每个 anchor 的 deterministic id 指向已有 source locator/template，domain model 不保存 OOXML tree 或完整 `w:rPr`。

**Preservation-critical does not automatically mean model-visible.** 已观察且可能需要随目标语义移动的 bold、italic、underline、visible color/highlight、font family/size、character style 与 hyperlink 是 translation-visible anchors。`w:spacing` 和 `w:w`（character scale）仍是 patcher-owned micro-layout metadata：投影从 source run template 复制它们，但不会把它们暴露给未来模型，也不承诺英文 micro-typography 边界能逐字符映射到中文。

`tagged-synthetic` 只为结构受限的 `TAGGED_SUPPORTED` block 建立 deterministic Unicode structured target，并在写入前执行 closed-world anchor QA：未知、缺失、重复 anchor 与改变 protected text 都会失败。投影仅替换已批准 paragraph 内的 text run/hyperlink container，保留 paragraph properties、relationships、media 与未触碰 package payload；field/composite、AlternateContent、未知 container 或含非 text run structure 的 block 明确记为 `TAGGED_UNSUPPORTED`。

M4.6 标为 likely prose fragment 的 block 在 view 中只能获得 `continuation_hint`、previous/next text；它们仍单独返回 target，M4.7 不做 global automatic TranslationUnit grouping 或修改段落边界。candidate 重新 extract 后按 expected target text、anchor style/container、hyperlink relationship、protected text、coverage 与 package preservation 验证。它是 synthetic proof，不是翻译系统或视觉 release gate。

## M4.8 Structural Text Islands + CJK Micro-layout Policy

M4.8 将 M4.7 的 `UNSUPPORTED_PARAGRAPH_STRUCTURE` 拆为实际 OOXML 子类型。连续且只含 text run/hyperlink 的 direct paragraph children 构成 `EditableTextIsland`；bookmark、tab、break、drawing 与 VML pict 等节点构成顺序固定、带 structural fingerprint 的 `StructuralAnchor`。投影只替换已证明边界安全的 island，绝不删除或移动 anchor、paragraph properties、关系或 drawing；mixed text/structural run、field composite、AlternateContent 等不能证明安全边界的类型仍为 `TAGGED_UNSUPPORTED`。

外层 drawing/VML anchor 的 fingerprint 忽略其内部独立 textbox paragraph 的可变文本 payload，但继续保护外围 drawing/VML 结构、关系和位置；嵌套 textbox paragraph 作为自己的 BlockIR 投影。unchanged/protected source text 保留 `w:spacing` 与 `w:w`。新生成且含 CJK 的 target run 不复制这两个 English micro-layout 属性，但仍复制字体映射 metadata、字号、bold、italic、underline、可见 color/highlight、character style 和 hyperlink/container template。这不是中文排版引擎；最终 render/layout QA 仍在 M7。

## M5.0 Real Translation Calibration Contract

M4 synthetic stress testing 已结束；后续 layout 设计应主要由真实中文 calibration evidence 驱动，而不是继续放大 synthetic target。`calibration-export` 从 `TAGGED_SUPPORTED` block 中以 deterministic strata 选择 30–50 条，输出 translator-facing `calibration_request.jsonl`、简洁 `translation_policy.md` 和 `visual_review_manifest.json`。请求不含 locator、OOXML、raw `w:rPr`、`w:spacing` 或 `w:w`；只含 source text、sanitized source segments、anchor semantics/protected text 和必要的 fragment 邻文。若 source 没有可靠 Heading-style IR，selector 只能明确标为 `title_like_heuristic` 的短文本样本，绝不伪称 heading。

外部 translator 以严格的 `TranslationResult` JSONL 返回 target segments；不接 provider/API。`calibration-import` 重新建立 source TranslationView 并以 closed-world validation 验证 unit、anchor、protected text、URL/email/DOI/ISBN、numeric value、island order 与 structural contract。仅 QA PASS unit 可投影到 candidate；随后沿用 re-extraction、structural-anchor、package/media/relationship preservation 和 coverage Harness。32 个 tagged unsupported block 是 known release debt，不参与 calibration，也不在此阶段扩展 patcher。

结构正确与视觉保真是两种不同证明。M5.0 的 manifest 让外部视觉 smoke review 聚焦已翻译 sample 的 drawing/textbox、structural island 与 fragment 风险；它不实现 M7 的完整 Word/PDF release gate。

## M5.1b Real Calibration Import + Conservative Review Gate

`calibration-import` 复用 M5.0 的 strict JSONL schema 和 M4.7/M4.8 closed-world anchor、island、protected literal、numeric、URL/email/DOI/ISBN、structural-anchor 及 package Harness。每条 response 明确报告 `PASS`、`FAIL` 或 `REVIEW_REQUIRED`。`translator_notes != null` 是 `REVIEW_REQUIRED`：它不是 deterministic QA FAIL，但默认不进入 QA/projection，候选中必须保留原文；`review_required.json` 记录其 unit、display id、note 和 source text。只有 `PASS` response 才能写入 candidate。

真实 calibration 还证明既有 `continuation_hint` 不能识别全部语言层 fragment：一个 source block 以 “For cases in” 截断但没有 hint。后续 prose translation request 应通常提供轻量 previous/current/next context；translator 仍只返回 CURRENT target。Context may cross Block boundaries; mutation remains Block-local。此为下一轮 request 合同改进建议，不是 global grouping 或段落重写。

candidate 的 re-extraction 继续要求 PASS block 等于 approved Chinese target，而 `FAIL`/`REVIEW_REQUIRED` block 等于 source text。`visual_review_manifest.json` 在 import run 中标明 actual patch status，并将已 patch 的 textbox/邻近 drawing、structural island、table/caption、footer、multi-anchor、long body 标为 high-priority smoke review；这不是 visual PASS。

## M5.1c Calibration Harness Hardening

protected anchor 的 `protected_text` 是对应 URL 等 protected token 的 authoritative representation。generic URL QA 只处理未被 protected anchor 覆盖的 URL，并仅移除末尾普通句子标点；host/path/query 内的 `.` 不会被截断。因此受保护 URL 后接英文或中文句号不会产生 competing token，同时真实 protected URL 改动仍由 `PROTECTED_TEXT` 阻断。

未来 calibration export 对 normal body prose 在同 story/part 可安全取得时默认填充 `previous_text` 与 `next_text`；`continuation_hint` 仍只表示已有 fragment evidence，而不再决定 context 是否存在。response schema 仍禁止返回邻文：Context may cross Block boundaries; mutation remains Block-local。`translator_notes` 仅代表需要人工判断的真实歧义或上下文不足；正确保留代码/protected content、正常专名选择和结构处理不应产生 note。index-like translation policy 是公开的 open finding，尚未实现 parser、engine 或全局 role classifier。

import run 会写出 `calibration_review_checklist.md`，按 high/normal visual priority 列出每个已 patch block 的 exact target、source、selection categories 与 visual reason，作为 Word 人工核查导航；它不是 Semantic Reviewer 或视觉 release verdict。

## M5.1d AI Semantic Calibration Review + Targeted Repair

M5.1d 审核全部 40 个 calibration unit，而不把 deterministic PASS 当作语义正确性的替代证明。Reviewer 使用 source、target、selection role、semantic anchor、translator note 与可安全读取的局部邻文，输出 `ACCEPT`、`REPAIR_REQUIRED` 或仅在信息确实无法恢复时使用 `HUMAN_REQUIRED`。AI review by default；human escalation by exception。它不是多 reviewer、评分或置信度系统。

修订后的 response 仍复用 M5.0 schema 和 M4.7/M4.8 translation contract；unknown/missing/duplicated anchors、protected text、island boundary、数字/URL 或 structural anchor 的任何失败都继续阻止 patch。索引层级仅在相邻 page-reference pattern 能可靠证明边界时作最小 repair；无法恢复上级条目的情况保留为 `HUMAN_REQUIRED`，不由自动 grouping 猜测。真实 prose fragment 可读取邻文以理解 CURRENT，但 mutation 仍严格 Block-local。

此阶段仅生成小型 `TRANSLATION_BIBLE.md`，记录已由真实样本证明需要统一的术语和决定；不建立 glossary database、dependency graph 或 Translation Bible 平台。DOCX structural correctness 与 visual fidelity 仍为独立证明：M5.1d 的 visual smoke 只报告 `VISUAL_PASS` 或 `VISUAL_REVIEW_REQUIRED`，完整 Word/PDF release gate 仍属于 M7。

## M1 检测范围与限制

检查器统计 main body 段落、基于 `styles.xml` 的 Heading 样式、表格/单元格、DrawingML/VML 图像引用、`w:hyperlink`，并识别 headers、footers、footnotes、endnotes、comments。它还报告 field markers、书签、修订、content controls、text boxes、Office Math、SmartArt package parts、OLE/embeddings、drawings 和 anchors。

这不是完整 OOXML 渲染器：复杂 field 不能被可靠还原成逻辑 field；SmartArt 仅由 package part 识别；某些 shape text、AltChunk、第三方或未来 OOXML 扩展可能无法完整统计。报告以 `detection_confidence` 说明这些限制，并将未分类 `word/` parts 输出为 warning，不会静默声称支持。

## 已批准施工顺序（M5+ 均未实现）

- M0 — Engineering foundation
- M1 — Read-only Inspect + Source Integrity
- M2 — Minimal Document IR + Coverage Ledger
- M3 — Identity Round-trip Patcher
- M4 — Fake Translator + Deterministic QA
- M5 — Real Translator + One Semantic Reviewer + Targeted Repair
- M6 — Evidence-driven consistency/context enhancements
- M7 — Microsoft Word render + PDF + Release Gate

## 设计约束

M0–M5.2 遵循 **high assurance, low ceremony**：每个 Harness 与 audit 必须解决一个当前能观察到的完整性风险或决策未知（source mutation、未分类可见文字、潜在可见内容不确定性、真实 write path、package payload drift、未经验证的 non-identity projection、format semantics/fragment root cause、semantic anchor projection、structural island boundary、CJK micro-layout、真实 response contract、连续章节的真实译文）；每个模型字段都服务于文本定位、coverage accounting、round-trip proof、写前 QA 或当前 evidence。复杂度只从已观察失败增长，不预建 provider routing、Reviewer、dependency graph 或完整样式系统。

## M5.2 Contiguous Chapter 1 Pilot

M5.2 是单本已知 source 的一次性、显式边界 Chapter 1 试点，不是通用 chapter parser 或整书运行。manifest 记录 literal source marker、body ordinal、每个 included unit 和 support decision；first-pass draft 与 repaired response 都保留。AI reviewer 以 `ACCEPT`、`REPAIR_REQUIRED` 和真正例外的 `HUMAN_REQUIRED` 复核，repair 仍是 untrusted input，必须完整重走 deterministic、anchor、round-trip 与 package-preservation Harness。Bible 在 run 开始时冻结为 snapshot，任何 proposal 仅供下一轮采用。邻文可帮助理解 CURRENT，却从不扩大 Block-local mutation scope。

## M5.2a Production Readiness Hardening

Raw Translator output and contract-normalized output are separate run artifacts. The only approved numeric normalization is a source-evidenced footnote superscript representation conversion; it cannot alter semantic values, protected text, anchors or islands. `REPAIR_REQUIRED` is a structured-diff state, not a reviewer intention: without a material target change it must be corrected to `ACCEPT` or flagged as a reviewer contract error. Canonical Bible additions are made only between runs; every run retains an immutable snapshot. Visual fidelity remains a separate proof from package correctness and can be unavailable when Word/another reliable renderer cannot be used.

## M5.2b Immutable Translation Artifacts

Every future real-translation run has an explicit, independently retained evidence chain: `translation_raw_response.jsonl` (`RAW_TRANSLATOR`) → `translation_normalized_response.jsonl` (`NORMALIZED`) → deterministic QA (`DETERMINISTIC_QA_PASS` or `DETERMINISTIC_QA_FAIL`) → `semantic_review_report.json` (`SEMANTIC_ACCEPT` or `SEMANTIC_REPAIR`) → `translation_repaired_response.jsonl` (`FINAL_APPROVED` after the unchanged Harness). The raw file is created once with exclusive creation and its SHA-256 is checked before and after normalization; drift is the `RAW_RESPONSE_MUTATED` blocker. A run with no normalization still emits both files and records `input_equals_output=true`.

The normalizer is not a translator or repair layer. Its sole approved rule is `FOOTNOTE_SUPERSCRIPT_TO_ASCII`: the exact ASCII value must exist in source, the exporter must explicitly provide footnote-reference evidence, the target occurrence must be its Unicode superscript representation, and the segment may not be protected. It preserves segment ordering, kind, anchor and island identity. Missing, extra, paraphrased, approximate or protected numbers remain raw and are handled by deterministic QA or later review. This fixes the historical M5.2 evidence gap: the former claim that all 23 numeric cases were safe raw-pass normalizations is superseded; 20 raw records cannot be reconstructed and 3 were demonstrably semantic changes.

## M5.2c Structural Fragment Visual Repair

Default mutation remains Block-local. The sole exception is an explicit `CompositeFragmentGroup`, whose closed member set is supported by source OOXML order/container evidence and observed visual reading order. It has one normal-flow primary and only audited secondary fragments. Writeback replaces the primary editable island and clears text payload only in approved secondary islands; drawing/textbox/footer structure, geometry, relationships and media remain in place. This is neither global TranslationUnit grouping nor a paragraph merger. Visual evidence also suggests that `body.p.01102` may be a recoverable structural continuation rather than source incompleteness; its classification is decided only after the footer/source relationship audit.

## M5.2d / M5.2e CJK Line-Break Evidence Boundary

The second Chapter 1 PDF validated all five approved `CompositeFragmentGroup` repairs. A separate, repeated visual finding remains: Latin/numeric tokens may break inside a token and Chinese closing punctuation may start a new line. The candidate’s affected normal-flow paragraphs carried neither explicit `w:wordWrap` nor paragraph-local `w:kinsoku`; document-level `w:characterSpacingControl=doNotCompress` is not a kinsoku control.

M5.2d's OOXML Harness passed, but its later real Word/PDF evidence is only `PARTIAL_VISUAL_PASS`. `CJK_KINSOKU` (`w:kinsoku="1"`) is visually validated for the narrow Simplified-Chinese prohibition on starting a line with `。`. The earlier `WORDWRAP_LATIN_TOKEN_GUARD` hypothesis (`w:wordWrap="0"`) did not prevent `Compiling`, `fork`, or mixed numeric token splitting in this document, so it is not a production policy. The remaining `MIXED_SCRIPT_TOKEN_WRAP` question is tested only in a closed-world probe: all variants have identical visible text and remain `EXPERIMENT_ONLY` until a new Word/PDF comparison supplies visual evidence.

The former candidate required source-integrity, re-extraction text equality, coverage, structural-anchor, media/relationship and untouched-payload proofs; these remain structural rather than visual proofs. The English running header remains `RUNNING_HEADER_TRANSLATION_POLICY_OPEN` and is not part of either policy or probe.

## M5.2f Positive-Control Mixed-Script Wrap Probe

The M5.2e generic probe did not reproduce the actual Chapter 1 `Compilin` / `g`, `for` / `k`, or numeric token splits in any A/B/C variant. Its visual result is therefore `INCONCLUSIVE`: the observed reflow in the explicit-language variant is a `PROMISING_HYPOTHESIS`, not evidence that `w:lang` prevents token splitting. It is neither approved nor rejected by that nondiagnostic control.

M5.2f moves the actual complete target paragraphs, including their pPr, indents, justification, run/anchor formatting, and section layout conditions, into a single experimental DOCX. It compares A baseline, B historical wordWrap, C explicit language plus wordWrap, and D explicit language without wordWrap under W0 actual width plus two small probe-only right-indent pressure levels. Within each case/width group all variants must have identical visible text. The experiment is diagnostic only when A or B reproduces a real token-internal split and C/D prevents it without overflow; otherwise `MIXED_SCRIPT_TOKEN_WRAP` is deferred to M7. This remains a local experiment, not typography infrastructure or a production candidate.

The final external Word→PDF review found zero A/B positive-control reproductions
across all 60 fixtures. M5.2f is therefore `PROBE_NOT_DIAGNOSTIC`; it supplies
no evidence for or against C/D explicit language metadata. `MIXED_SCRIPT_TOKEN_WRAP`
is now `M7_DEFERRED_VISUAL_DEBT`. M5 must not retain `w:wordWrap=0`, approve
C/D, or create another token-wrap probe. `CJK_KINSOKU` remains the only
visually validated narrow layout policy.

## M5.3a2 Story-Spanning Chapter Scope Closure

The approved Chapter 2, 11, and 18 body markers remain the boundaries for this
known source, but the 616-unit body interval is now explicitly a
`BODY_SCOPE_BASELINE`, not complete chapter scope. This conversion-derived
package can carry chapter prose, footnotes, code fragments, and lexical
continuations in footer parts. A physical story name is not semantic ownership.
The narrow ledger uses only `INCLUDE_CHAPTER_CONTENT`,
`EXCLUDE_RUNNING_DECORATION`, and `REVIEW_REQUIRED`; ordinary adjacent blocks
never become a group merely by proximity. A revised run can resume only when
every non-body candidate is accounted for or explicitly unresolved. Existing
RAW remains immutable: a changed contract receives new RAW and the old one is
retained as `SCOPE_SUPERSEDED_RAW`.

The final closure verified two additional Chapter 11 footer/body continuations:
`...system-scale tests,` → `asked themselves, ...` and `control flow state-` →
`ments like conditionals ...`.  Exact DOCX text plus locators are the mutation
authority; the PDF was used only as read-only corroboration of reading order.
With no remaining `REVIEW_REQUIRED` candidates, the M5.3 scope gate passes.

## M5.3b Composite Contract Parity

An approved composite combines semantic source only after it also aggregates
the members' existing deterministic validation envelope: numeric,
footnote-reference, protected literal, URL/email/DOI/ISBN, anchor and island
evidence. Flat text is a representation shortcut, never a QA shortcut: it uses
the ordinary deterministic primitive. A composite with movable anchor semantics
must use the existing structured target-segment contract. RAW created under an
incomplete envelope remains immutable `CONTRACT_SUPERSEDED_RAW`, excluded from
current-contract quality and final-approval counts.

## Original-source authority split

The registered English original PDF is read-only semantic, reading-order and
visual authority. The OOXML DOCX and IR remain mutation, locator and package
authority. PDF context may resolve a real conversion ambiguity but never changes
the response contract or becomes an OOXML write target. Material disagreement is
an explicit `PDF_DOCX_SOURCE_DIVERGENCE`; formatting-only conversion differences
are documented evidence, not a second source model.
