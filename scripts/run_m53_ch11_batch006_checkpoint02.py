"""Seal Chapter 11 batch_006 checkpoint_02."""
from __future__ import annotations

import run_m53_ch11_batch005_checkpoint01 as runner


runner.OUT = runner.RUN / "chapter_11/batch_006/checkpoint_02"
runner.ORDER = ["textbox.094.p.0001", "body.p.03874", "textbox.095.p.0001", "textbox.096.p.0001", "textbox.097.p.0001", "textbox.098.p.0001", "body.p.03875", "body.p.03877", "body.p.03881", "body.p.03882"]
runner.TARGETS = {
    "textbox.094.p.0001": ["（最差）"],
    "body.p.03874": ["。pH 在一到"],
    "textbox.095.p.0001": ["按"],
    "textbox.096.p.0001": ["的量表"],
    "textbox.097.p.0001": ["五"],
    "textbox.098.p.0001": ["测量，并在内部提供"],
    "body.p.03875": ["（最佳）。pH-1 项目被视为需要团队解决的问题。几乎每个运行持续构建的团队都会自动获得 pH 分数。"],
    "body.p.03877": ["228 | 第 11 章：测试概述"],
    "body.p.03881": ["随着时间推移，测试已成为 Google 工程文化不可或缺的一部分。我们有无数种方式向全公司的工程师强化其价值。通过培训、温和提醒、指导，以及一点友好的竞争，我们已形成清晰预期：测试是每个人的工作。"],
    "body.p.03882": ["为什么我们没有一开始就强制要求编写测试？"],
}
runner.COMPOSITES = {}


if __name__ == "__main__": runner.main()
