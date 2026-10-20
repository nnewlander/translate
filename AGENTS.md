# booktrans production rules

## Durable invariants

- Treat every user-provided DOCX, especially `Software Engineering at Google.docx`, as immutable source. Never overwrite it or write under `source/`; write production artifacts only under `runs/`, `data/`, or `output/`.
- DOCX/IR is the only mutation and locator authority. `Software Engineering at Google.pdf` is read-only semantic, reading-order, page-association, footnote-placement, and visual evidence; record any material PDF/DOCX divergence. Do not mutate a PDF.
- A translator response is untrusted. RAW responses are immutable, never overwritten, and must pass the existing schema, normalization, deterministic QA, semantic-review, and package Harness chain. Do not relax a failed contract: create a material, targeted repair or report the failure.
- Full-book production is fixed at 200 logical work orders per batch and 25 per checkpoint. A sealed checkpoint is a savepoint, not a stop point; batch closure is also not a stop point.
- Immutable artifacts remain the truth. Authoritative release membership is exactly scope `ALREADY_APPROVED` plus immutable passing final-QA records. A reconciled frozen selector is mandatory whenever its reconciliation evidence supersedes an earlier selector; never reuse an invalidated selection.
- D050 footnote routing is exact and closed-world: route evidence only by the exact `logical_work_order_id` and that order's `unit_uid`. Preserve and use the active scope's verified D050 registries.
- The production campaign state is a derived, atomically-written resumable index, not an evidence source. Validate it against immutable artifacts before work and refresh it before a worker exits.
- Never create duplicate checkpoints, rerun a sealed checkpoint, overwrite immutable artifacts, or run two writers for one active batch. Hold the campaign worker lock for the full worker lifetime.
- Visual QA and any full-book candidate/package delivery are deferred unless separately authorized. Do not claim visual PASS without reliable renderer evidence.

## Stop conditions

Continue through checkpoints and batches unless an immutable-artifact validation failure, unresolved deterministic/systemic failure, `HUMAN_REQUIRED`, artifact corruption, source mutation, or unrecoverable runtime failure occurs. Report such a condition explicitly; do not silently retry or bypass it.

## Python environment contract

- All booktrans Python development, debugging, testing, dependency management,
  and project-script execution must use the project virtual environment. From
  the project root, resolve its Windows interpreter as
  `.venv\Scripts\python.exe` and invoke that resolved path explicitly.
- Do not rely on terminal activation state, a bare `python`, a bare `pip`,
  PATH resolution, Conda `base`, a global Python installation, or another
  project's environment for any booktrans task.
- Before dependency changes, tests, or production-affecting operations, verify
  the resolved interpreter and virtual-environment prefix from the project
  root. First initialize `$python` with
  `$python = (Resolve-Path '.venv\Scripts\python.exe').Path`, then verify it,
  for example:
  `& $python -c "import pathlib, sys; expected = pathlib.Path('.venv').resolve(); actual = pathlib.Path(sys.prefix).resolve(); assert actual == expected, (actual, expected); print(sys.executable); print(sys.prefix)"`.
- Use the same explicit interpreter for package operations, for example
  `& $python -m pip ...`, and for Python modules, tests, and project scripts.
- Preserve the production Supervisor's existing explicit project-interpreter
  binding; do not replace it with a bare `python` invocation.
- If the project interpreter is missing, cannot start, has a `sys.prefix`
  different from the project `.venv`, fails `python -m pip check`, or cannot
  import `booktrans`, stop and report `PROJECT_VENV_UNAVAILABLE`. Do not
  repair, recreate, activate an alternate environment, or fall back to
  Conda/global Python without explicit owner approval.
