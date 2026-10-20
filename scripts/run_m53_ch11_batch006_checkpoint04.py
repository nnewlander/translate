"""Seal the legal seven-work-order final checkpoint for Chapter 11 first pass."""
from __future__ import annotations

import run_m53_ch11_batch005_checkpoint01 as runner


runner.OUT = runner.RUN / "chapter_11/batch_006/checkpoint_04"
runner.ORDER = ["body.p.03896", "body.p.03897", "body.p.03898", "body.p.03899", "body.p.03900", "body.p.03901"]
runner.TARGETS = {
    "body.p.03896": ["简而言之"],
    "body.p.03897": ["• 自动化测试是使软件能够变更的基础。"],
    "body.p.03898": ["• 要让测试具备可扩展性，必须自动化。"],
    "body.p.03899": ["• 维持健康测试覆盖率需要均衡的测试套件。"],
    "body.p.03900": ["• “如果你喜欢它，就该为它写个测试。”"],
    "body.p.03901": ["• 改变组织中的测试文化需要时间。"],
}
runner.COMPOSITES = {
    "m5_3_ch11_content_bullet": "• 它们可作为文档和示例，向工程师展示如何使用被测系统的部分，以及该系统预期应如何工作。",
}


if __name__ == "__main__": runner.main()
