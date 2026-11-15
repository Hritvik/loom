# Evidence — LOOM-5 model an attempt, and reap the ones that are gone

Repository: `~/repositories/loom`
Status: green, submitted for independent review

## The problem

One agent run had four different keys. Capacity deduplicated `launch` records by pid over a
six-hour window, reconciliation keyed on `(task, role, question)`, `loom workers` took the latest
`launch` per task, and the review launch compared `(task, question)` against the latest record. A
relaunch wrote a newer record for the same task and shadowed the one before it: that is how an
original worker kept running while capacity read only its dead replacement, and how two agents
could be started for one task. The worker checkout was keyed on the task alone, so both runs also
shared one directory and one branch.

## Delivered

- **An attempt is a record with an identity.** `event="attempt"` carries `attempt: "<task>#a<N>"`
  plus role, agent, epoch, revision, and — for reviews — question, source and base. The log is the
  only store; `attempt_views()` folds the log into one view per run.
- **Created inside the claim transaction.** `cmd_claim` and `cmd_takeover` allocate the next
  attempt number from the log and append the attempt in the same `store.transaction()` as the
  claim event, so a claim without a run and a run without a claim cannot exist. `claim` prints the
  attempt and the worker brief names it (`Your attempt: <task>#a<N>`).
- **A launch attaches the process.** The spawn appends `event="launch"` with the attempt id, pid,
  process start time, worktree and branch. An attempt with a launch is `running`; before that it is
  `starting`. The reaper closes each attempt once, as `done`, `failed` or `superseded`.
- **A checkout per attempt.** `worker_worktree` derives the path and the branch from the attempt
  (`worktrees/<slug>-a<N>` on `loom/<slug>-a<N>`), so two runs of one task cannot share a
  directory. A relaunch branches from the previous attempt's branch when that branch exists, so
  committed work survives the retry.
- **One key for every consumer.** Capacity (`cmd_tick`), reconciliation, `loom workers` and the
  review launch (`open_review_requests`) all read `attempt_views()`; `loom attempts` and `loom show`
  query the same fold.
- **Reaper.** An attempt still `starting` with no launch after ten minutes is closed `failed`
  (`no launch attached`, `reaped: true`). The claim it came from is left alone: a claim is
  authority, a run is work. A reaped attempt cannot requeue or block a task; an attempt whose spawn
  actually failed still does, and two consecutive worker failures still escalate as before.
- **Migration.** A `launch` written before attempts existed has no attempt id. It is folded as
  `<task>#legacy<seq>` and closed by the same reaper, so a worker started by an older engine keeps
  its slot across the upgrade. The live registry proves it: `loom attempts --live` on the running
  database reports `loom/LOOM-5#legacy1639 running implementer pid 94386` with worktree
  `~/.local/state/loom/worktrees/loom-loom-5` and branch `loom/loom-loom-5` — this worker, started
  by the released engine, folded as an attempt.
- **State checks.** `verify_registry_events` (engine) and `validate_work.py` no longer treat
  `attempt`/`launch` as the last word on task state; they carry a revision but no status.
- **Surfaces.** `loom attempts [<task>] [--live] [--json]` added (documented in the README CLI
  table), `loom show` prints the current attempt and `--json` carries all of them, A2A task history
  names the attempt id, and `loom workers` lists the attempt.

## Acceptance

| Criterion | Proof |
|---|---|
| Two attempts for one task use different worktrees | `test_a_relaunch_gets_its_own_worktree_and_leaves_the_prior_attempt`: `demo/W-1#a1` and `#a2` on `worktrees/demo-w-1-a1` / `-a2` and branches `loom/demo-w-1-a1` / `-a2`; both directories exist |
| Capacity equals the observed live attempts after consecutive passes | `test_capacity_equals_the_observed_live_attempts` (two passes: `active.implementer == len(attempts --live) == 2`) and `test_consecutive_passes_never_exceed_the_implementer_limit` (three passes, capacity line `implementer 2/2 attempts live 2`) |
| An attempt left in `starting` with no launch is reaped as failed | `test_an_attempt_left_starting_with_no_launch_is_reaped_as_failed`: still `starting` at +5m, `failed` / `reaped` / `no launch attached` at +11m, claim untouched |
| A relaunch leaves the prior attempt intact and queryable | same relaunch test: after the relaunch `#a1` is still `failed` with its own pid, worktree and launch record, and both appear in `loom show --json` |

Additional: `test_a_claim_opens_its_attempt_in_the_same_write` (a refused claim writes no attempt),
`test_repeated_spawn_failures_are_distinct_attempts`, `test_the_worker_brief_names_its_attempt`,
`test_reconcile_keeps_a_launch_it_cannot_prove_is_reused`, and the updated legacy-launch tests.

## Rework after review (answer event 1650, changes requested)

The reviewer found two consumers I had missed, both still keying on `launch state == "pending"`,
which this change stops writing:

- `changes_requested_tasks` — the "is a worker still running on it?" guard. With the guard dead,
  a changes-requested task owned by the scheduler worker was released while its worker was still
  running, and a following pass claimed it again and started a second implementer beside the
  first — the exact collision this task exists to remove.
- `review_request_sweep` — the `reviewer_live` guard, same dead shape.

Both now read `attempt_views()` / `live_attempts()`. Two regression tests launch a real sleeping
worker through `tick` and drive the rework path, which the previous tests did not do:

- `test_tick_holds_rework_while_the_worker_it_launched_is_running` — green, ask, changes requested:
  the task stays green, the claim is kept, and a following `--max-implementer 2` pass starts no
  second attempt (`Rework held (changes requested)`).
- `test_a_live_reviewer_attempt_holds_the_request_sweep` — a request at the current revision
  addressed to a known non-configured agent is reported `reviewer_live` rather than re-asked while
  the reviewer attempt is live.

Also taken: `next_attempt_id` is now called *inside* the claim's transaction, so its docstring
matches the code (the store lock made the earlier placement safe, but the ordering was not
self-evident).

## Rework after review, round 2 (answer event 1668, changes requested)

The same principle was applied in only one place, so two counters still treated a reaped ghost as
a worker that ran:

- `open_review_requests` counted reaped reviewer attempts against `--max-review-attempts`. Two
  passes that died between writing the reviewer attempt and attaching its process would exhaust the
  request and print `Review stalled (attempts exhausted; needs a human)` without a reviewer ever
  running.
- `trailing_failures` counted reaped ghosts, so one reaped claim plus one real spawn failure
  reported "two worker launches failed" with only one failure.

Both now skip reaped attempts; a reaped attempt is transparent to the run — it neither counts nor
breaks it. Regression tests:

- `test_a_reaped_reviewer_attempt_does_not_stall_the_request` — two starting reviewer attempts
  reaped at +11m, then a pass with a free reviewer slot launches the review instead of stalling.
- `test_a_reaped_attempt_does_not_count_as_a_worker_failure` — a reaped claim attempt plus one
  real spawn failure does not escalate; the second pass stays quiet.

Minor: `loom show` now names the current attempt's role in the text line, so a reviewer run on a
green task is not mistaken for the implementer.

## Verification

```bash
cd capabilities/work
python3 -m unittest discover -s tests      # 232 tests, OK
python3 validate_work.py                   # PASS (against the live registry)
./loom attempts --live                     # folds this worker's legacy launch correctly
./loom show loom/LOOM-5                    # prints the current attempt
```

## Review lenses to apply

- **Concurrency, delivery and recovery.** Claim/attempt atomicity, the reaper's grace window, the
  relaunch-from-branch path, live-slot counting for a closed attempt that is still winding down.
- **Persistence and migrations.** Legacy `launch` records are still read and closed; nothing is
  rewritten; a promotion mid-flight must not drop a running worker's slot.
- **Product behavior and regression.** `loom workers`, `show`, A2A history and the tick report are
  the surfaces that changed shape.

## Limits

- The reaper's ten-minute grace (`ATTEMPT_START_GRACE`) is a constant, not policy in `loom.json`.
- A manual claim (a human or agent claiming to work outside the dispatcher) also opens an attempt;
  after the grace it is closed as a reaped ghost while the claim stays live. That is intentional and
  tested, but it means `loom attempts` shows a `failed` run for work a person did by hand.
- A relaunch continues the previous attempt's branch. If the previous attempt went down a wrong
  path, that path is inherited; discarding it stays a human decision (`git branch -D`), not an
  automatic reset.
- `GLOSSARY.md` still defines "Launch" as the act binding task, role, agent and thread. It is
  outside this task's write path (`capabilities/work`); the vocabulary should become
  Attempt/Launch as written in `PROTOCOL.md` by whoever owns the root glossary.
- No promotion (`loom deploy promote`) was run: the release still runs the old engine until the
  owner integrates this and promotes.
