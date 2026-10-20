# Decision Log

## D001 — 不重新生成整个 DOCX

**决定：** 将来采用在原 OOXML package 上最小变更的 patch 策略，而不是高层库重新生成文档。

**原因：** 书籍 DOCX 可能包含域、关系、VML、绘图、批注、样式、兼容性设置及第三方扩展。重建会造成不可审计的结构损失。

## D002 — 核心检查不只使用 python-docx

**决定：** 核心 Inspector 使用 `zipfile + lxml` 直接检查 parts、relationships 与 XML；`python-docx` 仅生成测试 fixture。

**原因：** `paragraph.text` 无法揭示 stories、field codes、revisions、content controls、VML、关系和许多 package-level constructs。

## D003 — M1 不接入翻译模型

**决定：** 本阶段没有任何 Translator、Reviewer、Repair loop 或模型 API。

**原因：** 在 source guard、结构发现、覆盖率定义和可审计 Harness 建立之前，翻译输出不可信，也无法安全写回文档。

## D004 — 未知可见内容不得静默忽略

**决定：** 对检测到的风险结构、可选 XML 解析失败、未分类 `word/` part 输出 diagnostic 和 confidence；不把检测有限误称为完整支持。

**原因：** 高保真目标要求先暴露风险，后续再建立明确处理策略。

## D005 — SQLite 从最小 schema 起步

**决定：** 仅建立 `schema_version`、`documents`、`inspection_runs`、`parts`。

**原因：** M1 只需 source identity 去重和检查审计历史。提前设计翻译、术语、任务等大量表会把未验证假设固化进工程。

## D006 — 已批准 milestone 编号取代旧的简写编号

**决定：** 旧 milestone numbering 被 superseded。统一施工顺序为 M0 Engineering foundation、M1 Read-only Inspect + Source Integrity、M2 Minimal Document IR + Coverage Ledger、M3 Identity Round-trip Patcher、M4 Fake Translator + Deterministic QA、M5 Real Translator + One Semantic Reviewer + Targeted Repair、M6 Evidence-driven consistency/context enhancements、M7 Microsoft Word render + PDF + Release Gate。

**原因：** 旧 ARCHITECTURE 中的简写未来编号把 patcher、translation 和 QA 的顺序混在一起；该编号已被 superseded，但不改变核心架构原则。

## D007 — Stable unit identity

**决定：** `unit_uid = SHA256(source document SHA256 + part name + stable XML locator + source text hash)`；`display_id` 是独立的人类日志标识。

**原因：** immutable source 的重复 extraction 必须得到相同程序身份，同时日志仍应有 `body.p.00001`、`table.001.r.001.c.001.p.001` 等可读位置。运行 ID 不参与 unit identity。

## D008 — Coverage complete 是严格证明，不是提取量指标

**决定：** 只有 source integrity PASS、unclassified visible text 为零、unknown potential visible content 为零时，才输出 `coverage_complete=true`。

**原因：** “没有未分类 span”无法证明未知 OOXML shape、字段或 AlternateContent 分支已经覆盖。incomplete extraction 写审计产物并以 CLI exit code 4 暴露，而不是降低标准。

## D009 — AlternateContent baseline policy

**决定：** M2 只把 `mc:Fallback` 提取为 baseline，不双计 Choice；同时记录 Choice 分支 uncertainty。

**原因：** Choice 取决于宿主支持的 namespace/兼容能力，M2 不是 Word rendering engine，无法证明哪个分支实际可见。没有 Fallback 时同样保留 uncertainty，而非随意选择。

## D010 — Field、textbox、content-control 与 comment policy

**决定：** field instruction 不进入普通文本；已知 pagination/reference field result 为 Protected，未知 field result 是 coverage uncertainty。`w:sdt` 下钻提取并保留边界；`w:txbxContent` 与 VML textbox 中 Word paragraphs 提取；无法证明的 shape 记 unknown。comments 和 deleted revisions 作为 `EXCLUDED_BY_POLICY` 入账。

**原因：** 这些规则保留可见内容、结构边界和不确定性之间的差别，不把复杂 OOXML 伪装成已完全支持。

## D011 — M2 SQLite 只持久化审计实体

**决定：** M2 持久化 extraction runs 与 stable units；coverage 细节保留为 JSON artifact。不建立 Translator、QA、术语、任务、atoms、run-to-unit links 或 coverage-entry 明细表。

**原因：** source identity、run history 与稳定 block identity 已足以解决 M2 当前审计问题。旧版本已写入的额外表保持不删除以保护既有数据，但新 extraction 不再写入它们。

## D012 — M2 使用 JSON manifest + JSONL blocks

**决定：** M2 写出轻量 `document.json` manifest 和逐 block 的 `blocks.jsonl`，而不是一个巨大的嵌套 `document_ir.json`。

**原因：** 真实书籍已经证明单一嵌套 JSON 难以查看且浪费内存/磁盘。JSONL 是直接由 observed failure 驱动的简单 diff/debug 格式；Pydantic IR 仍由测试验证 round-trip serializability。

为避免在大型书籍中重复相同 locator，span 的主 `locator` 始终代表第一个 source text node；`source_run_locators` 只保存发生合并时的额外 node locator。这不损失 M2/M3 的重新定位信息。

## D013 — High assurance, low ceremony

**决定：** M2 采用 High assurance, low ceremony：Every Harness must pay rent；复杂度只从 observed failures 增长；避免 speculative abstractions。

**原因：** M2 的唯一问题是“translation-scope 可见文字是否已被稳定发现和定位”。source integrity、locator resolution 与 coverage ledger 直接回答该问题；完整 Atom taxonomy、Patcher/Translator/Reviewer 框架、dependency graph、context escalation 与样式系统不回答，因而不在本阶段实现。

## D014 — AlternateContent physical mirror（supersedes/refines D009）

**决定：** D009 保留为历史 M2 baseline policy，但其简写说明被本决定 supersede/refine。Choice/Fallback 可见文本等价时，logical coverage 只计一次且不产生 uncertainty；分支冲突时仍按当前明确的 Fallback baseline 提取并产生 coverage uncertainty。logical dedup 不代表 physical package 只有一份文字，M3 patcher 必须同步等价 AlternateContent 的物理 Choice/Fallback target。

**原因：** 实际样本与 M2 tests 已证明等价 AlternateContent 可安全逻辑去重；M3 又证明 patch 时不能遗漏另一个 physical representation。M3 不实现完整 Office compatibility engine：多 text-node branch 的非 identity target distribution 会明确拒绝，等待有真实写回需求时再扩展。

## D015 — SpanIR 是 source mapping，不是 translation granularity

**决定：** SpanIR 仅映射原始可见 text node/field boundary 及必要容器信息。未来 Translation Unit 仍以 paragraph-like BlockIR 为候选；不为每个 span 创建翻译任务、prompt 或完整 semantic segmentation framework。

**原因：** 真实书籍已有数十万 span。M3 需要它们来精确定位 OOXML text node，但不需要把它们升级为翻译层实体；这样直接解决 locator/write 证明，避免 speculative complexity。

## D016 — M3 identity write and preservation proof

**决定：** M3 从 source ZIP 复制所有 entries，只重序列化包含 planned locator 的 XML parts，并对 candidate 执行真实 text-node assignment（identity value 也不短路）。以 package payload、relationship/media、touched XML C14N 和 M2 re-extraction 共同证明 round-trip，而不比较整个 DOCX binary SHA 或跨 package `unit_uid`。

**原因：** ZIP metadata/XML serialization 可以合法变化，candidate SHA 无须与 source 一致；但未触碰 payload、关系、媒体、逻辑文本和 locator accounting 必须能被逐项审计。该组合是当前真实 M3 风险的最小充分 Harness，不引入 general Patcher 或 translation reflow。

## D017 — M4 以 synthetic target 发现 non-identity 写入约束

**决定：** M4 使用 deterministic、可重复、含 Unicode 且与 source 不同的 synthetic target，而不是接入任何翻译模型。每个 translatable BlockIR 先经保守 capability audit；只有 `SAFE_SIMPLE` block 可进入 write 前 deterministic QA 和 non-identity projection，其他 block 标为 `COMPLEX_UNSUPPORTED` 并保持 source text。

**原因：** 当前问题是确定 IR/Patcher 是否能安全承受长度、Unicode 与标点变化，而不是评价翻译质量。真实 capability distribution 才能证明是否值得在后续引入 format/container mapping；在此之前预建 semantic atom、rewrite island 或 Translator framework 都不会解决 M4 的验证问题。

## D018 — M4 用 expected block text 替代 identity character equality

**决定：** M4 保留 source integrity、package preservation 和 coverage complete，但不要求 source/candidate visible 或 translatable character totals 相同。重提取验收按 stable story/part/locator/block key：approved synthetic block 必须等于 target，未 patch block 必须等于 source。

**原因：** non-identity target 合法改变字符长度；继续使用 M3 的字符相等条件会把正确的写入误报为失败。按 explicit expected text 验证同时能发现 target 未写入、写入到错误 block、以及 complex/protected text 被误改。

## D019 — M4.5 先审计 format complexity 和 BlockIR 粒度，再批准新抽象

**决定：** M4.5 使用只读 `translation_relevant_format_signature` audit 区分可见 direct-format/container 差异与 language/metadata-only 噪声，并以稳定 locator 的 BlockIR 邻接关系报告疑似 prose fragments。该 signature 仅服务 audit，不改变 patcher；BlockIR 保持 paragraph-like candidate，不自动成为最终 Translation Unit。

**原因：** M4 的 11.33% SAFE_SIMPLE 可能来自真实的 bold/italic/font/container 语义，也可能主要是 Word run fragmentation。只有真实 distribution 能决定下一个最小投资是 format normalization、tagged formatting mapping，还是薄的 TranslationUnit grouping。完整 style inheritance、Atom taxonomy 或 Rewrite Island 在此之前仍不 pay rent。

## D020 — M4.6 回溯具体 rPr 属性与 fragment 根因，但不升级为写入模型

**决定：** M4.6 对 M4.5 的 primary `OTHER_DIRECT_FORMAT_DIFFERENCE` 按实际 `w:rPr` QName、共现差异和 evidence category 统计；logical regions 与 fragment buckets 都是只读报告字段。fragment 仅在邻接断句/大小写和结构信号同时支持时才标 likely true，其他保持 standalone 或 uncertain。

**原因：** `OTHER_DIRECT_FORMAT_DIFFERENCE` 过粗，无法判断下一步是否需要保留少数 direct properties。短 block 也不能自动等同于 prose fragment。该审计为下一步的最小 writeback 设计提供事实边界，但尚未证明需要完整格式系统或全局 grouping 层。

## D021 — M4.7 translation-visible anchors 与 patcher-owned layout 分离

**决定：** 在不改变 M2 `BlockIR`/`SpanIR` source-mapping 责任的前提下，增加薄的 `TranslationView`、ordered segment 与 deterministic `FormatAnchor`。只有已观察且可能需随目标语义移动的 bold、italic、underline、visible color/highlight、font family/size、character style、hyperlink 与 protected reference 进入 model-visible anchor。`w:spacing` 与 `w:w` 默认不成为 target tag；它们仍由 patcher 从 source run template 复制。fragment 不做 grouping，只在有 M4.6 continuation evidence 时携带 previous/next text 与 hint。

**原因：** M4.6 证明 `w:spacing`/`w:w` 是真实、且需保留的 physical metadata，但未证明未来模型需要知道这些 English micro-layout boundary。相反，bold/italic/hyperlink 等直接影响语义可见性或 container ownership，必须能随 structured target 移动。该边界落实“Preservation-critical does not automatically mean model-visible”，避免把完整 `w:rPr` 或 style inheritance engine 泄漏给未来 Translator。

## D022 — M4.7 用 closed-world tagged projection 证明可移动 anchor

**决定：** `tagged-synthetic` 只接受引用既有 anchor id 的 deterministic structured target；每个 source anchor 必须恰好出现一次，protected anchor 必须原样保留。已批准 paragraph 仅重建其 text run/hyperlink container，保留 paragraph properties、relationships、media 和未触碰 payload；target run count 不要求等于 source。字段、AlternateContent、未知 semantic container、非 text run structure 和其它无法安全投影的 block 记为 `TAGGED_UNSUPPORTED`，不做猜测性 fallback。

**原因：** M4.7 的真实问题是验证 format anchor 能否脱离原英文字符位置安全写回，而不是预建通用 reflow system。closed-world anchor QA、rPr-template/hyperlink relation verification、re-extraction expected-text 比较及既有 preservation Harness 已足以回答该问题。该 synthetic proof 不承诺中文的最终微观排版，视觉 render/layout QA 仍留给 M7。

## D023 — M4.8 以局部 Text Island 保护 paragraph structural anchor

**决定：** 连续且可证明仅含 text run/hyperlink 的 direct paragraph children 是可投影 `EditableTextIsland`；bookmark、tab、break、drawing、VML pict 等结构节点默认是 immutable `StructuralAnchor`，按 type、fingerprint 与相对顺序验证。复杂 paragraph 只替换 island，不整体删除 run 再重建。mixed text/structural run、field composite、AlternateContent 和未知 child 若边界不能证明则继续拒绝。

**原因：** M4.7 的 generic blocker 是真实 writeback 障碍，但不需要 general Rewrite Island framework。M2 coalesced SpanIR 的 `source_run_locators` 能恢复 physical text-node 边界，足以支持 text + bookmark/tab/drawing + text。drawing/VML fingerprint 不含内部 `w:txbxContent` 的可变文本 payload，因为 textbox 是独立 BlockIR；外围 drawing/VML 结构仍由 fingerprint、relationship/media preservation 保护。

## D024 — 新生成 CJK text 不盲目继承 English micro-layout

**决定：** unchanged/protected source text 保留原 `w:spacing` 与 `w:w`。新生成、含 CJK 的 target run 不复制这两个属性；仍复制 font mapping metadata、字号、bold、italic、underline、visible color/highlight、character style 和 hyperlink/container template。mixed Latin/CJK target 采用同一最小规则：output run 含 CJK 即去除这两个 English micro-layout 属性。

**原因：** M4.7 synthetic output 暴露 English spacing/character scale 在 `【测试】` 上可能形成不自然字间距。它们是 preservation-critical source metadata，却未被证明适合新中文字符。该决定只修复已观察的机械复制风险，不声称已完成字体 fallback、字距或最终视觉排版；render/layout QA 留在 M7。

## D025 — M5.0 先验证文件化真实翻译合同，再接 provider

**决定：** M5.0 采用 deterministic calibration export + 外部 response import，而非直接接 LLM/provider。response 复用 M4.7 `StructuredTargetSegment`，通过现有 closed-world format-anchor/island QA 后才允许 projection。校准规模限制为 30–50 条 representative `TAGGED_SUPPORTED` block。

**原因：** 当前风险是翻译数据合同是否能表达自然中文语序、anchor 移动与 protected content，而不是 provider routing、重试、并发或整书吞吐。文件合同让真实 translator output 先受 schema 与 Harness 约束，也使失败可审计、可复现。

校准 strata 以 IR 已可靠识别的 role 为准。若某本 source 没有 Heading-style IR，导出可选一个明确标记为 `title_like_heuristic` 的短文本样本供校准，但不会将其写成语义 heading。

## D026 — M4 synthetic stress 已完成；视觉 smoke 与结构证明分离

**决定：** 后续 layout 决策主要由真实中文 calibration candidate 的视觉 smoke evidence 驱动。M5.0 只输出 visual review manifest；完整 Word/PDF render/release gate 仍为 M7。M4.8 的 32 个 unsupported block 作为明确 release debt 保持拒绝，不为校准扩大 patcher。

**原因：** synthetic prefix 已能发现 micro-layout 的机械复制问题，但不能代表自然中文翻译的长度、断句与布局。结构 Harness PASS 也不等于视觉 PASS；将两类证明分开可避免用更多 synthetic 极端样本推动 speculative patcher 复杂度。

## D027 — M5.1b 译者备注触发保守 Review Gate；邻文是 request context 而非 mutation scope

**决定：** calibration response 的 `translator_notes != null` 一律为 `REVIEW_REQUIRED`，默认不 patch；它不是 deterministic QA FAIL，也不分析 note 内容以自动决定严重度。只有无 note 且通过既有 closed-world deterministic QA 的 response 才可投影。candidate 仍验证未 patch unit 保持 source text。真实 response 中 `5e7396afad7306ae46a9965a42d4078a2e48bc51a800a46575c933772e26aae5` 以 “For cases in” 截断但 `continuation_hint=false`，因此未来 prose request 应通常提供 lightweight previous/current/next context；translator 只返回 CURRENT，patch 仍严格 Block-local。

**原因：** 译者主动报告不确定性是当前真实 calibration evidence，静默 patch 会掩盖语义风险；把它当 QA FAIL 又会混淆结构合同失败与需要人工判断的语义/上下文信号。已有 M4.6 continuation evidence 不是完整 fragment detector，但单一新例也不足以证明需要 global TranslationUnit grouping。轻量邻文扩展直接解决已观察的 request-information 缺口，而不改变稳定 locator、block identity 或 OOXML mutation 边界。孤立 index-like block 同样可能语境不足，当前仅记录未来可考虑 role hint，不实现 Index engine 或 global classifier。

## D028 — M5.1c protected URL 优先、句末标点归普通文本；默认导出 prose 邻文

**决定：** 若 protected anchor 已识别 URL，`protected_text` 是唯一 authoritative token；generic URL extraction 不再对该 source substring建立竞争性要求。对于未 protected 的 URL，只去除末尾明显的 sentence punctuation，保留 hostname、path、query 和 percent encoding 中合法字符。译者 notes 仅用于需要人工判断的真实歧义或上下文不足。future normal body prose request 在安全可得时提供 previous/current/next，而 response 只允许 CURRENT。index-like calibration samples 记录 finding `INDEX_LIKE_TRANSLATION_POLICY_NEEDED`，暂不实现任何 Index engine。

**原因：** 真实 `body.p.02296` 的 protected URL 正确保留，但 source 句末 ASCII `.` 被 generic regex 错当为 URL 一部分，造成唯一 deterministic false positive。该局部修正消除冲突而不降低真正 URL/protected mutation 检查。`body.p.07097` 的 “For cases in” 同时证明 M4.6 hint 不足以决定 context availability；复用已有 neighbor 字段能补足 translator 信息，而不引入 global grouping。多条孤立 index-like REVIEW_REQUIRED note 说明其未来需要专门 policy，但当前 evidence 不足以证明 parser、role classifier 或自动策略的最小形式。

## D029 — M5.1d 单一 AI semantic calibration review，人工仅处理不可恢复例外

**决定：** 对固定的 40 条 calibration response 由当前 AI Reviewer 逐条复核 source、target、anchor、role、note 与可安全取得的局部邻文，输出 `ACCEPT`、`REPAIR_REQUIRED` 或 `HUMAN_REQUIRED`。可明确修复的译文仅以同一 M5.0 schema 写入 repaired response，并仍必须通过既有所有 Harness；不建设多 reviewer、投票、分数或 provider 框架。

**原因：** M5.1b/c 的 deterministic PASS 证明写入合同与 package preservation，却不能证明术语、完整性和自然中文。真实样本已显示 `flakiness`、`lock-in`、Readability mentorship、Fake 术语、技术标题及 Index 条目边界需要语言判断；这些是当前单次 calibration 可审计解决的问题。只有 `examples of, 417` 这类无法由 extracted 邻项恢复上级 Index 层级的情况才升级人工，不把普通判断外包给用户。

## D030 — 轻量邻文可解决 fragment 解释，不扩大 mutation scope

**决定：** `body.p.07097` 的后续可见正文证实完整从句为 “For cases in which such a change must happen”。Reviewer 可据此把 CURRENT 修为“在必须作出此类变更的情况下”，但不合并 Block、不写入邻居。未来 prose request 默认携带安全可得的 previous/current/next；`continuation_hint` 只保留为额外 evidence。

**原因：** 该实证说明 M4.6 fragment heuristic 不是 context availability 的充分条件，却仍不足以证明需要 global grouping。上下文和 mutation scope 分离是当前最小、可验证的修复。

## D031 — Index-like policy 仍开放；只修复可证明的条目边界

**决定：** 当一个 block 内由页码与相邻条目结构可可靠证明两个 index entry 粘连时，review 可插入最小的中文条目分隔；若无法恢复上级 term/缩进层级，保留 `HUMAN_REQUIRED`。不实现 Index parser、role classifier 或自动 grouping。

**原因：** `public versus private compute services, 543 public APIs, 237` 与 `use with canonical documentation, 188, 201 goals` 的边界可从相邻 page-reference pattern 与后续 subentry 证明；`examples of, 417` 则不能。用同一保守证据标准可修复已知问题而不假装 Index 结构已经支持。

## D032 — M5.2 用显式书籍 marker 进行一次性 Chapter 1 pilot

**决定：** 对这一本已知书，Chapter 1 的 pilot 从 exact body marker `What Is Software Engineering?` 开始，在 `PART II` 前结束，并额外验证 `How to Work Well on Teams` 随后出现。此为一次性、可审计边界合同，不是通用 Chapter parser。翻译仍以 Block 为 mutation 单位；request 默认提供可安全取得的 previous/current/next context。

**原因：** 当前 IR 没有可靠 Heading-style chapter recognition。已验证的 marker 及 ordinal 顺序足以避免把 ToC、Part II/Chapter 2 或 Index 混入本次 pilot，而不引入 speculative chapter/grouping architecture。真实 pilot 进一步表明 `body.p.01102` 在 source 中以 `inevi-` 截断；这保留为 `HUMAN_REQUIRED`，不能用猜测性跨块补全掩盖。

## D033 — 脚注上标只能作为未来 response 的窄 contract normalization

**决定：** future response 可在 raw response 持久化后、deterministic preflight 前执行 `FOOTNOTE_SUPERSCRIPT_TO_ASCII`：仅当 source 中有同一 ASCII 数值且处在 leading 或 inline footnote-like 位置、target 有对应 Unicode superscript sequence、且 segment 非 protected 时，才把该 sequence 转为 ASCII。raw 与 normalized JSONL 必须并存。M5.2 的历史 raw response 未保存，因此 23 条历史 NUMBER failure 不能回溯宣称全部安全；其中 `body.p.00839`、`body.p.00911`、`body.p.01052` 明确不是 representation-only。

**原因：** 这避免把数字 QA 变成通用“自动修翻译”层。规则不猜数、不补数、不处理普通 prose 或 protected literal；原始数据不足时宁可保留审计不完整性，也不制造事后证据。

## D034 — semantic repair 必须有 material structured-target change

**决定：** `REPAIR_REQUIRED` 必须比较 target segment text、kind/order、anchor ref、island ref 与 protected reference；完全相同则更正为 `ACCEPT`，或在 reviewer 声称必须修改却未给出可接受 target 时报告 `REVIEWER_CONTRACT_ERROR`。M5.2 的 `body.p.00812` 是唯一 no-op repair，修正后统计为 ACCEPT。

**原因：** joined text 相同不足以证明 structured projection 未变；反之没有 material change 的 repair 会虚高 repair rate，也会削弱 pilot 的真实度量。

## D035 — M5.2 历史 numeric 结论被 immutable artifact evidence 取代

**决定：** M5.2 的“197 raw-pass”与“23 个 numeric case 都是安全 normalization”旧表述被 superseded。历史 raw response 在原地改写前没有单独保存：20 个 case 无法恢复 raw evidence，`body.p.00839`、`body.p.00911`、`body.p.01052` 则分别是 decade semantic paraphrase、footnote omission 与 era approximation，不能归为 representation-only。未来每次 run 必须保留 RAW、NORMALIZED、deterministic QA、semantic review 和 repaired artifacts；raw 以 exclusive creation 持久化并在 normalization 前后复验 SHA-256。

**原因：** 不能用已经覆盖的 response 反推模型原始输出，更不能把 semantic rewrite 包装为 QA normalization。一个不可覆盖的 JSONL 与 hash proof 直接解决该证据链问题，无须 provenance database。

## D036 — `FOOTNOTE_SUPERSCRIPT_TO_ASCII` 是唯一 closed-world normalizer（supersedes/refines D033）

**决定：** 唯一批准的 normalization 是把 target 中的 Unicode superscript footnote reference 转为同值 ASCII；必须同时有 exact source number、exporter 提供的 footnote-reference evidence、matching target superscript 和 non-protected segment。normalizer 保留全部 segment text 之外的 contract 字段（kind/order/anchor/island），写入独立 normalized JSONL，并在 report 中记录 exact character span 与 invariant proof。缺号、额外号、`1990s` 语义改写、`mid-2000s` 近似表达、普通数字和 protected literal 都不改，继续交给 deterministic QA/review。

**原因：** 脚注排版的单一实证问题值得一个窄、可测试的表示转换；从字符串“看起来像脚注”推断却会误伤编号列表或正文数字。要求 exporter 的显式 evidence 保持 closed-world、可审计且不过度自动化。

## D037 — 视觉证据可证明窄的跨 Block prose composite，但不授权全局 grouping

**决定：** M5.2c 仅在 source OOXML container/locator/section relationship 与实际 PDF reading order 共同证明时建立 `CompositeFragmentGroup`。成员、物理顺序、semantic reading order、primary/secondary 角色和 target 都是 run artifact 的 closed world。primary 使用原 flow paragraph 的可编辑 island；secondary 仅清空经批准的 text payload，保留 textbox/drawing/footer 结构。`body.p.01102` 的 `inevi-` 与 section-bound `footer39.xml` 中的 `table.` 被证明构成 `inevitable.`，因此重分类为 `STRUCTURAL_CONTINUATION_RECOVERABLE`，不再是 HUMAN_REQUIRED。

**原因：** Chapter 1 PDF 显示 body/textbox/body prose 在视觉上断裂；OOXML 审计确认了五个封闭实例。普通相邻 Block 仍不能自动合并，未知或 multi-island 边界继续拒绝。`LATIN_MIDWORD_WRAP` 与英文 running header 仅记录为后续 policy/open gap，不在本次引入 typography 或 header translation 系统。

## D038 — CJK line break 使用段落级 OOXML policy，不改变译文或全局排版

**决定：** 第二轮 Word→PDF 已验证五个 `CompositeFragmentGroup` 的 visual repair；不再扩大 composite architecture。对于另一个重复 visual finding，M5.2d 只在 closed-world、已翻译的 normal-flow CJK body prose 上显式写入 `w:wordWrap="0"` 与 `w:kinsoku="1"`。`w:wordWrap="0"` 禁止 Word 的 Latin word-middle wrapping；`w:kinsoku="1"` 使该段按 Simplified-Chinese first/last-character line-breaking rules 处理，包括 `。` 不能作为行首。`w:characterSpacingControl=doNotCompress` 不承担此责任，且保持不变。文档设置不修改。

**原因：** 实际 candidate 的相关段落没有这两个 paragraph property；真实 PDF 复现 `Compilin`/`g`、`for`/`k` 以及行首孤立 `。`。Word 的 `ParagraphFormat.WordWrap` 文档明确说明 True 会允许 Latin text 在词中间换行；ISO/OOXML 对 `w:kinsoku` 的说明明确其为段落级 East Asian line-break policy，并将 U+3002 列入 Simplified Chinese 的禁行首字符。该窄策略直接处理已观察行为，却不触碰字体、字号、spacing、geometry、页边距或 page breaks，也不会处理 code、URL/protected literal、table/header/footer/textbox 或原始英文。其结果仍必须证明 extracted text 完全不变；视觉效果由下一轮 Word/PDF smoke review 验证，而非由 OOXML Harness 宣称通过。

## D039 — M5.2e 以真实 PDF 修正 D038 的 Latin guard 结论

**决定：** D038 保留其历史工程实验与 `w:kinsoku="1"` 的正确依据，但对 `w:wordWrap="0"` 能防止 Latin token 内断行的结论被 M5.2e superseded/refined。真实第二轮 Word/PDF 中 `Compiling` 仍拆为 `Compilin` / `g`，`fork` 仍拆为 `for` / `k`，并新增 `2016`、`15` 与 Chapter `24` 的 mixed-script token split。因此 production 的窄布局策略仅保留 `CJK_KINSOKU`；`WORDWRAP_LATIN_TOKEN_GUARD` 保持 OPEN、不得宣传为视觉通过。

M5.2e 使用一个独立的、从 Chapter 1 candidate 只读复制页面/正文条件的 `linebreak_probe.docx` 比较 A（kinsoku）、B（历史 wordWrap）和 C（explicit `w:lang` metadata）三种变体。三者的 visible payload 必须完全相同；B/C 都是 `EXPERIMENT_ONLY`，只有新的真实 Word→PDF evidence 才能批准策略。C 的 `w:lang` 依据是 OOXML 的 Latin/East Asian language metadata 语义，可能影响 Word 的 mixed-script 判定，但其 line-break 作用尚未得到证明。

**原因：** OOXML/property 写入成功和 structural preservation PASS 不能替代视觉证据。把 `CJK_KINSOKU` 与 `MIXED_SCRIPT_TOKEN_WRAP` 分离，既保留已经有效的窄修复，也避免一个未经验证的属性继续占据 production policy。当前 evidence 只支持小型对照实验，不支持 full typography engine、Unicode no-break text hack、字体/字号/geometry 修改或新的 Chapter 1 candidate。

## D040 — generic `w:lang` probe 无判别力；以真实段落作最终 positive control

**决定：** M5.2e 的 A/B/C generic probe 在真实 Word/PDF 中未复现任何已知 token-internal split，结论为 `INCONCLUSIVE`。C 的某些整体 reflow 差异只使 explicit script-language metadata 成为 `PROMISING_HYPOTHESIS`，不构成 production approval，也不构成否定证据。M5.2f 仅复制五个已审计的完整 Chapter 1 offending target paragraph，以 A/B/C/D 和 W0/W1/W2 的 closed-world 对照取得最后一次 M5 evidence。

**判别边界：** 只有 A 或 B 复现真实 `TOKEN_INTERNAL_SPLIT`，且 C 或 D 在同一 case/width 条件下消除 split、没有 overflow/clipping，才可将该 strategy 报为 `CANDIDATE_POLICY_VISUALLY_TESTABLE`，仍等待外部确认。若 A/B 仍不复现，或所有变体均复现，则 `MIXED_SCRIPT_TOKEN_WRAP_DEFER_TO_M7`；不再创建更多 M5 probe。

**原因：** generic sentence 即便共享 page setup，也没有复现实际的段落长度、justification、indents、run/anchor colors 与文本边界。复制现实事故现场并用最小 right-indent sweep 制造 token 边界压力，是最后一个足够窄且可解释的实验；它不改变 production candidate、target 字符、字体、字号、spacing、geometry 或段落结构，也不证明需要 typography engine。

## D041 — M5.2f 的外部正控结论收口；mixed-script wrap 延后至 M7

**决定：** 外部 Word→PDF 对 5 个真实段落 × 3 个宽度 × 4 个变体的 60 个 fixture 完成审核后，A/B control 未复现任何 `LATIN_TOKEN_INTERNAL_SPLIT` 或 `NUMERIC_TOKEN_INTERNAL_SPLIT`。M5.2f 正式为 `PROBE_NOT_DIAGNOSTIC`，`MIXED_SCRIPT_TOKEN_WRAP` 记为 `M7_DEFERRED_VISUAL_DEBT`。不得批准 C/D 的 explicit `w:lang`，不得保留 `w:wordWrap="0"` 为 production guard，也不得在 M5 新建 token-wrap probe。`CJK_KINSOKU` 仍是唯一因真实视觉证据而保留的窄策略。

**原因：** C/D 造成的某些段落重排不是 token-internal split 预防的对照证据；没有 baseline failure，就无法判断其是否解决真实问题。继续堆叠 probe 或属性会违反 high assurance, low ceremony。该问题应由 M7 的正式渲染/发布验证上下文重新评估，而不是在 M5 推测性地引入 typography engine。

## D042 — 章节边界可由 body marker 定义，但翻译 scope 必须跨 story 审计

**决定：** M5.3 的原 manifest 保留为 `BODY_SCOPE_BASELINE`。Chapter 2、11、18 仍使用已批准的 body title marker 作为范围边界；但 footer/header/textbox 的物理 part 名不再被当作自动排除依据。每个非-body visible candidate 必须以最小 ledger 分类为 `INCLUDE_CHAPTER_CONTENT`、`EXCLUDE_RUNNING_DECORATION` 或 `REVIEW_REQUIRED`。只有 section/page/reading-order/lexical continuity 等证据证明 ownership 时才纳入；连续语义由已批准的 closed-world `CompositeFragmentGroup` 表达，普通相邻段落不自动合并。

**原因：** Chapter 2 的 Michael Jordan 正文完整存于 `footer.41.xml`；Chapter 11 的 `...as the` / `system grows` 与 Chapter 18 的 `Proper` / `organization` 是跨 story lexical continuation。body-only 616 会漏译真实正文。反过来，页码和重复 running title 不能被误译。三分类 ledger 是解决当前 scope 漏洞的最小方法，不需要 generic page model、header engine 或 global grouping。

## D043 — scope 变化不会篡改初稿证据

**决定：** Chapter 2 batch_001 的 25 条 RAW 维持 immutable。`body.p.01128` 与 `body.p.01132` 被证明共同构成 `Project Hosting` composite 后，其旧 RAW 标记为 `SCOPE_SUPERSEDED_RAW`，而不是重写或计入 revised-scope RAW 指标；其余 contract 不变成员可标记 `RAW_REUSABLE`。新 composite contract 必须产生独立、不可覆盖的 RAW。

**原因：** 旧 RAW 的 `now deprecated` 已跨 CURRENT block 移动，证明它不能代表新的 composite contract；删除或覆盖则会再次破坏 M5.2b 的第一稿测量链。保留原始文件、记录 SHA 和可审计状态足以解决问题，无须 provenance database。

## D044 — M5.3a2 final closure uses PDF only to corroborate exact DOCX continuation

**决定：** `footer.168.p.0001` + `body.p.03715` 与 `footer.172.p.0001` + `body.p.03752` 均升级为 `PROVEN_COMPOSITE_PROSE`。前者由 `...system-scale tests,` / `asked themselves, ...`，后者由 `control flow state-` / `ments like ...` 的 exact DOCX lexical join、独立 locator 以及 PDF reading-order corroboration 共同证明。PDF 不提供任何新 target 文本，source DOCX 仍是唯一 mutation source。M5.3 final scope 因此为 635 个 physical members、627 个 logical translation work orders、8 个 composite，scope closure PASS。

**原因：** 这两条均不只是语义上“看起来合理”的相邻段落：词法续接、chapter-local page ownership 和物理 story locator 可单独审计。以 PDF 作为只读 corroboration 消除 conversion reading-order 的疑问，同时不让 PDF 取代 OOXML source。该结论不扩展为 generic page model 或 automatic grouping。

## D045 — Composite logical work order 必须满足普通 deterministic contract parity

**决定：** flat `target_text` composite 仅可用于单一 editable island、没有 movable semantic anchor、没有 protected literal 的经证实 simple group；其余 composite 不得降级为 plain-text contract。`m5_3_ch2_project_hosting` 的初始 RAW 中 `Ben¹` 不具 exporter footnote-reference evidence，故不得由 `FOOTNOTE_SUPERSCRIPT_TO_ASCII` 推断或改写 RAW，并以 `UNEVIDENCED_FOOTNOTE_SUPERSCRIPT` 阻断 parity。此前仅检查 group id/非空 target 的轻量 QA 不足以标记 FINAL_APPROVED，已由 parity report supersede。

**原因：** composite 是 logical work order，不是绕过 anchor/protected/island/numeric safety 的例外。保守 fail 比凭排版猜测 footnote evidence 更符合 closed-world normalizer；RAW 保持不可变，后续只能以明确的 source/exporter evidence 或有效的 downstream repair contract 处理。

## D046 — Composite evidence envelope 与 ordinary validator 共用

**决定：** composite request 必须聚合成员已有的 numeric、footnote-reference、protected literal、URL/email/DOI/ISBN、semantic anchor 与 island evidence。flat target 仍进入 ordinary deterministic primitive；含 italic/color 等 movable anchor 的 group 必须使用现有 structured target segment contract，不能降级 plain text。`body.p.01128` 的 ordinary exporter evidence 是 `footnote_reference_numbers=[]`，因此 `Ben1` 为 ordinary attached numeric token，`Ben¹` 是 `UNEVIDENCED_FOOTNOTE_SUPERSCRIPT`，不得被 normalizer 修复。初版 Project Hosting RAW（SHA `aa5dd31174f259c3c086b6ebd7d553c002d605af5ea8dfc38e99da0bc4014be4`）以及任何发现 envelope 不完整后产生的 RAW，都保留为 `CONTRACT_SUPERSEDED_RAW`，不计入现行 first-pass 或 FINAL_APPROVED 指标。

**原因：** semantic source 可以组合，safety evidence 也必须组合；否则 composite 会成为弱于普通 TranslationResult 的逃逸路径。共享同一 deterministic primitive 避免两套 validator 漂移，并能让转换型 OOXML 的 `Ben1` 一类 attached numeric 接受与普通数字相同的 preservation 约束。

## D047 — Original PDF 与 OOXML DOCX 的 authority split

**决定：** 已确认的英文原版 PDF 是只读 semantic、reading-order 与 visual authority，可用于 page association、footnote placement、conversion fragment reconstruction、reviewer context 和最终中文 PDF 的视觉比较。DOCX/IR 保持 mutation、unit identity、OOXML locator、anchor/island、patch/writeback 与 package preservation authority。PDF 永不成为 mutation target，也不能绕过 structured response 与 Harness。实质文字不一致必须记录 `PDF_DOCX_SOURCE_DIVERGENCE`；仅 line break、hyphenation、container split 或 spacing 的等义差异是 conversion evidence。

**原因：** 当前 DOCX 来自转换型 OOXML，已证实可把连续正文放入 footer/textbox；原版 PDF 能以最小、只读方式补足人类阅读顺序和页面语义，而不是重建 PDF parser/alignment architecture。最终交付可以是 DOCX 和/或 PDF，优先级是语义完整、自然中文、图表/脚注完整和可靠的 PDF 阅读体验，非关键 Word 编辑微排版不应无限阻塞 release。

## D048 — Immutable evidence 与 mutable orchestration state 分离

**决定：** request、RAW、normalized response、QA、semantic review 与 checkpoint evidence 均继续 exclusive-create 并保持不可变；macro `batch_manifest.json` 是 derived resumable orchestration index，使用验证单调进度与既有 checkpoint metadata 后的临时文件加 `os.replace` 原子更新。

**原因：** batch_004 checkpoint_02 证明进度索引必须从 10→20→… 更新，而把它当作 RAW 类证据 exclusive-create 会产生无关的 `FileExistsError`。该区分不引入数据库或工作流框架；immutable checkpoint artifacts 仍是审计事实来源。

## D049 — Resume selection uses logical work orders and completed composite coverage

**决定：** resume selection 从 immutable ordinary 与 composite checkpoint evidence 聚合 completed logical ids；已完成 composite 的全部 physical members 标记为 covered，不得再次作为 ordinary candidate。macro manifest 继续只是 derived index。

**原因：** `m5_3_ch11_system_scale_tests` 已完成但其 `body.p.03715` member 不存在于 ordinary request JSONL，证明仅扫描 ordinary artifacts 会重复选择一个 logical work order。

## D050 — Verified footnote evidence participates in numeric tokenization boundary before generic extraction

**决定：** 只有 exact closed-world evidence 同时匹配 `unit_uid`、`exact_source_marker_token`、`footnote_number` 与已导出的 source-segment binding 时，numeric QA 才能在 generic numeric extraction 前处理该 marker。处理方式是移除完整、精确匹配的 flattened marker，并注入 evidence 中已明确列出的 marker 前 ordinary numeric tokens 和独立的 verified footnote number。其他 numeric QA 仍按既有规则运行。

**边界：** 不得根据 punctuation、`w:position`、raised text 或 generic pattern 推断；不得建立 generic footnote detector、generic decimal exception 或 number-word parser。未通过 sidecar exact verification 的文本、错误 unit、错误 marker 与真实 decimal 一律继续走 generic extraction。

**原因：** `body.p.03718` 的 source segments 已明确 `Figure 11-2` + `.4`，verified sidecar 又证明 `4` 是 footnote。flattened `11-2.4` 被当作 decimal `2.4` 是一个已观察、可由精确 evidence 修复的 contract collision。把 source-segment binding 一并带入 request 使边界可重验，而非从标点反推。

## D051 — Explicit reviewed numeric representation equivalence for exact closed-world recovery cases

**决定：** 只有 explicit evidence 同时精确绑定 `unit_uid`、唯一 `exact_source_span`、其完整 `source_numeric_tokens`、唯一 `approved_target_representation`、相等的 reviewed source/target semantic numeric value，及可选 semantic unit 时，numeric QA 才可免除该 source span 内的 literal numeric token 要求。target 仍必须包含 approved representation；span 外所有 source/target numeric token 继续走原有 strict QA。若同值 token 还出现在 span 外，保守拒绝该 equivalence，因为现有 primitive 不假装拥有 positional numeric parser。

**边界：** 该决定不计算 million/billion，不解析中文数字，不猜 target value，不做 approximate/fuzzy matching，也不自动为其它 unit 或其它 wording 建立 equivalence。没有逐字段 exact evidence 时，原 strict literal contract 保持不变。

**原因：** `body.p.03804` 的 `25 million lines` 与“**两千五百万行**”已由审计明确同为 25,000,000 lines；其自然中文 release wording 不应被 `25` 的 literal-only rule 阻断。专项 probe 证明可只移除这个唯一 span 的单一 token，同时保留 `Chapter 22` 等同 unit 其它数字的普通 QA，因此无需 generic numeric conversion system。
