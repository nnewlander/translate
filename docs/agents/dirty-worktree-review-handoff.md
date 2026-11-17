# Dirty-worktree review handoff

Use this template when an independent reviewer must assess local changes that are not committed. Replace every placeholder; do not omit untracked files.

## Fixed point and location

```text
Base: <full commit SHA used as the review fixed point>
HEAD: <full current commit SHA>
Branch: <branch name or detached HEAD>
Worktree: <absolute path>
Scope / originating Spec or Issue: <links or paths>
```

## Complete change inventory

```powershell
git status --short
git diff --stat <Base>
git diff --no-ext-diff <Base> -- <tracked paths, or omit -- and paths for all tracked changes>
git ls-files --others --exclude-standard
```

Record the output or attach it unchanged. For every untracked path, state whether the reviewer must inspect its full content, why it is in scope, and how its content was supplied. A tracked diff does not include untracked files.

```text
Tracked diff coverage: <all tracked changes / explicit exclusions with reason>
Untracked content coverage:
- <path>: <full content supplied by command, attachment, or direct file inspection>
```

## Verification evidence

List commands exactly as run from this worktree. Include exit status and the relevant test count or failure; do not substitute a historical run.

```text
Focused checks:
- <command> -> <PASS/FAIL, count, duration if useful>

Authoritative full verify:
- <command> -> <PASS/FAIL, count, duration, required browser identity if applicable>

Not run / blocked:
- <check and reason>
```

## Independent review axes

Review and report these axes separately even if one passes:

```text
Standards review:
- Inputs: repository instructions and coding standards
- Findings: <severity, path/line, evidence, or “none”>

Spec review:
- Inputs: originating Spec/Issue and explicit Owner overrides
- Findings: <severity, requirement, path/line, evidence, or “none”>

Residual uncertainty:
- <missing evidence, unverified behavior, or “none”>
```

The handoff is incomplete until Base, HEAD, branch, worktree, tracked changes, every untracked file, focused checks, full verify, and both review axes are accounted for.
