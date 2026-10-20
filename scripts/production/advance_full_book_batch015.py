"""Close reconciled Batch 015 and initialize immutable Batch 016."""
from __future__ import annotations
from pathlib import Path
import sys
SCRIPTS=Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))
import advance_full_book_batch013 as policy

RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
class _ReconciledBatch:
    def __init__(self,path: Path): self.path=path
    def __truediv__(self,name: str) -> Path:
        return self.path / ("batch_manifest_v2.json" if name=="batch_manifest.json" else name)

policy.PRIOR=RUN/"full_book_progress_v16.json"
policy.BATCH=_ReconciledBatch(RUN/"full_book_batch_015")
policy.SELECTION=RUN/"full_book_batch_015_selection.json"
policy.NEXT_BATCH=RUN/"full_book_batch_016"
policy.NEXT_SELECTION=RUN/"full_book_batch_016_selection.json"
policy.COMPLETION=RUN/"full_book_batch_015_completion_report.json"
policy.PROGRESS=RUN/"full_book_progress_v17.json"
policy.BATCH_LABEL="FULL_BOOK_BATCH_015"
policy.NEXT_BATCH_LABEL="FULL_BOOK_BATCH_016"
_write_once=policy.write_once
def write_once(path: Path,value: object) -> None:
    if not isinstance(value,dict): raise RuntimeError("ADVANCEMENT_PAYLOAD_INVALID")
    if path==policy.COMPLETION and value.get("batch")!=policy.BATCH_LABEL: raise RuntimeError("ADVANCEMENT_COMPLETION_TEMPLATE_DRIFT")
    if path==policy.PROGRESS and value.get("latest_completed_batch")!=policy.BATCH_LABEL: raise RuntimeError("ADVANCEMENT_PROGRESS_TEMPLATE_DRIFT")
    if path==policy.NEXT_SELECTION and value.get("batch")!=policy.NEXT_BATCH_LABEL: raise RuntimeError("ADVANCEMENT_SELECTION_TEMPLATE_DRIFT")
    _write_once(path,value)
policy.write_once=write_once
if __name__=="__main__": policy.main()
