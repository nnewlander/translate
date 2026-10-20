"""Start the final 37-work-order Chapter 11 first-pass batch."""
from __future__ import annotations

import run_m53_ch11_batch005_checkpoint01 as runner


runner.OUT = runner.RUN / "chapter_11/batch_006/checkpoint_01"
runner.ORDER = ["body.p.03842", "body.p.03843", "body.p.03866", "body.p.03867", "body.p.03868", "body.p.03869", "body.p.03870", "body.p.03871", "body.p.03872", "body.p.03873"]
runner.TARGETS = {
    "body.p.03842": ["Noogler 在入职培训后加入团队时，便开始编写测试，并质问团队中不这样做的人。仅仅一两年内，接受过测试教育的工程师人数就超过了测试文化出现之前的工程师。因此，许多新项目从一开始就走上了正确道路。"],
    "body.p.03843": ["如今，测试在行业中已得到更广泛的实践，因此大多数新员工带着对自动化测试的坚定预期入职。尽管如此，入职培训课程仍会设定有关测试的期望，并将 Noogler"],
    "body.p.03866": ["2006 年 4 月，一篇介绍如何改进 Python 测试的短文出现在 Google 各处的卫生间隔间中。第一期由一小群志愿者张贴。说反响两极分化都算轻描淡写；有些人认为这是侵犯个人空间，并强烈反对。邮件列表充满抱怨，但 TotT 的创作者很满意：抱怨的人仍在谈论测试。"],
    "body.p.03867": ["最终，骚动平息，TotT 很快成为 Google 文化中的固定组成部分。迄今为止，来自全公司的工程师已制作数百期内容，涵盖几乎所有能想到的测试方面（以及各种其他技术主题）。新一期内容令人期待，有些工程师甚至自愿在自己所在的楼宇张贴。我们有意将每期限制为恰好一页，促使作者聚焦最重要、最可操作的建议。一期好的内容应包含工程师能立即带回工位尝试的东西。"],
    "body.p.03868": ["讽刺的是，TotT 虽然出现在较为私密的地点之一，却产生了超乎寻常的公共影响。大多数外部访客都会在访问期间看到某一期内容，这类邂逅常常引发有趣的谈话：Google 员工似乎总在思考代码。此外，TotT 内容非常适合作为博客文章，最初的 TotT 作者很早便意识到这一点。他们开始公开发布", "经过轻微编辑的版本", "，帮助与整个行业分享我们的经验。"],
    "body.p.03869": ["尽管起初只是个玩笑，TotT 却在 Testing Grouplet 发起的所有测试举措中持续最久、影响最为深远。"],
    "body.p.03870": ["当今的测试文化"],
    "body.p.03871": ["如今 Google 的测试文化与 2005 年相比已大为进步。Noogler 仍会参加测试入职培训，TotT 也几乎每周持续发放。不过，对测试的期望已更深地嵌入日常开发者工作流。"],
    "body.p.03872": ["Google 的每一项代码变更都必须经过代码评审。每项变更都应同时包含功能代码和测试。评审者应审查两者的质量和正确性。事实上，如果变更缺少测试，完全可以阻止其提交。"],
    "body.p.03873": ["作为 Test Certified 的替代方案，我们的一个工程生产力团队最近推出了一款名为 Project Health（pH）的工具。pH 工具持续收集数十项有关项目健康状况的指标，包括测试覆盖率和测试延迟，"],
}
runner.COMPOSITES = {}


if __name__ == "__main__":
    manifest = runner.OUT.parent / "batch_manifest.json"
    if not manifest.exists():
        runner.update_manifest(manifest, {"chapter": "chapter_11", "batch": "batch_006", "target_count": 37, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 37, "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": []})
    runner.main()
