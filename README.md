# booktrans

`booktrans` 是高保真英文 DOCX → 中文 DOCX 书籍翻译系统的地基。当前完成 M0–M5.0：只读 OOXML 检查、源文件完整性保护、最小 Document IR、Coverage Ledger、identity round-trip、synthetic 验证、tagged text-island projection，以及真实翻译 calibration 文件合同。

本阶段**不翻译、不调用模型、不修改 source DOCX**。M3 只在 `runs/` 下生成 candidate，并把相同源文字经过真实 OOXML 写入路径写回，用于验证 locator、repackage 与 preservation Harness。

## 安装与使用

需要 Python 3.11+：

```powershell
python -m pip install -e ".[dev]"
python -m booktrans --help
python -m booktrans inspect --input "D:\path\to\book.docx"
python -m booktrans extract --input "D:\path\to\book.docx"
python -m booktrans identity --input "D:\path\to\book.docx"
python -m booktrans synthetic --input "D:\path\to\book.docx"
python -m booktrans tagged-synthetic --input "D:\path\to\book.docx"
python -m booktrans structural-tagged-synthetic --input "D:\path\to\book.docx"
python -m booktrans calibration-export --input "D:\path\to\book.docx"
python -m booktrans calibration-import --input "D:\path\to\book.docx" --request "D:\path\to\calibration_request.jsonl" --response "D:\path\to\calibration_response.jsonl"
python -m booktrans format-audit --input "D:\path\to\book.docx"
python -m booktrans format-fragment-audit --input "D:\path\to\book.docx"
```

`inspect` 从 ZIP/OOXML/XML 层读取文档。`extract` 在同样的只读 source guard 下写出 `document.json`、`blocks.jsonl`、`coverage.json` 和 `extract_report.json`。运行产物位于 `runs/<run_id>/`，最小审计数据库位于 `data/translation.db`。输入文件在操作前后会做 SHA-256 对比。

`coverage_complete=true` 不是“提取了大量文本”的同义词。它只在没有 unclassified visible text、没有 unknown potential visible content 且 source integrity 通过时成立；incomplete coverage 仍会生成报告并以 exit code 4 返回。

`identity` 写出 `candidate/identity.docx`、`patch_report.json`、`roundtrip_report.json` 与两侧 coverage。它只计划 `TRANSLATABLE` text-node locator，跳过 protected/excluded 内容与 field instruction，并验证未触碰 ZIP payload、relationships 和 media 不变；candidate 会重新 extract 后按 locator/text/coverage accounting 对比。M3 不提供任何 Translator、Reviewer、Repair 或真实翻译写回。

`synthetic` 不是翻译：它先输出 non-identity capability report，只对 `SAFE_SIMPLE` block 生成含 Unicode 的可重复测试 target。target 在 patch 前经 deterministic QA，complex block 不写入且会保留 reason code。候选包在重新 extract 后按“已批准 target 或必须保持的 source text”验收；不同 target 长度导致的字符数变化是合法的。

`tagged-synthetic` 是 M4.7 的 synthetic proof，不是翻译。它建立不含 OOXML tree 的 `TranslationView`：只暴露 bold、italic、underline、visible color/highlight、font family/size、character style、hyperlink 与 protected content anchor。`w:spacing` 和 `w:w` 仍由 patcher 模板保留，不会暴露为未来模型 tag。只有 `TAGGED_SUPPORTED` paragraph 会写入 `runs/` 下的 synthetic candidate；anchor 必须闭合引用且受 protected/hyperlink/rPr-template QA、re-extraction、coverage 与 package-preservation Harness 共同验证。

`structural-tagged-synthetic` 是 M4.8 的 synthetic proof。它只增加可验证的 `EditableTextIsland` 与 immutable `StructuralAnchor`：安全的 text + bookmark/tab/drawing/VML + text paragraph 只替换 text island，结构节点原位保留；mixed run、field composite、AlternateContent 等边界不明确构造继续拒绝。新生成 CJK target 不复制 English `w:spacing`/`w:w`，unchanged/protected source text 则保留它们。命令会写出 `structural_capability_report.json`，并继续通过 source integrity、anchor/order、re-extraction、coverage、media/relationship 与 untouched payload Harness；它不是 Translator 或 visual release gate。

`calibration-export` 是 M5.0 的只读 translator contract：它 deterministic 地从 `TAGGED_SUPPORTED` block 选择 30–50 个真实样本，写出 sanitized request JSONL、翻译 policy 和 visual smoke manifest，不调用模型。外部 translator 可生成 response JSONL；`calibration-import` 严格验证 unit、anchor、protected text、数字和稳定 island 后，仅 patch 通过者。它不是整书翻译、Reviewer 或 Repair pipeline；剩余 32 个 unsupported block 继续作为明确 release debt。

`format-audit` 是 M4.5 的只读 evidence 工具。它不会创建 candidate 或修改 Patcher，而是拆分 `MULTI_FORMAT_CONTEXT` 的真实 format/container 差异、估算可折叠噪声，并输出 BlockIR 文本粒度与 suspicious fragment samples。

`format-fragment-audit` 是 M4.6 的最终只读决策审计：它把 `OTHER_DIRECT_FORMAT_DIFFERENCE` 拆回实际 `w:rPr` 属性，统计 logical format regions，并按容器与相邻文本证据复核 fragment 根因；不会自动 grouping 或写入 DOCX。

可检测但语义统计仍可能不完整的构造见 [架构说明](docs/ARCHITECTURE.md) 与 [Harness 说明](docs/HARNESS.md)。
