# loom — agent orientation

This repository is the coordination engine. It holds the CLI, the store, the MCP and A2A
surfaces, the validator and the tests — not the interaction contracts, which live in
`~/repositories/portfolio-contracts`.

## Read first

1. `capabilities/work/AGENT-GUIDE.md` — the loop, the enforced rules, escalation.
2. `capabilities/work/PROTOCOL.md` — state model, claims, leases, review.
3. `docs/adr/` — why state lives in SQLite and why the engine is separate from planning.

## Rules

- **State is not in this repository.** It lives at `~/.local/state/loom/work.db`. Do not commit a
  database, and do not recreate `registry.yaml`.
- **Every mutation goes through `./task`.** It owns revisions, leases, gates and the event chain.
- **`FileStore` is an importer.** It exists so `task migrate` can read a pre-SQLite work home; it is
  not a runtime backend.
- **The hash chain is load-bearing.** `events.prev` and `events.hash` are what make the log
  tamper-evident; never write events outside the store.
- **Commands hold a lock.** SQLite makes a write durable, not atomic across read-decide-write. The
  sidecar lock is what stops two claims both succeeding.
- **Planning content stays out.** Maps, plans, specs and evidence belong to the private planning
  repository.

## Commands

```bash
git config core.hooksPath .githooks     # once per clone
cd capabilities/work
./task list --ready
python3 validate_work.py
python3 -m unittest discover -s tests
```
