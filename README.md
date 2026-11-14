# loom

Coordination engine for a portfolio of autonomous agents: one shared registry of work, with claims,
leases, roles, dependencies and a hash-chained audit log.

It exists so several agents — in different harnesses, at different times — can work one portfolio
without colliding, without a human dispatching tasks, and without trusting each other's memory.

## What it provides

| Surface | Purpose |
|---|---|
| `capabilities/work/loom` | the front door: `work`, `tick`, `workers`, `schedule`, `linear`, `logs` |
| `capabilities/work/task` | the CLI: claim, renew, release, takeover, transition, ask, inbox, search, dependents, graph, tick, decisions, migrate, export |
| `capabilities/work/mcp/` | MCP server, so the registry appears in an agent's tool list |
| `capabilities/work/a2a/` | A2A v1.0 façade for agents that speak it |
| `capabilities/work/validate_work.py` | schema, dependency, lease, epoch, review, event-chain and contract verification |
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

## Running unattended

`loom tick --apply` is one policy pass: reconcile launches whose process is gone, record reviews
that were approved but never applied, then fill the implementer and reviewer slots from the ranked
queue. An implementer runs in its own git worktree; a reviewer runs in a detached checkout at the
exact commit under review. Every launch is an event carrying its pid, role and revision.

The launchd job runs it every **120 seconds** with `--quiet`, so an idle pass prints nothing.
`loom schedule status` prints the interval, label, plist and last log lines.

Claims carry a **fencing epoch**: every claim and takeover increments it and the holder must present
it on each mutation, so a worker whose lease was taken over is refused even if it re-reads the
revision. Takeover also waits out a **lock-delay** (two minutes), because a git branch cannot
validate a token. Authority is a live lease, not a recorded claim.

## Boundaries

This repository holds the coordination engine and nothing else. It does not hold portfolio planning
content — maps, plans, specs and evidence live in a private planning repository — and it does not
hold task state, which lives in the database. Interaction contracts live in
your contracts registry (`$WORK_CONTRACTS`, your own repository); the validator checks them,
it does not own them.

## Licence

No licence has been chosen yet; all rights reserved.
