# Architecture Explorer T2 candidate scanner

T2 scans one closed, explicit source set for a proposed Supervisor → Python
Worker relationship. It does not traverse the repository, execute a production
entrypoint, read production data, or approve an architecture relationship.

The scanner emits two views of the same automatic facts:

- `candidate.json`: a machine-checkable candidate record;
- `candidate-review.md`: a human-readable review report with the exact same
  evidence IDs and a structured GitHub review template.

Evidence IDs are derived from the repository-relative path, evidence kind,
symbol or structural anchor, repeatable syntax position, and normalized snippet
hash. Current line numbers are recorded only for human navigation and do not
participate in the ID.

Run with the project interpreter from a clean, fixed worktree:

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = 'D:\translate-worktrees\architecture-explorer-t2\src'
Set-Location 'D:\translate-worktrees\architecture-explorer-t2'
& $python scripts\architecture_explorer\generate_supervisor_worker_candidate.py `
  --repository . `
  --output-directory output\architecture-explorer\t2-supervisor-worker-candidate
```

The relation remains `candidate` with review status
`PENDING_HUMAN_REVIEW`. Runtime state is always `NOT_COLLECTED`; this scanner is
not a production monitor. Missing or multiply matching required evidence makes
the scan `incomplete` or `ambiguous` and forces static reachability to
`UNKNOWN`.
