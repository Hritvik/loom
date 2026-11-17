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
| `loom` | The single CLI: every engine verb, plus `tick`, `workers`, `schedule`, `deploy`, `linear` and `logs`; policy in `~/.config/loom/loom.json` |
| `engine.py` | The engine verbs and the policy pass, imported by `loom`; not a separate command |
| `AGENT-GUIDE.md` | The briefing to point a product agent at: the loop, the enforced rules, discovery and escalation |
| `validate_work.py` | Schema, drift, dependency, lease, epoch, review, event-chain and checkpoint verification |
| `schemas/task.schema.json` | The central task contract |
| `decisions/` | Reconciliation decisions |
| `a2a/` | A2A v1.0 façade: Agent Card, JSON-RPC, read and claim operations |
| `mcp/` | MCP stdio server: registry tools that appear in an agent's tool list |
| `tests/` | CLI, validator, A2A and MCP tests on disposable work homes |

## CLI

Every mutation goes through `./loom`; MCP and A2A delegate to it, so the guarantees below are
identical on all three surfaces.

| Command | Purpose |
|---|---|
| `init` | Create an empty registry and its first board |
| `create` | Author a central task on a registered board |
| `list` | Filter by board, state, readiness or ownership; `--ready` ranks by downstream work |
| `search` | Find tasks by text without knowing the namespace |
| `dependents` | Who is waiting on a task, directly and transitively |
| `graph` | Render the dependency graph: `text`, `mermaid`, `dot` or standalone `html` |
| `decisions` | What needs the owner: human-blocked tasks and questions addressed to them |
| `integration-plan` | The lane-3 worklist: merge commands, delta re-reviews, reopens and the decisions the loop is holding; `--out` writes markdown |
| `tick` | Policy pass: reconcile, record approvals, claim and launch implementers and reviewers; `--apply` acts, `--quiet` is silent when idle |
| `migrate` | Copy a file work home into SQLite, verifying the event chain |
| `export` | Render the store as YAML or JSON, replacing diffable state in git |
| `show` | One task with dependencies, claim, evidence and review |
| `attempts` | Every agent run for a task: identity, worktree, process and outcome; `--live` for the running ones |
| `claim` | Take a lease with compare-and-swap on revision; prints the fencing epoch and the attempt |
| `renew` | Heartbeat and extend a live lease (presents the epoch) |
| `release` | Return a claimed task to the ready pool with a reason (presents the epoch) |
| `takeover` | Take over an expired lease after the lock-delay, with a recorded reason |
| `transition` | Move through the state machine, enforcing evidence and review |
| `annotate` | Record an integration receipt for a task integrated or accepted before receipts existed |
| `ask` | Ask another agent a question about a task |
| `inbox` | List unanswered questions addressed to an agent |
| `answer` | Answer a question addressed to you, once |
| `events` | Read or verify the hash-chained audit log |
| `snapshot` | Write the fold baseline: the registry as a document at this event sequence; `--list` shows the ones taken |
| `foldcheck` | Check the registry against a fold of the log from the latest snapshot, naming every field that disagrees |
| `rebuild` | Rewrite the registry from the fold, behind a `VACUUM INTO` restore point and a diff that passes |
| `edge` | Inspect or edit cross-task dependency edges |
| `board` | List, add, remove, sync or import boards |
| `reconcile` | Compare or adopt board statuses for imported tasks |
| `genesis` | Write the single bootstrap event |

## Integration receipts

An integration is only proven by the event that records it: the `transition` to
`integrated` carries `integrated_source`, `integration_tip` and the digest of the content it
approved, verified against the board's integration branch. Recording `reviewed` binds the
receipt to that content: the receipt stores the changed path and blob pairs and a SHA-256
digest over them, taken from the approved commit. Integration checks each recorded blob at the
ref tip instead of re-diffing a history that may have moved. When a path moved it names the exact
paths in the error, e.g. `approved paths changed in refs/heads/main: src/one/value.txt`.

Receipts written before content binding keep a null digest. They are reported as incomplete —
by `loom show`, `loom tick` (`receipt_incomplete`), `loom integration-plan` and
`validate_work.py` — and are never silently repaired by recomputing the paths at integration
time. Reopen the task and run the review again.

Tasks migrated from the pre-SQLite registry have no integration event, so `validate_work.py`
warns and `loom show` prints `integrated: NO RECEIPT`.

Pre-migration integrations whose commits no longer exist are accepted as proven by evidence
only, per `decisions/integration-receipts-001.md`; the validator reports the count as one
accepted-gap line. `loom annotate` repairs a task when a provable commit exists, without
rewriting history and without inventing a hash:

```bash
./loom annotate <id> --revision <n> --integrated-source <full-commit> \
  --base <base-commit> --reason "how the commit was established" --agent <you>
```

The commit must exist in the owner checkout and be in the history of the board's
`integration_ref`. `--base` is optional; when given, the receipt also records the path/blob
pairs and their digest, and re-checks that the paths the commit changed are unchanged at the
integration tip. Annotating is refused when a receipt already exists, and the event is appended
to the audit log like any other.

## One command

`./loom` is the single CLI. The engine verbs are top-level — `loom claim`, `loom transition`,
`loom events` — and the unattended verbs sit alongside them. Policy lives in
`~/.config/loom/loom.json`, not in a launchd plist.

```bash
./loom list --ready             # engine verbs are top-level
./loom claim alpha/W-1 --revision 0 --ttl 45m
./loom tick                     # preview; limits come from the config
./loom tick --apply             # claim, launch and review
./loom tick --apply --quiet     # the scheduled form: silent when it did nothing
./loom workers                  # running, with pid, branch and worktree
./loom workers stop <task>
./loom schedule status          # label, plist, interval, job state and the last log lines
./loom schedule install --restart   # write both plists for the current engine, then start
./loom schedule start|stop
./loom deploy status            # development head, released head, what the schedule runs
./loom deploy promote           # copy the committed tree into the release the scheduler runs
./loom deploy rollback
./loom linear sync --all --apply --if-changed
./loom logs <task>
```

`loom` routes engine verbs to `engine.py` in-process; there is no second command name.

The launchd job runs `loom tick --apply --quiet` every **120 seconds**. An idle pass is ~0.5s and
prints nothing; a pass that claims, reviews, records or escalates prints its report. `schedule status`
shows the interval alongside the label and plist, so the cadence is visible rather than implied.

### Development is not deployment

The scheduler runs a **promoted release**, not the development checkout. `loom deploy promote`
archives the committed tree at `HEAD` into `~/.local/state/loom/engine/` and records the commit and a
tree digest in `engine.release`; `~/.local/state/loom/engine.prev/` keeps the previous one for
`rollback`. Promotion refuses an uncommitted tree, so what runs is always a commit.

That matters because the engine coordinates every board: a commit to `capabilities/work` must not
change behaviour on the next 120s pass. The loop is edit → test → commit → promote → restart, and
`loom deploy status` says `STALE` whenever development is ahead of the release. `loom schedule
install` writes both plists against the promoted tree, so the path is never hand-edited.

## Running unattended

One `tick` pass does these phases in order:

1. **Reconcile** attempts whose process is gone. An attempt that advanced the task is `done`; one
   that did not is `failed`, and two consecutive failures block the task for a human. A reviewer
   attempt is `done` when it recorded a verdict. An attempt left `starting` with no launch is
   reaped as `failed`; it never ran, so it never requeues.
2. **Record** `reviewed` for every green task whose latest review request carries an approval. The
   coordinator acts; the reviewer of record is the agent that answered.
3. **Re-issue** a review request when a green task has no current request (a drifted revision or a
   known-agent addressee that is not the configured reviewer). A source from an older revision is
   reported `resubmit_required`, never re-asked: the coordinator must not attach an old commit to new
   work.
4. **Requeue** changes-requested work: release the scheduler worker’s claim, or take over an expired
   claim, so a new implementer can pick it up.
5. **Integrate** reviewed work only when enabled (`auto_integrate` or `--integrate`). The approved
   commit must already be an ancestor of the board’s integration ref and its paths unchanged; this
   records a receipt, it does not merge. Everything else is reported as `merge-needed`,
   `paths-diverged`, `dependency-blocked` or `receipt-missing`.
6. **Launch implementers** for claimable tasks, ranked by transitive downstream weight, up to the
   `implementer` limit. Each attempt runs in `~/.local/state/loom/worktrees/<slug>-a<N>` on
   `loom/<slug>-a<N>`; a relaunch branches from the attempt before it.
7. **Launch reviewers** for open requests addressed to `opencode/reviewer-agent`, up to the
   `reviewer` limit. Each runs in a detached checkout under `~/.local/state/loom/reviews/<slug>`.

The manual preview ends with the roots that are gating the queue (reviewed, human, agent, planned,
green, expired). `--quiet` prints that report once per distinct root set, not every pass.

Capacity, TTL, worker command, reviewer identity, humans, known agents and the integration opt-in
are in `~/.config/loom/loom.json`.

## Storage

State lives in SQLite at `~/.local/state/loom/work.db`. The database is outside every repository,
so state is not a git artifact any more. The file backend is retired; `FileStore` survives only as
the importer `loom migrate` reads a pre-SQLite work home with.

Tasks and boards are documents `(id, body)`; status and program are expression indexes, not
duplicated columns. A mutation commits the registry and its event in one transaction, and
`work.db.head` mirrors the chain head. `loom events --verify` checks both; see
`docs/adr/0002-store-is-a-document-log.md` for the threat model.

The commit is differential: the store remembers the body it read for each document and writes
only the ones the command changed, so a claim updates one task row instead of rewriting the
registry. What that saves is the write — one statement and 16 KiB of WAL — not the in-memory
pass that compares documents, which is still linear in registry size. The event log is unchanged
by this — it was and remains the audit chain.

`export` renders tasks, boards, claims and edges in rowid order, so the same database exports the
same bytes in every process, which is what makes it diffable in git and usable as a genesis digest.

```bash
./loom list --ready                                                    # default store
./loom migrate --from <old work home> --to ~/.local/state/loom/work.db # import a file home
./loom export --out registry.yaml                                      # snapshot for review
```

The exported file is a snapshot for review, not the source of truth. `validate_work.py` reads the
same store, so it validates live state.

From the latest `loom snapshot` forward, every event that changes state carries the change it made
(`state_delta`) in its own body. `loom foldcheck` replays those deltas onto the snapshot and
compares the result with the registry field by field: a lost field is named, a forged one is named,
and neither is silently repaired. `loom rebuild` rewrites the registry from the fold only after a
`VACUUM INTO` restore point and a diff in which every record the rebuild would delete is explained
by the log. Events written before the snapshot are not folded and not backfilled.

## Three repositories

| Repository | Holds | Visibility |
|---|---|---|
| **loom** (this one) | the engine: CLI, store, MCP, A2A, validator, templates, examples | public |
| **content** | the portfolio's own artefacts: maps, plans, specs, capability stewardship, evidence, memory | private |
| **contracts** | the wire formats services exchange, with fixtures and consumer pins | public |

loom knows the *kinds*; a content repository holds the *instances*. Point it at yours:

```bash
export WORK_CONTENT_HOME=~/repositories/your-content         # your content repository
export WORK_CONTRACTS=~/repositories/your-contracts     # your contract registry
export WORK_DB=~/.local/state/loom/work.db                   # the task database
```

`docs/artifacts.md` lists every kind, where it lives, and how it is promoted.

## Authority

- **Central registry** — task identity, program, cross-repo dependencies, write-path conflicts,
  claims/leases and the revision counter. Authoritative once a task has been adopted
  (`revision >= 1`, i.e. mutated through `./loom`).
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
export WORK_AGENT=opencode/alpha-agent-3      # identity recorded in every event

./loom list --ready                          # claimable: ready + deps met + no path conflict
./loom show alpha/W-1
./loom claim alpha/W-1 --revision 0 --ttl 45m
```

`claim` is a compare-and-swap: the `--revision` you pass must equal the registry revision. A stale
revision fails, a live lease held by another agent fails, and overlapping `write_paths` fail. The
claim also returns a **fencing epoch**, a counter incremented on every claim and takeover:

```
claimed alpha/W-1 at revision 1 until 2030-01-01T10:30:00Z epoch 1
```

Present that epoch on every mutation you make as the holder. A takeover increments it, so a worker
whose lease was taken over is refused even if it re-reads the revision:

```bash
./loom renew    alpha/W-1 --revision 1 --epoch 1 --ttl 45m   # revision stays 1
./loom release  alpha/W-1 --revision 1 --epoch 1 --reason "handing back for review"
# takeover is a separate case: after the lease expires and the lock-delay passes
./loom takeover alpha/W-1 --revision 2 --reason "lease expired; agent went silent"
```

Authority is a *live* lease, not a recorded claim: once the lease expires, the holder can no longer
move the task to `red` or `green` (stopping with `blocked` or `release` is always allowed). Heartbeat
with `renew` before expiry.

`takeover` works only after expiry **plus a lock-delay** — two minutes by default. The registry can
reject a stale epoch, but a git branch cannot, so the grace window gives a stale worker time to
notice and stop before the task is re-granted. It records the previous agent, its epoch and a reason.

## Move work through the state machine

```bash
./loom transition alpha/W-1 --revision 1 --to red
./loom transition alpha/W-1 --revision 2 --to green
./loom transition alpha/W-1 --revision 3 --to reviewed \
  --epoch 1 --reviewer reviewer-7 --decision approve --review-answer <answer-event>
./loom transition alpha/W-1 --revision 4 --to integrated \
  --integrated-source <approved-commit> --evidence evidence/W-1.md
./loom transition alpha/W-1 --revision 5 --to accepted   --evidence evidence/W-1.md
```

Rules enforced by the CLI:

- `reviewed` requires an independent reviewer (`--reviewer`, `--decision approve`). The reviewer
  cannot be the acting agent, the live claim holder, or any agent that previously claimed the task —
  releasing a lease does not let the implementer approve its own work.
- `integrated` and `accepted` require evidence.
- `accepted` requires every dependency to be accepted/integrated.
- Illegal transitions are refused; `claim` is the only way to enter `claimed`.
- Reviewing clears the implementation claim.

## Reviewing

An implementer submits by asking an independent reviewer:

```bash
./loom ask alpha/W-1 --to opencode/reviewer-agent --question "Review the submitted revision" \
  --review-source <40-char commit> --review-evidence evidence/W-1.md
```

The reviewer answers with a recorded verdict — `approve` or `changes requested`, with a nonblank
note. The reviewer of record may never be the acting agent or an earlier claimant.

`./loom tick --apply` runs the reviewer side unattended:

1. **Launch** — for each open request addressed to `opencode/reviewer-agent`, start an independent
   reviewer in a *detached* checkout at the exact source commit (under `~/.local/state/loom/reviews/`,
   separate from the implementer's branch). The reviewer reads the diff, the evidence and the code,
   runs what it can, and answers.
2. **Record** — for each green task whose latest request carries an approval, the coordinator
   (`opencode/loom-coordinator`) records `reviewed`, naming the reviewer and the answer event. The
   reviewer decides; the coordinator records, so the acting agent is never the reviewer of record.
3. **Bound** — a request that crashes `--max-review-attempts` times is reported as *stalled* and
   left for a human. A `changes requested` verdict leaves the task green for rework and is never
   auto-transitioned.

Requests addressed to a human (for example `<your-identity>`) are never picked up by a reviewer worker.
The reviewer identity, attempt bound and coordinator are policy in `~/.config/loom/loom.json`.

## Create work centrally

New work starts here, not in a repository.

```bash
./loom create --id alpha/W-15 --board alpha.program --title "…" --goal "…" \
  --status ready --depends-on beta/W-1 --write-path src/main/java/...
```

Then add the detailed work-item section to the owning document (program) or materialise the packet:

```bash
./loom board sync  --board gamma.board
./loom board sync  --board gamma.board --check   # CI: non-zero if stale
./loom board import --board alpha.w07 --program alpha  # adopt existing packets
```

## A2A façade

Agents that speak A2A can use the same registry without the CLI:

```bash
python3 a2a/server.py --port 8787
curl -s http://127.0.0.1:8787/.well-known/agent-card.json
```

`ListTasks`, `GetTask`, `SendMessage` (claim/renew/release/takeover/transition) and `CancelTask` are
supported; mutations are delegated to `./loom`, so CAS, leases and audit are identical. The card
declares no streaming/push/extended-card support and implements no authentication; it binds
loopback by default. See [`a2a/README.md`](a2a/README.md).

## MCP discovery

Agents discover the registry from their tool list, not from a document:

```bash
python3 mcp/server.py --print-config opencode     # or claude / codex
```

Tools: `work_status`, `work_list`, `work_get`, `work_claim`, `work_renew`, `work_release`,
`work_takeover`, `work_transition`, `work_inbox`, `work_search`, `work_dependents`, `work_ask`,
`work_answer`. `work_status` is the pre-planning entry point; mutations delegate to `./loom`, so
the guarantees are identical. The server infers `repo` from the workspace path and `agent` from the
task’s program namespace (`<program>-agent`), so one global registration works in every repository. See
[`mcp/README.md`](mcp/README.md).

## Integration map (optional)

A program map is a cross-service view that per-repository work items do not carry: who owns which
contract, which task appears in which journey, and which slice an agent can start. It is optional and
lives in your content repository; when present, `validate_work.py --programs <dir>` checks that every
task, gate and contract reference exists.

## Reconcile

```bash
./loom reconcile --board gamma.board                         # report drift, exit 1
./loom reconcile --board gamma.board --apply --reason "board authoritative"
```

`--apply` adopts the board status for imported/board-sourced tasks and writes a `reconcile` event.
Never silently overwrite another agent; record a reason.

## Audit and verify

```bash
./loom events --verify                 # hash chain + terminal state vs registry
./loom events --task alpha/W-1
python3 validate_work.py               # full cross-repo check
python3 -m unittest discover -s tests -v   # full suite: CLI, validator, A2A, MCP, surface and plans
```

`validate_work.py` checks: task schema, duplicate IDs, board/status drift, unregistered packets,
dependency cycles and readiness, unknown edges/dependencies, active leases (expiry, base revision,
heartbeat ordering), shared-artifact ownership conflicts, cross-repo write-path overlap, the event
chain's sequence, hashes, timestamps and terminal state, and the capability-steward registry beside
this home (artifact paths, owner ids, dormant owners).

### Public boundary

The engine ships to repositories that share none of this portfolio's identities, so the suite
enforces the boundary instead of an evidence file claiming it:

| Check | Fails when |
|---|---|
| `tests/test_boundary.py` (source) | a shipped module carries a task-id literal, branches on a program namespace, or defaults `--program`/`--focus` to one |
| `tests/test_boundary.py` (tree) | a tracked file holds an absolute home path or a personal mailbox |
| `LOOM_BOUNDARY_DENYLIST` sweep | a name the operator guards appears in a tracked file |

The denylist lives outside this repository: point `LOOM_BOUNDARY_DENYLIST` at a file with one
literal per line (`#` comments allowed). Without it the sweep skips with a pointer, so a public
checkout still runs its suite. Examples in source use `<program>/<NAME>`; examples in docs and
tests use generic programs and reserved example domains.

## Messages

Claims say who is working; messages say what is blocking. A question is an event, not a chat
message, so it survives session ends and is attributable.

```bash
./loom ask gamma/R-1 --to opencode/reviewer --question "Who owns the privacy review?" \
  --needed-by 2026-11-07 --agent opencode/gamma-agent
./loom inbox --agent opencode/reviewer          # open questions only; --all includes answered
./loom answer gamma/R-1 --question 454 --decision "I do, starting Monday" \
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
./loom search avatar                      # id, title, goal, program, board, write paths
./loom search "association" --limit 5
./loom dependents beta/W-1                # who is blocked on it, directly and transitively
./loom graph --open --format html         # the whole picture, written to task-graph.html
./loom graph --task beta/W-1              # one chain: its dependencies and dependents
./loom list --ready                       # ranked by how much downstream work each releases
./loom list --awaiting human              # decisions only the owner can make
./loom show gamma/R-1               # includes a `blocks:` line
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

- IDs are namespaced (`alpha/W-1`, `beta/W-6`, `gamma/W11-A`) and are never renumbered or reused.
- The registry owns identity/edges/claims; the repo board owns packet detail and evidence.
- A shared contract has exactly one active owner; other repos pin it read-only.
- Every mutation is CAS + a recorded agent identity; every acceptance has evidence.
- Do not copy packet bodies here.
- Run `python3 validate_work.py` at the start and end of every cross-repo session.

### Linear mirror

The read-only projection lives in `linear_projection.py`; the sync is `linear_sync.py` and the front
door is `loom linear sync --all --apply`. The registry owns claims, review and integration; the
mirror is one-way and never writes back to the registry; it creates and updates Linear issues.

Cards are one per task, titled `[task-id] title`, with a managed description block (runway, next
actor, implementer, reviewer, dependencies, blockers, unlocks, revision). Human notes outside the
markers are preserved. The machine config lives in `~/.config/loom/linear-portfolio.json`;
`templates/linear-portfolio.example.json` shows the state and label map.

The board is organised for the questions a human actually asks:

| State | Lanes |
|---|---|
| Todo | claimable |
| In Progress | implementing, changes_requested |
| Needs Review | needs_review |
| In Review | in_review |
| Awaiting Integration | awaiting_integration |
| Blocked | blocked |
| Backlog | planned, draft |
| Canceled | rejected, released |
| Done | integrated, accepted, done |

Each card carries one program label and `Needs Human` when the next actor is the human or the task
is blocked on a human decision. Priority comes from transitive downstream weight: 6+ urgent, 3–5
high, 1–2 medium, 0 low; Done cards stay No priority. The sync is idempotent: a run that changes
nothing writes nothing.

The launchd job runs `loom linear sync --all --apply --if-changed` every **300 seconds**.
`~/.local/state/loom/linear.head` records the `(scope, config hash) → (seq, hash)` last projected; an unchanged head exits
without an API call. A rewritten database with the same event count still has a different hash, so
it triggers a sync, and the chain/checkpoint verification then refuses to project it.
`loom schedule start|stop` controls both jobs; `loom schedule status` shows both.
