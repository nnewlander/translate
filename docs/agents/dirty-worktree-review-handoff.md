# Dirty-worktree review handoff

Use the applicable acceptance rules below and record their evidence in the existing issue or review handoff. Use the same handoff for committed and uncommitted changes. Replace every placeholder and account for every untracked file.

## Early Architecture Explorer acceptance

- **UI changes.** At the first usable combined preview, exercise the affected paths through the real UI and have the Owner perform semantic and visual acceptance on that preview. Use the actual input gesture for changed interactions and cover the applicable desktop and narrow viewports. Record the preview revision and inputs, the paths and gestures exercised, and the Owner's decision. Record outstanding acceptance as pending.
- **Cross-ticket public interface changes.** As soon as the changed interface can be exercised, pass the producer's actual output through the consumer's public entry point before final integration. Record the exact producer and consumer revisions, the output used, the entry point and the result together. Recheck the affected path if either side changes in a way that invalidates the evidence.
- **Contract activation.** Before freezing the final candidate, preview the proposed switch in isolation. Run the existing contract state matrix and historical preflight against the proposed state, with historical inputs bound to their original revisions. Record the preview revision, state transitions, current-contract uniqueness, effective baseline and historical compatibility results. Resolve failures before freezing the candidate and apply the existing activation and delivery gates.

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
git log --oneline <Base>..HEAD
git diff --stat <Base>...HEAD
git diff --no-ext-diff <Base>...HEAD
git diff --stat HEAD
git diff --no-ext-diff HEAD
git ls-files --others --exclude-standard
```

Record the output or attach it unchanged. For every untracked path, state whether the reviewer must inspect its full content, why it is in scope, and how its content was supplied. A tracked diff does not include untracked files.

```text
Tracked diff coverage: <all tracked changes / explicit exclusions with reason>
Untracked content coverage:
- <path>: <full content supplied by command, attachment, or direct file inspection>
```

## Verification evidence

Use the existing delivery gates applicable to the change. For documentation-only changes, record the relevant scenario walkthrough, rule consistency and diff checks, and identify product checks that are inapplicable. List executed commands exactly as run, with exit status and the relevant result or failure.

Reuse evidence only when the candidate revision (or a verified identical tree for checks that do not depend on commit identity or history), verification scope and relevant environment match. Cite the original run, record the match, and rerun affected checks when the match fails. Keep Windows performance measurements separate from expensive local verification.

When the Owner has authorized a Draft PR and its CI, run Draft CI and independent re-review in parallel on the same candidate when their inputs are ready. Retain the applicable formal CI and review gates on the delivery candidate.

```text
Focused checks:
- <command> -> <PASS/FAIL, count, duration if useful>

Applicable delivery gates:
- <command> -> <PASS/FAIL, count, duration, required browser identity if applicable>

Reused / not run / blocked:
- <original evidence and match, or check and reason>
```

## Incremental re-review

Use the exact previously reviewed revision as the re-review fixed point. For a previous dirty review, use its saved reviewed snapshot, including reviewed untracked content. Record that fixed point and the new candidate in the handoff. Supply their direct diff (for two commits, `git diff --no-ext-diff <reviewed-commit> <candidate-commit>`) and account for any remaining uncommitted or untracked changes against the reviewed snapshot. Keep the original ticket Base for the full-scope comparison.

Use one Finding and one retest record per defect, including defects found on both axes. Link the original Finding, changed hunk, candidate revision and retest command or artifact with its outcome. Keep the Standards and Spec decisions independent. Each applicable axis records whether its Finding is closed or still open and links to the shared evidence.

```text
Re-review fixed point: <reviewed commit or saved dirty snapshot>
Candidate: <commit or dirty snapshot>
Delta: <exact diff command or artifact>
Finding / retest: <ID, original report, changed hunk, candidate, check and result>
Standards decision / Spec decision: <independent outcomes referencing the Finding>
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

The handoff is complete when it accounts for Base, HEAD, branch, worktree, all tracked and untracked changes, applicable verification, and both review axes (mark pending when awaiting review). For re-review, include the fixed point, delta and Finding closure evidence.
