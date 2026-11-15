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
commit. So every check in the evidence file passes while the goal — "the public engine must not
name or embed this portfolio's programs, maps or identifiers" — is not met as published.

**The exposure is 27 tracked paths, not two.** `36e2973` ("refactor: keep portfolio content out of
the public engine", the commit that carries this task's removals) deleted 27 paths in one commit:
`capabilities/work/linear-sync.md`, `capabilities/work/programs/identity-platform-integration.yaml`,
and 25 under `contracts/`. The two documentation files are the ones the evidence file names first;
the 25 contract paths are the larger part of the exposure and were deleted in the same commit.

| Where | Count | Paths |
| --- | --- | --- |
| `capabilities/work/` | 2 | `linear-sync.md`, `programs/identity-platform-integration.yaml` |
| `contracts/registry*` | 2 | `registry.yaml`, `registry.schema.json` |
| `contracts/events/` | 7 | three under `iam.app-subject.onboarding-ready.v1/v1/`, two under `ums.profile.association-report.v1/v1/`, `ums.profile.state-changed.v1/v1/schema.json`, `dutch.member-profile.mapping-changed.v1/v1/schema.json` |
| `contracts/http/` | 4 | two under `iam.onboarding-snapshot.v1/v1/`, `ums.profile-status.v1/v1/openapi.json`, `dutch.profile-state.v1/v1/openapi.yaml` |
| `contracts/fixtures/` | 12 | two `dutch.member-profile.mapping-changed.v1`, two `dutch.profile-state.v1`, one `iam.app-subject.onboarding-ready.v1`, one `iam.onboarding-snapshot.v1`, one `ums.profile-status.v1`, four `ums.profile.association-report.v1`, one `ums.profile.state-changed.v1` |

The paths carry the identifiers directly: `contracts/events/iam.*`, `contracts/events/ums.*`,
`contracts/http/dutch.*`, `contracts/fixtures/iam.*`, `contracts/fixtures/ums.*` and
`contracts/fixtures/dutch.*`. `contracts/registry.yaml` at `a68c436` names `owner: iam` and a note
that `UMS` and `Dutch` must re-pin. Six of the 27 were fetched from
`https://raw.githubusercontent.com/Hritvik/loom/a68c436/<path>` and each returned `200`
(`contracts/registry.yaml`, an `iam` event schema, the `dutch` HTTP contract, a `ums` status
fixture, `linear-sync.md`, `programs/identity-platform-integration.yaml`). The other 21 were not
fetched one by one; the deletion set below is the tracked set at `a68c436`, and the raw service
serves any tracked path at that commit.

Whoever acts on this decision must treat the contract paths as in scope. Option A as it was first
drafted said "purge the removed paths", which read as the two documentation files; it would have
left the 25 portfolio-identifying contract paths public. Full deletion set at `36e2973`:

```
capabilities/work/linear-sync.md
capabilities/work/programs/identity-platform-integration.yaml
contracts/events/dutch.member-profile.mapping-changed.v1/v1/schema.json
contracts/events/iam.app-subject.onboarding-ready.v1/v1/event-envelope.schema.json
contracts/events/iam.app-subject.onboarding-ready.v1/v1/payload.schema.json
contracts/events/iam.app-subject.onboarding-ready.v1/v1/schema.json
contracts/events/ums.profile.association-report.v1/v1/common.json
contracts/events/ums.profile.association-report.v1/v1/schema.json
contracts/events/ums.profile.state-changed.v1/v1/schema.json
contracts/fixtures/dutch.member-profile.mapping-changed.v1/v1/invalid/forbidden-identity.json
contracts/fixtures/dutch.member-profile.mapping-changed.v1/v1/valid/member-profile-mapping-changed.json
contracts/fixtures/dutch.profile-state.v1/v1/valid/profile-state-ready.json
contracts/fixtures/dutch.profile-state.v1/v1/valid/profile-state-required.json
contracts/fixtures/iam.app-subject.onboarding-ready.v1/v1/valid/onboarding-ready-event.json
contracts/fixtures/iam.onboarding-snapshot.v1/v1/valid/onboarding-snapshot-response.json
contracts/fixtures/ums.profile-status.v1/v1/valid/status-ready.json
contracts/fixtures/ums.profile.association-report.v1/v1/association-report-invalid-display.json
contracts/fixtures/ums.profile.association-report.v1/v1/association-report-invalid-environment.json
contracts/fixtures/ums.profile.association-report.v1/v1/association-report-invalid-tombstone.json
contracts/fixtures/ums.profile.association-report.v1/v1/association-report.json
contracts/fixtures/ums.profile.state-changed.v1/v1/valid/state-event.json
contracts/http/dutch.profile-state.v1/v1/openapi.yaml
contracts/http/iam.onboarding-snapshot.v1/v1/onboarding-snapshot-response.schema.json
contracts/http/iam.onboarding-snapshot.v1/v1/openapi.yaml
contracts/http/ums.profile-status.v1/v1/openapi.json
contracts/registry.schema.json
contracts/registry.yaml
```

A worker cannot close this: the remediation rewrites a public remote and re-points published SHAs,
which needs the owner's decision and an owner's push. Recording it as an accepted limit would be
the owner accepting a limit on the owner's behalf, so this entry is proposed, not accepted.

## Decision

Not yet taken. The options as they stand:

**A. Rewrite the published history (recommended).** Purge all 27 paths listed above — the two
documentation files and the 25 `iam`/`ums`/`dutch` contract paths — from all commits with
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
