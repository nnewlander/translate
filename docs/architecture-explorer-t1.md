# Architecture Explorer T1

T1 keeps two authorities separate:

- `snapshot.json` is generated read-only from one Git worktree plus an explicit list of small source inputs. It records revision, complete porcelain worktree state, scanner version, scan time, and SHA-256 hashes.
- `architecture-explorer/modules.json` is the human-maintained, versioned map of the seven approved logical modules. Logical approval and source-coverage review are separate fields.

The build command validates both inputs, writes `explorer-data.json`, and packages the same data as `explorer-data.js` so `index.html` can be opened directly with `file://`. The generic frontend renders only `window.ARCHITECTURE_EXPLORER_DATA`; it contains no second module list and performs no source scanning or architecture inference.

The project-approved interpreter is shared by the primary checkout and its Git
worktrees; a worktree does not require a second environment. Resolve and verify
that interpreter before running T1, and keep the worktree script path explicit
so imports come from the worktree under test:

```powershell
$projectRoot = 'D:\translate'
$worktree = 'D:\translate-worktrees\architecture-explorer-t1'
$python = (Resolve-Path "$projectRoot\.venv\Scripts\python.exe").Path
& $python -c "import pathlib, sys; expected = pathlib.Path(r'D:\translate\.venv').resolve(); actual = pathlib.Path(sys.prefix).resolve(); assert actual == expected, (actual, expected); print(sys.executable); print(sys.prefix)"
Set-Location $worktree
& $python scripts\architecture_explorer\generate_top_level.py scan `
  --repository . `
  --source-file pyproject.toml `
  --output output\architecture-explorer\snapshot.json
& $python scripts\architecture_explorer\generate_top_level.py build `
  --repository . `
  --snapshot output\architecture-explorer\snapshot.json `
  --mapping architecture-explorer\modules.json `
  --web-assets architecture-explorer\web `
  --output-directory output\architecture-explorer\site
```

The CLI accepts only repository-relative outputs whose resolved targets remain
inside that repository's `output/` directory. Direct generator functions remain
available to tests that use pytest-controlled temporary directories.

Open `output\architecture-explorer\site\index.html` directly in a browser. No server, network connection, database, or AI service is required.

Coverage values are closed: `unverified`, `partial`, or `verified`. An unverified module cannot claim evidence; partial or verified coverage requires reviewed evidence. T1 deliberately records every top-level module as unverified because this ticket approves the logical map but does not perform source-coverage review.
