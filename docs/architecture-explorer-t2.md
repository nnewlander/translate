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

Before reading any source bytes, the scanner validates every allowlisted path
against the resolved repository and rejects symlinks, junctions, and reparse
points. Each file is read once; that same byte snapshot supplies parsing, file
hashes, sizes, and the source-snapshot record. A metadata change during the
read fails the scan instead of mixing two source states.

The supported Python control-flow shape is intentionally narrow. The wrapper
must import the exact Worker `main` and call that imported name under an exact
`__name__ == "__main__"` guard. Worker `main` must parse `args` from
`build_parser()`, exit its exact canary branch, then apply the worker-id and
batch/checkpoint guards in order before its top-level production return. Other
shapes remain incomplete/unknown rather than being guessed.

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

PowerShell test evidence is auxiliary only. T2 recognizes the concrete
assertion that a child process exited successfully and launch diagnostics mark
it as created; a success-marker string alone is not behavior evidence.
