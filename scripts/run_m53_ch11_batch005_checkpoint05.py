"""Seal the final micro checkpoint of Chapter 11 batch_005."""
from __future__ import annotations

import run_m53_ch11_batch005_checkpoint01 as runner


runner.OUT = runner.RUN / "chapter_11/batch_005/checkpoint_05"
runner.ORDER = ["body.p.03858", "textbox.088.p.0001", "textbox.089.p.0001", "textbox.090.p.0001", "body.p.03859", "textbox.091.p.0001", "textbox.092.p.0001", "textbox.093.p.0001", "body.p.03860", "body.p.03862"]
runner.TARGETS = {
    "body.p.03858": ["Testing Grouplet 曾考虑定期发送电子邮件通讯，但考虑到 Google 每个人每天处理的海量电子邮件，它很可能会淹没在噪声中。稍作头脑风暴后，有人提出了张贴"],
    "textbox.088.p.0001": ["笑话"],
    "textbox.089.p.0001": ["很快"],
    "textbox.090.p.0001": ["认识到"],
    "body.p.03859": ["其中的妙处：洗手"],
    "textbox.091.p.0001": ["传单"],
    "textbox.092.p.0001": ["在卫生间隔间内，作为一个"],
    "textbox.093.p.0001": ["。我们"],
    "body.p.03860": ["间是每个人每天至少都得去一次的地方，不管怎样。无论这是不是玩笑，这个想法的实施成本很低，值得一试。"],
    "body.p.03862": ["Google 的测试历史 | 227"],
}
runner.COMPOSITES = {}


if __name__ == "__main__":
    runner.main()
