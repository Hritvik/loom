# 0002 — The store is a document log with an atomic commit and an external head

Status: accepted (2026-11-14)

## Context

The CLI works in whole-registry units: it loads the registry, decides, mutates a task dict, then
writes the registry and appends an audit event. SQLite was introduced to make that durable, but the
first version left four gaps that a review called out:

1. `save()` and `append_event()` were separate transactions, so a crash between them could leave
   state without its audit record.
2. The sidecar lock was a convention: the CLI took it, but the store did not require it.
3. Task rows duplicated fields that also lived in the JSON body, so the two representations could
   disagree, and every save rewrote both.
4. The hash chain had no anchor outside the database it protects.

## Decisions

### One commit for state and event

`Store.commit(**event)` writes the registry and appends the event in one transaction. `Store.transaction()`
covers a batch that appends several events (reconciliation, board adoption). `SqliteStore` implements
both with `BEGIN IMMEDIATE` / `COMMIT`; `FileStore` keeps the legacy sequential behaviour and is only
the importer for `loom migrate`.

Every write method calls `_require_lock()`. The CLI takes `store.lock()` around the whole command, so
the read-decide-write cycle is serialised; a direct store user that forgets the lock now gets a
`WorkError` instead of a silent lost update.

### Tasks and boards are documents

`tasks` is `(id, body)` and `boards` is `(id, body)`. The body is the authoritative record. Status and
program are indexed with SQLite expression indexes on `json_extract(body, ...)`, not stored twice.
Edges stay relational because the graph is queried as edges, and `meta` stays key/value.

The cost is that a whole-registry save rewrites every document. That is acceptable while the CLI is
coarse; the migration path is intent-level operations that update one row, without changing the file
format. The benefit is that there is no second representation to drift.

### Reads are snapshots

`Store.snapshot()` reads the registry and the event log inside one read transaction. The MCP server
caches one snapshot per request and refreshes it after a mutation; the A2A façade passes the same
snapshot through task and history assembly; `linear_sync` takes one snapshot under the store lock.
A response therefore cannot combine a new task status with an earlier event count.

### The audit head lives outside the database

Every commit writes `work.db.head` with the last sequence and hash. `loom events --verify` compares
the chain to that head. If they differ, either the database was rewritten or a commit reached the
database but not the checkpoint; `--reanchor` rewrites the head only after the chain verifies.

**Threat model.** This is tamper-evident against accidental corruption and edits that do not also
rewrite the checkpoint. It is not a defence against a privileged writer who can modify both the
database and the head file. A cryptographic anchor would need a key outside the writer's reach, or
an append-only external store; neither exists in this single-user setup. The chain plus the head is
the honest boundary, and the docs say so.

## Consequences

- A mutation either leaves both state and its event, or neither.
- A caller that bypasses the CLI must take the lock; the store enforces it.
- The database has one representation of a task; the JSON body is it.
- A rewritten log is detectable unless the writer also rewrites the head.
- Whole-registry writes remain; replacing them with intent-level operations is the next step and
  does not require another file-format change.

## Alternatives considered

- **Event first, state second.** A crash would leave an event for a mutation that never committed.
  Worse than the original order.
- **Structured columns authoritative.** Every field would need a column and a migration; the body
  would become a cache. More surface, no gain for a whole-registry CLI.
- **No external head.** The chain would only detect edits that forgot to recompute the hashes, and
  a rewrite of the database would be invisible.
- **A signed export with a local key.** The key sits beside the database, so it adds ceremony
  without a stronger boundary.
