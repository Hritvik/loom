# Differential commits: write the documents a mutation changed

Status: accepted
Owner: engine maintainer
Date: 2026-11-15
Revised: 2026-11-15 after review 1718 — edge order fixed, durable-write reading stated
Task: loom/LOOM-11

## Context

`SqliteStore` accepted a whole registry from the CLI and wrote it back whole: `DELETE FROM tasks`,
`DELETE FROM boards`, `DELETE FROM edges`, `DELETE FROM claims`, then one `INSERT` per document.
On the live registry (213 tasks, 24 boards, 147 edges, 1.7k events) a single claim issued 395 write
statements and dirtied 241 KiB of WAL to change one task. Event dispatch is the case that makes
this untenable: it wants to advance a cursor per event, and every cursor advance would carry the
whole registry with it.

ADR 0002 called that cost "acceptable while the CLI is coarse" and named intent-level operations as
the migration path. This decision takes that step without changing the file format or the CLI
contract.

## Decision

`load()` records the serialised body of every document it read. A commit diffs the registry against
that record and writes only what differs:

- a changed document is `UPDATE`d in place, so it keeps its position in the registry order;
- a new document is appended with `INSERT`;
- a document absent from the registry is `DELETE`d;
- edges are diffed as a set of `(from, to, kind)` but read and inserted in order (see below);
- `meta` is written only when a value changed.

The row decides whether it exists (`UPDATE`, then `INSERT` if it matched nothing), so a stale cache
cannot lose or duplicate a document. A caller that assigns `registry` without loading first — the
`loom migrate --force` path — gets one read of the current rows before its first write, so stale
rows are still removed. The new baseline is adopted only after `COMMIT`: a rolled-back commit
leaves the store's memory exactly as it was, so a retry writes its change again.

Registry order is rowid order, for edges as for documents. `load()` reads edges into a list and
`_sync_edges` inserts along the registry list; the set is the difference, never the iteration order.
Order is part of the contract because `export` renders it and `genesis` digests `export_bytes`, and
a set-derived order changes with `PYTHONHASHSEED` — the first cut of this change did exactly that
and was caught in review (`evidence/LOOM-11.md`, revision after `8bacbf7`).

Nothing else moved. The event log, its hash chain, the external checkpoint, `snapshot()` and the
load-decide-commit CLI contract are unchanged; the event was and remains the audit chain.

## Measured

One-document commit against a copy of the live `~/.local/state/loom/work.db` (213 tasks, 24 boards,
147 edges), median of twelve. "Before" is the same tree at `31ef45e`. Both runs use the same
harness; the commit excluding `load` is the span the store itself owns.

| | before | after |
|---|---|---|
| document write statements per commit | 391 | 1 |
| WAL written per commit | 241 KiB | 16 KiB |
| commit, excluding `load` | 2.71 ms | 1.25 ms |
| commit, `load` included | 3.74 ms | 2.29 ms |

Synthetic registries built from real task bodies, same commit measured at three sizes:

| tasks | document writes before → after | WAL per commit | commit ms before → after |
|---|---|---|---|
| 10 | 48 → 1 | 48 → 16 KiB | 0.47 → 0.33 |
| 200 | 375 → 1 | 221 → 16 KiB | 2.51 → 1.15 |
| 1000 | 1175 → 1 | 507 → 16 KiB | 7.61 → 3.24 |

What reaches the disk is flat. What still grows is memory work: `load` (0.10 / 0.97 / 2.90 ms at
10 / 200 / 1000 tasks) and the change-detection pass (0.10 / 0.92 / 2.96 ms). Total commit latency
therefore still rises with registry size; the durable cost does not.

## Alternatives considered

- **Intent-level write API (`store.write_task(id)`) from every call site.** The engine mutates task
  dicts in place in ~60 places; each would have to declare what it touched. One missed declaration
  is a silently dropped write — a correctness hazard traded for a millisecond.
- **Dirty-tracking dict/list subclasses.** Precise and invisible to the engine, but the documents
  would stop being plain JSON types, and `yaml.safe_dump` in `export` refuses subclasses. The risk
  is in the export path, where it would show up as a user-facing failure.
- **`INSERT OR REPLACE` for changed documents.** SQLite deletes and re-inserts, so the task moves
  to the end of the registry order on every edit, and the YAML export becomes non-diffable.
- **Keep whole-registry writes and batch events only.** Leaves the amplitude in place; the WAL cost
  per cursor advance is what the dispatcher cannot pay.

## Consequences

- Commit amplitude follows the change set; the durable cost is flat in registry size. This is the
  reading this decision claims, and it is the one the event dispatcher pays per cursor advance:
  one statement and 16 KiB per event, not 391 and 241 KiB.
- Total commit latency is **not** flat. The change-detection pass is linear in registry size: one
  JSON serialisation per document, plus the `load` the CLI already performs. It is CPU, not I/O,
  and it disappears if the CLI ever stops handing over a whole registry. Anyone reading "commit
  cost does not grow linearly" as wall time should read it as a statement about durable writes.
- A document that is byte-identical after a round trip is not written, so format drift in `_dump`
  cannot cause a spurious write.
- Registry order is rowid order and no longer depends on process hash seed, so `export` and the
  `genesis` digest are reproducible. New rows still land at the end of their table: a document
  inserted mid-list in memory is written after the existing rows. Not reachable through a command
  (the CLI appends), but `migrate --force` into a populated database keeps the target's rowids, so
  exported order can differ from the source file's. A position column would fix it and would be
  stripped by `_upgrade`, which rebuilds any document table with columns past `id, body`.
- `docs/adr/0002-store-is-a-document-log.md` still says a whole-registry save rewrites every
  document. That paragraph is now stale and needs a maintainer edit; this task's write path is
  `capabilities/work`, so the ADR itself was left alone.
