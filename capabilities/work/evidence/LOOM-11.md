# Evidence — LOOM-11 write only the documents a mutation changes

Repository: `~/repositories/loom`
Status: green, awaiting independent review

## Delivered

- `capabilities/work/store.py`: `SqliteStore` keeps the body it read for every document and commits
  the difference. A changed document is `UPDATE`d in place (keeping its registry position), a new
  one is `INSERT`ed, a document dropped from the registry is `DELETE`d, edges are diffed as a set,
  and `meta` is written only when a value changes. The baseline is adopted only after `COMMIT`, and
  a caller that assigns `registry` without loading first gets one read of the rows on disk, which
  keeps `loom migrate --force` honest.
- `capabilities/work/tests/test_store.py`: 9 tests covering the write amplitude, a claim touching
  one row, a no-op save, registry order after a targeted write, deletion, an edge change, a
  registry assigned without a load, a stale second store, and a retry after a rolled-back commit.
- `capabilities/work/decisions/differential-commits-001.md`: the decision, the numbers, and why the
  intent-level and dirty-tracking alternatives were rejected.
- `capabilities/work/README.md`: the storage section now says the commit is differential.

Unchanged on purpose: the `events` table, `event_bytes`, the hash chain, `work.db.head`, `snapshot()`
and the load-decide-commit CLI contract. No schema change, no file-format change.

## Acceptance

| Criterion | Evidence |
|---|---|
| A claim rewrites only the changed task document | `test_a_claim_rewrites_only_the_changed_task_document` traces the connection: exactly one statement, `UPDATE tasks`, and zero against `boards`, `edges` and `claims` |
| Commit cost does not grow linearly with the number of tasks | statements/commit is 2 at 10, 213 and 1000 tasks; WAL per commit is 16, 16 and 20 KiB against 48/125/439 before |
| The event log, its chain and the checkpoint are unchanged in shape | no change to the events table or `_append_event`; `test_a_batch_transaction_anchors_its_last_event`, `test_events_verify_detects_a_rewritten_event` and `loom events --verify` still pass |

## Measured

One-document commit against a copy of the live `~/.local/state/loom/work.db`
(213 tasks, 24 boards, 147 edges), median of twelve commits. Baseline is the same tree at `HEAD`
before this branch's change.

| | before | after |
|---|---|---|
| write statements per commit | 395 | 2 |
| WAL written per commit | 241 KiB | 16 KiB |
| write path only | 2.38 ms | 0.97 ms |
| whole commit, load included | 4.07 ms | 2.36 ms |

Synthetic registries, same commit measured at three sizes:

| tasks | statements before | after | WAL before | after |
|---|---|---|---|---|
| 10 | 28 | 2 | 48 KiB | 16 KiB |
| 213 | 434 | 2 | 125 KiB | 16 KiB |
| 1000 | 2008 | 2 | 439 KiB | 16 KiB |

Write-path time still grows with the registry (0.05 / 0.59 / 2.73 ms): detecting a change means one
JSON serialisation per document, because the CLI hands the store a whole registry and the engine
mutates the dicts it was given in place. That pass is CPU, not I/O, and costs about 0.4 ms per
200 KiB. What no longer grows is what reaches the disk.

## Verification

```
python3 -m unittest discover -s tests   # Ran 234 tests ... OK
python3 validate_work.py                # PASS
```

`validate_work.py` warnings are pre-existing and accepted elsewhere (board readiness, one backwards
event timestamp, 52 evidence-only integrations accepted by `decisions/integration-receipts-001.md`).

## Limits

- `docs/adr/0002-store-is-a-document-log.md` still describes the whole-registry rewrite as the cost
  of the coarse CLI contract. This task's write path is `capabilities/work`, so the ADR was not
  edited; it needs a maintainer follow-up. `decisions/differential-commits-001.md` records the
  decision in the meantime.
- The change-detection pass is linear in registry size. Removing it needs either an intent-level
  write API (every engine call site declares what it touched) or dirty-tracking documents (which
  break `yaml.safe_dump` in `export`); both were rejected as riskier than the millisecond they save.
- `load()` still reads every document, and every command still materialises the whole registry. This
  task made the *write* proportional to the change; it did not make the read proportional to it.
