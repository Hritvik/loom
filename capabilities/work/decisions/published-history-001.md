# Published history still carries the material LOOM-1 removed

Status: proposed — needs the engine maintainer
Owner: engine maintainer
Date: 2026-11-15
Task: loom/LOOM-1

## Context

The engine was split so the public repository carries the engine, not this portfolio's programs,
maps or identifiers. LOOM-1 removed the last of that material from the working tree; the evidence
file names the files and the checks, and it is the place to look for what was removed.

The repository is public, and a working-tree check cannot see history. `origin/main` reaches the
commits that added the removed files, and the raw file service serves any tracked path at any
commit. Two files removed by LOOM-1 return `200` at their historical paths today and still name the
portfolio's programs, ownership and workspace. So every check in the evidence file passes while the
goal — "the public engine must not name or embed this portfolio's programs, maps or identifiers" —
is not met as published.

A worker cannot close this: the remediation rewrites a public remote and re-points published SHAs,
which needs the owner's decision and an owner's push. Recording it as an accepted limit would be
the owner accepting a limit on the owner's behalf, so this entry is proposed, not accepted.

## Decision

Not yet taken. The options as they stand:

**A. Rewrite the published history (recommended).** Purge the removed paths from all commits with
`git filter-repo`, force-push the rewritten refs, and re-point the clone URL's users. Every commit
SHA in the public engine changes. The approval records for in-flight tasks name those SHAs, so the
rewrite must be scheduled when no review is mid-flight, or the review requests must be re-issued
against the rewritten commits.

**B. Replace the repository.** Publish the cleaned tree as a new repository with no history and
archive the old one. Clear history, but loses the engine's public development record and leaves
the old repository public unless it is archived or made private.

**C. Accept the exposure.** Keep the history as it is and record, in the owner's own decision, that
the goal is met in the tree and not in the history. This is a defensible choice if the removed
material is no longer sensitive; it is only acceptable if the owner says so.

## Consequences

- Until the owner decides and acts, the evidence file's "Limits" section stands and the goal stays
  open in the published repository.
- Option A or B invalidates every commit SHA quoted in an open review request for this repository,
  including this task's. Whoever executes it must re-issue those requests.
- Whichever option is chosen, a follow-up registry task should carry the remediation so it is not
  lost with this task's closure.
