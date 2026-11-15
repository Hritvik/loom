# Differential commits: write the documents a mutation changed

Status: accepted
Owner: engine maintainer
Date: 2026-11-15
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
- edges are diffed as a set of `(from, to, kind)`;
- `meta` is written only when a value changed.

The row decides whether it exists (`UPDATE`, then `INSERT` if it matched nothing), so a stale cache
cannot lose or duplicate a document. A caller that assigns `registry` without loading first — the
`loom migrate --force` path — gets one read of the current rows before its first write, so stale
rows are still removed. The new baseline is adopted only after `COMMIT`: a rolled-back commit
leaves the store's memory exactly as it was, so a retry writes its change again.

Nothing else moved. The event log, its hash chain, the external checkpoint, `snapshot()` and the
load-decide-commit CLI contract are unchanged; the event was and remains the audit chain.

## Measured on the live registry

One-document commit, median of twelve, against a copy of `~/.local/state/loom/work.db`:

| | before | after |
|---|---|---|
| write statements per commit | 395 | 2 |
| WAL written per commit | 241 KiB | 16 KiB |
| write path | 2.38 ms | 0.97 ms |
| whole commit, load included | 4.07 ms | 2.36 ms |

Synthetic registries (10 / 213 / 1000 tasks) show the same flat write with a linear `load`:

| tasks | statements | WAL per commit | write path |
|---|---|---|---|
| 10 | 2 / 2 | 48 → 16 KiB | 0.10 → 0.05 ms |
| 213 | 434 → 2 | 125 → 16 KiB | 1.67 → 0.59 ms |
| 1000 | 2008 → 2 | 439 → 16 KiB | 7.93 → 2.73 ms |

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

- Commit amplitude follows the change set; the durable cost is flat in registry size.
- The change-detection pass is still linear in registry size: one JSON serialisation per document,
  ~0.4 ms per 200 KiB, on top of the `load` the CLI already performs. It is CPU, not I/O, and it
  disappears if the CLI ever stops handing over a whole registry.
- A document that is byte-identical after a round trip is not written, so format drift in `_dump`
  cannot cause a spurious write.
- `docs/adr/0002-store-is-a-document-log.md` still says a whole-registry save rewrites every
  document. That paragraph is now stale and needs a maintainer edit; this task's write path is
  `capabilities/work`, so the ADR itself was left alone.
