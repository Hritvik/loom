# Work coordination

Central home for planning and coordinating work across the portfolio. Task *bodies* stay in the
owning program document; this layer owns **identity, dependencies, claims, leases, revisions and
reconciliation**.

## Layout

| Path | Purpose |
|---|---|
| `work-home.yaml` | Marks this directory as the authoritative coordination home |
| `store.py` | Storage: the SQLite backend, the interface, and the legacy file importer |
| `~/.local/state/loom/work.db` | Live state: boards, tasks, edges, claims and the hash-chained events |
| `task` | The CLI agents use to create, claim, renew, release, take over, transition, message and reconcile work |
| `AGENT-GUIDE.md` | The briefing to point a product agent at: the loop, the enforced rules, discovery and escalation |
| `validate_work.py` | Schema, drift, dependency, lease, claim and event-chain verification |
| `schemas/task.schema.json` | The central task contract |
| `decisions/` | Reconciliation decisions |
| `a2a/` | A2A v1.0 façade: Agent Card, JSON-RPC, read and claim operations |
| `mcp/` | MCP stdio server: registry tools that appear in an agent's tool list |
| `programs/` | Cross-service integration map: journeys, ownership, contracts, gates and startable slices |
| `tests/` | CLI, validator, A2A and MCP tests on disposable work homes |

## CLI

Every mutation goes through `./task`; MCP and A2A delegate to it, so the guarantees below are
identical on all three surfaces.

| Command | Purpose |
|---|---|
| `create` | Author a central task on a registered board |
| `list` | Filter by board, state, readiness or ownership; `--ready` ranks by downstream work |
| `search` | Find tasks by text without knowing the namespace |
| `dependents` | Who is waiting on a task, directly and transitively |
| `graph` | Render the dependency graph: `text`, `mermaid`, `dot` or standalone `html` |
| `decisions` | What needs the owner: human-blocked tasks and questions addressed to them |
| `tick` | Policy preview: what would be claimed and launched under WIP limits |
| `migrate` | Copy a file work home into SQLite, verifying the event chain |
| `export` | Render the store as YAML or JSON, replacing diffable state in git |
| `show` | One task with dependencies, claim, evidence and review |
| `claim` | Take a lease with compare-and-swap on revision |
| `renew` | Heartbeat and extend a live lease |
| `release` | Return a claimed task to the ready pool with a reason |
| `takeover` | Take over an expired lease with a recorded reason |
| `transition` | Move through the state machine, enforcing evidence and review |
| `ask` | Ask another agent a question about a task |
| `inbox` | List unanswered questions addressed to an agent |
| `answer` | Answer a question addressed to you, once |
| `events` | Read or verify the hash-chained audit log |
| `edge` | Inspect or edit cross-task dependency edges |
| `board` | List, add, remove, sync or import boards |
| `reconcile` | Compare or adopt board statuses for imported tasks |
| `genesis` | Write the single bootstrap event |

## Storage

State lives in SQLite at `~/.local/state/loom/work.db`. The database is outside every repository,
so state is not a git artifact any more. The file backend is retired; `FileStore` survives only as
the importer `task migrate` reads a pre-SQLite work home with.

```bash
./task list --ready                                                    # default store
./task migrate --from <old work home> --to ~/.local/state/loom/work.db # import a file home
./task export --out registry.yaml                                      # snapshot for review
```

The exported file is a snapshot for review, not the source of truth. `validate_work.py` reads the
same store, so it validates live state.

## Three repositories

| Repository | Holds | Visibility |
|---|---|---|
| **loom** (this one) | the engine: CLI, store, MCP, A2A, validator, templates, examples | public |
| **content** | the portfolio's own artefacts: maps, plans, specs, capability stewardship, evidence, memory | private |
| **contracts** | the wire formats services exchange, with fixtures and consumer pins | public |

loom knows the *kinds*; a content repository holds the *instances*. Point it at yours:

```bash
export WORK_CONTENT_HOME=~/repositories/portfolio-planning   # your content repository
export WORK_CONTRACTS=~/repositories/portfolio-contracts     # your contract registry
export WORK_DB=~/.local/state/loom/work.db                   # the task database
```

`docs/artifacts.md` lists every kind, where it lives, and how it is promoted.

## Authority

- **Central registry** — task identity, program, cross-repo dependencies, write-path conflicts,
  claims/leases and the revision counter. Authoritative once a task has been adopted
  (`revision >= 1`, i.e. mutated through `./task`).
- **Repo boards** — packet body, detailed scope, evidence files, reviews and execution checkpoints.
- **Imported tasks** (`revision == 0`, `status_source: board`) — the repo board is authoritative;
  use `reconcile` to adopt a status change.
- **Program documents** (`work-items.md`) — the human narrative remains the authority for headings;
  the registry mirrors their status.

Status drift is an error for imported tasks and a warning for centrally-managed ones. `board sync`
materialises the registry into packet boards; it never deletes packets and never rewrites packet
scope, evidence or write paths.

## Pick up work

```bash
export WORK_AGENT=opencode/iam-agent-3      # identity recorded in every event

./task list --ready                          # claimable: ready + deps met + no path conflict
./task show iam/W-12
./task claim iam/W-12 --revision 0 --ttl 45m
```

`claim` is a compare-and-swap: the `--revision` you pass must equal the registry revision. A stale
revision fails, a live lease held by another agent fails, and overlapping `write_paths` fail.

```bash
./task renew   iam/W-12 --revision 1 --ttl 45m
./task release iam/W-12 --revision 2 --reason "handing back for review"
./task takeover iam/W-12 --revision 3 --reason "lease expired; agent went silent"
```

Heartbeat with `renew` before the lease expires. `takeover` only works after expiry and always
records the previous agent and a reason. Mutating another agent's live claim is impossible.

## Move work through the state machine

```bash
./task transition iam/W-12 --revision 1 --to red
./task transition iam/W-12 --revision 2 --to green
./task transition iam/W-12 --revision 3 --to reviewed \
  --reviewer reviewer-7 --decision approve
./task transition iam/W-12 --revision 4 --to integrated --evidence evidence/W-12.md
./task transition iam/W-12 --revision 5 --to accepted   --evidence evidence/W-12.md
```

Rules enforced by the CLI:

- `reviewed` requires an independent reviewer (`--reviewer`, `--decision approve`). The reviewer
  cannot be the acting agent, the live claim holder, or any agent that previously claimed the task —
  releasing a lease does not let the implementer approve its own work.
- `integrated` and `accepted` require evidence.
- `accepted` requires every dependency to be accepted/integrated.
- Illegal transitions are refused; `claim` is the only way to enter `claimed`.
- Reviewing clears the implementation claim.

## Create work centrally

New work starts here, not in a repository.

```bash
./task create --id iam/W-15 --board iam.program --title "…" --goal "…" \
  --status ready --depends-on ums/W-18 --write-path src/main/java/...
```

Then add the detailed work-item section to the owning document (program) or materialise the packet:

```bash
./task board sync  --board dutch.ref.w12
./task board sync  --board dutch.ref.w12 --check   # CI: non-zero if stale
./task board import --board iam.w07 --program iam  # adopt existing packets
```

## A2A façade

Agents that speak A2A can use the same registry without the CLI:

```bash
python3 a2a/server.py --port 8787
curl -s http://127.0.0.1:8787/.well-known/agent-card.json
```

`ListTasks`, `GetTask`, `SendMessage` (claim/renew/release/takeover/transition) and `CancelTask` are
supported; mutations are delegated to `./task`, so CAS, leases and audit are identical. The card
declares no streaming/push/extended-card support and implements no authentication; it binds
loopback by default. See [`a2a/README.md`](a2a/README.md).

## MCP discovery

Agents discover the registry from their tool list, not from a document:

```bash
python3 mcp/server.py --print-config opencode     # or claude / codex
```

Tools: `work_status`, `work_list`, `work_get`, `work_claim`, `work_renew`, `work_release`,
`work_transition`. `work_status` is described as the pre-planning entry point; mutations delegate
to `./task`, so the guarantees are identical. The server infers `repo` from the workspace path and
`agent` as `<repo>-agent`, so one global registration works in every repository. See
[`mcp/README.md`](mcp/README.md).

## Integration map

`programs/identity-platform-integration.yaml` is the cross-service view that the per-repo work items
do not carry: who owns which contract, which task appears in which journey, and which slice an agent
can start. It links to real task IDs and is checked by `validate_work.py`.

```bash
./task edge list        # dependency edges between tasks
```

## Reconcile

```bash
./task reconcile --board dutch.ref.w12                         # report drift, exit 1
./task reconcile --board dutch.ref.w12 --apply --reason "board authoritative"
```

`--apply` adopts the board status for imported/board-sourced tasks and writes a `reconcile` event.
Never silently overwrite another agent; record a reason.

## Audit and verify

```bash
./task events --verify                 # hash chain + terminal state vs registry
./task events --task iam/W-12
python3 validate_work.py               # full cross-repo check
python3 -m unittest discover -s tests -v   # 56 tests across CLI, validator, A2A, MCP, surface and plans
```

`validate_work.py` checks: task schema, duplicate IDs, board/status drift, unregistered packets,
dependency cycles and readiness, unknown edges/dependencies, active leases (expiry, base revision,
heartbeat ordering), shared-artifact ownership conflicts, cross-repo write-path overlap, the event
chain's sequence, hashes, timestamps and terminal state, and the capability-steward registry beside
this home (artifact paths, owner ids, dormant owners).

## Messages

Claims say who is working; messages say what is blocking. A question is an event, not a chat
message, so it survives session ends and is attributable.

```bash
./task ask dutch-ref/W12-R --to opencode/reviewer --question "Who owns the privacy review?" \
  --needed-by 2026-11-07 --agent opencode/dutch-agent
./task inbox --agent opencode/reviewer          # open questions only; --all includes answered
./task answer dutch-ref/W12-R --question 454 --decision "I do, starting Monday" \
  --agent opencode/reviewer
```

- `ask` appends an `ask` event and **does not mutate the task**: it copies the current revision and
  status so the event chain still verifies against the registry.
- `inbox` shows unanswered questions addressed to the agent, with the task, asker and needed-by.
- `answer` appends an `answer` event linked by `in_reply_to`; only the addressee may answer, and only
  once. Answers are facts on the log, not state changes.
- MCP equivalents: `work_inbox`, `work_ask`, `work_answer`.
- A handoff is an `ask` whose question states the decision needed; the receiving agent answers with
  the outcome. Do not release a lease just to signal that a question is outstanding.

## Discovery

Work is found by question, not by remembering a namespace. Every view is computed from the registry
and edges at call time; nothing is cached.

```bash
./task search avatar                      # id, title, goal, program, board, write paths
./task search "association" --limit 5
./task dependents ums/W-18                # who is blocked on it, directly and transitively
./task graph --open --format html         # the whole picture, written to task-graph.html
./task graph --task ums/W-18              # one chain: its dependencies and dependents
./task list --ready                       # ranked by how much downstream work each releases
./task list --awaiting human              # decisions only the owner can make
./task show dutch-ref/W12-R               # includes a `blocks:` line
```

- `--ready` sorts by transitive downstream weight and prints `+N downstream`, so a task that
  unblocks a chain outranks one that unblocks nothing.
- `blocked` records who can resolve it: `--needs human` (the owner decides) or `agent` (default).
  `--awaiting human` is the owner's queue; it is not the same wait as an agent blocking on an agent.
- `search` requires every term to match somewhere in the task; results are ranked by field weight
  (id, then title, then goal, then paths).
- `graph` draws the dependency graph: `--format text` for the terminal (blocked tasks first, then
  chains), `mermaid` or `dot` for a diagram tool, `html` for a standalone file with zoom. Filter
  with `--open`, `--program`, `--board`, or `--task <id>` for one chain.
- `board titles` backfills missing task titles from program-board headings, so a task registered
  without a title is still findable.

## Rules

- IDs are namespaced (`iam/W-12`, `ums/W-6`, `dutch-ref/W11-A`) and are never renumbered or reused.
- The registry owns identity/edges/claims; the repo board owns packet detail and evidence.
- A shared contract has exactly one active owner; other repos pin it read-only.
- Every mutation is CAS + a recorded agent identity; every acceptance has evidence.
- Do not copy packet bodies here.
- Run `python3 validate_work.py` at the start and end of every cross-repo session.

### Linear mirror

The [Linear sync runbook](linear-sync.md) covers the one-way Portfolio Delivery mirror.
The registry continues to own claims, review and integration.
