# 0001 — State leaves Git; loom is the engine

Date: 2026-11-09
Status: accepted. Steps 1–4 implemented; the file backend is retired.

## Context

The coordination layer keeps its authoritative state in two tracked files: `registry.yaml`
(116 KB, 168 tasks, 127 edges) and `events.ndjson` (248 KB, 707 events). Every mutation rewrites
the registry, so state changes are Git changes.

That produced two failures this month:

- Concurrent agents sweeping each other's in-flight edits, because `git add -A` cannot tell state
  from code. A pre-commit hook now polices it; the class of bug remains.
- Drift between the registry and the packet boards, because both are mutable files that must be
  reconciled by hand.

Separately, the repository cannot be published while it contains task titles, decisions and
evidence pointers for a private portfolio. Publishing was the goal that surfaced the problem.

## Decision

Split into three parts:

| Part | Contents | Visibility |
|---|---|---|
| **loom** (engine) | CLI, schema, migrations, MCP, A2A, validator, tests, docs | public |
| **state** | tasks, edges, claims, events, launches | private database |
| **content** | `wayfinder/`, `plans/`, `specs/`, `capabilities/` | private |

- **SQLite first.** Transactions and `UPDATE … WHERE revision = ?` without a server. Postgres only
  when agents run on more than one host.
- **The hash chain and revision CAS survive.** `events` keeps `prev`/`hash`; every mutation stays
  conditional on `revision`. A database gives ordering, not tamper-evidence.
- **Boards and Linear become projections.** Today a packet board is both a copy of the registry and
  something `board sync` writes. After the split there is one truth and two projectors.
- **The repository is named `loom`.**

## Migration

1. `Store` interface with two backends: `files` (today) and `sqlite`. The existing suite becomes the
   conformance test for both.
2. `task migrate` imports `registry.yaml` + `events.ndjson`, verifying the chain.
3. `task export --format yaml` restores reviewability — the thing lost by leaving Git.
4. Switch the default, keep `files` for one release.
5. Rename the repository and publish the engine.

## Consequences

- State stops being diffable in Git; `export` is the replacement, and the event log remains the
  audit.
- 54 files across 12 repositories reference `portfolio-planning` by path. Rename them **once**, as
  part of the split, not before it.
- Planning content must move out before publishing, or the public repository is half engine and half
  product strategy.
- `validate_work.py` loses its board-drift checks and gains schema constraints plus a consistency
  checker.

## Alternatives rejected

- **Keep files, move to a private repository.** Preserves diffability, loses transactional CAS and
  keeps the sweep hazard. Revisit only if the engine never needs a second host.
- **Postgres now.** A server, backups and containers for a single-machine workload.
- **Publish without the split.** Would expose private portfolio state.
