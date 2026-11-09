# Agent work-coordination protocol

Status: adopted and implemented 2026-10-31. This is a **composition of existing standards**, not a
new wire protocol. Phase 2 (central CLI, leases, board materialisation, audit log) and phase 3 (A2A
façade) are complete.

## What already exists

| Layer | Standard | What it gives us | What it does not |
|---|---|---|---|
| Tools/data | **MCP** (Agentic AI Foundation) | Agents reach repos, CI, databases through typed tools | No task board, claims or handoffs |
| Agent-to-agent | **A2A v1.0** (Linux Foundation → AAIF, 2026) | Agent Cards, Task lifecycle, ListTasks/GetTask, streaming, push notifications, artifacts | No shared backlog, no leases, no cross-repo reconciliation |
| Task graph | **Beads** (`bd`) | Git/Dolt-synced issue graph, hash IDs, dependencies, `bd ready`, gates, multi-agent routing | Not a portfolio capability map; needs a shared remote |
| Human tracker | GitHub Issues/Projects, Linear | Familiar UI, notifications | No CAS claims or agent-native dependency semantics |

ACP merged into A2A in 2025. MCP and A2A are complementary, not competing.

## Our model: central authority, local execution

```text
~/.local/state/loom/work.db            # central: identity, edges, claims, revisions, events
  └── events                          # append-only hash-chained audit trail
  └── references ──> repo board.json   # local: packet scope, evidence, checkpoints
        └── git commits / PRs          # durable audit + code
```

- **All plans and work items are authored centrally** through `work/task`. Repos no longer invent
  task IDs.
- **Packets are materialised locally** from a central task (`task board sync`). Packet bodies,
  evidence and checkpoints stay in the owning repository.
- **Git is the transport.** Claims and transitions are registry mutations plus event-log entries;
  commits and PRs are the review surface. No service to operate.

### Authority after adoption

| State | Authority |
|---|---|
| Imported task (`revision == 0`, `status_source: board`) | Repo board; differences are errors, resolved with `reconcile` |
| Adopted task (`revision >= 1`) | Central registry; boards are mirrors, refreshed with `board sync` |
| Program heading/narrative (`work-items.md`) | The program document; the registry mirrors status |

The registry decides *who may work and in what order*; the board decides *what the packet contains*.
`board sync` never deletes packets and never rewrites packet scope, evidence or write paths.

## State model

A2A-compatible externally, richer internally:

| Internal | A2A `TaskState` | Meaning |
|---|---|---|
| `draft` | `submitted` | authored, not ready |
| `planned` | `submitted` | scheduled, not yet unblocked |
| `ready` | `submitted` | unblocked, claimable |
| `claimed` / `red` / `green` | `working` | owned; RED/GREEN/refactor loop |
| `blocked` | `input-required` | needs a decision or external input; `blocked_needs` records whether an agent or the owner can resolve it |
| `reviewed` | `working` | packet green, independent review passed |
| `integrated` | `working` | merged and gate-passed, awaiting acceptance |
| `accepted` | `completed` | parent accepted after reviews |
| `rejected` | `failed` | gate or review failed |
| `released` | `canceled` | voluntarily returned to the pool |
| `in-progress`, `done` | `working` / `completed` | legacy imported labels only |

`reviewed`, `accepted`, evidence paths and review records travel in `metadata` when exposed over A2A.

## Claim / lease protocol

A claim is a compare-and-swap on the task revision plus a time-boxed lease:

```yaml
claim:
  agent: opencode/alpha-agent-3
  base_revision: 42
  lease_expires: 2026-10-31T12:30:00Z
  heartbeat: 2026-10-31T12:00:00Z
```

```bash
task claim    <id> --revision N --ttl 30m
task renew    <id> --revision N --ttl 30m
task release  <id> --revision N --reason TEXT
task takeover <id> --revision N --reason TEXT   # only after expiry
```

Rules enforced by the CLI:

- `--revision` must equal the registry revision, otherwise the operation fails (stale writer).
- Only `ready` tasks with satisfied dependencies can be claimed.
- A live lease blocks other agents; renewal by a non-owner is refused.
- `takeover` requires an expired lease and a recorded reason; the previous agent is in the event log.
- Overlapping `write_paths` with another active claim are refused.
- Default lease is 30 minutes; heartbeat with `renew` before expiry.
- One file lock serialises mutations, so concurrent processes cannot interleave writes.

## Event log

Every mutation appends one hash-chained row to the `events` table:

```json
{"seq":2,"ts":"2026-10-31T10:00:00Z","event":"claim","task":"alpha/W-1","agent":"opencode/alpha-agent-3","status":"claimed","revision":1,"lease_expires":"2026-10-31T10:30:00Z","prev":"<sha256 of previous line>"}
```

`seq` is contiguous, `prev` chains each record to its predecessor, and the terminal event for every
task must match the registry's revision, status and claim owner. `task events --verify` checks the
chain; `validate_work.py` additionally cross-checks it against registry state, so a hand edit or an
interrupted write is detected rather than trusted.

## A2A façade

`work/a2a/server.py` exposes the registry over A2A v1.0 without a second source of truth:

```text
client agent ──JSON-RPC──> a2a/server.py ──CLI──> work/task ──> registry + events
```

- Discovery at `/.well-known/agent-card.json`; JSON-RPC at `/`; protocol `1.0`.
- `ListTasks`, `GetTask`, `SendMessage` (claim/renew/release/takeover/transition), `CancelTask`.
- Agent Card declares no streaming, push notifications or extended card, and those operations return
  the matching A2A capability error.
- Mutations delegate to the CLI, so CAS, leases, dependency gates, path conflicts and the audit log
  are identical on both surfaces.
- `Task.metadata` carries the richer internal state (revision, board, claim, dependencies, write
  paths, evidence, review) plus `claimable`/`blockedBy`, which mirror the CLI claim gate.
- No authentication; the server binds loopback by default and the agent identity is self-asserted.
  Add authentication and TLS before exposing it beyond a trusted local host.

## MCP façade

`work/mcp/server.py` exposes the same registry to agents that discover capabilities through MCP:

```text
agent tool list ──tools/call──> mcp/server.py ──CLI──> work/task ──> registry + events
```

- `work_status` is the discovery entry point: ready work, active claims, expired leases and
  registry-to-board drift. Its tool description tells the model to call it before planning.
- `work_list` / `work_get` for discovery; `work_claim` / `work_renew` / `work_release` /
  `work_transition` for execution.
- Newline-delimited JSON-RPC over stdio; no network listener and no authentication.
- Mutations delegate to the CLI, so CAS, leases, dependency gates, path conflicts and the audit log
  are shared with the CLI and A2A surfaces.

## Integration map (optional)

A program map is an optional cross-service view held in the content repository. It owns service
ownership, the contract matrix (id, kind, owner, producers, consumers, gating task), end-to-end
journeys, and which slices an agent may start. It contains no task bodies. When present,
`validate_work.py --programs <dir>` checks that every task, gate and contract reference exists.

## Review routing

Green tasks are available for review submission. A reviewer receives a task inbox request
with the immutable source revision and evidence, then records their own decision in the
audit log. The transition validates the approval event, latest submission, reviewer and
green revision. Integration verifies the approved Git commit and touched paths in the
owner repository integration branch. Review
and integration are separate gates. The reviewer chooses normal or adversarial review
and whether to review directly or with independent subagents. One assigned reviewer
owns the verdict. See the [review pipeline](AGENT-GUIDE.md#independent-review-pipeline).

## Reconciliation

1. Pull the registry and all referenced boards.
2. Run `python3 work/validate_work.py`: schema, drift, duplicate IDs, unregistered packets,
   dependency cycles/readiness, expired leases, contract-claim conflicts, write-path overlap and the
   event chain.
3. Reconcile evidence: integrated packets must carry an evidence path; reviews name a reviewer.
   The CLI refuses a reviewer that is the acting agent, the current claim holder, or any agent that
   previously claimed the task.
4. Resolve status drift with `task reconcile --board …` (report) or `--apply --reason …` (adopt).
5. Record the outcome as a decision or a registry revision; never silently overwrite another agent.

## Adoption phases

1. **Done** — central registry, namespaced IDs, validator, reconciliation decisions, reference-adapter
   scope, import of all existing boards (69 tasks).
2. **Done** — task schema, `work/task` CLI with CAS/lease/heartbeat/takeover, append-only event log,
   board sync/import, reconcile, and a 12-test suite.
3. **Done** — A2A v1.0 façade: Agent Card, `ListTasks`, `GetTask`, `SendMessage` actions and
   `CancelTask`, delegating mutations to the CLI.
4. **Done** — MCP stdio server so agents discover the registry from their tool list before
   planning. Covered by the `work_status` tool description and `--print-config` client snippets.
5. **Next** — a shared git remote and pull/push discipline for multi-machine agents; authentication
   only if the A2A endpoint is exposed beyond loopback. The integration map and dependency graph are
   current as of 2026-11-01.
6. **Scale** — if concurrent agents exceed what git+validator can arbitrate, move the task graph to
   Beads (Dolt remote + `bd ready`/gates) or a small Postgres service behind the same A2A facade.

## Rules

- One authoritative task ID; never renumber or reuse.
- The registry owns identity/edges/claims; the repo board owns packet detail.
- A shared contract has exactly one active owner; others pin it read-only.
- No task bodies copied into the registry.
- Every state change carries an agent identity; every acceptance has evidence.
- Run the validator at the start and end of every cross-repo session.
