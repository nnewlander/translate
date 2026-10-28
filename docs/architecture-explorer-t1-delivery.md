# Architecture Explorer T1 delivery record

This record describes the local T1 repair delivery for GitHub Issue #2. It is
evidence for independent review, not an integration or merge claim.

## Revision and integration status

- Latest application-and-test commit actually exercised: `bc9d405227863abfee573311ddef9419ec80d622`.
- Branch: `codex/architecture-explorer-t1`.
- The commit has **not** been authorized for integration and is not described as merged.
- No authorized T2 integration baseline has been established. T2 must not infer
  a baseline from this branch, this document, or the local generated site.

The delivery-record commit that contains this file and the versioned sample is
documentation/evidence only; the application and test tree is the tested commit
above.

## Project interpreter

The repository-approved interpreter shared by the primary checkout and this Git
worktree was resolved explicitly as:

```text
D:\translate\.venv\Scripts\python.exe
sys.prefix = D:\translate\.venv
booktrans = D:\translate-worktrees\architecture-explorer-t1\src\booktrans\__init__.py
pip check = No broken requirements found.
```

No system Python, Conda environment, or new worktree-specific environment was
used or created.

## Commands and results

T1 behavior suite:

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONDONTWRITEBYTECODE = '1'
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp 'D:\translate\.test-tmp\architecture-explorer-t1-bc9d405' `
  tests\architecture_explorer\test_top_level_explorer.py
```

Result: `30 passed, 1 skipped in 11.20s`. The skipped case attempts a real
directory symlink and then a Windows junction; the managed sandbox denied both
creation mechanisms. Absolute paths, `..`, output/input overlap, protected file
targets, real CLI success, malformed re-signed snapshots, and porcelain status
columns all executed and passed.

Python compilation check: `PYTHON_COMPILE_PASS` for the generator, CLI, and T1
test module. `git diff --check` passed before the tested commit.

Repository-wide collection was checked without running production tests:

```powershell
& $python -m pytest --collect-only -q -p no:cacheprovider `
  --basetemp 'D:\translate\.test-tmp\architecture-explorer-t1-final-collect' tests
```

Result: `741 tests collected, 1 error`. The sole error remains the pre-existing
missing production fixture
`runs/full-book-20260916T000200Z-decb09/full_book_batch_013_selection.json`
imported by `tests/unit/test_validate_authored_target_map.py`. T1 did not modify
that test or manufacture production data.

## Reproducible sample

The sample was generated from a clean `4be1c19d...` worktree with only
`pyproject.toml` as the explicit source input:

- Snapshot: `output/architecture-explorer/t1-delivery-4be1c19/snapshot.json`
- Versioned Explorer data:
  `output/architecture-explorer/t1-delivery-4be1c19/site/explorer-data.json`
- Local offline page:
  `output/architecture-explorer/t1-delivery-4be1c19/site/index.html`
- Snapshot facts SHA-256:
  `03feb49f9a9a8cf8b9b1193e6688a30ef88eee3fced9f78743ca279018db99ee`
- Deterministic content SHA-256:
  `a5c1a6b197f7b24c09f9bee7eff6a3d1962a71611ef5e8aae7e2ea6483d1dcb9`
- `explorer-data.json` file SHA-256:
  `ed92a9bcf8054db6130bbd9cb48fd4a3f2b374271cc7544273654e540510d577`

Building the same snapshot and mapping into a second isolated output directory
produced the same deterministic content SHA-256. The generated site contains
`index.html`, `styles.css`, `app.js`, `explorer-data.js`, and
`explorer-data.json`, and reports seven modules.

### Final high-readability page

The restrained high-contrast visual refinement was generated from a clean
`bc9d4052...` worktree and repeated into a second output directory:

- Local offline page:
  `output/architecture-explorer/t1-final-bc9d405/site/index.html`
- Snapshot facts SHA-256:
  `4805bfb14b2167c92b4af109699515931b6f5a998856eec8e3cc7cac1d978495`
- Deterministic content SHA-256:
  `6daf1e5409faa8a895ebe776e02db5775b462616acf0b7a12caea7285425bd06`
- `explorer-data.json` file SHA-256:
  `8447f394ee6222696c997e0505938a328cd4e11c466c9c66f5dd93a68e8f7873`
- Generated `styles.css` file SHA-256:
  `4c25286e481ff057d6514af8a4d61b273854eeb4d0803ee28101f86fc4dc0170`

Both builds produced the same deterministic content SHA-256 and contained all
five required offline resources. This generated page is an ignored local
artifact, not an authorized integration baseline.

## Known unverified items

- The managed environment could not create a real Windows symlink or junction,
  so that one filesystem-specific rejection case remains skipped here. The path
  validator resolves both the allowed root and final target before containment
  checks; the test will execute where link creation is permitted.
- The final high-contrast spring/watercolor CSS and generated resource set were
  verified statically and through generation tests, but this last visual
  refinement was not evaluated in a real browser by the automated delivery run.
  Human visual acceptance is still required.
- Full repository execution remains outside T1 and is blocked at collection by
  the existing production fixture absence described above.
