# Qwen deterministic production worker — Phase 3

This phase adds the production hot path that no longer calls `codex exec`.
It does **not** relax the Translation Harness, mutate sealed artifacts, or
advance `FULL_BOOK_BATCH_022/checkpoint_03` by itself.

## Architecture

```
Supervisor
  → deterministic Python worker
  → Provider Router
  → Qwen-MT Plus or Qwen3.8-Max
  → existing Translation Harness
  → non-authoritative QwenMaxReviewerProvider
  → narrow Repair gate / Registry-approved QwenMaxRepairProvider
  → Final QA
  → seal/handoff
```

The worker does not explore the repository, change frozen selection, invent
recovery policy, overwrite RAW/QA, or modify a sealed checkpoint.

## Isolated canary

Read-only source: current `FULL_BOOK_BATCH_022` frozen selection slice for
`checkpoint_03` (first ID `body.p.06939`).  Output must be a new isolated run
directory.  The command must not create
`runs/full-book-20260916T000200Z-decb09/full_book_batch_022/checkpoint_03`.

```powershell
D:\translate\.venv\Scripts\python.exe D:\translate\scripts\production\run_qwen_production_canary.py --root D:\translate --run-path D:\translate\runs\full-book-20260916T000200Z-decb09 --isolated-output D:\translate\runs\qwen-production-canary-YYYYMMDDTHHMMSSZ
```

Do not start continuous production until the canary gate passes.
