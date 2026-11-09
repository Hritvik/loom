# Integration receipts for pre-migration tasks

Status: accepted
Owner: engine maintainer
Date: 2026-11-09
Task: none (coordination policy)

## Context

Integration is proven by the event that records it: the `transition` to `integrated`
carries `integrated_source` and `integration_tip`, verified against the board's
integration branch. Tasks that were already `integrated` or `accepted` when the
registry moved into SQLite have no such event, because the file registry had no
place to put one.

`./task annotate` repairs a task when a provable commit exists. 24 tasks were
annotated this way: 17 from the commit named in their own integrated-transition
reason, six from the commit that introduced their declared write paths, and one
whose commit its evidence records.

The remaining 45 cannot be repaired honestly:

- **Two squashed repositories (31 tasks):** both were reduced to a root
  `Initial commit` that already contains the task evidence. The per-task commits
  no longer exist in history.
- **Four scaffold-only tasks:** only the scaffold commit contains their artifacts;
  that is not an integration point.
- **The remaining tasks:** evidence and transition reasons name no commit, or name
  a commit only as a reference ("before `65c2e60`"), which is not proof of integration.

## Decision

Pre-migration integrations are proven by their evidence artifacts and git history,
not by an `integrated_source` event. The validator reports the count as a known,
accepted gap instead of an outstanding defect. No task may be annotated with a
commit that does not both exist and sit in the history of the board's
`integration_ref`; annotating with a squash commit to clear the warning is
forbidden.

Integrations performed after the migration must still produce a receipt: the
`transition` refuses `integrated` without `--integrated-source`, so the gap cannot
recur for new work.

## Consequences

- `validate_work.py` prints one accepted-gap line naming this decision.
- A future reader cannot tell which commit landed a pre-migration task from the
  registry alone; they must read the evidence file in the owning repository and the
  repository history.
- If one of these tasks is later re-opened and redone, it earns a normal receipt
  and drops out of the count.
- `./task annotate` remains available for any pre-migration task whose commit is
  later found and verified.
