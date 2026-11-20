# Evidence — LOOM-17 refuse an integration that would land an unreviewed path

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-17-a1` (worktree `~/.local/state/loom/worktrees/loom-loom-17-a1`), taken from
`main` at `8056368`; the review request names the full commit this file sits in.

## Goal

A review receipt is computed over `review_base..source`, and the base is chosen by the worker. A
base inside the worker's own branch produces an honest-looking receipt that excludes the branch's
earlier commits, and the merge lands them anyway: integration only ever compared the paths the
receipt recorded, so the uncovered code reached `main` unreviewed. LOOM-15's receipt covered only
evidence while its base already contained its 61-line plist fix, and LOOM-10's branch still
differed from `main` by 862 lines of `engine.py` behind a receipt that named four paths. This
refuses that integration instead of recording it.

## What changed

- `engine.py`
  - `first_parent`, `fork_point` and `unreviewed_paths` are new. `fork_point` answers where the
    source's line of work forked from the integration branch's mainline. Because the ancestry
    check already requires the source to be an ancestor of the ref, `git merge-base source tip`
    is the source itself and says nothing about what landed; the branch is visible only through
    the mainline, so it walks the ref's first parents back to the oldest commit that already
    contains the source — the merge that brought it in — and takes the merge base of the source
    with that commit's first parent. A source that sits on the mainline has its own child as that
    commit, the range collapses to the source, and a linear integration is unaffected.
  - `require_reviewed_source` refuses when the paths the source landed since the fork point
    include a path the receipt does not name. The failure names the task and the exact uncovered
    paths. The recorded-digest check, the blob-equality check and the ancestry check are
    unchanged and still run, in that order, before and after it.
  - `integration_action` classifies the refusal as `uncovered_paths`; `plan_integration` reports
    it instead of a generic `integration_blocked`, and `integration-plan` renders the uncovered
    paths per task with the reopen command.
- `tests/test_work.py`
  - `test_integration_refuses_a_receipt_that_skips_the_branch` builds the real shape: a branch
    with two commits, review asked from a base inside that branch, `merge --no-ff` into `main`,
    then integration. It asserts the refusal, that the uncovered path is named, that the covered
    path is not, that the task is named, that the task stays `reviewed`, and that
    `integration-plan` reports the bucket with the path.
  - `test_integration_accepts_a_receipt_that_covers_the_whole_branch` is the same branch with the
    fork point as the base: every path the branch changed is in the receipt and it integrates.
  - The existing receipt tests are untouched, which is what proves the linear and already-merged
    shapes keep their behaviour: they integrate exactly as before.

## Verification

From `capabilities/work`:

```
python3 -m unittest discover -s tests
python3 validate_work.py
```

The suite collects <!-- loom:tests.count:tests/test_work.py -->142<!-- /loom --> tests in
`tests/test_work.py`; the full discovery run reports `OK (skipped=2)` over the whole `tests/`
directory, including the two cases above.

The validator still reports the portfolio's pre-existing state — 41 errors, all `reviewed` sources
that are not yet merged onto their board's integration ref, and 113 warnings. That is the same
count the untouched checkout prints, because this change adds no validator check and the
validator never called `require_reviewed_source`; it is stated here as prose rather than as a span
because it reads the shared registry that a clone does not have.

## Decision points

- **The range is decided by the fork point, not by the receipt's base.** The base is the thing a
  worker can choose to make a receipt look complete, so it cannot also be the thing that defines
  what must be covered. Deriving the range from the ref's mainline is the only reading a worker
  cannot influence: either the merge that landed the work is found, or there is no merge and
  nothing was carried in beside the source.
- **A linear integration is deliberately left alone.** When the source is a commit on the ref's
  own first-parent line, there is no branch to leave uncovered, and the collapse to an empty range
  keeps every existing receipt behaving exactly as before (the whole receipt test set passes
  unchanged). The refusal is about work that arrived on a branch, which is the board's documented
  flow (`merge --no-ff`, then review the merge).
- **Path sets, not content.** Coverage is a set difference on path names from `--no-renames`
  diffs, matching what `approved_content` records, so a rename is a deletion plus an addition on
  both sides and the two always agree.

## Limits

- A **fast-forward** integration leaves no fork point to compare against: the ref simply advanced
  along the source's own line, so the earlier commits are indistinguishable from reviewed ones
  from the ref alone. The board's flow is `merge --no-ff` (`integration-plan` emits exactly that
  command, and `AGENT-GUIDE.md` sequences merge → review → record), so this is the shape the check
  covers; a fast-forward is not detected and is not claimed to be.
- A branch that was cut from **another task's reviewed branch** will have that task's paths counted
  as its own and refused. The repair is the honest one — submit from that fork point — but it is a
  false positive against a stacked-branch workflow the board does not use today.
- The check refuses at integration, which is the last moment it can: a worker who submits from a
  base inside its branch only learns when the integrator records the receipt. Moving the same
  check to `loom ask` would fail earlier; it is not done here because the acceptance is stated for
  integration, and duplicating the rule in two places invites them to drift.
- Nothing here changes what the reviewer reads. The reviewer still approves `base..source`; the
  engine now refuses to act on an approval that does not cover what the merge lands.
