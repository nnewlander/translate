# Architecture Explorer T2 delivery record

This record describes the local candidate implementation for GitHub Issue #3.
It is evidence for independent code review and Owner architecture review. It is
not an architecture approval, Push, Merge, Issue closure, or T3 authorization.

## Revisions and status

- Authorized T1 integration baseline:
  `21e922f06da3aa692514413bcd0911f259d11881`.
- T2 branch: `codex/architecture-explorer-t2`.
- Candidate source snapshot and scanner revision:
  `d41b2058003b033a3d066fdc4716e2799f41b2e8`.
- Candidate evidence package and final tested code revision:
  `68dd8758cdbc51370e5b12ce476a26729ef0cc23`.
- The candidate source worktree was clean when scanned.
- The branch is local only and has not been pushed or merged.
- No authorized T3 integration baseline exists. T3 must not infer one from this
  branch, these artifacts, or this document.

The commit containing this delivery record is documentation-only. The code,
tests, machine candidate, and Markdown report actually exercised by final
validation are fixed at `68dd8758cdbc51370e5b12ce476a26729ef0cc23`.

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
entrypoints.

## Candidate artifacts

- Machine record:
  `output/architecture-explorer/t2-candidate-d41b205/candidate.json`
- Human review report:
  `output/architecture-explorer/t2-candidate-d41b205/candidate-review.md`
- Scanner version: `architecture-explorer-t2/1.0`
- Candidate report summary SHA-256:
  `663711c3d2ee2587501bbae2c7e781c4904887a3ad03ca3dfb9d619d9b79b572`
- Machine file SHA-256:
  `6529e28a2dc348022d87548d5d61f9a5fd81976d59be1e43d383ffc8bcad0a9b`
- Markdown file SHA-256:
  `1b24784e4b0e995f1ba6c3daa8e23366cdbf560d1c0ae44b50aa73fdebdb5399`
- Scan outcome: `complete`.
- Relation status: `candidate` / `PENDING_HUMAN_REVIEW`.
- Runtime state: `NOT_COLLECTED`.
- Evidence: 9 unique required production records and 3 auxiliary-test
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
  --output-directory output\architecture-explorer\t2-candidate-d41b205
```

Final regression command at
`68dd8758cdbc51370e5b12ce476a26729ef0cc23`:

```powershell
$python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t2-final-68dd875 `
  tests\architecture_explorer\test_top_level_explorer.py `
  tests\architecture_explorer\test_supervisor_worker_candidate.py
```

Result: `48 passed, 0 failed, 0 skipped in 13.02s`.

Compilation by the project interpreter passed for all 10 Python files in the
Architecture Explorer source, script, and test directories. `git diff --check`
passed and the tested worktree was clean.

Repository-wide pytest was not run. Its previously documented collection is
blocked by the missing production fixture
`runs/full-book-20260916T000200Z-decb09/full_book_batch_013_selection.json`;
T2 did not create, copy, or modify production data to bypass that condition.

## Confirmed automatic facts and retained unknowns

The candidate records unique source evidence for the Supervisor script binding,
production argument vector, process boundary definition and invocation, Python
wrapper import and main guard, Worker mode and authorization contracts, and the
Worker production call. These are automatic source facts, not reviewed
architecture facts.

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
branch, line-move-stable evidence IDs, auxiliary evidence separation, and
machine/Markdown parity.
