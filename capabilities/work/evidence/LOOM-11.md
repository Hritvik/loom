# Evidence — LOOM-11 write only the documents a mutation changes

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-11`
Status: green, awaiting review (second round; review 1718 requested changes on `8bacbf7`)

## This revision answers review 1718

The reviewer found one undisclosed regression and asked for one thing to be stated rather than
implied. Both are addressed; the acceptance question is put back to the reviewer in the ask rather
than settled here.

**Fixed — edge order no longer depends on the process.** `load()` built `data["edges"]` by iterating
a set comprehension, so the registry's edge order was randomised by `PYTHONHASHSEED`. `export`
renders that order (`export_bytes` is "a stable rendering of the registry, used for genesis digests
and review") and `genesis` records `sha256(export_bytes)`, so an unchanged database exported a
different file, and recorded a different genesis digest, in every process. Reproduced on `8bacbf7`
with the reviewer's fixture — eight edges, four seeds, four digests:

```
0 075aae60c9281990 ['a/T-7', 'a/T-1', 'a/T-2', 'a/T-3']
1 42887f59cc3ba2b1 ['a/T-3', 'a/T-1', 'a/T-5', 'a/T-4']
2 4728dd6a26a6a597 ['a/T-1', 'a/T-3', 'a/T-6', 'a/T-4']
3 5fe84965992b2d7f ['a/T-1', 'a/T-0', 'a/T-4', 'a/T-7']
```

`load()` now reads edges in `rowid` order into a list — the same order tasks, boards and claims
already used — and `_stored["edges"]` keeps a set for membership only. `_sync_edges` still diffs as
a set but walks the registry list when inserting, so a newly added edge keeps the position the
command gave it instead of the arbitrary set order. The same fixture now yields one digest under
every seed (`6061481967f81d56`), and the live registry (147 edges) exports identical bytes under
`PYTHONHASHSEED=0` and `PYTHONHASHSEED=7` (`c7114604ad9c14ce`).

Three regression tests were added and confirmed to fail on `8bacbf7` and pass on this commit:

- `test_edge_order_follows_the_registry_and_not_a_set` — the loaded edge list matches registry
  order, and a no-op save leaves `export_bytes` byte-identical.
- `test_edge_order_survives_a_reload_in_list_order` — edges added at the head of the registry land
  after the existing rows, in registry order, not in hash order.
- `test_export_bytes_do_not_depend_on_the_process` — four fresh interpreters with different
  `PYTHONHASHSEED` values must produce the same digest for one database.

**Stated — acceptance item 2 is met on the durable-write reading, not on the total-latency
reading.** See "Acceptance" below; the interpretation is stated, not implied by a table.

## Delivered

- `capabilities/work/store.py`: `SqliteStore` keeps the body it read for every document and commits
  the difference. A changed document is `UPDATE`d in place (keeping its registry position), a new
  one is `INSERT`ed, a document dropped from the registry is `DELETE`d, edges are diffed as a set
  and inserted in registry order, `meta` is written only when a value changes, and edges are read
  in rowid order so the exported registry is stable across processes. The baseline is adopted only
  after `COMMIT`, and a caller that assigns `registry` without loading first gets one read of the
  rows on disk, which keeps `loom migrate --force` honest.
- `capabilities/work/tests/test_store.py`: 12 tests. The nine from the first round (write
  amplitude, a claim touching one row, a no-op save, registry order after a targeted write,
  deletion, an edge change, a registry assigned without a load, a stale second store, a retry after
  a rolled-back commit) plus the three edge-order tests above.
- `capabilities/work/decisions/differential-commits-001.md`: the decision, the numbers, the
  ordering rule, and why the intent-level and dirty-tracking alternatives were rejected.
- `capabilities/work/README.md`: the storage section says the commit is differential and that the
  exported order is rowid order.

Unchanged on purpose: the `events` table, `event_bytes`, the hash chain, `work.db.head`,
`snapshot()` and the load-decide-commit CLI contract. No schema change, no file-format change.

## Acceptance

| Criterion | Evidence |
|---|---|
| A claim rewrites only the changed task document | `test_a_claim_rewrites_only_the_changed_task_document` traces the connection: exactly one statement, `UPDATE tasks`, and zero against `boards`, `edges` and `claims` |
| Commit cost does not grow linearly with the number of tasks | **Met for what a commit writes, not for total commit latency.** One document statement and 16 KiB of WAL per commit at 10, 200 and 1000 tasks (375 statements / 221 KiB and 1175 statements / 507 KiB before). Total commit time still grows — 0.33 → 1.15 → 3.24 ms — because `load` and the change-detection pass are both linear in registry size; that cost is CPU in memory, not I/O, and it is bounded below by the unchanged CLI contract, which hands the store a whole registry. The dispatcher pays the flat figure per cursor advance, which is what the goal names. See "Limits" for what the literal reading would need |
| The event log, its chain and the checkpoint are unchanged in shape | no change to the events table or `_append_event`; `loom events --verify` on the live registry prints `event chain OK`; `test_a_batch_transaction_anchors_its_last_event`, `test_events_verify_detects_a_rewritten_event` and `test_events_reanchor_repairs_a_stale_checkpoint` pass |

## Measured

Method: a copy of the live database is read for one real task body, 24 real boards and the task
list; a synthetic registry of N tasks is built from them (147 edges, no claims) and saved; then one
task's `status` and `revision` are changed and committed with an event, twelve times, median
reported. "Before" is the base module from `31ef45e`; "after" is this commit. Both run in one
process against the same registry shape. `ms` is the commit itself, excluding the `load` the
command performs before it.

| tasks | writes before | writes after | WAL before | WAL after | ms before | ms after |
|---|---|---|---|---|---|---|
| 10 | 48 | 1 | 48 KiB | 16 KiB | 0.47 | 0.33 |
| 200 | 375 | 1 | 221 KiB | 16 KiB | 2.51 | 1.15 |
| 1000 | 1175 | 1 | 507 KiB | 16 KiB | 7.61 | 3.24 |

Where the remaining milliseconds go at 10 / 200 / 1000 tasks (median of twelve): `load` 0.10 / 0.97
/ 2.90 ms; a save with nothing changed — the change-detection pass and its statements-free commit —
0.10 / 0.92 / 2.96 ms. The durable write is one `UPDATE` and 16 KiB of WAL at every size.

Against the live registry (213 tasks, 24 boards, 147 edges), a one-document commit issues two
statements — one `UPDATE tasks` and the event `INSERT` — and 16 KiB of WAL, where the base module
issued 395 statements and 241 KiB.

## Verification

```
python3 -m unittest discover -s tests   # Ran 237 tests ... OK
python3 validate_work.py                # PASS
./loom events --verify                  # event chain OK
```

`validate_work.py` warnings are pre-existing and accepted elsewhere (board readiness, one backwards
event timestamp at line 1040, 52 evidence-only integrations accepted by
`decisions/integration-receipts-001.md`). The three new tests were run against `8bacbf7` with the
fix reverted: all three fail there.

## Limits

- **The change-detection pass is linear in registry size**, and so is `load`. Removing the former
  needs either an intent-level write API (every engine call site declares what it touched; ~60
  call sites mutate task dicts in place, and one missed declaration is a silently dropped write) or
  dirty-tracking documents (which break `yaml.safe_dump` in `export`). Both were rejected as
  riskier than the milliseconds they save. The literal reading of acceptance item 2 — total commit
  cost — is therefore not satisfied; it cannot be without changing the CLI contract, which this
  task deliberately kept.
- **A document inserted mid-list in memory is written at the end on disk.** Registry order is rowid
  order, and a new row takes the next rowid. The CLI only ever appends, so this is not reachable
  through a command, but `migrate --force` into a populated database keeps the old rowids, so the
  exported order can differ from the source file's. Fixing it needs a position column, which
  `_upgrade` would strip again on the next open (it rebuilds any document table that has columns
  beyond `id, body`), so it is a schema decision, not a local fix.
- `docs/adr/0002-store-is-a-document-log.md` still describes the whole-registry rewrite as the cost
  of the coarse CLI contract. This task's write path is `capabilities/work`, so the ADR was not
  edited; it needs a maintainer follow-up. `decisions/differential-commits-001.md` records the
  decision in the meantime.
- `load()` still reads every document, and every command still materialises the whole registry.
  This task made the *write* proportional to the change; it did not make the read proportional to
  it.
