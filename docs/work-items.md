# Work items — loom

The engine's own backlog. Bodies live here; identity, state, claims and dependency edges live in the
registry, so a section carries the problem and its acceptance and nothing else.
`capabilities/work/tests/test_body_claims.py` fails the suite when a section carries either.

## LOOM-1 — Remove portfolio content from the public engine

The public engine must not name or embed this portfolio's programs, maps or identifiers.

## LOOM-2 — Publish the artifact taxonomy and templates

Every artifact kind needs a template an adopter can copy instead of inventing.

## LOOM-3 — Write the adopting guide and a worked example

A stranger must be able to run the protocol end to end without reading this portfolio.

## LOOM-4 — Prove the public boundary with a test

The engine must fail its own suite when it carries a private program namespace, an absolute home
path or a personal email, so the boundary is a test rather than a claim in an evidence file. A
namespace literal in engine source is the case that shipped; a path check does not catch it.

**Acceptance.** A structural test fails on a literal namespace id or a branch on one. A test fails on
absolute home paths and personal emails in tracked files. The private denylist runs against a public
checkout without the list entering the public repository.

## LOOM-5 — Model an attempt, and reap the ones that are gone

One agent run is one attempt with a stable identity, created inside the claim transaction. Capacity,
reconcile, the worker list and review launch currently invent four different keys for the same run,
which is how a relaunch shadowed a live worker and how two agents ended up in one worktree.

**Acceptance.** Two attempts for one task use different worktrees. Capacity equals the observed live
attempts after consecutive passes. An attempt left in `starting` with no launch is reaped as failed.
A relaunch leaves the prior attempt intact and queryable.

## LOOM-6 — Report health as an artifact

A stopped dispatcher, a stopped schedule and a wedged cursor are currently indistinguishable from an
idle engine. The idle-looking-but-dead failure is what started this work.

**Acceptance.** Each pass rewrites `health.json` with the pass time, cursor, log head, live attempts
and the last error. `loom health` exits non-zero when the last pass is older than three intervals.

## LOOM-7 — Bind an integration receipt to the content it approved

A receipt records the paths and blobs it covered, so integration verifies exact content instead of
re-diffing a history that may have moved.

**Acceptance.** A receipt stores the changed path and blob pairs and a digest over them. Integration
fails with the exact path list when the approved paths changed on the ref. Legacy receipts keep a
null digest and are reported as incomplete rather than silently repaired.

## LOOM-8 — Check the registry against a fold of the log

The registry becomes a checked projection from a snapshot forward, so a mutation without an event or
a forged field is detected instead of argued about. The log is a journal of changes, not a record of
state, so this is forward-only: there is no backfill.

**Acceptance.** `foldcheck` reports zero divergence on a database created after the snapshot, and
names a dropped field instead of deleting records. A rebuild refuses to write without a `VACUUM INTO`
restore point and a passing diff. Historical tasks arrive through the snapshot.

## LOOM-9 — Run the review and receipt phases as event handlers

Derive handlers own one event each and survive being run twice, in shadow first, so the order of the
tick's phases stops being load-bearing.

**Acceptance.** In shadow mode the handler projection equals the tick projection for consecutive
passes. The receipt handler calls the verified review path and does nothing when validation fails.
The rework handler sets a flag only and never releases or takes over a claim.

## LOOM-10 — Deduplicate agent launches with an intent record

Spawning is not idempotent, so it is gated by a unique intent written with the cursor rather than by
a head check that races the next event.

**Acceptance.** A second dispatcher or a redelivery finds the intent and spawns nothing. The store
lock is not held across a spawn. An attempt in `starting` with no launch is retried rather than
skipped.

## LOOM-11 — Write only the documents a mutation changes

Every command rewrites the whole registry: `save` deletes and reinserts every task and board
document. Commit cost therefore grows with registry size, and the event dispatcher cannot afford a
cursor advance per event. Write only the documents a mutation touches and keep the event log as the
audit chain.

**Acceptance.** A claim rewrites only the changed task document; untouched task rows are not
rewritten. Commit cost does not grow linearly with the number of tasks in the registry. The event
log, its chain and the external checkpoint are unchanged in shape.

## LOOM-12 — Make evidence claims executable

An evidence file asserts test counts, validator results and sweeps; those assertions are believed and
have already been wrong. Generate or re-derive them so a stale claim fails the build instead of
persuading a reviewer.

**Acceptance.** A stale test count in an evidence file fails the suite. A claimed validator result is
re-derived rather than copied. The evidence template marks which lines are machine-checked and which
are prose.

## LOOM-13 — Make bodies carry no state, and enforce it with a test

This file had become a second copy of registry state: sections carried a status label that the
registry had moved past, and several carried a dependency sentence that no edge backed. A body is
the problem statement and its acceptance. A status, a claim or an edge lives in the registry, where
it is transactional, and the precedent for the rest is `capabilities/work/tests/test_boundary.py`: a
rule the suite enforces is a rule that cannot drift.

**Acceptance.** A suite test fails on a synthetic body section carrying a status claim or a
dependency claim, and its detector is exercised on that fixture so it cannot become a no-op. This
file carries no status or dependency claim, while every section keeps its problem statement and its
acceptance. `python3 -m unittest discover -s tests` passes.

## LOOM-14 — Review the integration candidate, and flag a review that cannot integrate

Integration compares the approved blob with the blob on the integration branch, so a branch cut from
an older main can never integrate as reviewed — it goes stale before anyone notices, and an integrator
spends a merge discovering it. This package records the merge → review the merge commit → integrate
that commit order, and makes the impossibility visible in the validator.

**Acceptance.** `validate_work.py` reports an error naming the task and its branch when a reviewed
task's approved source is not in the history of the board's `integration_ref`, and reports nothing for
a source that is. `PROTOCOL.md` and `AGENT-GUIDE.md` state the sequencing rule.

## LOOM-15 — Schedule install must not drop WORK_HOME

`loom schedule install` wrote `EnvironmentVariables` with `PATH` alone, so a hand-set work home was
lost on the next install and the scheduled tick resolved relative evidence paths against its own tree.
That is how a promoted release acquired an evidence file and drifted its digest to `MISMATCH`.

**Acceptance.** The generated plist declares the resolved work home next to `PATH`; a tick started
from it writes evidence under that work home, never inside the promoted release; `deploy status`
stays verified.

## LOOM-16 — Retire a task or a board with a tombstone, without breaking the chain

There was no way to remove work: `board remove` refused while tasks existed, and no task delete
existed, so an abandoned programme could not be retired. Deleting rows would break the hash chain.

**Acceptance.** `loom delete <task|--board> --reason` records the deletion as an event; the task
disappears from every view while `foldcheck` still verifies; a retired board can be removed; the
validator stays silent; a live claim or undeleted dependent refuses the delete; and no evidence or
bodies file is removed by the command.

## LOOM-17 — Refuse an integration that would land an unreviewed path

The receipt is computed from `review-base..source`, and the base is chosen by the worker. A worker can
ask for review of a commit that already contains its own code, so the receipt covers only what came
after — and integration checks just the paths in the receipt. Two packages reached `reviewed` that way
with their whole change uncovered.

**Acceptance.** Integration refuses when the source changes any path, since the merge-base with the
integration branch, that the receipt does not cover, naming the uncovered paths and the task. A
receipt that covers every changed path still integrates, proven in the suite.
