# Architecture Explorer T1 delivery record

This record describes the local T1 repair delivery for GitHub Issue #2. It is
evidence for independent review, not an integration or merge claim.

## Revision and integration status

- Latest application-and-test commit actually exercised: `64cc40302d34fff4f8ee9eb44ef7c17d23cc09a0`.
- Branch: `codex/architecture-explorer-t1`.
- The commit has **not** been authorized for integration and is not described as merged.
- No authorized T2 integration baseline has been established. T2 must not infer
  a baseline from this branch, this document, or the local generated site.

The delivery-record commit that contains this file is documentation/evidence
only; the application and test tree is the tested commit above. Generated sample
sites remain ignored local artifacts.

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
  --basetemp 'D:\translate\.test-tmp\architecture-explorer-t1-review-fixes-r3' `
  tests\architecture_explorer\test_top_level_explorer.py
```

Result: `37 passed, 2 skipped in 12.56s`. The two skipped cases are the
repository-external link escape test and the repository-internal
`output -> protected` junction regression. The managed environment denied every
available isolated link-creation mechanism, so neither skip is described as a
pass. Absolute paths, `..`, output/input overlap, protected file targets,
normal real CLI scan/build, malformed re-signed snapshots (including
`"   fake"`), legal staged/unstaged/untracked/rename porcelain records, and
porcelain column preservation all executed and passed.

Python source compilation check passed for all five Python files in
`src/booktrans/architecture_explorer` and `tests/architecture_explorer`.
`git diff --check` passed before the tested commit. The project interpreter was
also used to import
`D:\translate-worktrees\architecture-explorer-t1\src\booktrans\architecture_explorer\generator.py`
explicitly.

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
- Generated Explorer data:
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

### Current final page and repair sample

The already user-approved visual assets were not changed by this repair. The
site was generated from the clean tested implementation commit
`64cc40302d34fff4f8ee9eb44ef7c17d23cc09a0` and repeated into a second output
directory:

- Local offline page:
  `output/architecture-explorer/t1-review-fix-64cc403/site/index.html`
- Snapshot facts SHA-256:
  `3d9ec623383218a82b194e5ccea058bb56aae76a0e76da94960c1ee3ef774798`
- Deterministic content SHA-256:
  `2c0ecee9a29ad3e486d0432f275bcc614f23e8a705638eef8a59535a25aaf801`
- `explorer-data.json` file SHA-256:
  `a5b0ae06e5c98235fc42e0ad4798f81e03dde0a528667a383bcd30af15780642`
- Generated `styles.css` file SHA-256:
  `d6b1a23a3f1a6441055aba0ec5eeaf670074a509a751cf7c8a3aae492fd3dc93`

Both builds produced byte-identical `explorer-data.json`, the same deterministic
content SHA-256, seven modules with `approved` logical definitions and
`unverified` source coverage, and all five required offline resources. This
generated page is an ignored local artifact, not an authorized integration
baseline.

## Known unverified items

- The managed environment could not create a real Windows symlink or junction.
  Consequently, both the repository-external link escape regression and the
  exact `repo\output -> repo\protected` junction regression remain skipped and
  require execution by an independent reviewer in a Windows environment that
  permits isolated test links. The implementation rejects an existing output
  root whose Windows attributes identify any reparse point before resolving or
  writing it, while final target containment still rejects nested escapes.
- The final high-contrast spring/watercolor CSS and generated resource set were
  verified statically and through generation tests. This repair did not change
  or re-evaluate the page visually; it retains the user's prior visual
  acceptance.
- Full repository execution remains outside T1 and is blocked at collection by
  the existing production fixture absence described above.
