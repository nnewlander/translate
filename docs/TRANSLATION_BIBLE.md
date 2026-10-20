# Minimal Translation Bible

## Scope

This is a small M5.1d calibration aid, not a glossary service or a book-wide style engine. Terms appear here only because the reviewed calibration evidence required a decision.

## Translation policy summary

- Preserve source meaning, logical relations, negation, conditions, numbers, protected literals and semantic anchors; natural Chinese word order may differ.
- For normal prose, use available previous/current/next context to understand CURRENT, but return and mutate CURRENT only.
- Code, URLs and other protected literals remain exact. A normal literal-preservation decision does not require `translator_notes`.
- Do not invent missing Index hierarchy. Repair an Index separator only when the visible local structure proves it.

## Canonical terminology

| Source term | Chinese | Level | Evidence / note |
| --- | --- | --- | --- |
| Readability | 可读性 | STRICT | Google’s standardized mentorship process; where needed clarify as “标准化导师指导”. |
| owner | 所有者 | PREFERRED | Code/library approval role; retain role sense rather than translating as generic proprietor. |
| fidelity | 保真度 | STRICT | Confirmed in Figure 14-1 and CI/testing discussion. |
| Fake | Fake（测试替身的一种） | PREFERRED | Keep `Fake` in the section title to avoid conflating it with all test doubles. |
| flakiness | 测试不稳定性 | STRICT | Not generic “脆弱性”. |
| lock-in | 供应商锁定 | PREFERRED | Use where the cloud-provider context is explicit. |
| Live at Head | Live at Head | STRICT | Named dependency-management model; retain English. |
| culprit finding | 定位问题根源 | PREFERRED | Technical section title for locating the change/cause behind a failure. |
| small test | 小型测试 | PREFERRED | Preserve distinction from unit test. |
| unit test | 单元测试 | STRICT | Standard technical term. |
| Hyrum’s Law | 海勒姆定律 | STRICT | Repeated in Chapter 1 explanatory prose and summary. |
| Beyoncé Rule | 碧昂丝规则 | PREFERRED | Keep the song-reference explanation at first use. |
| fork (verb/noun) | 派生（fork）/fork | FLEXIBLE | Retain recognizability; choose grammar-appropriate form. |

## Decision notes

- Index-like text can be semantically valid while its hierarchy is unavailable to the current BlockIR. Treat unrecoverable hierarchy as a review exception, not as prose.
- Current calibration evidence supports lightweight neighbor context, not automatic TranslationUnit grouping.
- This file does not claim validation beyond the reviewed calibration sample.
- Each translation run starts by copying this canonical Bible to an immutable run snapshot. Translator and Reviewer use that same snapshot; only post-run proposals may be merged into this canonical file in a later approved step.
