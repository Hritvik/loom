# Work items — loom

The engine's own backlog. Bodies live here; identity, state and claims live in the registry.

## LOOM-1 — Remove portfolio content from the public engine

**Status.** Reviewed.

## LOOM-2 — Publish the artifact taxonomy and templates

**Status.** Reviewed.

## LOOM-3 — Write the adopting guide and a worked example

**Status.** Reviewed.

## LOOM-4 — Prove the public boundary with a test

**Status.** Ready.

The engine must fail its own suite when it carries a private program namespace, an absolute home
path or a personal email, so the boundary is a test rather than a claim in an evidence file. A
namespace literal in engine source is the case that shipped; a path check does not catch it.

**Acceptance.** A structural test fails on a literal namespace id or a branch on one. A test fails on
absolute home paths and personal emails in tracked files. The private denylist runs against a public
checkout without the list entering the public repository.

## LOOM-5 — Model an attempt, and reap the ones that are gone

**Status.** Ready.

One agent run is one attempt with a stable identity, created inside the claim transaction. Capacity,
reconcile, the worker list and review launch currently invent four different keys for the same run,
which is how a relaunch shadowed a live worker and how two agents ended up in one worktree.

**Acceptance.** Two attempts for one task use different worktrees. Capacity equals the observed live
attempts after consecutive passes. An attempt left in `starting` with no launch is reaped as failed.
A relaunch leaves the prior attempt intact and queryable.

## LOOM-6 — Report health as an artifact

**Status.** Ready. Depends on LOOM-5.

A stopped dispatcher, a stopped schedule and a wedged cursor are currently indistinguishable from an
idle engine. The idle-looking-but-dead failure is what started this work.

**Acceptance.** Each pass rewrites `health.json` with the pass time, cursor, log head, live attempts
and the last error. `loom health` exits non-zero when the last pass is older than three intervals.

## LOOM-7 — Bind an integration receipt to the content it approved

**Status.** Ready.

A receipt records the paths and blobs it covered, so integration verifies exact content instead of
re-diffing a history that may have moved.

**Acceptance.** A receipt stores the changed path and blob pairs and a digest over them. Integration
fails with the exact path list when the approved paths changed on the ref. Legacy receipts keep a
null digest and are reported as incomplete rather than silently repaired.

## LOOM-8 — Check the registry against a fold of the log

**Status.** Ready. Depends on LOOM-5.

The registry becomes a checked projection from a snapshot forward, so a mutation without an event or
a forged field is detected instead of argued about. The log is a journal of changes, not a record of
state, so this is forward-only: there is no backfill.

**Acceptance.** `foldcheck` reports zero divergence on a database created after the snapshot, and
names a dropped field instead of deleting records. A rebuild refuses to write without a `VACUUM INTO`
restore point and a passing diff. Historical tasks arrive through the snapshot.

## LOOM-9 — Run the review and receipt phases as event handlers

**Status.** Ready. Depends on LOOM-8.

Derive handlers own one event each and survive being run twice, in shadow first, so the order of the
tick's phases stops being load-bearing.

**Acceptance.** In shadow mode the handler projection equals the tick projection for consecutive
passes. The receipt handler calls the verified review path and does nothing when validation fails.
The rework handler sets a flag only and never releases or takes over a claim.

## LOOM-10 — Deduplicate agent launches with an intent record

**Status.** Ready. Depends on LOOM-5 and LOOM-9.

Spawning is not idempotent, so it is gated by a unique intent written with the cursor rather than by
a head check that races the next event.

**Acceptance.** A second dispatcher or a redelivery finds the intent and spawns nothing. The store
lock is not held across a spawn. An attempt in `starting` with no launch is retried rather than
skipped.
