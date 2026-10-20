"""Author the staged, closed-world target map for Batch 015 checkpoint 08."""
from __future__ import annotations
import json
from pathlib import Path
import sys
SCRIPTS=Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))
import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.translation_view import TranslationView
RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09"); SELECTION=RUN/"full_book_batch_015_selection.json"; TARGET_MAP=Path(r"D:\translate\data\full-book-20260916T000200Z-decb09\full_book_batch_015_checkpoint_08_authored_targets.json")
T={"body.p.04763":"为什么伪实现很重要？","body.p.04764":"伪实现是强大的测试工具：它们执行快速，能让你有效测试代码，同时避免使用真实实现的缺点。","body.p.04765":"一个伪实现就能从根本上改善某个 API 的测试体验。若把它扩展为适用于各种 API 的大量伪实现，就能极大提升整个软件组织的工程效率。","body.p.04766":"在另一端，若软件组织中伪实现很少，效率会更低，因为工程师可能难以使用会导致测试缓慢且不稳定的真实实现。工程师也可能转而采用桩实现或交互测试等其他测试替身技术；正如本章后文将讨论的，这可能使测试不清晰、脆弱且效果较差。","body.p.04767":"何时应编写伪实现？"}
def main() -> None:
 if TARGET_MAP.exists(): raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
 s=json.loads(SELECTION.read_text(encoding="utf-8")); order=s["selected_logical_work_order_ids"][175:200]
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if x)}
 if len(order)!=25 or any(i not in views for i in order): raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
 targets={i:([T[i]]+["" for _ in views[i].segments[1:]] if i in T else [x.text for x in views[i].segments]) for i in order}
 if any(len(targets[i])!=len(views[i].segments) for i in order): raise RuntimeError("TARGET_SEGMENT_COUNT_MISMATCH")
 TARGET_MAP.parent.mkdir(parents=True,exist_ok=True)
 with TARGET_MAP.open("x",encoding="utf-8",newline="\n") as f: json.dump({"artifact_type":"FULL_BOOK_BATCH_AUTHORED_TARGET_MAP","batch":"FULL_BOOK_BATCH_015","checkpoint":"checkpoint_08","logical_work_orders":order,"targets":targets},f,ensure_ascii=False,indent=2); f.write("\n")
 print(f"PASS: authored target map created at {TARGET_MAP}")
if __name__=="__main__": main()
