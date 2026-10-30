# Architecture Explorer T2 delivery record

This record describes the local candidate implementation for GitHub Issue #3.
It is evidence for independent code review and Owner architecture review. It is
not an architecture approval, Push, Merge, Issue closure, or T3 authorization.

## Revisions and status

- Authorized T1 integration baseline:
  `21e922f06da3aa692514413bcd0911f259d11881`.
- T2 branch: `codex/architecture-explorer-t2`.
- Candidate source snapshot and scanner revision:
  `e494c1a25347d72ae9c279dc8ddc875ec87af144`.
- Candidate evidence package and final tested code revision:
  `06ca0f5a0c0e2c81912c53d71dd57c96a999a9a5`.
- The candidate source worktree was clean when scanned.
- The branch is local only and has not been pushed or merged.
- No authorized T3 integration baseline exists. T3 must not infer one from this
  branch, these artifacts, or this document.

The commit containing this delivery record is documentation-only. The code,
tests, machine candidate, and Markdown report actually exercised by final
validation are fixed at `06ca0f5a0c0e2c81912c53d71dd57c96a999a9a5`.

The earlier `d41b205...` candidate artifacts are retained as immutable history
but are superseded by the repaired `e494c1a...` candidate and must not be used
for architecture review.

## Closed scan scope

Production source inputs:

- `scripts/production/production_campaign_supervisor.ps1`
- `scripts/production/run_qwen_deterministic_worker.py`
- `src/booktrans/production_worker/worker.py`

Auxiliary tests, always labelled as auxiliary evidence:

- `tests/production/test_supervisor_launch_boundary.ps1`
- `tests/unit/test_production_entrypoint_bootstrap.py`
- `tests/unit/test_qwen_deterministic_worker.py`

The scanner does not traverse the repository, read production data or logs, or
execute Supervisor, Worker, Provider, Reviewer, Repair, or other production
entrypoints. It validates every allowlisted path before reading, rejects links
and reparse points, and uses one stable byte read per file for parsing, hashing,
size, and source-snapshot metadata.

## Candidate artifacts

- Machine record:
  `output/architecture-explorer/t2-candidate-e494c1a/candidate.json`
- Human review report:
  `output/architecture-explorer/t2-candidate-e494c1a/candidate-review.md`
- Scanner version: `architecture-explorer-t2/1.1`
- Candidate report summary SHA-256:
  `0f0f016de85759c20193b05b342da643fc59738ce376de0443ac4aa4f4debf5c`
- Machine file SHA-256:
  `ad881fe85948ddf2904028aad037f65404c787dc8d3928afa8643de6f6ab7492`
- Markdown file SHA-256:
  `87e35ecc1799089a6435a7a6edfae51786869b257433f2cca80ce0fb442550d4`
- Scan outcome: `complete`.
- Relation status: `candidate` / `PENDING_HUMAN_REVIEW`.
- Runtime state: `NOT_COLLECTED`.
- Evidence: 13 unique required production records and 3 auxiliary-test
  records.

The summary hash is computed from the canonical report model used to render the
Markdown report. It is included in both artifacts and in the structured human
review template.

## Commands and final results

The project interpreter was resolved explicitly as
`D:\translate\.venv\Scripts\python.exe`; `sys.prefix` was
`D:\translate\.venv`, `pip check` reported no broken requirements, and
`booktrans` imported from the T2 worktree.

Formal scan command:

```powershell
$python scripts\architecture_explorer\generate_supervisor_worker_candidate.py `
  --repository . `
  --output-directory output\architecture-explorer\t2-candidate-e494c1a
```

Final regression command at
`06ca0f5a0c0e2c81912c53d71dd57c96a999a9a5`:

```powershell
$python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t2-repair-final-06ca0f5 `
  tests\architecture_explorer\test_top_level_explorer.py `
  tests\architecture_explorer\test_supervisor_worker_candidate.py
```

Result: `57 passed, 0 failed, 0 skipped in 17.82s`.

The two P1 counterexample families were also rerun explicitly at the same
commit:

```powershell
$python -m pytest -q -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t2-p1-final-06ca0f5 `
  tests\architecture_explorer\test_supervisor_worker_candidate.py `
  -k 'wrapper_requires_exact_main_guard_and_imported_function_identity or production_call_inside_canary_branch_is_not_non_canary_evidence'
```

Result: `3 passed, 0 failed, 0 skipped in 0.92s`.

Compilation by the project interpreter passed for all 11 Python files in the
Architecture Explorer source, script, and test directories. `git diff --check`
passed and the tested worktree was clean.

Repository-wide pytest was not run. Its previously documented collection is
blocked by the missing production fixture
`runs/full-book-20260916T000200Z-decb09/full_book_batch_013_selection.json`;
T2 did not create, copy, or modify production data to bypass that condition.

## Confirmed automatic facts and retained unknowns

The candidate records unique source evidence for the Supervisor script binding,
production argument vector, process boundary definition and invocation, the
exact Python wrapper import and `__name__ == "__main__"` call, Worker mode and
authorization contracts, `args` parsing, canary exit, worker-id guard,
batch/checkpoint pairing guard, and the ordered top-level Worker production
call. These are automatic source facts, not reviewed architecture facts.

Static scanning does not prove:

- that the Supervisor operational loop reaches this launch on a particular
  runtime execution;
- that runtime preconditions, credentials, or external services are available;
- parameter business meaning beyond observed names, literals, and direct
  bindings;
- that the proposed module direction or `process-launch` relationship type has
  been approved.

## Required human decision

An authorized Owner reviewer must use the template in the Markdown report to
approve or reject the exact evidence IDs, source and target modules, direction,
relationship type, statically approved reachability, and retained unknowns.
There is no GitHub architecture-review comment yet, so the state is explicitly
`PENDING_HUMAN_REVIEW`.

The minimal fixture intended for downstream T5 invalidation and ambiguity work
is `tests/architecture_explorer/test_supervisor_worker_candidate.py`. It covers
the complete chain, missing evidence, duplicate-match ambiguity, unknown static
branch, incorrect `__main__` conditions, unrelated `.main()` calls, changed
wrapper imports, production calls nested in canary, missing/reordered guards,
external links, source changes during read, line-move-stable evidence IDs,
behavioral auxiliary assertions, auxiliary evidence separation, and
machine/Markdown parity.
