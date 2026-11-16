# Evidence — LOOM-8 check the registry against a fold of the log

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-8-a3` (worktree `~/.local/state/loom/worktrees/loom-loom-8-a3`)
Status: green, submitted for independent review (round 2)

## The problem

The registry was the authority and the log was a commentary on it. `loom events --verify` proved the
chain was intact and that the *last status event* agreed with the task, and nothing else: a hand edit
to `write_paths`, `claim`, `revision` or a title was invisible as long as the last transition still
matched. A mutation written without an event was undetectable, and the only repair was to argue about
what the state should have been.

The log records transitions, not documents, so the registry cannot be rebuilt from the beginning
without inventing history. The honest shape is therefore forward-only: a **baseline snapshot**, then
a log that carries the state change it made.

## Round 2 — the baseline is anchored to the chain, not to itself

The first review found the hole the whole task turns on: `foldcheck` compared the `snapshots` body
against the digest column **in the same row** (`latest_snapshot` → engine's old `fold_check`). It never
read the digest from the `snapshot` event, so one raw `INSERT INTO snapshots` that agreed with itself
— forged body and a recomputed `digest` — was accepted as the baseline, and `foldcheck` printed "no
divergence", `events --verify` printed "event chain OK", and the validator exited 0. Because
`fold_registry` starts *after* the baseline's `seq`, a high row `seq` also hid every real delta beneath
it. That is exactly the "mutation with no event, forged field accepted silently" failure this task
exists to detect, so it was blocking.

Fixed by making the event the authority:

- `snapshot_baseline_problems(snapshot, records)` requires an event at the baseline's `seq`, requires
  it to be `event == "snapshot"`, reads the digest **from that event** and compares it with
  `sha256(canonical_registry(body))`, and verifies the hash chain up to that sequence. A missing
  event, a different event kind, a missing digest, a mismatched digest or a broken chain each produce
  a `problem`, which `foldcheck` prints as `BROKEN` (exit 1) and a rebuild refuses.
- `validate_work.py` calls the same helper, so a tampered baseline is reported as `fold: BROKEN …`
  rather than as ordinary registry-vs-log field drift.
- The misleading message is gone: it now really names the digest recorded in the event chain.

Proven on a scratch store with the reviewer's repro (snapshot, claim, then one raw
`INSERT INTO snapshots (seq=99, body=forged registry, digest=sha256(body))` plus the matching task
row edits):

```text
foldcheck exit 1
BROKEN snapshot 99: no event at that sequence in the log; the baseline is unanchored and any delta beneath it would go unchecked
task demo/W-1: field 'claim' is in the registry with no event behind it
…
foldcheck: 6 divergence(s) from snapshot 99

events --verify exit 0 | event chain OK        # the log itself was never touched — which is why
validate exit 1                                 # the baseline, not the chain, had to anchor it
ERROR fold: BROKEN snapshot 99: no event at that sequence …
```

## Delivered

- **Every state-changing event carries its own delta.** `state_delta` is a document in the event
  body: `tasks`/`boards`/`claims` entries are `{id, created}` for a new document, `{id, set, unset}`
  for a field-level change, `{id, deleted}` for a removal; `edges` carries `added`/`removed`. It is
  computed in the store's transaction, from the documents it read against the registry it is about
  to write, so a command cannot reach the state without leaving the change in the log.
- **Attribution is explicit.** A single `commit` puts its task's delta on its own event; a batch that
  changes several documents (reconcile, a tick's requeue) leaves the remainder on the last event of
  the transaction. A claim's delta lands on `claim`, never on the `attempt` that follows it.
- **Events are hashed when they reach the database.** `SqliteStore` now stages events in the open
  transaction and computes `prev`/`hash` at flush, after any delta is attached, so the hash chain
  covers what was written. `read_events` returns staged events, because the reconciler folds the log
  it just appended to.
- **`loom snapshot`** writes the baseline: the registry as a canonical JSON document in a `snapshots`
  table plus a `snapshot` event carrying its digest and counts. The digest sits inside the hash
  chain, and `foldcheck` reads it from the **event**, not from the row, so editing the body and the
  row digest together is still reported `BROKEN`. A baseline row with no event at its sequence is
  `BROKEN` too — otherwise its `seq` would silence every delta beneath it.
  A new baseline refuses to be written while the log and registry disagree; `--force --reason TEXT`
  is the deliberate escape hatch, and the reason is recorded.
- **`loom foldcheck`** folds `snapshot + deltas` and compares it with the registry **field by field**
  (dotted paths for nested documents), naming each divergence: a lost field, a field with no event
  behind it, a changed value with both values shown, a record the log does not hold, a record with no
  create event, an edge added or lost. It never writes. Exit 0 clean, 1 divergent, 2 no baseline.
- **`loom rebuild`** rewrites the registry from the fold. It prints the diff, and `--apply` writes
  only after (a) a `VACUUM INTO` restore point that is reopened and proven to hold the same task and
  event counts, and (b) a diff that **passes**: no record or edge in the registry may be unknown to
  the log, because repairing it would delete evidence. A refused rebuild writes no restore point and
  changes nothing. The `rebuild` event records the snapshot, the restore point, the divergence count
  and a diff digest, and carries **no** `state_delta` — it restores what the log already says, so
  recording the change again would double-apply it on the next fold.
- **`validate_work.py`** runs the same baseline-anchor check and the same comparison when a baseline
  exists, reporting a tampered baseline as `fold: BROKEN …` and each divergence as an error with the
  field name; without a baseline it warns instead of passing silently.
- **README** documents the three verbs and the forward-only rule (see Limits: the row is required by
  `tests/test_surface.py`).

## Acceptance

| Criterion | Proof |
|---|---|
| `foldcheck` reports zero divergence on a database created after the snapshot | `test_foldcheck_reports_zero_divergence_after_a_snapshot` (snapshot → claim → red → `no divergence`, JSON `divergences: []`), and `test_a_database_copied_after_the_snapshot_reports_zero_divergence` (`VACUUM INTO` a second file, foldcheck on it exits 0) |
| it names a dropped field instead of deleting records | `test_a_dropped_field_is_named_and_the_record_is_not_deleted`: output names `field 'title' is missing from the registry (the log set it)`, the task keeps every other field and the registry still holds 4 tasks; `test_a_forged_field_is_named_with_both_values` names `status`/`revision` with `log 'ready'` and `registry 'accepted'`; `test_a_record_with_no_event_is_named` and `test_a_hand_deleted_edge_is_named` cover the record and edge shapes |
| a rebuild refuses to write without a `VACUUM INTO` restore point and a passing diff | `test_a_rebuild_needs_a_restore_point_and_a_passing_diff`: the dry run exits 1 and writes nothing; `--apply` writes a restore file that reopens with the pre-rebuild state, records it on the `rebuild` event and leaves `foldcheck` clean; `test_a_rebuild_refuses_to_delete_a_record_the_log_cannot_explain` exits 2 naming the ghost task, keeps it and writes no restore point; `test_an_applied_rebuild_with_nothing_to_repair_writes_nothing`; store-level `test_an_unlocked_snapshot_is_refused` proves a restore point is never overwritten |
| historical tasks arrive through the snapshot; there is no backfill | `test_historical_tasks_arrive_through_the_snapshot_and_nothing_is_backfilled`: pre-snapshot events carry no `state_delta` and are not rewritten, the snapshot body holds all four tasks, and the fold starts after `seq`. `test_foldcheck_refuses_without_a_snapshot` shows the log alone is not used (exit 2, "does not backfill") |

Supporting: `test_a_claim_carries_its_delta_and_the_attempt_does_not`,
`test_a_batch_mutation_puts_its_remaining_delta_on_the_last_event`,
`test_a_tampered_snapshot_body_is_reported`,
`test_a_baseline_whose_row_digest_was_forged_with_it_is_still_broken` (body **and** digest column
forged, still `BROKEN` because the event names the digest),
`test_a_snapshot_row_with_no_event_at_its_sequence_is_broken` (the reviewer's laundering path),
`test_the_validator_reports_a_tampered_baseline`,
`test_a_second_snapshot_refuses_to_bless_a_divergent_registry`,
`test_the_validator_reports_a_dropped_field`.

## Verification

```bash
cd capabilities/work
python3 -m unittest discover -s tests      # 296 tests, OK (2 skipped)
python3 -m unittest discover -s tests -p "test_fold.py"   # 20 tests, OK
python3 validate_work.py --today 2026-11-16 # same pre-existing error as the release, plus the new
                                            # "no fold baseline" warning until the live DB snapshots
./loom --help                              # snapshot, foldcheck, rebuild are routed engine verbs
```

The live registry has one error before this branch and after it: `midas/W-8: heading W-8 missing from
midas.program`, reproduced with the promoted release's own validator. Nothing here touches it.

A scratch database demonstrated the whole loop by hand:

```text
snapshot 3: 1 tasks, 0 edges, digest 9a462ea98aff…
# hand-edit: drop `title`, set status green
task demo/W-1: field 'status' differs — log 'red', registry 'green'
task demo/W-1: field 'title' is missing from the registry (the log set it)
foldcheck: 2 divergence(s) from snapshot 3
rebuild: repaired 2 divergence(s) from snapshot 3; restore point …/work.db.restore-…db
foldcheck: no divergence from snapshot 3 (2 state delta(s) over 4 event(s))
```

## Design decisions

- **A baseline, not a reconstruction.** Events before the snapshot are never read. Making the whole
  log self-describing would have meant rewriting history, which is exactly what the hash chain exists
  to prevent.
- **Field-level deltas, not whole bodies.** A whole-document delta would make every event large and
  would make "which field moved" the diff at read time instead of a fact in the log.
- **The delta is computed, never passed in.** The command mutates the registry dict; the store diffs
  what it read against what it is about to write. A command cannot forget to describe its change.
- **Batch attribution on the last event** matches the transaction: one event covers the batch it
  closes. The alternative was a synthetic state event per write, which doubles the log.
- **The rebuild event does not carry a delta**, for the same reason a restore is not a change.
- **The baseline is anchored by its event, not its row.** A digest stored beside the body it
  describes only proves internal consistency; storing it in the hash-chained event is what makes the
  body tamper-evident. Requiring an event at the baseline's `seq` also closes the second half of the
  attack, where a high `seq` would hide the deltas the fold never reads.
- **`unexplained` records fail the diff.** A task with no create event is evidence of a writer that
  bypassed the CLI; deleting it would destroy the evidence the check exists to surface.

## Limits

- **`PROTOCOL.md` is not updated.** The `state_delta` field, the snapshot baseline and the three
  commands belong in the event-log and storage sections there; the file is outside this task's write
  paths, so the contract currently lives in the `store.py`/`engine.py` docstrings, the README rows and
  this evidence. It should be promoted into `PROTOCOL.md` by whoever owns it.
- **`README.md` was edited from outside the declared write paths**, because `tests/test_surface.py`
  fails unless every CLI verb appears in its command table. Three table rows and one paragraph; no
  other prose changed. Flagged for the reviewer rather than left as a failing suite.
- **No baseline was written to the live registry.** The scheduler still runs the released engine,
  which does not write deltas; snapshotting now would freeze a baseline the old engine would
  immediately diverge from. The coordinator should run `./loom snapshot` **after** promoting this
  change, then `./loom foldcheck`.
- **A baseline can be blessed deliberately.** `snapshot --force --reason` accepts the current state as
  truth. That is a human decision with an audit trail, not backfill, but it is the one way to make a
  divergent registry look clean, so it is guarded and recorded.
- **Write cost.** Every transaction parses the stored document bodies to diff them. That is the same
  order as the existing differential save, and the delta is only built from documents that changed,
  but a very large registry pays it on each mutation.
- **No delta for `save()`/`migrate`.** A direct `save()` (fixtures, the file-home importer) writes
  state with no event and therefore no delta; `foldcheck` will report it if it happens after a
  snapshot. That is the intended diagnosis, not a supported path.

## Review lenses to apply

- **Event-log integrity.** Hash computed at flush after delta attachment; `read_events` including
  staged events; the checkpoint written only after COMMIT; the rebuild event deliberately delta-free.
- **The diff's honesty.** Does field-level comparison name everything a hand edit can do, and does
  refusing `unexplained` records actually preserve the evidence rather than block repair?
- **The restore point.** `VACUUM INTO` outside a transaction, reopened and count-checked, never
  overwritten, recorded on the event.
- **Operator paths.** Snapshot refusal on a divergent registry, the `--force --reason` escape hatch,
  and the "no baseline, no backfill" error.
