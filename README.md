# loom

Coordination engine for a portfolio of autonomous agents: one shared registry of work, with claims,
leases, roles, dependencies and a hash-chained audit log.

It exists so several agents — in different harnesses, at different times — can work one portfolio
without colliding, without a human dispatching tasks, and without trusting each other's memory.

## What it provides

| Surface | Purpose |
|---|---|
| `capabilities/work/task` | the CLI: claim, renew, release, takeover, transition, ask, inbox, search, dependents, graph, tick, decisions, migrate, export |
| `capabilities/work/mcp/` | MCP server, so the registry appears in an agent's tool list |
| `capabilities/work/a2a/` | A2A v1.0 façade for agents that speak it |
| `capabilities/work/validate_work.py` | schema, dependency, lease, claim, event-chain and contract verification |
| `docs/adr/` | decisions, including why state left git |

## Install

```bash
git clone https://github.com/Hritvik/loom.git ~/repositories/loom
cd ~/repositories/loom
git config core.hooksPath .githooks          # the coordination pre-commit guard
python3 -m unittest discover -s capabilities/work/tests
```

State lives in SQLite at `~/.local/state/loom/work.db`, outside this repository. Point the CLI
elsewhere with `--db` or `WORK_DB`.

## Use

```bash
cd capabilities/work
./task list --ready          # claimable work, ranked by what it unblocks
./task decisions             # what needs the owner, and nothing else
./task graph --open          # the dependency picture
python3 validate_work.py     # is the registry healthy?
```

Agents working in a product repository should read
[`capabilities/work/AGENT-GUIDE.md`](capabilities/work/AGENT-GUIDE.md) first.

## Boundaries

This repository holds the coordination engine and nothing else. It does not hold portfolio planning
content — maps, plans, specs and evidence live in a private planning repository — and it does not
hold task state, which lives in the database. Interaction contracts live in
`portfolio-contracts` (your own repository); the validator checks them,
it does not own them.

## Licence

No licence has been chosen yet; all rights reserved.
